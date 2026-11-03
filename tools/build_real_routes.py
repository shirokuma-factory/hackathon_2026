"""実際の線の形の経路を作る（SPEC §17.1）。1回だけ実行して data/routes/ の結果をコミットする。

段階:
  net     国土数値情報 N02 と OSM の貨物専用線から線路の図を組み、貨物駅を図に結びつけ、空港を取得する
  osrm    OSRM の道路の経路を取得する（1.1 秒に 1 件、取得済みは取らない）。data/raw/osrm_cache.json に保存
  compose トラック・電車・船・飛行機の経路を組み立てて data/routes/ に書く
標準ライブラリだけ。生の取得物は data/raw/（.gitignore）に置く。

使い方: python tools/build_real_routes.py [--stage net|osrm|compose|all] [--max-requests 450] [--plan]
出典: 国土数値情報（鉄道データ）（国土交通省）を加工して作成（CC BY 4.0）。OSM・OSRM は ODbL。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import railgraph as rg  # noqa: E402
from shipcompare import routes as rt  # noqa: E402
from shipcompare.geo import (  # noqa: E402
    ORIGINS, freight_ferry_ports, haversine_km, is_ferry_route, load_hubs, load_osrm, load_points,
)

RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "routes"
N02_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-24/N02-24_GML.zip"
N02_ZIP = RAW / "N02-24_GML.zip"
N02_MEMBER = "UTF-8/N02-24_RailroadSection.geojson"
OVERPASS = "https://overpass-api.de/api/interpreter"
UA = rt.USER_AGENT
OSRM_CACHE = RAW / "osrm_cache.json"

# N02_002: 1 新幹線 / 2 JR在来線 / 3 公営鉄道 / 4 民営鉄道 / 5 第三セクター。貨物列車が走るのは JR 在来線と一部の第三セクター。
# 新幹線・公営・民営は図に入れない（貨物専用線は OSM から足す）。第三セクターは貨物列車が走る会社だけ。
N02_FREIGHT_THIRD_SECTOR = (
    "アイジーアールいわて銀河鉄道", "青い森鉄道", "あいの風とやま鉄道", "IRいしかわ鉄道", "えちごトキめき鉄道",
    "しなの鉄道", "道南いさりび鉄道", "肥薩おれんじ鉄道", "鹿島臨海鉄道", "水島臨海鉄道", "ハピラインふくい",
)
SNAP_M = 30.0
CONNECT_M = 150.0
STATION_WARN_M = 300.0
BRIDGE_M = 1000.0  # 図の外れにある駅を最大の成分につなぐ距離の上限
DP_NETWORK_M = 30.0  # コミットする線路の図の簡略化（経路の点が図の線から 50m 以内に収まる値）

OSM_FREIGHT_QUERY = ('[out:json][timeout:300];area["ISO3166-1"="JP"][admin_level=2]->.jp;'
                     '(way["railway"="rail"]["usage"~"^(freight|industrial)$"](area.jp););out geom tags;')
OSM_AIRPORT_QUERY = ('[out:json][timeout:180];area["ISO3166-1"="JP"][admin_level=2]->.jp;'
                     '(nwr["aeroway"="aerodrome"]["iata"](area.jp););out center tags;')
SPUR_RADIUS_M = 3000  # 貨物駅の周り 3km の線路（貨物線は N02 に無いので、駅と本線の間を OSM でつなぐ）


def log(msg: str) -> None:
    print(msg, flush=True)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 取得

def download_n02() -> None:
    if N02_ZIP.exists() and N02_ZIP.stat().st_size > 10_000_000:
        log(f"N02: 取得済み {N02_ZIP.stat().st_size / 1e6:.1f}MB")
        return
    RAW.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(N02_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as res, open(N02_ZIP, "wb") as f:
        f.write(res.read())
    log(f"N02: {N02_ZIP.stat().st_size / 1e6:.1f}MB を取得")


def overpass(query: str, cache: Path) -> dict[str, Any]:
    if cache.exists():
        log(f"{cache.name}: 取得済み")
        return read_json(cache)
    data = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(OVERPASS, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=360) as res:
                obj = json.load(res)
            write_json(cache, obj)
            log(f"{cache.name}: {len(obj.get('elements', []))} 件")
            return obj
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"Overpass 失敗 {attempt + 1}: {e}")
            time.sleep(30)
    raise RuntimeError(f"Overpass から取得できませんでした: {last}")


def spur_query(stations: list[dict[str, Any]]) -> str:
    parts = "".join(f'way["railway"="rail"](around:{SPUR_RADIUS_M},{s["lat"]},{s["lon"]});'
                    for s in stations)
    return f'[out:json][timeout:300];({parts});out geom tags;'


# ---------------------------------------------------------------- 線路の図

def dedupe_stations() -> list[dict[str, Any]]:
    """同名は 1 件。名前が違っても 300m 以内に先にある駅は同じ駅として省く（梶ヶ谷貨物ターミナル／同駅 など）。"""
    seen: dict[str, dict[str, Any]] = {}
    for s in load_hubs("freight_stations"):
        seen.setdefault(s["name"], s)
    out: list[dict[str, Any]] = []
    for s in seen.values():
        if not any(haversine_km(s["lon"], s["lat"], o["lon"], o["lat"]) < 0.3 for o in out):
            out.append(s)
    return out


def n02_lines() -> tuple[list[list[list[float]]], dict[str, int]]:
    with zipfile.ZipFile(N02_ZIP) as z:
        d = json.load(z.open(N02_MEMBER))
    lines, count = [], {"JR在来線": 0, "第三セクター(貨物あり)": 0, "除外": 0}
    for ft in d["features"]:
        p = ft["properties"]
        cls, op = p["N02_002"], p["N02_004"]
        if cls == "2":
            count["JR在来線"] += 1
        elif cls == "5" and op in N02_FREIGHT_THIRD_SECTOR:
            count["第三セクター(貨物あり)"] += 1
        else:
            count["除外"] += 1
            continue
        lines.append(ft["geometry"]["coordinates"])
    return lines, count


def osm_lines(obj: dict[str, Any]) -> list[list[list[float]]]:
    out = []
    for el in obj.get("elements", []):
        g = el.get("geometry") or []
        if len(g) >= 2:
            out.append([[p["lon"], p["lat"]] for p in g])
    return out


def build_network(stations: list[dict[str, Any]]) -> dict[str, Any]:
    download_n02()
    n02, counts = n02_lines()
    g = rg.build(n02, SNAP_M)
    base = rg.graph_summary(g)
    log(f"N02 のみ: 節点 {base['nodes']} 辺 {base['edges']} 連結成分 {base['components']} 最大 {base['largest']} ({counts})")

    freight = osm_lines(overpass(OSM_FREIGHT_QUERY, RAW / "osm_freight_lines.json"))
    spurs = osm_lines(overpass(spur_query(stations), RAW / "osm_spur_lines.json"))
    osm = freight + spurs
    connectors = rg.connect_lines(g, osm, CONNECT_M)
    full = rg.graph_summary(g)
    log(f"OSM 貨物線 {len(freight)} 本 + 側線 {len(spurs)} 本を追加（つないだ端 {len(connectors)}）: "
        f"節点 {full['nodes']} 辺 {full['edges']} 連結成分 {full['components']} 最大 {full['largest']}")
    return {"graph": g, "n02": n02, "osm": osm, "connectors": connectors, "counts": counts,
            "summary_n02": base, "summary": full, "n_freight_lines": len(freight), "n_spur_lines": len(spurs)}


def snap_stations(g: rg.Graph, stations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = rg.component_labels(g)
    sizes: dict[int, int] = {}
    for lab in labels:
        sizes[lab] = sizes.get(lab, 0) + 1
    out = []
    for s in stations:
        hit = rg.snap(g, s["lon"], s["lat"], max_m=20000)
        if hit is None:
            out.append({**s, "node": None, "snap_m": None, "component_nodes": None})
            continue
        node, dist = hit
        out.append({**s, "node": node, "node_lon": g.coords[node][0], "node_lat": g.coords[node][1],
                    "snap_m": round(dist), "component_nodes": sizes[labels[node]]})
    return out


def network_lines(net: dict[str, Any], snapped: list[dict[str, Any]]) -> list[list[list[float]]]:
    """コミットする線路の図。分岐・行き止まり・貨物駅の節点で区切った線を DP_NETWORK_M で簡略化する
    （両端は元の節点の座標のまま。組み直したときのつながり方は元の図と同じ）。"""
    g = net["graph"]
    keep = [s["node"] for s in snapped if s["node"] is not None]
    return [rg.simplify(l, DP_NETWORK_M) for l in rg.chains(g, keep)]


def write_network(net: dict[str, Any], snapped: list[dict[str, Any]]) -> None:
    lines = network_lines(net, snapped)
    q = lambda l: [[round(x, 5), round(y, 5)] for x, y in l]  # noqa: E731
    write_json(OUT / "rail_network.json", {
        "attribution": "国土数値情報（鉄道データ）（国土交通省）を加工して作成。貨物専用線・側線は © OpenStreetMap contributors",
        "license": "国土数値情報 N02: CC BY 4.0。OSM: ODbL 1.0",
        "built_on": date.today().isoformat(),
        "note": f"JR 在来線と貨物列車が走る第三セクター（N02-24）、OSM の貨物線・貨物駅の周り {SPUR_RADIUS_M}m の線路。"
                f"分岐・行き止まり・貨物駅の節点で区切った線を約 {DP_NETWORK_M:.0f}m で簡略化。"
                f"約 {SNAP_M:.0f}m 以内の頂点は同じ節点にまとめ、OSM の線の端は {CONNECT_M:.0f}m 以内の N02 の頂点につないである",
        "snap_m": SNAP_M, "connect_m": CONNECT_M,
        "stats": {"n02": net["summary_n02"], "with_osm": net["summary"], "n02_sections": net["counts"],
                  "osm_freight_lines": net["n_freight_lines"], "osm_spur_lines": net["n_spur_lines"],
                  "connectors": len(net["connectors"]), "chains": len(lines),
                  "station_bridges_m": net.get("bridges", [])},
        "lines": [q(l) for l in lines],
    })
    write_json(OUT / "rail_stations.json", {
        "license": "国土数値情報 N02: CC BY 4.0。OSM: ODbL 1.0",
        "attribution": "© OpenStreetMap contributors（貨物駅の位置）。国土数値情報（鉄道データ）（国土交通省）を加工して作成",
        "note": "node_lon/node_lat は図の中でいちばん近い節点。snap_m は駅の位置からその節点までの距離 m",
        "stations": [{k: v for k, v in s.items() if k not in ("node", "query")} for s in snapped],
    })


def stage_net() -> None:
    stations = dedupe_stations()
    net = build_network(stations)
    snapped = snap_stations(net["graph"], stations)
    bridges = []
    for s in snapped:  # 最大の連結成分に入らない駅は、1km 以内なら最大の成分につなぐ（つないだ距離を報告する）
        if s["node"] is not None:
            gap = rg.bridge_to_largest(net["graph"], s["node"], BRIDGE_M)
            if gap is not None:
                bridges.append((s["name"], round(gap)))
    net["bridges"] = bridges
    log(f"最大の成分につないだ駅（駅名, 距離 m）: {bridges}")
    snapped = snap_stations(net["graph"], stations)
    far = [s for s in snapped if s["snap_m"] is None or s["snap_m"] > STATION_WARN_M]
    log(f"貨物駅 {len(snapped)} 件（同名を除いた数）。図への距離が {STATION_WARN_M:.0f}m を超える駅: {len(far)} 件")
    for s in snapped:
        flag = "  <-- 遠い" if s in far else ""
        log(f"  {s['name']}: {s['snap_m']}m 成分の節点数 {s['component_nodes']}{flag}")
    airports = overpass(OSM_AIRPORT_QUERY, RAW / "osm_airports.json")
    rows = []
    for el in airports.get("elements", []):
        t = el.get("tags", {})
        c = el.get("center") or {"lon": el.get("lon"), "lat": el.get("lat")}
        if not t.get("iata") or c.get("lon") is None:
            continue
        rows.append({"name": t.get("name:ja") or t.get("name") or t["iata"], "iata": t["iata"],
                     "lon": round(c["lon"], 5), "lat": round(c["lat"], 5), "osm": f"{el['type']}/{el['id']}"})
    write_json(OUT / "airports.json", {"source": "OpenStreetMap（Overpass API）aeroway=aerodrome で iata があるもの",
                                       "license": "ODbL 1.0", "attribution": "© OpenStreetMap contributors",
                                       "fetched_on": date.today().isoformat(), "airports": rows})
    log(f"空港 {len(rows)} 件")
    write_network(net, snapped)





# ---------------------------------------------------------------- 経路の対象

TOP_PAIRS = 60
PAIR_MIN_KM = 200.0
HUB_MAX_KM = 250.0  # 出発地・着地から最寄りの貨物駅・空港までの直線距離の上限（これより遠いと島など）
PORT_MAX_KM = 150.0
FERRY_MIN_ROUTE_KM = 100.0
FERRY_MIN_SHARE = 0.25  # 航路の長さが、出発地〜着地の直線距離のこの割合に満たないものは経路にしない
FERRY_DETOUR_MAX = 1.6  # 出発地→港→航路→港→着地が、直線距離のこの倍を超えるものは経路にしない
AIR_MIN_KM = rt.AIR_MIN_KM
CHAIN_MAX_LEGS = 10  # 1 回の問い合わせにまとめる拠点との区間の数
TINY_LEG_KM = 0.3  # これより短い第一/最終区間は作らない（2 点の線になるため）


def _ll(p: dict[str, Any]) -> tuple[float, float]:
    return (round(p["lon"], 5), round(p["lat"], 5))


def key_of(a: tuple[float, float], b: tuple[float, float]) -> str:
    return f"{a[0]:.5f},{a[1]:.5f};{b[0]:.5f},{b[1]:.5f}"


def hav(a: tuple[float, float], b: tuple[float, float]) -> float:
    return haversine_km(a[0], a[1], b[0], b[1])


class World:
    """経路の対象と、拠点（貨物駅・港・空港）の一覧。"""

    def __init__(self) -> None:
        pts = load_points()
        self.points = pts
        self.origins = {c: (ORIGINS[c], _ll(pts[c])) for c in ORIGINS}
        bh = read_json(ROOT / "data" / "stats" / "backhaul_2021.json")
        self.capitals = {name: (round(ll[0], 5), round(ll[1], 5)) for name, ll in bh["capitals"].items()}
        pairs = [p for p in bh["modes"]["truck"]["pairs"] if p["km"] >= PAIR_MIN_KM]
        pairs.sort(key=lambda p: -p["shortfall_t"])
        self.pairs = pairs[:TOP_PAIRS]
        self.trips = read_json(ROOT / "data" / "demo" / "carriers.json")["trips"]
        sj = read_json(OUT / "rail_stations.json")["stations"]
        self.stations = [s for s in sj if s.get("node_lon") is not None]
        self.airports = read_json(OUT / "airports.json")["airports"]
        terms = load_hubs("ferry_terminals")
        routes = [r for r in load_hubs("ferry_routes") if r["km"] >= FERRY_MIN_ROUTE_KM]
        port_idx = freight_ferry_ports(terms, routes)
        self.ports = [terms[i] for i in port_idx]
        self.links = self._ferry_links(routes)

    def _nearest_port(self, lon: float, lat: float, max_km: float = 3.0) -> dict[str, Any] | None:
        best = None
        for p in self.ports:
            d = haversine_km(lon, lat, p["lon"], p["lat"])
            if d <= max_km and (best is None or d < best[0]):
                best = (d, p)
        return best[1] if best else None

    def _ferry_links(self, routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in routes:
            path = r["path"]
            a, b = self._nearest_port(*path[0]), self._nearest_port(*path[-1])
            if a and b and a["name"] != b["name"]:
                out.append({"name": r["name"], "km": r["km"], "osm": r["osm"], "path": path, "a": a, "b": b})
        return out

    def muni_near(self, ll: tuple[float, float]) -> str:
        return min(self.points, key=lambda c: (self.points[c]["lon"] - ll[0]) ** 2 + (self.points[c]["lat"] - ll[1]) ** 2)

    def nearest(self, ll: tuple[float, float], items: list[dict[str, Any]]) -> tuple[dict[str, Any], float]:
        best = min(items, key=lambda h: hav(ll, (h["lon"], h["lat"])))
        return best, hav(ll, (best["lon"], best["lat"]))

    def ferry_between(self, o: tuple[float, float], d: tuple[float, float]) -> tuple[dict[str, Any] | None, str]:
        """出発地と着地をつなぐ航路。(向きをそろえた航路, 理由)。"""
        direct = hav(o, d)
        if direct < 100:
            return None, "出発地と着地が近いため、フェリーの経路は作りません"
        best = None
        for l in self.links:
            for s, e, path in ((l["a"], l["b"], l["path"]), (l["b"], l["a"], l["path"][::-1])):
                d1, d2 = hav(o, (s["lon"], s["lat"])), hav(d, (e["lon"], e["lat"]))
                if best is None or d1 + d2 < best[0]:
                    best = (d1 + d2, d1, d2, l, s, e, path)
        if best is None:
            return None, "取得済みのフェリー航路がありません"
        _, d1, d2, l, s, e, path = best
        if d1 > PORT_MAX_KM or d2 > PORT_MAX_KM:
            return None, f"出発地または着地の近く（{PORT_MAX_KM:.0f}km 以内）に、両端がつながる 100km 以上のフェリー航路がありません"
        if l["km"] < FERRY_MIN_SHARE * direct:
            return None, "つながる航路が出発地〜着地の距離に比べて短すぎます（直通の航路がありません）"
        if d1 + l["km"] + d2 > FERRY_DETOUR_MAX * direct:
            return None, "つながる航路が出発地〜着地の方向と合いません（直通の航路がありません）"
        return {"link": l, "start": s, "end": e, "path": path}, ""


# ---------------------------------------------------------------- 必要な区間（OSRM の要求を数える）

class Need:
    """OSRM で取る区間の一覧。同じ区間（向きを問わない）は 1 回だけ。
    main/trips は 1 区間ごとの問い合わせ。hub_* は点と拠点の短い区間で、まとめて 1 回の問い合わせ（鎖）で取る。"""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def add(self, a: tuple[float, float], b: tuple[float, float], kind: str) -> None:
        if key_of(a, b) in self.items or key_of(b, a) in self.items:
            return
        self.items[key_of(a, b)] = {"a": a, "b": b, "kind": kind}

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for it in self.items.values():
            out[it["kind"]] = out.get(it["kind"], 0) + 1
        return out

    def singles(self) -> list[dict[str, Any]]:
        return [it for it in self.items.values() if not it["kind"].startswith("hub_")]

    def hubs(self) -> list[dict[str, Any]]:
        return [it for it in self.items.values() if it["kind"].startswith("hub_")]


def trails(edges: list[tuple[tuple[float, float], tuple[float, float]]], max_legs: int = CHAIN_MAX_LEGS,
           ) -> list[list[tuple[float, float]]]:
    """辺（点と拠点の区間）を、続けて通れる点の列（鎖）に分ける。1 本の鎖は最大 max_legs 区間。
    同じ拠点を共有する区間をつなぐので、問い合わせの数が辺の数より少なくなる。"""
    adj: dict[tuple[float, float], list[int]] = {}
    for i, (a, b) in enumerate(edges):
        adj.setdefault(a, []).append(i)
        adj.setdefault(b, []).append(i)
    used = [False] * len(edges)
    out: list[list[tuple[float, float]]] = []

    def remaining(v: tuple[float, float]) -> int:
        return sum(1 for i in adj[v] if not used[i])

    while not all(used):
        # 残りの辺の数が奇数の点から始めると、鎖が長くなる
        starts = [v for v in adj if remaining(v) % 2 == 1] or [v for v in adj if remaining(v) > 0]
        cur = min(starts, key=lambda v: remaining(v))
        chain = [cur]
        while len(chain) <= max_legs:
            nxt = [i for i in adj[cur] if not used[i]]
            if not nxt:
                break
            i = min(nxt, key=lambda i: remaining(edges[i][1] if edges[i][0] == cur else edges[i][0]))
            used[i] = True
            cur = edges[i][1] if edges[i][0] == cur else edges[i][0]
            chain.append(cur)
        out.append(chain)
    return out


def load_graph() -> rg.Graph:
    return rg.build(read_json(OUT / "rail_network.json")["lines"], SNAP_M)


def rail_between(g: rg.Graph, cache: dict[int, Any], s_from: dict[str, Any], s_to: dict[str, Any],
                 ) -> tuple[float, list[list[float]]] | None:
    a = g.node_for(s_from["node_lon"], s_from["node_lat"])
    b = g.node_for(s_to["node_lon"], s_to["node_lat"])
    if a not in cache:
        cache[a] = rg.dijkstra(g, a)
    dist, prev = cache[a]
    p = rg.path(prev, a, b)
    if p is None:
        return None
    return dist[b], rg.path_coords(g, p)


def truck_blocked(world: World, j: dict[str, Any]) -> str | None:
    """OSRM に問い合わせる前に分かる「道がつながっていない」理由。"""
    if hav(j["o"], j["d"]) < TINY_LEG_KM:
        return "出発地と着地が同じ場所です"
    if "沖縄県" in (j["o_name"], j["d_name"]):
        return "道路でつながっていません（離島）"
    if j["origin_code"]:
        row = load_osrm(j["origin_code"]).get(world.muni_near(j["d"]))
        if row is not None and row["km"] is None:
            return "道路でつながっていません（離島など）"
    return None


def job_list(world: World) -> list[dict[str, Any]]:
    """経路を作る (出発点, 着地点) の一覧。出発地 7 × 県庁所在地 47 と、帰り便の上位 60 区間。"""
    jobs = []
    for oc, (oname, oll) in world.origins.items():
        for pref, cll in world.capitals.items():
            jobs.append({"kind": "origin", "id": f"{oc}>{pref}", "o": oll, "d": cll, "o_name": oname,
                         "d_name": pref, "origin_code": oc})
    for p in world.pairs:
        jobs.append({"kind": "pair", "id": f"{p['from']}>{p['to']}", "o": world.capitals[p["from"]],
                     "d": world.capitals[p["to"]], "o_name": p["from"], "d_name": p["to"], "origin_code": None,
                     "meta": {k: p[k] for k in ("go_t", "back_t", "ratio", "shortfall_t", "km")}})
    return jobs


def plan_mode(world: World, g: rg.Graph, cache: dict[int, Any], j: dict[str, Any], mode: str) -> dict[str, Any]:
    o, d = j["o"], j["d"]
    if mode == "rail":
        sa, da = world.nearest(o, world.stations)
        sb, db = world.nearest(d, world.stations)
        if da > HUB_MAX_KM or db > HUB_MAX_KM:
            return {"reason": f"最寄りの貨物駅が遠すぎます（{HUB_MAX_KM:.0f}km 超。島など）"}
        if sa["name"] == sb["name"]:
            return {"reason": "出発地と着地の最寄りの貨物駅が同じです"}
        r = rail_between(g, cache, sa, sb)
        if r is None:
            return {"reason": "線路がつながっていません（線路の図で貨物駅の間の経路がありません）"}
        return {"a": sa, "b": sb, "rail": r, "hubs": [(o, (sa["lon"], sa["lat"])), (d, (sb["lon"], sb["lat"]))]}
    if mode == "air":
        aa, da = world.nearest(o, world.airports)
        ab, db = world.nearest(d, world.airports)
        if da > HUB_MAX_KM or db > HUB_MAX_KM:
            return {"reason": f"最寄りの空港が遠すぎます（{HUB_MAX_KM:.0f}km 超）"}
        if aa["iata"] == ab["iata"]:
            return {"reason": "出発地と着地の最寄りの空港が同じです"}
        gc = hav((aa["lon"], aa["lat"]), (ab["lon"], ab["lat"]))
        if gc < AIR_MIN_KM:
            return {"reason": f"空港間が {gc:.0f}km で、{AIR_MIN_KM:.0f}km 未満のため飛行機の経路は作りません"}
        return {"a": aa, "b": ab, "hubs": [(o, (aa["lon"], aa["lat"])), (d, (ab["lon"], ab["lat"]))]}
    link, reason = world.ferry_between(o, d)
    if link is None:
        return {"reason": reason}
    return {"link": link, "hubs": [(o, (link["start"]["lon"], link["start"]["lat"])),
                                    (d, (link["end"]["lon"], link["end"]["lat"]))]}


def plan_all(world: World, g: rg.Graph, jobs: list[dict[str, Any]]) -> Need:
    """全経路の計画（j["plan"]）を立て、必要な OSRM の区間を数える。"""
    need = Need()
    cache: dict[int, Any] = {}
    for j in jobs:
        j["blocked"] = truck_blocked(world, j)
        if j["blocked"] is None:
            need.add(j["o"], j["d"], "main")
    for t in world.trips:
        need.add(_ll(world.points[t["origin_code"]]), _ll(world.points[t["dest_code"]]), "trips")
    for mode in ("rail", "air", "ferry"):
        for j in jobs:
            plan = plan_mode(world, g, cache, j, mode)
            j.setdefault("plan", {})[mode] = plan
            if j["blocked"] is None:
                for pt, hub in plan.get("hubs", []):
                    need.add(pt, (round(hub[0], 5), round(hub[1], 5)), f"hub_{mode}")
    return need


# ---------------------------------------------------------------- OSRM の取得

def load_cache() -> dict[str, Any]:
    return read_json(OSRM_CACHE) if OSRM_CACHE.exists() else {}


def save_cache(cache: dict[str, Any]) -> None:
    write_json(OSRM_CACHE, cache)


def cached(cache: dict[str, Any], a: tuple[float, float], b: tuple[float, float]) -> dict[str, Any] | None:
    """a→b の取得結果。逆向きに取ってあれば反転して返す。{"error": ...} は経路がなかった記録。"""
    v = cache.get(key_of(a, b))
    if v is not None:
        return v
    v = cache.get(key_of(b, a))
    if v is None or "error" in v:
        return v
    return {**v, "coordinates": v["coordinates"][::-1]}


def _store(cache: dict[str, Any], a: tuple[float, float], b: tuple[float, float], r: dict[str, Any]) -> None:
    cache[key_of(a, b)] = {
        "km": round(r["km"], 2), "minutes": round(r["minutes"], 1),
        "coordinates": [[round(x, 5), round(y, 5)] for x, y in rg.simplify(r["coordinates"], 15.0)]}


def plan_requests(need: Need, cache: dict[str, Any]) -> tuple[list[dict[str, Any]], list[list[tuple[float, float]]]]:
    """これから取る単独の区間と、鎖の一覧（取得済みは除く）。"""
    singles = [it for it in need.singles() if cached(cache, it["a"], it["b"]) is None]
    edges = [(it["a"], it["b"]) for it in need.hubs() if cached(cache, it["a"], it["b"]) is None]
    return singles, trails(edges)


def stage_osrm(need: Need, max_requests: int) -> int:
    cache = load_cache()
    client = rt.OsrmClient(min_interval=1.1)
    singles, chains = plan_requests(need, cache)
    log(f"OSRM: 必要 {len(need.items)} 区間（取得済み {len(need.items) - len(singles) - sum(len(c) - 1 for c in chains)}）。"
        f"これから: 単独 {len(singles)} 回 + 鎖 {len(chains)} 回（上限 {max_requests}）")
    retry_edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    done = 0
    for it in singles:
        if client.requests >= max_requests:
            break
        try:
            _store(cache, it["a"], it["b"], client.route(it["a"], it["b"]))
        except rt.NoRoute as e:
            cache[key_of(it["a"], it["b"])] = {"error": str(e)}
        except rt.RouteError as e:
            log(f"失敗 {it['kind']}: {it['a']} -> {it['b']}: {e}")
        done += 1
        if done % 25 == 0:
            save_cache(cache)
            log(f"  単独 {done}/{len(singles)}（要求 {client.requests} 件）")
    for ch in chains:
        if client.requests >= max_requests:
            log(f"上限 {max_requests} 件に達したので止めます")
            break
        try:
            for (a, b), r in zip(zip(ch, ch[1:]), client.chain(ch)):
                _store(cache, a, b, r)
        except rt.RouteError as e:
            log(f"鎖が失敗（{len(ch) - 1} 区間、{e}）。1 区間ずつ取り直します")
            retry_edges += list(zip(ch, ch[1:]))
    for a, b in retry_edges:
        if client.requests >= max_requests:
            break
        try:
            _store(cache, a, b, client.route(a, b))
        except rt.NoRoute as e:
            cache[key_of(a, b)] = {"error": str(e)}
        except rt.RouteError as e:
            log(f"失敗 {a} -> {b}: {e}")
    save_cache(cache)
    log(f"OSRM: 今回の要求 {client.requests} 件")
    return client.requests


# ---------------------------------------------------------------- 経路の組み立て

TOL_ROAD_M = 75.0
TOL_RAIL_M = 50.0
TOL_FERRY_M = 200.0
UNFETCHED = "トラックの区間を OSRM から取得できませんでした"


def _road(cache: dict[str, Any], a: tuple[float, float], b: tuple[float, float], label: str,
          keep_tiny: bool = False) -> dict[str, Any] | None | str:
    """a→b のトラック区間。取得できていなければ理由の文字列、短すぎる区間は None（作らない）。"""
    a = (round(a[0], 5), round(a[1], 5))
    b = (round(b[0], 5), round(b[1], 5))
    r = cached(cache, a, b)
    if r is None:
        return UNFETCHED
    if "error" in r:
        return "トラックの区間に道路の経路がありません（島など）"
    if r["km"] < TINY_LEG_KM and not keep_tiny:
        return None
    return rt.make_leg("truck", r["coordinates"], km=r["km"], minutes=r["minutes"], label=label, tolerance_m=TOL_ROAD_M)


def _sandwich(first: Any, middle: dict[str, Any], last: Any) -> tuple[dict[str, Any] | None, str]:
    legs = []
    for leg in (first, middle, last):
        if isinstance(leg, str):
            return None, leg
        if leg is not None:
            legs.append(leg)
    route = rt.make_route(legs)
    return route, ""


def compose_job(cache: dict[str, Any], j: dict[str, Any]) -> dict[str, Any]:
    o, d = j["o"], j["d"]
    modes: dict[str, Any] = {}
    reasons: dict[str, str] = {}
    # トラック
    if j["blocked"]:
        modes["truck"], reasons["truck"] = None, j["blocked"]
    else:
        r = cached(cache, o, d)
        if r is None:
            modes["truck"], reasons["truck"] = None, "OSRM から取得できませんでした"
        elif "error" in r:
            modes["truck"], reasons["truck"] = None, "道路の経路がありません"
        else:
            modes["truck"] = rt.make_route([rt.make_leg("truck", r["coordinates"], km=r["km"], minutes=r["minutes"],
                                                        label=f"{j['o_name']}→{j['d_name']}", tolerance_m=TOL_ROAD_M)])
            modes["truck"]["via_ferry_possible"] = is_ferry_route(r["km"], r["minutes"])
    plans = j["plan"]
    for mode in ("rail", "ferry", "air"):
        plan = plans[mode]
        if plan.get("reason"):
            modes[mode], reasons[mode] = None, plan["reason"]
            continue
        if j["blocked"]:
            modes[mode], reasons[mode] = None, j["blocked"]
            continue
        (po, ha), (pd, hb) = plan["hubs"]
        if mode == "rail":
            km, coords = plan["rail"]
            mid = rt.make_leg("rail", coords, km=km, label=f"{plan['a']['name']}→{plan['b']['name']}",
                              tolerance_m=TOL_RAIL_M)
            names = (plan["a"]["name"], plan["b"]["name"])
        elif mode == "ferry":
            link = plan["link"]
            mid = rt.make_leg("ferry", link["path"], label=f"{link['start']['name']}→{link['end']['name']}",
                              tolerance_m=TOL_FERRY_M)
            names = (link["start"]["name"], link["end"]["name"])
        else:
            gc = rt.great_circle((plan["a"]["lon"], plan["a"]["lat"]), (plan["b"]["lon"], plan["b"]["lat"]))
            mid = rt.make_leg("air", gc, label=f"{plan['a']['iata']}→{plan['b']['iata']}", simplify_line=False)
            names = (plan["a"]["name"], plan["b"]["name"])
        first = _road(cache, po, ha, f"{j['o_name']}→{names[0]}")
        last = _road(cache, hb, pd, f"{names[1]}→{j['d_name']}")
        route, why = _sandwich(first, mid, last)
        if route is None:
            modes[mode], reasons[mode] = None, why
        else:
            route["hubs"] = list(names)
            modes[mode] = route
    return {"modes": modes, "reasons": reasons}


def compose_trips(world: World, cache: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for t in world.trips:
        a, b = _ll(world.points[t["origin_code"]]), _ll(world.points[t["dest_code"]])
        r = cached(cache, a, b)
        name = f"{world.points[t['origin_code']]['name']}→{world.points[t['dest_code']]['name']}"
        if r is None or "error" in r:
            out[t["id"]] = {"origin_code": t["origin_code"], "dest_code": t["dest_code"], "truck": None,
                            "reason": "OSRM から取得できませんでした"}
        else:
            out[t["id"]] = {"origin_code": t["origin_code"], "dest_code": t["dest_code"],
                            "truck": rt.make_route([rt.make_leg("truck", r["coordinates"], km=r["km"],
                                                                minutes=r["minutes"], label=name,
                                                                tolerance_m=TOL_ROAD_M)])}
    return out


def header(extra: dict[str, Any]) -> dict[str, Any]:
    return {**rt.LICENSE_FIELDS, "built_on": date.today().isoformat(), **extra}


def stage_compose(world: World, jobs: list[dict[str, Any]], requests: int | None) -> dict[str, Any]:
    cache = load_cache()
    by_origin: dict[str, dict[str, Any]] = {}
    pairs: dict[str, Any] = {}
    for j in jobs:
        rec = compose_job(cache, j)
        rec["lonlat"] = list(j["d"])
        if j["kind"] == "origin":
            by_origin.setdefault(j["origin_code"], {})[j["d_name"]] = rec
        else:
            rec["from"], rec["to"] = j["o_name"], j["d_name"]
            rec.update(j["meta"])
            pairs[j["id"]] = rec
    stats = {"modes_ok": {m: 0 for m in rt.MODES}, "modes_null": {m: 0 for m in rt.MODES}, "reasons": {},
             "two_point_legs": 0, "routes": 0}
    sizes: dict[str, int] = {}

    def tally(rec: dict[str, Any]) -> None:
        stats["routes"] += 1
        for m in rt.MODES:
            r = rec["modes"].get(m)
            if r is None:
                stats["modes_null"][m] += 1
                why = rec["reasons"].get(m, "")
                key = f"{m}: {why}"
                stats["reasons"][key] = stats["reasons"].get(key, 0) + 1
            else:
                stats["modes_ok"][m] += 1
                stats["two_point_legs"] += sum(1 for l in r["legs"] if len(l["coordinates"]) <= 2)

    for oc, recs in by_origin.items():
        for rec in recs.values():
            tally(rec)
        path = OUT / f"from_{oc}.json"
        write_json(path, header({"kind": "origin", "origin": {"code": oc, "name": world.origins[oc][0],
                                                             "lonlat": list(world.origins[oc][1])},
                                 "note": "dests のキーは都道府県名（県庁所在地への経路）。modes の各手段は legs（mode・km・minutes・coordinates）を持ち、"
                                         "できない手段は null で reasons に理由。minutes はトラックの区間だけ", "dests": recs}))
        sizes[path.name] = path.stat().st_size
    for rec in pairs.values():
        tally(rec)
    path = OUT / "pairs.json"
    write_json(path, header({"kind": "backhaul_pairs", "note": "帰り便の上位区間（営業用トラック、直線 200km 以上の上位 60、不足量の順）。"
                                                             "出発点と着地は県庁所在地", "pairs": pairs}))
    sizes[path.name] = path.stat().st_size
    trips = compose_trips(world, cache)
    path = OUT / "trips.json"
    write_json(path, header({"kind": "demo_trips", "note": "data/demo/carriers.json の架空の便の道路の経路（架空の便だが道路は実在）",
                             "trips": trips}))
    sizes[path.name] = path.stat().st_size
    for name in ("rail_network.json", "rail_stations.json", "airports.json"):
        sizes[name] = (OUT / name).stat().st_size
    total = sum(sizes.values())
    manifest = {"built_on": date.today().isoformat(), "osrm_requests_this_run": requests, "stats": stats,
                "file_bytes": sizes, "total_bytes": total, "trips_ok": sum(1 for t in trips.values() if t["truck"])}
    write_json(OUT / "manifest.json", manifest)
    log(f"経路 {stats['routes']} 件。手段ごとの成功 {stats['modes_ok']}、null {stats['modes_null']}、2 点だけの区間 {stats['two_point_legs']}")
    for k, v in sorted(stats["reasons"].items(), key=lambda kv: -kv[1]):
        log(f"  null {v:4d} 件  {k}")
    for k, v in sizes.items():
        log(f"  {k}: {v / 1024:.0f}KB")
    log(f"合計 {total / 1e6:.2f}MB")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", default="all", choices=["net", "osrm", "compose", "all"])
    ap.add_argument("--max-requests", type=int, default=450)
    ap.add_argument("--plan", action="store_true", help="OSRM の要求を数えるだけで取得しない")
    a = ap.parse_args(argv)
    if a.stage in ("net", "all"):
        stage_net()
    if a.stage in ("osrm", "compose", "all"):
        world = World()
        g = load_graph()
        jobs = job_list(world)
        need = plan_all(world, g, jobs)
        log(f"必要な OSRM 区間: 合計 {len(need.items)} {need.counts()}")
        singles, chains = plan_requests(need, load_cache())
        log(f"問い合わせの数: 単独 {len(singles)} + 鎖 {len(chains)} = {len(singles) + len(chains)}（鎖は拠点との区間をまとめたもの）")
        if a.plan:
            return 0
        requests = None
        if a.stage in ("osrm", "all"):
            requests = stage_osrm(need, a.max_requests)
        if a.stage in ("compose", "all"):
            stage_compose(world, jobs, requests)
    return 0


if __name__ == "__main__":
    sys.exit(main())
