"""JARTIC 交通量 API から、全国の直轄国道の観測点の大型車の1時間値を取り、
「いまの大型車」ページ用の web/data/trucks_now.json を作る。

- 規約の要点は docs/plan/2026-11-03_JARTIC規約の要点.md。API キーは不要。
- 1回の要求で全国の1時間分（常設 約 1000 地点、CCTV 約 1000 地点）が返る。1時間につき 1 種類 1 回で、
  直近の時間と過去 23 時間、先週の同じ時刻の分を合わせて 50 回ほど。要求の間は 3.5 秒あける。
- 応答そのものは data/traffic/raw/now/ に置く（.gitignore 済み、コミットしない）。過去の時間はキャッシュを再利用し、
  直近の時間だけは毎回取り直す。HTTP エラーのときは再試行せず止める。
- 欠測は null のままにして、0 台として数えない。

CCTV トラカンの欠測の判定（2026-11-06 の応答 981 件で確認）:
  - 台数が空（null）のときは欠測。「5分欠測処理フラグ」はそのうち 314 件で 2、残りで 0 だった。
  - フラグが 2 の行は台数がすべて空だった。これも欠測として扱う。
  - フラグが 1 の行（33 件）は台数が入っている。5分値の一部を補った値と読めるが、仕様書で意味を確かめていない。
    台数が入っているので使い、フラグ 1 の地点数を notes に書く。意味が分かれば扱いを変える。

使い方:
    uv run python tools/fetch_trucks_now.py
    uv run python tools/fetch_trucks_now.py --loop 20
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_jartic as fj  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "data" / "trucks_now.json"
PREF_GEOJSON = ROOT / "web" / "data" / "pref.geojson"
CACHE_NAME_DIR = "now"  # fj.RAW の下の置き場所

TYPE_1H = fj.TYPE_1H
TYPE_CCTV = "t_travospublic_measure_1h_img"
JST = timezone(timedelta(hours=9))
JAPAN_BBOX = (122, 24, 146, 46)
SNAP_KM = 20.0  # 海上の点を最寄りの県の境界に寄せる上限

ATTRIBUTION_PROCESSED = "国土交通省 API 機能による交通量(参考値)を加工して作成"
ATTRIBUTION_SERVICE = "このサービスは、交通量 API 機能を使用していますが、サービスの内容は国土交通省によって保証されたものではありません。"

CCTV_KEYS = {
    "up_heavy": "上り・大型交通量", "down_heavy": "下り・大型交通量",
    "up_small": "上り・小型交通量", "down_small": "下り・小型交通量",
    "up_flag": "上り・5分欠測処理フラグ", "down_flag": "下り・5分欠測処理フラグ",
}


# ---------- 時間コード（通信しない） ----------

def code_to_dt(code: int) -> datetime:
    s = str(code)
    return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]), int(s[8:10]), tzinfo=JST)


def dt_to_code(dt: datetime) -> int:
    return int(dt.strftime("%Y%m%d%H")) * 100


def shift_code(code: int, hours: int) -> int:
    return dt_to_code(code_to_dt(code) + timedelta(hours=hours))


def hours_ending_at(latest: int, n: int = 24) -> list[int]:
    """latest で終わる n 時間分の時間コード（古い順）。"""
    return [shift_code(latest, -i) for i in range(n - 1, -1, -1)]


# ---------- 1件の読み取り（通信しない） ----------

def _cctv_value(props: dict, key: str, flag_key: str):
    v = props.get(key)
    if v is None or v == "":
        return None
    if str(props.get(flag_key, "0")) == "2":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_props(props: dict, kind: str) -> dict:
    """属性から上り・下りの大型と小型の台数を返す。欠測・異常は None（0 にしない）。kind は "常設" か "CCTV"。"""
    if kind == "CCTV":
        k = CCTV_KEYS
        return {
            "up_heavy": _cctv_value(props, k["up_heavy"], k["up_flag"]),
            "down_heavy": _cctv_value(props, k["down_heavy"], k["down_flag"]),
            "up_small": _cctv_value(props, k["up_small"], k["up_flag"]),
            "down_small": _cctv_value(props, k["down_small"], k["down_flag"]),
            "flag1": "1" in (str(props.get(k["up_flag"])), str(props.get(k["down_flag"]))),
        }
    up_flags, down_flags = fj.FLAG_KEYS_UP, fj.FLAG_KEYS_DOWN
    return {
        "up_heavy": fj._value(props, "上り・大型交通量", up_flags),
        "down_heavy": fj._value(props, "下り・大型交通量", down_flags),
        "up_small": fj._value(props, "上り・小型交通量", up_flags),
        "down_small": fj._value(props, "下り・小型交通量", down_flags),
        "flag1": False,
    }


def parse_feature(ft: dict, kind: str) -> dict | None:
    """GeoJSON の1地点を {id, kind, lon, lat, code, up_heavy, ...} にする。座標か地点コードが無ければ None。"""
    props = ft.get("properties") or {}
    geom = ft.get("geometry") or {}
    coords = geom.get("coordinates") or []
    pid = props.get(fj.K["point"])
    if pid is None or not coords:
        return None
    c = coords[0]
    if geom.get("type") == "Point":
        c = coords
    try:
        lon, lat = float(c[0]), float(c[1])
    except (TypeError, ValueError, IndexError):
        return None
    return {"id": pid, "kind": kind, "lon": lon, "lat": lat, "code": props.get("時間コード"), **parse_props(props, kind)}


def pair_total(up, down):
    """上りと下りの両方が有効なときだけ合計。どちらかが欠けたら None。"""
    if up is None or down is None:
        return None
    return up + down


# ---------- 県の割り当て（通信しない） ----------

def _ring_bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _in_ring(x: float, y: float, ring) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class PrefIndex:
    """県の多角形を持ち、点の県名を返す。外側の輪の中で、穴の外にある点を県内とする。"""

    def __init__(self, geojson: dict):
        self.polys = []  # (名前, bbox, [外側の輪, 穴...])
        self.vertices = []  # (名前, lon, lat) 境界の頂点（海上の点を寄せるため）
        for ft in geojson["features"]:
            name = ft["properties"]["n"]
            g = ft["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            for rings in polys:
                self.polys.append((name, _ring_bbox(rings[0]), rings))
                for p in rings[0]:
                    self.vertices.append((name, p[0], p[1]))

    def find(self, lon: float, lat: float) -> str | None:
        for name, (x0, y0, x1, y1), rings in self.polys:
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            if _in_ring(lon, lat, rings[0]) and not any(_in_ring(lon, lat, h) for h in rings[1:]):
                return name
        # 海上や海岸線の外にある点。最寄りの県の境界の頂点が SNAP_KM 以内ならその県
        best, best_d = None, SNAP_KM
        kx = 111.32 * math.cos(math.radians(lat))
        for name, vx, vy in self.vertices:
            if abs(vy - lat) * 110.57 > best_d:
                continue
            d = math.hypot((vx - lon) * kx, (vy - lat) * 110.57)
            if d < best_d:
                best, best_d = name, d
        return best


# ---------- 集計（通信しない） ----------

def build_points(records: dict, hours: list[int], week_code: int, pref_of) -> list[dict]:
    """records: {(種類, id): {"lon","lat","by_hour": {時間コード: parse_feature の結果}}}。
    最新の時間の台数、24 時間の列、先週の同時刻を持つ地点の一覧を返す。24 時間すべて null の地点は落とす。
    """
    latest = hours[-1]
    out = []
    for (kind, pid), r in records.items():
        by = r["by_hour"]
        series = [pair_total(by[h]["up_heavy"], by[h]["down_heavy"]) if h in by else None for h in hours]
        if all(v is None for v in series):
            continue
        cur = by.get(latest)
        wk = by.get(week_code)
        small = pair_total(cur["up_small"], cur["down_small"]) if cur else None
        out.append({
            "id": pid, "type": kind, "lon": round(r["lon"], 5), "lat": round(r["lat"], 5),
            "pref": pref_of(r["lon"], r["lat"]),
            "up": cur["up_heavy"] if cur else None, "down": cur["down_heavy"] if cur else None,
            "small": small, "series": series,
            "week_ago": pair_total(wk["up_heavy"], wk["down_heavy"]) if wk else None,
        })
    return out


def week_ratio(points: list[dict], min_common: int = 3):
    """最新の合計 ÷ 先週同時刻の合計。両方が有効な地点だけで計算し、そのような地点が min_common 未満なら None。"""
    cur = wk = n = 0
    for p in points:
        now_v, ago = p["series"][-1], p["week_ago"]
        if now_v is None or ago is None:
            continue
        cur += now_v
        wk += ago
        n += 1
    if n < min_common or wk == 0:
        return None
    return round(cur / wk, 3)


def series_average(points: list[dict], n_hours: int = 24) -> list:
    """時間ごとに、その時間に観測できた地点だけで平均した大型車の台数。誰も観測できない時間は None。"""
    out = []
    for i in range(n_hours):
        vals = [p["series"][i] for p in points if p["series"][i] is not None]
        out.append(round(sum(vals) / len(vals), 1) if vals else None)
    return out


def summarize(points: list[dict]) -> dict:
    """地点の一覧から n_points、n_observed、avg_heavy、series_avg、week_ratio を作る。観測できた地点の平均だけを使う。"""
    obs = [p["series"][-1] for p in points if p["series"][-1] is not None]
    return {
        "n_points": len(points),
        "n_observed": len(obs),
        "avg_heavy": round(sum(obs) / len(obs), 1) if obs else None,
        "series_avg": series_average(points),
        "week_ratio": week_ratio(points),
    }


def heavy_share(points: list[dict]):
    """大型 ÷（大型 + 小型）。最新の時間で大型と小型の両方が有効な地点だけを合計して計算する。"""
    h = s = 0
    for p in points:
        t = p["series"][-1]
        if t is None or p["small"] is None:
            continue
        h += t
        s += p["small"]
    return round(h / (h + s), 3) if h + s > 0 else None


def build_prefs(points: list[dict]) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for p in points:
        if p["pref"]:
            by.setdefault(p["pref"], []).append(p)
    out = []
    for name, ps in by.items():
        out.append({"pref": name, **summarize(ps), "heavy_share": heavy_share(ps)})
    out.sort(key=lambda x: (x["avg_heavy"] is None, -(x["avg_heavy"] or 0)))
    return out


# ---------- 通信 ----------

def _cql(code: int) -> str:
    b = JAPAN_BBOX
    return f"道路種別=3 AND 時間コード={code} AND BBOX(ジオメトリ,{b[0]},{b[1]},{b[2]},{b[3]},'EPSG:4326')"


def _cache_name(type_names: str, code: int) -> str:
    return f"{CACHE_NAME_DIR}/{'img' if type_names == TYPE_CCTV else '1h'}_{code}"


def fetch_hour(f, type_names: str, code: int, refresh: bool = False) -> dict:
    name = _cache_name(type_names, code)
    path = fj.RAW / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if refresh and path.exists():
        path.unlink()
    data = f.get(name, type_names, _cql(code))
    if not data.get("features") and path.exists():
        path.unlink()  # 空の応答は残さない（まだ公開されていない時間を空で覚えないため）
    return data


def find_latest_hour(f, now: datetime | None = None) -> tuple[int, dict]:
    """(今 − 1 時間) の時刻から最大 4 時間さかのぼり、常設の1時間値が返る最新の時間を探す。"""
    now = now or datetime.now(JST)
    start = dt_to_code(now - timedelta(hours=1))
    for i in range(4):
        code = shift_code(start, -i)
        data = fetch_hour(f, TYPE_1H, code, refresh=True)
        if data.get("features"):
            return code, data
    raise SystemExit("直近 4 時間の1時間値が見つかりません。API の更新が止まっている可能性があります。")


def collect(f, latest: int, latest_1h: dict) -> tuple[dict, list[int], int, int]:
    hours = hours_ending_at(latest)
    week_code = shift_code(latest, -24 * 7)
    wanted = hours + [week_code]
    records: dict = {}
    flag1 = set()

    def ingest(data: dict, kind: str):
        for ft in data.get("features", []):
            rec = parse_feature(ft, kind)
            if rec is None or rec["code"] is None:
                continue
            r = records.setdefault((kind, rec["id"]), {"lon": rec["lon"], "lat": rec["lat"], "by_hour": {}})
            r["by_hour"][int(rec["code"])] = rec
            if rec["flag1"] and rec["code"] == latest:
                flag1.add((kind, rec["id"]))

    for code in wanted:
        for type_names, kind in ((TYPE_1H, "常設"), (TYPE_CCTV, "CCTV")):
            if code == latest and kind == "常設":
                data = latest_1h
            else:
                data = fetch_hour(f, type_names, code, refresh=(code == latest))
            ingest(data, kind)
        print(f"  {code} 取得済み（要求 {f.count} 回）", flush=True)
    return records, hours, week_code, len(flag1)


def notes(n_flag1: int) -> list[str]:
    return [
        "積荷の有無は分かりません。台数は大型車の通過台数です。",
        "直轄国道の観測点だけの値です。高速道路と都道府県道は含まれません。",
        "台数は観測点を通った台数で、いま走っている台数そのものではありません。",
        "上り・下りは JARTIC の定義（路線の終点から起点へ向かう方向を上りとする）で、行き帰りとは一致しません。",
        "欠測は null で、0 台として数えていません。上りと下りの両方が有効な時間だけ大型車の合計を作り、どちらかが欠けた地点は、その時間の平均に入れていません。",
        "常設の観測点は、欠測・停電・ループ異常・超音波異常のどれかが 1 の方向を欠測としました。CCTV は、台数が空か 5分欠測処理フラグが 2 の方向を欠測としました。"
        f"フラグが 1 の方向（最新の時間で {n_flag1} 地点）は台数が入っているので使っています。意味は仕様書で確かめていません。",
        "県は観測点の座標から割り当てています。海岸線の外にある点は、20 km 以内の最寄りの県境に寄せました。",
        "先週比は、最新の時間と先週の同じ時刻の両方が有効な地点だけで計算し、そのような地点が 3 未満のときは出していません。",
        "値は国土交通省の参考値で、品質は保証されていません。",
    ]


def run_once() -> Path:
    prefs_geo = json.loads(PREF_GEOJSON.read_text(encoding="utf-8"))
    index = PrefIndex(prefs_geo)
    f = fj.Fetcher()
    latest, latest_1h = find_latest_hour(f)
    print(f"最新の時間: {latest}", flush=True)
    records, hours, week_code, n_flag1 = collect(f, latest, latest_1h)
    cache: dict = {}

    def pref_of(lon, lat):
        k = (round(lon, 5), round(lat, 5))
        if k not in cache:
            cache[k] = index.find(lon, lat)
        return cache[k]

    points = build_points(records, hours, week_code, pref_of)
    out = {
        "attribution": {"processed": ATTRIBUTION_PROCESSED, "service": ATTRIBUTION_SERVICE},
        "notes": notes(n_flag1),
        "fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
        "latest_hour_code": latest,
        "hours": hours,
        "week_ago_hour_code": week_code,
        "requests": f.count,
        "points": points,
        "prefs": build_prefs(points),
        "national": summarize(points),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    tmp.replace(OUT)
    nat = out["national"]
    print(f"要求 {f.count} 回、地点 {len(points)} 件（観測できた {nat['n_observed']} 件）、全国の平均 {nat['avg_heavy']} 台/時、先週比 {nat['week_ratio']} -> {OUT}")
    return OUT


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--loop", type=int, default=0, metavar="MINUTES", help="この間隔(分)で繰り返す。20 以上")
    args = ap.parse_args()
    if args.loop and args.loop < 20:
        ap.error("--loop は 20 分以上にしてください（規約の大量アクセスを避けるため）")
    sys.stdout.reconfigure(encoding="utf-8")
    while True:
        run_once()
        if not args.loop:
            return 0
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    sys.exit(main())
