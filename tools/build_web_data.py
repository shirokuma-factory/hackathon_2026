"""地図 UI（web/）用のデータを data/geo と shipcompare から作る。

画面側は運賃を計算しない。ここで作った JSON を読むだけにする。
使い方: uv run python tools/build_web_data.py
出力: web/data/ （muni.geojson, pref.geojson, index.json, meta.json,
      origin_<コード>.json, routes.json, market.json, hubs.json, jartic.json）。
      目安額は /api/estimate が計算するので作らない。市場を見る（SPEC §16）の数字と、最寄りの拠点（SPEC §15.5）は
      ここで Python が計算して書き、画面は表示するだけにする
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import geo, market  # noqa: E402
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
            pf.append({"type": "Feature", "properties": {"n": f["properties"].get("N03_001")}, "geometry": g})
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


def r4(v):
    return round(v, 4)


def build_market() -> None:
    """市場を見る（SPEC §16.1）。手段ごとの区間（不足量の大きい順）、県の出と入の差、S と実車率の曲線。"""
    data = market.load_backhaul()
    prefs = list(data["capitals"])
    pidx = {n: i for i, n in enumerate(prefs)}
    modes, totals = {}, {}
    for m in market.MODES:
        pairs = sorted(data["modes"][m]["pairs"], key=lambda p: (-p["shortfall_t"], -p["km"], p["from"], p["to"]))
        modes[m] = {
            "pairs": [[pidx[p["from"]], pidx[p["to"]], p["go_t"], p["back_t"], p["ratio"], p["shortfall_t"], p["km"]] for p in pairs],
            "balance": [data["modes"][m]["balance_out_minus_in_t"][n] for n in prefs],
        }
        totals[m] = {"interpref_t": data["modes"][m]["interpref_t"], "shortfall_t": data["modes"][m]["shortfall_total_t"],
                     "shortfall_far_t": data["modes"][m]["shortfall_far_t"], "share": round(market.shortfall_share(data, m), 4)}
    s = market.scale_s(data["modes"]["truck"]["pairs"])
    write_json("market.json", {
        "source": data["source"], "unit": data["unit"], "distance": data["distance"],
        "prefs": prefs, "cap": [[r4(data["capitals"][n][0]), r4(data["capitals"][n][1])] for n in prefs],
        "modes": modes, "totals": totals,
        "summary": {"load_factor_pct": market.LOAD_FACTOR_PCT, "actual_rate_pct": market.ACTUAL_RATE_PCT,
                    "shortfall_share_pct": round(totals["truck"]["share"] * 100)},
        "scale_s": round(s, 4), "curve": market.rate_curve(s),
    })


def build_jartic() -> None:
    """JARTIC の大型車（SPEC §16.4）。区間名は backhaul の多い向き（from→to）に結びつける。"""
    src = read_json(ROOT / "data" / "traffic" / "heavy_updown.json")
    data = market.load_backhaul()
    pairs = data["modes"]["truck"]["pairs"]
    pts = []
    for p in src["points"]:
        pr = market.pair_from_label(p["pair"], pairs)
        pts.append({"id": p["id"], "lon": r4(p["lon"]), "lat": r4(p["lat"]), "pair": p["pair"],
                    "from": pr["from"] if pr else None, "to": pr["to"] if pr else None,
                    "dist_km": p["dist_to_line_km"], "up": p["up"], "down": p["down"],
                    "up_total": p["up_total"], "down_total": p["down_total"], "ratio": p["ratio"],
                    "missing_hours": p["missing_hours"], "observed_hours": p["observed_hours"], "granularity": p["granularity"]})
    write_json("jartic.json", {
        "attribution": src["attribution"], "notes": src["notes"], "fetched_at": src["fetched_at"],
        "period": src["period"], "first_hour": src["hours"][0], "points": pts,
    })


def build_hubs(codes: list[str], points: dict) -> None:
    """貨物駅・港・航路（SPEC §15.5）と、出発地・着地ごとの最寄りの拠点（直線距離）。"""
    st = geo.load_hubs("freight_stations")
    tm = geo.load_hubs("ferry_terminals")
    rt = geo.load_hubs("ferry_routes")
    major = set(geo.major_terminals(tm, rt))
    long_haul = set(geo.major_terminals(tm, [r for r in rt if r["km"] >= 150]))
    nearest_ports = sorted(major)

    def near(lon, lat):
        a = geo.nearest_hub(lon, lat, st)
        b = geo.nearest_hub(lon, lat, [tm[k] for k in nearest_ports])
        return a, (nearest_ports[b[0]], b[1])

    org = {}
    for oc in geo.ORIGINS:
        a, b = near(points[oc]["lon"], points[oc]["lat"])
        org[oc] = {"st": [a[0], round(a[1], 1)], "pt": [b[0], round(b[1], 1)]}
    dst = {"st": [], "st_km": [], "pt": [], "pt_km": []}
    for c in codes:
        a, b = near(points[c]["lon"], points[c]["lat"])
        dst["st"].append(a[0]); dst["st_km"].append(round(a[1], 1))
        dst["pt"].append(b[0]); dst["pt_km"].append(round(b[1], 1))
    write_json("hubs.json", {
        "attribution": "© OpenStreetMap contributors（ODbL 1.0）",
        "fetched_on": "2026-11-03",
        "stations": [[h["name"], r4(h["lon"]), r4(h["lat"])] for h in st],
        "terminals": [[h["name"], r4(h["lon"]), r4(h["lat"]), 2 if i in long_haul else 1 if i in major else 0] for i, h in enumerate(tm)],
        "routes": [{"n": r["name"], "km": r["km"], "p": [[round(x, 3), round(y, 3)] for x, y in r["path"][::3] + [r["path"][-1]]]} for r in rt],
        "origin": org, "dest": dst,
    })


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
    build_market()
    build_jartic()
    build_hubs(codes, points)
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
