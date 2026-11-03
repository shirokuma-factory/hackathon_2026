"""OSRM 公開デモから、出発地ごとの道路距離・運転時間と、デモの区間の道路の線を取得して data/geo に保存する。

SPEC §14.3。1回だけ実行して結果をコミットする（アプリは通信しない）。
- 1秒に1件を超えない（各要求の後に 1.1 秒待つ）。失敗は3回まで、間を 5 秒空けて再試行する。
- 経路がない着地は km と minutes を null にする（0 や推定値で埋めない）。
- 既にファイルがある出発地・区間は取得しない（--force で取り直す）。

使い方: uv run python tools/fetch_osrm.py [--force] [--data-dir DIR] [--origins 13104,14104]
利用条件: OSRM 公開デモは非商用の少量利用向け。データは OpenStreetMap（ODbL）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shipcompare.geo import DEMO_ROUTES, ORIGINS, municipality_name, representative_point  # noqa: E402

BASE_URL = "https://router.project-osrm.org"
USER_AGENT = "shipcompare-hackathon/0.3 (non-commercial demo)"
SLEEP_AFTER_REQUEST = 1.1  # 秒。公開デモは 1 秒に 1 件まで
RETRIES = 3
RETRY_WAIT = 5.0  # 秒
CHUNK = 99  # 1回に出発地1点 + 着地最大 99 点
MUNI_FILE = "municipalities_N03-21_s0010.geojson"

LICENSE_FIELDS = {
    "source": "OSRM 公開デモ（router.project-osrm.org）。道路は OpenStreetMap のデータ、乗用車（driving）の経路",
    "license": "ODbL 1.0（Open Database License）",
    "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
    "attribution": "© OpenStreetMap contributors",
}


class FetchError(RuntimeError):
    pass


class Client:
    """間隔と再試行を守る HTTP クライアント。sleep と urlopen は試験で差し替える。"""

    def __init__(self, sleep: Callable[[float], None] = time.sleep,
                 urlopen: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.sleep = sleep
        self.urlopen = urlopen
        self.requests = 0

    def get_json(self, url: str) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(1, RETRIES + 1):
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            self.requests += 1
            try:
                with self.urlopen(req, timeout=60) as res:
                    data = json.loads(res.read().decode("utf-8"))
                if data.get("code") != "Ok":
                    raise FetchError(f"OSRM の応答が Ok ではありません: {data.get('code')} {data.get('message', '')}")
                return data
            except (urllib.error.URLError, OSError, ValueError, FetchError) as e:
                last = e
            finally:
                self.sleep(SLEEP_AFTER_REQUEST)  # 成功でも失敗でも、要求のたびに待つ
            if attempt < RETRIES:
                self.sleep(RETRY_WAIT)
        raise FetchError(f"{RETRIES}回失敗しました: {url[:120]} ({last})")


# ---------------------------------------------------------------- 出力

def _dump(path: Path, header: dict[str, Any], key: str, entries: dict[str, Any]) -> None:
    """先頭に出典などの項目、続けて key の中身を1件1行で書く。LF 固定。"""
    lines = ["{"]
    for k, v in header.items():
        lines.append(f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)},")
    lines.append(f"  {json.dumps(key)}: {{")
    items = list(entries.items())
    for i, (k, v) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f"    {json.dumps(k)}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}{comma}")
    lines.append("  }")
    lines.append("}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def _header(today: date, **extra: Any) -> dict[str, Any]:
    return {**LICENSE_FIELDS, "fetched_on": today.isoformat(), **extra}


# ---------------------------------------------------------------- 代表点

def build_points(geo_dir: Path, today: date, force: bool = False) -> dict[str, dict[str, Any]]:
    """境界から代表点を計算して municipality_points.json に書く。ファイルがあれば読み込むだけ。"""
    out = geo_dir / "municipality_points.json"
    if out.exists() and not force:
        with open(out, encoding="utf-8") as f:
            return json.load(f)["points"]
    with open(geo_dir / MUNI_FILE, encoding="utf-8") as f:
        features = json.load(f)["features"]
    points: dict[str, dict[str, Any]] = {}
    for ft in features:
        props = ft["properties"]
        code = props.get("N03_007")
        if not code or code in points:  # コードが空の所属未定地には代表点を作らない
            continue
        lon, lat = representative_point(ft["geometry"])
        points[code] = {"name": municipality_name(props), "pref": props["N03_001"],
                        "lon": round(lon, 6), "lat": round(lat, 6)}
    points = dict(sorted(points.items()))
    header = {
        "source": "japan-topography（smartnews-smri）の行政区域を簡略化したもの。国土数値情報（行政区域データ）（国土交通省）を加工して作成",
        "computed_on": today.isoformat(),
        "rule": "多角形の重心。MultiPolygon は面積が最大の多角形。重心が外に出たらその多角形の頂点で最も近い点",
    }
    _dump(out, header, "points", points)
    return points


# ---------------------------------------------------------------- 取得

def _coord(p: dict[str, Any]) -> str:
    return f"{p['lon']:.6f},{p['lat']:.6f}"


def fetch_origin(client: Client, origin: str, points: dict[str, dict[str, Any]]) -> dict[str, dict[str, float | None]]:
    """出発地から全着地への距離 km・運転時間 分。"""
    codes = sorted(points)
    result: dict[str, dict[str, float | None]] = {}
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        coords = ";".join([_coord(points[origin])] + [_coord(points[c]) for c in chunk])
        url = f"{BASE_URL}/table/v1/driving/{coords}?sources=0&annotations=distance,duration"
        data = client.get_json(url)
        dist, dur = data["distances"][0][1:], data["durations"][0][1:]
        if len(dist) != len(chunk) or len(dur) != len(chunk):
            raise FetchError("応答の件数が要求と合いません")
        for c, d, t in zip(chunk, dist, dur):
            if d is None or t is None:
                result[c] = {"km": None, "minutes": None}  # 経路なし。0 や推定値にしない
            else:
                result[c] = {"km": round(d / 1000, 1), "minutes": round(t / 60, 1)}
    return result


def fetch_route(client: Client, points: dict[str, dict[str, Any]], origin: str, dest: str) -> dict[str, Any]:
    url = (f"{BASE_URL}/route/v1/driving/{_coord(points[origin])};{_coord(points[dest])}"
           "?overview=simplified&geometries=geojson")
    r = client.get_json(url)["routes"][0]
    return {"km": round(r["distance"] / 1000, 1), "minutes": round(r["duration"] / 60, 1),
            "coordinates": [[round(x, 5), round(y, 5)] for x, y in r["geometry"]["coordinates"]]}


def run(geo_dir: Path, *, force: bool = False, origins: list[str] | None = None, client: Client | None = None,
        today: date | None = None, log: Callable[[str], None] = print) -> dict[str, Any]:
    client = client or Client()
    today = today or date.today()
    points = build_points(geo_dir, today, force)
    summary: dict[str, Any] = {"fetched": [], "skipped": [], "nulls": {}}

    for origin in origins or list(ORIGINS):
        out = geo_dir / f"osrm_{origin}.json"
        if out.exists() and not force:
            summary["skipped"].append(origin)
            log(f"{origin}: 既にあるので取得しません")
            continue
        if origin not in points:
            raise FetchError(f"出発地 {origin} の代表点がありません")
        dests = fetch_origin(client, origin, points)
        nulls = sum(1 for v in dests.values() if v["km"] is None)
        _dump(out, _header(today, origin={"code": origin, "name": ORIGINS.get(origin, points[origin]["name"])},
                           profile="driving", units="km と 分（km は 0.1 単位、経路がない着地は null）"),
              "destinations", dests)
        summary["fetched"].append(origin)
        summary["nulls"][origin] = nulls
        log(f"{origin}: {len(dests)}件、経路なし(null) {nulls}件")

    out = geo_dir / "routes.json"
    if out.exists() and not force:
        summary["skipped"].append("routes")
        log("routes: 既にあるので取得しません")
    else:
        routes = {}
        for key, spec in DEMO_ROUTES.items():
            routes[key] = {"route_id": spec["route_id"], "origin": spec["origin"], "dest": spec["dest"],
                           **fetch_route(client, points, spec["origin"], spec["dest"])}
            log(f"{key}: {routes[key]['km']}km {routes[key]['minutes']}分")
        _dump(out, _header(today, profile="driving", overview="simplified"), "routes", routes)
        summary["fetched"].append("routes")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="既にあるファイルも取り直す")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data" / "geo")
    ap.add_argument("--origins", help="コンマ区切りの出発地コード（既定は7か所）")
    a = ap.parse_args(argv)
    origins = a.origins.split(",") if a.origins else None
    try:
        run(a.data_dir, force=a.force, origins=origins)
    except FetchError as e:
        print(f"失敗: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
