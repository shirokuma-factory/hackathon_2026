"""地図 UI（web/）用のデータを data/geo と shipcompare から作る。

画面側は運賃を計算しない。ここで作った JSON を読むだけにする。
使い方: uv run python tools/build_web_data.py
出力: web/data/ （muni.geojson, pref.geojson, index.json, meta.json,
      origin_<コード>.json, routes.json, market.json, hubs.json, jartic.json,
      routes_meta.json, routes_<出発地>.json, routes_pairs.json, routes_trips.json, routes_rail.json）。
      routes_*（SPEC §17）は data/routes/ の実際の線（道路・線路・航路・空の弧）を、座標を 1e-4 度の整数の差分にして小さくしたもの。
      muni.json・c28.json・osm_points.json（SPEC §18）は市区町村の推定、物流の集積、空港、OSM の倉庫の点。
      local/p31.json は国土数値情報 P31（非商用・再配布不可）。data/raw/p31/ にあるときだけ web/data/local/（gitignore）へ作る。
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
    nearest_ports = sorted(geo.freight_ferry_ports(tm, rt))  # 最寄りの港は貨物を載せる長距離航路の端だけ

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


# ---------------------------------------------------------------- 実際の線の形（SPEC §17）

ROUTES_SRC = ROOT / "data" / "routes"
# 4 手段の色（画面もここから読む）。赤は使わない（SPEC §17.2）。橙・緑・水色・白に近い薄紫
MODE_COLORS = {"truck": [255, 152, 38], "rail": [72, 214, 112], "ferry": [58, 196, 255], "air": [210, 192, 255]}
MODE_LABELS = {"truck": "トラック", "rail": "電車", "ferry": "船", "air": "飛行機"}
LEG_CODE = {"truck": "t", "rail": "r", "ferry": "f", "air": "a"}


def enc_line(coords) -> list[int]:
    """座標の列を 1e-4 度の整数にして、最初の点だけ絶対値、あとは前の点との差分の平らな列にする。"""
    out: list[int] = []
    px = py = 0
    for x, y in coords:
        X, Y = round(x * 1e4), round(y * 1e4)
        out += [X - px, Y - py]
        px, py = X, Y
    return out


def dec_line(flat) -> list[list[float]]:
    """enc_line の逆（試験用。画面の JS も同じ計算をする）。"""
    out, x, y = [], 0, 0
    for i in range(0, len(flat), 2):
        x += flat[i]
        y += flat[i + 1]
        out.append([x / 1e4, y / 1e4])
    return out


def slim_leg(leg: dict) -> dict:
    o = {"m": LEG_CODE[leg["mode"]], "km": leg["km"], "c": enc_line(leg["coordinates"])}
    if leg.get("minutes") is not None:
        o["min"] = leg["minutes"]
    if leg.get("label"):
        o["l"] = leg["label"]
    return o


def slim_mode(m: dict | None) -> dict | None:
    if not m:
        return None
    o = {"km": m["km"], "tm": m["truck_minutes"], "legs": [slim_leg(l) for l in m["legs"]]}
    if m.get("hubs"):
        o["hubs"] = m["hubs"]
    if m.get("via_ferry_possible"):
        o["vfp"] = True
    return o


def slim_modes(modes: dict, reasons: dict, which=("truck", "rail", "ferry", "air")) -> dict:
    return {"m": {k: slim_mode(modes.get(k)) for k in which}, "why": {k: v for k, v in (reasons or {}).items() if k in which}}


def build_real_routes() -> None:
    manifest = read_json(ROUTES_SRC / "manifest.json") if (ROUTES_SRC / "manifest.json").exists() else {}
    meta = read_json(ROUTES_SRC / "pairs.json")
    write_json("routes_meta.json", {
        "colors": MODE_COLORS, "labels": MODE_LABELS, "built_on": meta.get("built_on"),
        "attribution": meta.get("attribution"), "license": meta.get("license"), "sources": meta.get("sources"),
        "total_bytes_source": manifest.get("total_bytes"),
    })
    for oc in geo.ORIGINS:
        src = ROUTES_SRC / f"from_{oc}.json"
        if not src.exists():
            print(f"routes_{oc}.json: 元の {src.name} がないので作りません")
            continue
        d = read_json(src)
        dests = {}
        for pref, x in d["dests"].items():
            dests[pref] = {"ll": [round(x["lonlat"][0], 4), round(x["lonlat"][1], 4)], **slim_modes(x["modes"], x.get("reasons"))}
        write_json(f"routes_{oc}.json", {"o": oc, "name": d["origin"]["name"], "dests": dests})
    pairs = read_json(ROUTES_SRC / "pairs.json")["pairs"]
    write_json("routes_pairs.json", {k: {"from": v["from"], "to": v["to"], **slim_modes(v["modes"], v.get("reasons"), ("truck", "rail", "ferry"))}
                                     for k, v in pairs.items()})
    trips = read_json(ROUTES_SRC / "trips.json")["trips"]
    write_json("routes_trips.json", {k: {"o": v["origin_code"], "d": v["dest_code"], "truck": slim_mode(v["truck"])} for k, v in trips.items()})
    net = read_json(ROUTES_SRC / "rail_network.json")
    write_json("routes_rail.json", {"attribution": net["attribution"], "lines": [enc_line(l) for l in net["lines"]]})


# ---------------------------------------------------------------- 市区町村の推定・集積・施設（SPEC §18）

STATE_CODE = {"ok": 0, "none": 1, "secret": 2, "missing": 3}  # 画面は 0 以外の理由を灰色にして出す
OSM_TYPES = ["warehouse", "logistics", "industrial_warehouse", "depot", "truck_stop"]
P31_RAW = ROOT / "data" / "raw" / "p31" / "p31_points.json"
LOCAL_OUT = OUT / "local"  # .gitignore 済み（P31 は再配布不可）


def build_muni(codes: list[str]) -> None:
    """市区町村の推定 B(m)（data/stats/muni_estimate.json）と物流の集積（44+47）を、地図の市区町村の番号（codes の順）の列にする。
    秘匿・数字なしは null のまま（0 にしない）。階級の境界（分位数）はここで計算する。"""
    est = read_json(ROOT / "data" / "stats" / "muni_estimate.json")
    mpts = read_json(GEO / "municipality_points.json")["points"]
    osm = read_json(GEO / "osm_logistics_counts.json")
    rows = [est["municipalities"].get(c) for c in codes]
    cols: dict[str, list] = {k: [] for k in ("out", "in", "bal", "ost", "ist", "emp", "pop", "area", "reason", "c44", "c47", "cemp", "cest", "cdens", "cst", "pt", "osm")}
    for c, r in zip(codes, rows):
        if r is None:
            for k in cols:
                cols[k].append(None)
            continue
        cols["out"].append(r["out_t"]); cols["in"].append(r["in_t"]); cols["bal"].append(r["balance_t"])
        cols["ost"].append(STATE_CODE[r["out_state"]]); cols["ist"].append(STATE_CODE[r["in_state"]])
        cols["emp"].append(r["mfg_trucking_warehouse_employees"]); cols["pop"].append(r["population"])
        cols["area"].append(r["area_km2"]); cols["reason"].append(r["gray_reason"])
        a, b = r["trucking_44"], r["warehousing_47"]
        cols["c44"].append([a["establishments"], a["employees"], STATE_CODE[a["status"]]])
        cols["c47"].append([b["establishments"], b["employees"], STATE_CODE[b["status"]]])
        both = [a["status"], b["status"]]
        if "missing" in both:
            cst = 3
        elif "secret" in both:
            cst = 2
        else:
            cst = 0
        emp = a["employees"] + b["employees"] if cst == 0 else None
        cols["cemp"].append(emp)
        cols["cest"].append((a["establishments"] or 0) + (b["establishments"] or 0) if cst != 3 else None)
        cols["cdens"].append(None if emp is None or not r["area_km2"] else round(emp / r["area_km2"], 3))
        cols["cst"].append(cst)
        p = mpts.get(c)
        cols["pt"].append([round(p["lon"], 4), round(p["lat"], 4)] if p else None)
        oc = osm["municipalities"].get(c, {})
        cols["osm"].append([oc.get(t, 0) for t in OSM_TYPES])
    edges = {"dens": geo.quantile_edges([v for v in cols["cdens"] if v is not None], 7),
             "emp": geo.quantile_edges([v for v in cols["cemp"] if v is not None], 7)}
    prefs = {n: [p["out_t"], p["in_t"], p["unallocated_out_t"], p["unallocated_in_t"], p["n_municipalities"], p["n_gray"]]
             for n, p in est["prefectures"].items()}
    write_json("muni.json", {
        "source": est["source"], "terms": est["terms"], "disclaimer": est["disclaimer"], "formula": est["formula"], "unit": est["unit"],
        "status_notes": est["status_notes"], "state_names": ["数字あり", "事業所なし", "秘匿", "表にない"],
        "prefs": prefs, "edges": edges, "osm_types": OSM_TYPES, "osm_unmatched": osm["unmatched"], **cols,
    })


def build_facilities() -> None:
    """空港（C28、商用可）と OSM の倉庫・物流施設の点（ODbL）は公開用に。P31 は手元にあるときだけ web/data/local/ へ。"""
    c28 = read_json(GEO / "c28_airports_2021.json")
    write_json("c28.json", {"source": c28["source"], "terms": c28["terms"],
                            "items": [[r4(i["lon"]), r4(i["lat"]), i["name"], i["status"]] for i in c28["items"]]})
    o = read_json(GEO / "osm_logistics_points.json")
    write_json("osm_points.json", {"attribution": o["attribution"], "license": o["license"], "fetched_on": o["fetched_on"],
                                   "types": o["types"], "tags": o["tags"], "items": [[r4(x), r4(y), t] for x, y, t, _ in o["items"]]})
    if not P31_RAW.exists():
        print("local/p31.json: data/raw/p31/ に P31 がないので作りません（tools/fetch_p31.py で取得）")
        return
    p = read_json(P31_RAW)
    LOCAL_OUT.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_OUT / "p31.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump({"source": p["source"], "terms": p["terms"], "fetched_on": p["fetched_on"], "kinds": p["kinds"],
                   "items": [[round(i["lon"], 5), round(i["lat"], 5), i["kind"], i["name"], i["address"], i["operator"]] for i in p["items"]]},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"local/p31.json: {(LOCAL_OUT / 'p31.json').stat().st_size / 1024:.0f} KB（gitignore 済み。コミットしない）")


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
    build_muni(codes)
    build_facilities()
    build_real_routes()
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
