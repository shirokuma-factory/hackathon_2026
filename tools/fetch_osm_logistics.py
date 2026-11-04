"""OpenStreetMap（Overpass API）から倉庫・物流施設の点を取り、市区町村ごとに数える（SPEC §18.3）。

取るもの（建物や敷地の形は取らず、中心点だけ）:
  warehouse          building=warehouse
  logistics          industrial=logistics
  industrial_warehouse industrial=warehouse
  depot              industrial=depot
  truck_stop         amenity=truck_stop
出力:
  data/geo/osm_logistics_points.json   点（経度, 緯度, 種類, OSM の ID）。ODbL でコミットしてよい
  data/geo/osm_logistics_counts.json   市区町村コードごとの種類別の件数（点がどの市区町村に入るかは多角形で判定）
Overpass への負担を抑える: building=warehouse は地方ごとの範囲（bbox）に分け、問い合わせの間を 12 秒以上あける。
応答は data/raw/osm/ に保存し（gitignore）、再実行では保存済みの応答を使う（--refetch で取り直す）。
出典: © OpenStreetMap contributors（ODbL 1.0）。派生データベースは同じ条件で公開する。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import muni  # noqa: E402

ENDPOINT = "https://overpass-api.de/api/interpreter"
USER_AGENT = "shipcompare-hackathon/0.3 (non-commercial demo)"
MIN_GAP_S = 12
RAW = ROOT / "data" / "raw" / "osm"
GEO = ROOT / "data" / "geo"

# 地方ごとの範囲（南, 西, 北, 東）。重なる所は ID で重複を除く
REGIONS = {
    "hokkaido": (41.3, 139.3, 45.6, 145.9),
    "tohoku": (36.8, 139.0, 41.6, 142.2),
    "kanto_north": (35.7, 138.4, 37.0, 140.9),
    "kanto_south": (34.85, 138.8, 36.0, 140.9),
    "chubu_hokuriku": (35.2, 135.5, 38.6, 140.0),
    "tokai": (33.7, 136.0, 35.5, 139.2),
    "kinki_west": (33.4, 134.1, 35.8, 135.5),
    "kinki_east": (33.4, 135.4, 35.8, 136.9),
    "chugoku_west": (33.6, 130.7, 35.0, 132.8),
    "chugoku_east": (33.6, 132.7, 35.7, 134.5),
    "shikoku": (32.7, 132.0, 34.4, 134.9),
    "kyushu": (30.9, 128.5, 34.1, 132.2),
    "okinawa": (24.0, 122.9, 27.9, 131.4),
}
JP = 'area["ISO3166-1"="JP"][admin_level=2]->.jp;'
TYPES = ["warehouse", "logistics", "industrial_warehouse", "depot", "truck_stop"]


def run_query(query: str) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(ENDPOINT, data=data, headers={"User-Agent": USER_AGENT})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=420) as res:
                return json.load(res)
        except Exception as exc:  # 3 回まで。間隔は長めにあける
            last = exc
            print(f"失敗 {attempt + 1}/3: {exc}")
            time.sleep(60)
    raise RuntimeError(f"Overpass から取得できませんでした: {last}")


def cached(name: str, query: str, refetch: bool, state: dict) -> dict:
    path = RAW / f"{name}.json"
    if path.exists() and not refetch:
        return json.loads(path.read_text(encoding="utf-8"))
    wait = MIN_GAP_S - (time.time() - state.get("last", 0))
    if wait > 0:
        time.sleep(wait)
    print(f"問い合わせ {name}")
    result = run_query(query)
    state["last"] = time.time()
    RAW.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  {len(result.get('elements', []))} 件 {path.stat().st_size / 1024:.0f}KB")
    return result


def center(el: dict) -> tuple[float, float] | None:
    if "lon" in el:
        return el["lon"], el["lat"]
    c = el.get("center")
    return (c["lon"], c["lat"]) if c else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args()
    state: dict = {}
    found: dict[tuple[str, str], tuple[float, float]] = {}  # (種類, "n123") → 中心

    def add(kind: str, el: dict) -> None:
        c = center(el)
        if c:
            found[(kind, f"{el['type'][0]}{el['id']}")] = c

    # building=warehouse: 件数が多いので地方ごとに分ける。ID と中心だけ受け取る
    for name, (s, w, n, e) in REGIONS.items():
        q = f'[out:json][timeout:300][bbox:{s},{w},{n},{e}];nwr["building"="warehouse"];out ids center;'
        for el in cached(f"warehouse_{name}", q, args.refetch, state).get("elements", []):
            add("warehouse", el)
    # 少ない種類は全国を 1 回で。種類の見分けにタグが要る
    q = (f'[out:json][timeout:300];{JP}(nwr["industrial"~"^(logistics|warehouse|depot)$"](area.jp);'
         f'nwr["amenity"="truck_stop"](area.jp););out center tags;')
    for el in cached("industrial_truckstop_jp", q, args.refetch, state).get("elements", []):
        tags = el.get("tags", {})
        if tags.get("amenity") == "truck_stop":
            add("truck_stop", el)
        ind = tags.get("industrial")
        if ind == "logistics":
            add("logistics", el)
        elif ind == "warehouse":
            add("industrial_warehouse", el)
        elif ind == "depot":
            add("depot", el)

    locator = muni.Locator(muni.load_municipalities())
    counts: dict[str, dict[str, int]] = {}
    unmatched = {t: 0 for t in TYPES}
    items = []
    for (kind, oid), (lon, lat) in sorted(found.items(), key=lambda kv: (TYPES.index(kv[0][0]), kv[0][1])):
        items.append([round(lon, 5), round(lat, 5), TYPES.index(kind), oid])
        code = locator.locate(lon, lat)
        if code is None:
            unmatched[kind] += 1
        else:
            counts.setdefault(code, {})[kind] = counts.get(code, {}).get(kind, 0) + 1

    today = date.today().isoformat()
    head = {"source": "OpenStreetMap（Overpass API で取得）", "license": "ODbL 1.0",
            "attribution": "© OpenStreetMap contributors", "fetched_on": today}
    with open(GEO / "osm_logistics_points.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump({**head, "types": TYPES,
                   "tags": {"warehouse": "building=warehouse", "logistics": "industrial=logistics",
                            "industrial_warehouse": "industrial=warehouse", "depot": "industrial=depot",
                            "truck_stop": "amenity=truck_stop"},
                   "row": "[経度, 緯度, 種類（types の番号）, OSM の ID（n=node, w=way, r=relation）]",
                   "items": items}, f, ensure_ascii=False, separators=(",", ":"))
    totals = {t: sum(1 for k in found if k[0] == t) for t in TYPES}
    with open(GEO / "osm_logistics_counts.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump({**head, "method": "点が市区町村の多角形（国土数値情報 N03-21 の簡略化）の中にあるかを光線法で判定。"
                                      "どの多角形にも入らない点（沖合・簡略化で外れた海岸）は unmatched に数える",
                   "totals": totals, "unmatched": unmatched, "municipalities": counts},
                  f, ensure_ascii=False, separators=(",", ":"))
    for fn in ("osm_logistics_points.json", "osm_logistics_counts.json"):
        print(fn, f"{(GEO / fn).stat().st_size / 1024:.0f}KB")
    print("種類別の件数", totals, "市区町村に入らなかった点", unmatched, "件数のある市区町村", len(counts))


if __name__ == "__main__":
    main()
