"""JARTIC 交通量 API（SPEC §16.4）から、帰り便の上位3区間の沿線の常設トラカンの大型車の1時間値を取り、
上り・下りの集計値 data/traffic/heavy_updown.json を作る。

- 規約の要点は docs/plan/2026-11-03_JARTIC規約の要点.md。API キーは不要。
- 標準ライブラリだけで動く。要求の間は 3 秒以上あける。
- API の応答そのものは data/traffic/raw/ に置く（.gitignore 済み、コミットしない）。同じ名前の応答があれば再利用して、再要求しない。
- 欠測の時間は null のままにして、0 台として数えない。
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "traffic" / "raw"
OUT = ROOT / "data" / "traffic" / "heavy_updown.json"
BACKHAUL = ROOT / "data" / "stats" / "backhaul_2021.json"

ENDPOINT = "https://api.jartic-open-traffic.org/geoserver"
USER_AGENT = "shipcompare-hackathon/0.3 (non-commercial demo)"
MIN_INTERVAL = 3.5  # 秒。規約の「短時間における大量のアクセス」を避けるため 3 秒以上あける
TYPE_1H = "t_travospublic_measure_1h"
TYPE_5M = "t_travospublic_measure_5m"  # 1時間値が無い地点の予備。名前は未確認の推測で、通らなければ失敗として記録する
ATTRIBUTION = "国土交通省 API 機能による交通量(参考値)を加工して作成"

PAIRS = {
    "兵庫–東京": ("兵庫県", "東京都"),
    "東京–三重": ("東京都", "三重県"),
    "神奈川–愛知": ("神奈川県", "愛知県"),
}
MAX_PER_PAIR = 5
MAX_POINTS = 20
BBOX_PAD_DEG = 0.04
# 3 区間の経路線は data/geo/routes.json に無い（札幌・仙台などの出発地の経路だけ）ので、県庁所在地どうしの直線で近似する。
# 道路は直線から離れるので、線からの距離の許容を 300m ではなく 3 km に広げる。
THRESHOLD_KM = 3.0

K = {  # API の項目名
    "point": "常時観測点コード",
    "date": "観測年月日",
    "time": "時間帯",
    "up": "上り・大型交通量",
    "down": "下り・大型交通量",
    "region": "地方整備局等番号",
}
FLAG_KEYS_UP = ("上り・欠測", "上り・停電", "上り・ループ異常", "上り・超音波異常")
FLAG_KEYS_DOWN = ("下り・欠測", "下り・停電", "下り・ループ異常", "下り・超音波異常")


# ---------- 集計（試験の対象。通信しない） ----------

def _value(props: dict, key: str, flag_keys: tuple[str, ...]):
    """台数を返す。欠測・機器異常のフラグが立っているか値が空なら None（0 にしない）。"""
    v = props.get(key)
    if v is None or v == "":
        return None
    if any(str(props.get(f, "0")) == "1" for f in flag_keys):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def hour_code_of(props: dict) -> int | None:
    d, t = props.get(K["date"]), props.get(K["time"])
    if d in (None, "") or t in (None, ""):
        return None
    return int(d) * 10000 + int(t)  # 20261028 と 1000 から 202610281000


def hourly_series(feature_props: list[dict], hour_codes: list[int]) -> tuple[list, list]:
    """1時間値の応答の属性から、hour_codes の並びで上り・下りの台数の列を作る。応答に無い時間は None。"""
    by_hour: dict[int, dict] = {}
    for p in feature_props:
        h = hour_code_of(p)
        if h is not None:
            by_hour[h] = p
    up, down = [], []
    for h in hour_codes:
        p = by_hour.get(h)
        up.append(_value(p, K["up"], FLAG_KEYS_UP) if p else None)
        down.append(_value(p, K["down"], FLAG_KEYS_DOWN) if p else None)
    return up, down


def hourly_from_5min(feature_props: list[dict], hour_codes: list[int]) -> tuple[list, list]:
    """5分値の応答を1時間に足す。12 コマそろった時間だけ合計を作り、1コマでも欠けた時間は None。"""
    slots: dict[int, dict[int, tuple]] = {}
    for p in feature_props:
        h = hour_code_of(p)
        if h is None:
            continue
        hour, minute = h // 100, h % 100
        slots.setdefault(hour * 100, {})[minute] = (_value(p, K["up"], FLAG_KEYS_UP), _value(p, K["down"], FLAG_KEYS_DOWN))
    up, down = [], []
    for h in hour_codes:
        s = slots.get(h, {})
        ok = len(s) == 12 and all(v[0] is not None and v[1] is not None for v in s.values())
        up.append(sum(v[0] for v in s.values()) if ok else None)
        down.append(sum(v[1] for v in s.values()) if ok else None)
    return up, down


def ratio(up_total, down_total):
    """上り ÷ 下り。下りが 0 か合計が無いときは None。"""
    if up_total is None or down_total is None or down_total == 0:
        return None
    return round(up_total / down_total, 3)


def aggregate(hour_codes: list[int], up: list, down: list) -> dict:
    """時間ごとの列から日ごとの合計、全体の比、欠測の時間数を作る。

    欠測（None）は合計に入れず、0 としても数えない。上りと下りのどちらかが欠けた時間は、比の計算からも外す。
    """
    days: dict[int, dict] = {}
    pair_up = pair_down = pair_n = missing = 0
    for h, u, d in zip(hour_codes, up, down):
        day = days.setdefault(h // 10000, {"up": 0, "down": 0, "n_up": 0, "n_down": 0, "missing_hours": 0, "pair_up": 0, "pair_down": 0})
        if u is not None:
            day["up"] += u
            day["n_up"] += 1
        if d is not None:
            day["down"] += d
            day["n_down"] += 1
        if u is None or d is None:
            day["missing_hours"] += 1
            missing += 1
        else:
            day["pair_up"] += u
            day["pair_down"] += d
            pair_up += u
            pair_down += d
            pair_n += 1
    daily = []
    for date in sorted(days):
        x = days[date]
        daily.append({
            "date": date,
            "up_total": x["up"] if x["n_up"] else None,
            "down_total": x["down"] if x["n_down"] else None,
            "ratio": ratio(x["pair_up"], x["pair_down"]),
            "missing_hours": x["missing_hours"],
        })
    return {
        "daily": daily,
        "up_total": sum(x["up"] for x in days.values()) if any(x["n_up"] for x in days.values()) else None,
        "down_total": sum(x["down"] for x in days.values()) if any(x["n_down"] for x in days.values()) else None,
        "ratio": ratio(pair_up, pair_down) if pair_n else None,
        "missing_hours": missing,
        "observed_hours": len(hour_codes) - missing,
    }


# ---------- 位置 ----------

def _xy(lon: float, lat: float, lat0: float) -> tuple[float, float]:
    return (lon * 111.32 * math.cos(math.radians(lat0)), lat * 110.57)


def dist_to_segment_km(p, a, b) -> tuple[float, float]:
    """点 p と線分 a-b の距離(km)と、線分上の位置 t(0〜1)。経度緯度を局所の平面に直して計算する。"""
    lat0 = (a[1] + b[1]) / 2
    px, py = _xy(*p, lat0)
    ax, ay = _xy(*a, lat0)
    bx, by = _xy(*b, lat0)
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t


def pick_spread(candidates: list[dict], n: int) -> list[dict]:
    """線に沿って n 個の区画に分け、各区画で線に最も近い地点を1つ選ぶ。"""
    chosen = []
    for i in range(n):
        lo, hi = i / n, (i + 1) / n
        inbin = [c for c in candidates if lo <= c["t"] < hi or (i == n - 1 and c["t"] == 1.0)]
        if inbin:
            chosen.append(min(inbin, key=lambda c: c["dist_km"]))
    return chosen


# ---------- 通信 ----------

class Fetcher:
    def __init__(self):
        self.count = 0
        self.last = 0.0
        RAW.mkdir(parents=True, exist_ok=True)

    def get(self, name: str, type_names: str, cql: str) -> dict:
        path = RAW / f"{name}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        wait = MIN_INTERVAL - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": type_names,
            "srsName": "EPSG:4326", "outputFormat": "application/json", "exceptions": "application/json", "cql_filter": cql,
        }
        req = urllib.request.Request(ENDPOINT + "?" + urllib.parse.urlencode(params), headers={"User-Agent": USER_AGENT})
        self.last = time.monotonic()
        self.count += 1
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            raise SystemExit(f"API が拒否しました: HTTP {e.code} {e.read()[:300]!r}（回避は試さず止めます）")
        except urllib.error.URLError as e:
            raise SystemExit(f"API に接続できません: {e}")
        data = json.loads(body.decode("utf-8"))
        if "features" not in data:  # exceptions=application/json のエラー応答
            raise SystemExit(f"API がエラーを返しました: {json.dumps(data, ensure_ascii=False)[:400]}")
        path.write_bytes(body)
        return data


def jst_today() -> datetime:
    return datetime.now(timezone(timedelta(hours=9)))


def period_hours(today: datetime) -> tuple[list[int], str, str]:
    """今日を除く直近 7 日（168 時間）の時間コードの列。"""
    end = (today - timedelta(days=1)).date()
    start = end - timedelta(days=6)
    codes = []
    for i in range(7):
        d = start + timedelta(days=i)
        for h in range(24):
            codes.append(int(d.strftime("%Y%m%d")) * 10000 + h * 100)
    return codes, start.isoformat(), end.isoformat()


def discovery_hour_code(codes: list[int]) -> int:
    """地点の一覧に使う 1 時刻。期間の中の最初の平日の 10 時。"""
    for c in codes:
        d = datetime.strptime(str(c)[:8], "%Y%m%d")
        if d.weekday() < 5 and c % 10000 == 1000:
            return c
    return codes[10]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    caps = json.loads(BACKHAUL.read_text(encoding="utf-8"))["capitals"]
    lines = {p: [caps[a], caps[b]] for p, (a, b) in PAIRS.items()}
    codes, start, end = period_hours(jst_today())
    disc_code = discovery_hour_code(codes)
    f = Fetcher()

    cands: dict[int, dict] = {}
    for pair, coords in lines.items():
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        bb = (min(lons) - BBOX_PAD_DEG, min(lats) - BBOX_PAD_DEG, max(lons) + BBOX_PAD_DEG, max(lats) + BBOX_PAD_DEG)
        cql = f"道路種別=3 AND 時間コード={disc_code} AND BBOX(ジオメトリ,{bb[0]:.4f},{bb[1]:.4f},{bb[2]:.4f},{bb[3]:.4f},'EPSG:4326')"
        data = f.get(f"discover_{pair}_{disc_code}", TYPE_1H, cql)
        found = []
        for ft in data["features"]:
            props = ft["properties"]
            lon, lat = ft["geometry"]["coordinates"][0]
            d, t = dist_to_segment_km((lon, lat), coords[0], coords[1])
            if d <= THRESHOLD_KM:
                found.append({"id": props[K["point"]], "lon": lon, "lat": lat, "region": props.get(K["region"]), "dist_km": round(d, 2), "t": t})
        print(f"{pair}: 範囲内 {len(data['features'])} 件のうち {THRESHOLD_KM} km 以内 {len(found)} 件", flush=True)
        for c in pick_spread(found, MAX_PER_PAIR):
            e = cands.setdefault(c["id"], {**c, "pairs": []})
            e["pairs"].append(pair)
    if len(cands) > MAX_POINTS:
        raise SystemExit(f"地点が {len(cands)} 件で上限 {MAX_POINTS} を超えます")

    points = []
    for pid, c in sorted(cands.items(), key=lambda kv: (kv[1]["pairs"][0], kv[1]["t"])):
        cql = f"常時観測点コード={pid} AND 道路種別=3 AND 時間コード>={codes[0]} AND 時間コード<={codes[-1]}"
        data = f.get(f"point_{pid}_{codes[0]}_{codes[-1]}", TYPE_1H, cql)
        props = [ft["properties"] for ft in data["features"]]
        granularity = "1h"
        up, down = hourly_series(props, codes)
        if not props:
            granularity = "5min"
            d5 = f.get(f"point5_{pid}_{codes[0]}_{codes[-1]}", TYPE_5M, cql)
            up, down = hourly_from_5min([ft["properties"] for ft in d5["features"]], codes)
        agg = aggregate(codes, up, down)
        points.append({
            "id": pid, "name": None, "lon": c["lon"], "lat": c["lat"], "region": c["region"],
            "pair": c["pairs"][0], "pairs": c["pairs"], "dist_to_line_km": c["dist_km"],
            "granularity": granularity, "up": up, "down": down, **agg,
        })
        print(f"  {pid} {c['pairs']} {granularity} 欠測 {agg['missing_hours']}/{len(codes)} 時間", flush=True)

    out = {
        "attribution": ATTRIBUTION,
        "notes": [
            "台数は参考値です。積荷の有無は分かりません。",
            "欠測の時間は null で、0 台として数えていません。日ごとの合計は欠測を除いた時間の合計で、比は上りと下りの両方がある時間だけで計算しています。",
            "上り・下りは JARTIC の定義（路線の終点から起点へ向かう方向を上りとする）で、区間の行き帰りとは一致しません。",
            "上り・欠測、停電、ループ異常、超音波異常のどれかが 1 の時間は欠測として扱いました。",
        ],
        "line_approximation": (
            "区間の経路線は、OSRM の経路が data/geo/routes.json に無かったため、backhaul_2021.json の県庁所在地の座標を結ぶ直線で近似しました。"
            f"道路は直線から離れるので、線からの距離の許容を {THRESHOLD_KM} km に広げています。"
        ),
        "fetched_at": jst_today().isoformat(timespec="seconds"),
        "period": {"start": start, "end": end, "hours": len(codes)},
        "discovery_hour_code": disc_code,
        "source_type": TYPE_1H,
        "requests": f.count,
        "hours": codes,
        "points": points,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    print(f"要求 {f.count} 回、地点 {len(points)} 件 -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
