"""地図 UI（web/）用のデータを data/geo と shipcompare から作る。

画面側は運賃を計算しない。ここで作った JSON を読むだけにする。
使い方: uv run python tools/build_web_data.py
出力: web/data/ （muni.geojson, pref.geojson, index.json, meta.json,
      origin_<コード>.json, routes.json, estimates.json）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import geo  # noqa: E402
from shipcompare.estimate import estimate_all  # noqa: E402
from shipcompare.storage import shipment_from_dict  # noqa: E402
from shipcompare.tariffs import ROUTES, load_tariff, source_of  # noqa: E402

OUT = ROOT / "web" / "data"
GEO = ROOT / "data" / "geo"
DAYS_BASIS_URL = "https://driver-roudou-jikan.mhlw.go.jp/truck/notice"

STATUS_LABEL = {"公開条件に合う": "使える候補", "要確認": "要確認", "使えない": "使えない", "計算できない": "計算できない"}
MODE_LABEL = {"takuhai": "宅配", "rosen": "路線便", "jitbox": "カゴ台車", "kashikiri": "貸切"}


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
    for oc, oname in geo.ORIGINS.items():
        osrm = geo.load_osrm(oc)
        km, mins, days, brk, fare = [], [], [], [], []
        for c in codes:
            d = osrm.get(c) or {}
            k, m = d.get("km"), d.get("minutes")
            km.append(k)
            mins.append(m)
            days.append(geo.drive_days(m))
            brk.append(geo.break_minutes(m))
            kf = geo.kashikiri_for_distance(k, None, tariff)  # 2t（荷物未入力と同じ扱い）
            fare.append(kf[1] if kf else None)
        edges, cls_ = {}, {}
        for key, vals in (("km", km), ("days", days), ("fare", fare)):
            present = [v for v in vals if v is not None]
            if key == "days":  # 日数は値が少ないので、1・2・3・4日以上の階級にする
                edges[key] = [0, 1, 2, 3, max(present)]
            else:
                edges[key] = geo.quantile_edges(present)
            cls_[key] = [geo.class_of(v, edges[key]) if v is not None else None for v in vals]
        write_json(f"origin_{oc}.json", {"code": oc, "name": oname, "km": km, "min": mins, "days": days,
                                         "brk": brk, "fare": fare, "edges": edges, "cls": cls_})


def mode_of(row_mode: str) -> str:
    return "kashikiri" if row_mode.startswith("kashikiri") else row_mode


def build_estimates(routes: dict, tariff_k: dict) -> dict:
    scn = read_json(ROOT / "data" / "scenarios" / "S1.json")
    transit = read_json(ROOT / "data" / "transit_days.json")["modes"]
    out = {}
    for key, info in geo.DEMO_ROUTES.items():
        if info["origin"] not in geo.ORIGINS:
            continue
        rid = info["route_id"]
        sd = dict(scn["shipment"])
        sd["route"] = rid
        rows = estimate_all(shipment_from_dict(sd))
        r = routes[key]
        assumed = ROUTES[rid].km
        modes = {}
        for row in rows:
            m = mode_of(row.mode)
            if m in modes:
                continue  # 貸切は載る最小の車格（先頭）だけ
            t = transit.get(m, {}).get("routes", {}).get(rid, {})
            days_text = t.get("text")
            note = row.notes[0] if row.notes else None
            alt = None
            if m == "kashikiri":
                n = geo.drive_days(r["minutes"])
                days_text = f"運転だけで{n}日（積込み・荷待ちを除く）"
                kf = geo.kashikiri_for_distance(r["km"], 120, tariff_k)
                alt = {"km": r["km"], "amount": kf[1]} if kf else None
                note = f"想定距離 {assumed}km で計算した国の目安です"
            modes[m] = {
                "label": MODE_LABEL[m],
                "vehicle": row.detail.get("vehicle") if m == "kashikiri" else None,
                "status": STATUS_LABEL.get(row.status, row.status),
                "amount": row.amount if row.has_amount() else None,
                "low": row.low, "high": row.high,
                "days": days_text,
                "reason": row.reasons[0] if row.reasons else None,
                "note": note,
                "alt": alt,
                "source_name": row.source.name if row.source else None,
                "source_url": row.source.url if row.source else None,
            }
        out[key] = {"route_id": rid, "assumed_km": assumed, "modes": modes}
    return out


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
    write_json("estimates.json", build_estimates(routes, tariff_k))
    src = source_of(tariff_k)
    scn = read_json(ROOT / "data" / "scenarios" / "S1.json")
    items = scn["shipment"]["items"]
    n = sum(i["count"] for i in items)
    kg = sum(i["count"] * i["weight_kg"] for i in items)
    it = items[0]
    write_json("meta.json", {
        "origins": [{"code": c, "name": nm, "lon": round(points[c]["lon"], 5), "lat": round(points[c]["lat"], 5)}
                    for c, nm in geo.ORIGINS.items()],
        "cargo": {"label": "家具の箱", "count": n, "total_kg": kg,
                  "detail": f"{it['length_cm']}×{it['width_cm']}×{it['height_cm']}cm、1個 {it['weight_kg']}kg（場面 S1）"},
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
