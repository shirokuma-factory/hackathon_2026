"""OpenStreetMap（Overpass API）から JR 貨物の駅、フェリーの港、フェリー航路を取得する。

1回だけ実行して結果をコミットする。出典: © OpenStreetMap contributors（ODbL）。
"""

from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ENDPOINT = "https://overpass-api.de/api/interpreter"
USER_AGENT = "shipcompare-hackathon/0.3 (non-commercial demo)"
OUT = Path(__file__).resolve().parent.parent / "data" / "geo"

AREA = 'area["ISO3166-1"="JP"][admin_level=2]->.jp;'
QUERIES = {
    "freight_stations": AREA + '(nwr["railway"~"^(station|yard)$"]["operator"~"日本貨物鉄道|JR貨物"](area.jp);nwr["railway"~"^(station|yard)$"]["name"~"貨物(ターミナル)?駅?$|貨物ターミナル|オフレールステーション"](area.jp););out center tags;',
    "ferry_terminals": AREA + '(nwr["amenity"="ferry_terminal"](area.jp););out center tags;',
    "ferry_routes": AREA + '(way["route"="ferry"](area.jp););out geom tags;',
}
HEADER = {
    "source": "OpenStreetMap（Overpass API で取得）",
    "license": "ODbL 1.0",
    "attribution": "© OpenStreetMap contributors",
}


def fetch(query: str) -> dict:
    data = urllib.parse.urlencode({"data": "[out:json][timeout:180];" + query}).encode()
    req = urllib.request.Request(ENDPOINT, data=data, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=240) as res:
                return json.load(res)
        except Exception as exc:  # 3回まで取り直す
            print(f"失敗 {attempt + 1}: {exc}")
            time.sleep(30)
    raise RuntimeError("Overpass から取得できませんでした")


def point(el: dict) -> tuple[float, float] | None:
    if "lon" in el:
        return el["lon"], el["lat"]
    c = el.get("center")
    return (c["lon"], c["lat"]) if c else None


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def points(elements: list[dict]) -> list[dict]:
    rows = []
    for el in elements:
        p = point(el)
        name = el.get("tags", {}).get("name:ja") or el.get("tags", {}).get("name")
        if p and name:
            rows.append({"name": name, "lon": round(p[0], 5), "lat": round(p[1], 5), "osm": f"{el['type']}/{el['id']}"})
    return rows


def routes(elements: list[dict], min_km: float) -> list[dict]:
    rows = []
    for el in elements:
        geom = [(g["lon"], g["lat"]) for g in el.get("geometry", [])]
        if len(geom) < 2:
            continue
        length = sum(km(a, b) for a, b in zip(geom, geom[1:]))
        if length < min_km:
            continue
        name = el.get("tags", {}).get("name:ja") or el.get("tags", {}).get("name") or ""
        rows.append({"name": name, "km": round(length, 1), "osm": f"way/{el['id']}",
                     "path": [[round(x, 4), round(y, 4)] for x, y in geom]})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-ferry-km", type=float, default=30.0)
    args = ap.parse_args()
    today = date.today().isoformat()
    for key, query in QUERIES.items():
        raw = fetch(query)
        els = raw.get("elements", [])
        body = routes(els, args.min_ferry_km) if key == "ferry_routes" else points(els)
        out = {**HEADER, "fetched_on": today, "query": query, "items": body}
        path = OUT / f"osm_{key}.json"
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        print(f"{key}: 取得 {len(els)} 件 → 保存 {len(body)} 件 {path.name} {path.stat().st_size // 1024}KB")
        time.sleep(5)


if __name__ == "__main__":
    main()
