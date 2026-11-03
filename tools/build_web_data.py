"""地図 UI（web/）用のデータを data/geo と shipcompare から作る。

画面側は運賃を計算しない。ここで作った JSON を読むだけにする。
使い方: uv run python tools/build_web_data.py
出力: web/data/ （muni.geojson, pref.geojson, index.json, meta.json,
      origin_<コード>.json, routes.json）。目安額は /api/estimate が計算するので作らない
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import geo  # noqa: E402
from shipcompare.tariffs import load_tariff, source_of  # noqa: E402

OUT = ROOT / "web" / "data"
GEO = ROOT / "data" / "geo"
DAYS_BASIS_URL = "https://driver-roudou-jikan.mhlw.go.jp/truck/notice"


def write_json(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{name}: {p.stat().st_size / 1024:.0f} KB")


def read_json(p: Path):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def round_ring(ring, nd=4):
    out = []
    for x, y in ring:
        pt = [round(x, nd), round(y, nd)]
        if not out or out[-1] != pt:
            out.append(pt)
    if len(out) > 1 and out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 else None


def slim_geometry(g, nd=4):
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    res = []
    for poly in polys:
        rings = [round_ring(r, nd) for r in poly]
        if rings[0] is None:
            continue
        res.append([r for r in rings if r])
    if not res:
        return None
    if len(res) == 1:
        return {"type": "Polygon", "coordinates": res[0]}
    return {"type": "MultiPolygon", "coordinates": res}


def build_geometry(codes: list[str]) -> None:
    idx = {c: i for i, c in enumerate(codes)}
    src = read_json(GEO / "municipalities_N03-21_s0010.geojson")
    feats = []
    for f in src["features"]:
        code = f["properties"].get("N03_007")
        g = slim_geometry(f["geometry"])
        if g is None:
            continue
        feats.append({"type": "Feature",
                      "properties": {"i": idx.get(code, -1), "n": geo.municipality_name(f["properties"])},
                      "geometry": g})
    write_json("muni.geojson", {"type": "FeatureCollection", "features": feats})
    pref = read_json(GEO / "prefectures_s0010.geojson")
    pf = []
    for f in pref["features"]:
        g = slim_geometry(f["geometry"], 3)
        if g:
            pf.append({"type": "Feature", "properties": {}, "geometry": g})
    write_json("pref.geojson", {"type": "FeatureCollection", "features": pf})


def build_origins(codes: list[str], tariff: dict) -> None:
    """地図の層用の値。荷物が変わったときの貸切の額は /api/layer が同じ関数で計算し直す。
    フェリーを含む経路の可能性（SPEC §15.3）と経路なしは、日数と額を null にする。距離だけは残す。"""
    for oc, oname in geo.ORIGINS.items():
        osrm = geo.load_osrm(oc)
        km, mins, days, brk, ferry = [], [], [], [], []
        for c in codes:
            d = osrm.get(c) or {}
            k, m = d.get("km"), d.get("minutes")
            f = geo.is_ferry_route(k, m)
            km.append(k)
            mins.append(m)
            ferry.append(f)
            days.append(None if f else geo.drive_days(m))
            brk.append(None if f else geo.break_minutes(m))
        fare = geo.fare_layer(oc, None, tariff, codes)  # 2t（荷物未入力と同じ扱い）
        edges, cls_ = {}, {}
        for key, vals in (("km", km), ("days", days), ("fare", fare)):
            present = [v for v in vals if v is not None]
            if key == "days":  # 日数は値が少ないので、1・2・3・4日以上の階級にする
                edges[key] = [0, 1, 2, 3, max(present)]
            else:
                edges[key] = geo.quantile_edges(present)
            cls_[key] = [geo.class_of(v, edges[key]) if v is not None else None for v in vals]
        write_json(f"origin_{oc}.json", {"code": oc, "name": oname, "km": km, "min": mins, "days": days,
                                         "brk": brk, "ferry": ferry, "fare": fare, "edges": edges, "cls": cls_})


def main() -> None:
    points = geo.load_points()
    codes = sorted(points)
    tariff_k = load_tariff("kashikiri")
    build_geometry(codes)
    write_json("index.json", {
        "codes": codes,
        "names": [points[c]["name"] for c in codes],
        "prefs": [points[c]["pref"] for c in codes],
        "lon": [round(points[c]["lon"], 5) for c in codes],
        "lat": [round(points[c]["lat"], 5) for c in codes],
    })
    build_origins(codes, tariff_k)
    routes_src = read_json(GEO / "routes.json")["routes"]
    routes = {k: v for k, v in routes_src.items() if v["origin"] in geo.ORIGINS}
    write_json("routes.json", {k: {"route_id": v["route_id"], "km": v["km"], "minutes": v["minutes"],
                                   "coordinates": v["coordinates"]} for k, v in routes.items()})
    src = source_of(tariff_k)
    write_json("meta.json", {
        "origins": [{"code": c, "name": nm, "lon": round(points[c]["lon"], 5), "lat": round(points[c]["lat"], 5)}
                    for c, nm in geo.ORIGINS.items()],
        "kashikiri_source": {"name": src.name, "url": src.url, "checked_on": src.checked_on},
        "days_rule": {"max_hours_per_day": 9, "url": DAYS_BASIS_URL},
        "layers": {
            "km": {"label": "道路距離", "unit": "km"},
            "days": {"label": "運転日数", "unit": "日"},
            "fare": {"label": "貸切の目安額", "unit": "円", "note": "2t車・税抜・200km未満は表の範囲外"},
        },
    })


if __name__ == "__main__":
    main()
