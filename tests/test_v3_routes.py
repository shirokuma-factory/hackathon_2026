"""SPEC §17.1 / §17.3 の試験: 線路の図、簡略化、経路の組み立て、その場の取得（通信は差し替え）。"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from shipcompare import railgraph as rg
from shipcompare import routes as rt
from shipcompare.tariffs import data_dir

ROUTES = data_dir() / "routes"


def _read(name: str):
    with open(ROUTES / name, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 線路の図（小さな図）

def _small_graph() -> rg.Graph:
    #   A(0,0) --- B(0.01,0) --- C(0.02,0)        短い道: A-B-C
    #     \                       /
    #      D(0.01,0.05) ---------                 遠回り: A-D-C
    #   E(0.5,0.5) - F(0.51,0.5)                  つながっていない島
    lines = [
        [[0.0, 0.0], [0.01, 0.0]], [[0.01, 0.0], [0.02, 0.0]],
        [[0.0, 0.0], [0.01, 0.05]], [[0.01, 0.05], [0.02, 0.0]],
        [[0.5, 0.5], [0.51, 0.5]],
    ]
    return rg.build(lines, 30.0)


def test_spec17_small_graph_shortest_path():
    g = _small_graph()
    a, _ = rg.snap(g, 0.0, 0.0)
    c, _ = rg.snap(g, 0.02, 0.0)
    dist, coords = rg.shortest_path(g, a, c)
    assert coords[0] == [0.0, 0.0] and coords[-1] == [0.02, 0.0]
    assert [round(p[0], 3) for p in coords] == [0.0, 0.01, 0.02]  # A-B-C を通る
    assert dist == pytest.approx(rg.polyline_km([[0.0, 0.0], [0.02, 0.0]]), rel=1e-6)


def test_spec17_small_graph_disconnected_is_none():
    g = _small_graph()
    a, _ = rg.snap(g, 0.0, 0.0)
    e, _ = rg.snap(g, 0.5, 0.5)
    assert rg.shortest_path(g, a, e) is None
    assert rg.components(g) == [4, 2]


def test_spec17_snap_merges_nearby_vertices_and_reports_distance():
    # 約 20m 離れた端点は同じ節点になり、約 100m 離れた端点は別の節点になる
    g = rg.build([[[0.0, 0.0], [0.01, 0.0]], [[0.01, 0.0002], [0.02, 0.0]]], 30.0)
    assert g.node_count == 3
    g2 = rg.build([[[0.0, 0.0], [0.01, 0.0]], [[0.01, 0.001], [0.02, 0.0]]], 30.0)
    assert g2.node_count == 4
    node, dist = rg.snap(g, 0.0, 0.0009)
    assert dist == pytest.approx(100, abs=2)
    assert rg.snap(g, 1.0, 1.0, max_m=5000) is None


def test_spec17_connect_lines_joins_within_radius_only():
    g = rg.build([[[0.0, 0.0], [0.01, 0.0]]], 30.0)
    rg.connect_lines(g, [[[0.0105, 0.0], [0.02, 0.0]]], radius_m=150.0)  # 端が約 55m 離れている
    a, _ = rg.snap(g, 0.0, 0.0)
    b, _ = rg.snap(g, 0.02, 0.0)
    assert rg.shortest_path(g, a, b) is not None
    g2 = rg.build([[[0.0, 0.0], [0.01, 0.0]]], 30.0)
    rg.connect_lines(g2, [[[0.0125, 0.0], [0.02, 0.0]]], radius_m=150.0)  # 約 280m
    a, _ = rg.snap(g2, 0.0, 0.0)
    b, _ = rg.snap(g2, 0.02, 0.0)
    assert rg.shortest_path(g2, a, b) is None


def test_spec17_chains_keep_connectivity():
    g = _small_graph()
    lines = [rg.simplify(l, 30.0) for l in rg.chains(g)]
    g2 = rg.build(lines, 30.0)
    assert len(rg.components(g2)) == len(rg.components(g)) == 2
    a, _ = rg.snap(g2, 0.0, 0.0)
    c, _ = rg.snap(g2, 0.02, 0.0)
    assert rg.shortest_path(g2, a, c) is not None


# ---------------------------------------------------------------- 簡略化

def test_spec17_simplify_keeps_endpoints():
    line = [[0.0, 0.0], [0.001, 0.00001], [0.002, -0.00001], [0.003, 0.0]]
    out = rg.simplify(line, 50.0)
    assert out[0] == line[0] and out[-1] == line[-1]
    assert len(out) == 2  # ほぼ直線なので両端だけ
    bend = [[0.0, 0.0], [0.005, 0.005], [0.01, 0.0]]
    assert rg.simplify(bend, 50.0) == bend  # 大きく曲がる点は残る
    assert rg.simplify([[0.0, 0.0], [1.0, 1.0]], 50.0) == [[0.0, 0.0], [1.0, 1.0]]


def test_spec17_simplify_error_is_within_tolerance():
    line = [[0.0 + i * 0.0005, 0.0003 * ((i * 7) % 5)] for i in range(200)]
    out = rg.simplify(line, 80.0)
    assert len(out) < len(line)
    for p in line:
        assert rg.distance_to_lines_m((p[0], p[1]), [out], 1000) <= 80.0 + 1.0


def test_spec17_make_leg_never_two_points_when_source_has_more():
    coords = [[139.0, 35.0], [139.0005, 35.00001], [139.001, 35.0]]
    leg = rt.make_leg("truck", coords, tolerance_m=75.0)
    assert len(leg["coordinates"]) == 3
    assert leg["coordinates"][0] == [139.0, 35.0] and leg["coordinates"][-1] == [139.001, 35.0]


def test_spec17_great_circle_has_at_least_64_interpolated_points():
    haneda, itami = (139.78, 35.55), (135.44, 34.78)
    arc = rt.great_circle(haneda, itami)
    assert len(arc) >= 64 + 2 - 1  # 補間 64 区間 + 両端
    assert arc[0] == [139.78, 35.55] and arc[-1] == [135.44, 34.78]
    mid = arc[len(arc) // 2]
    assert 134 < mid[0] < 141 and 34 < mid[1] < 36
    leg = rt.make_leg("air", arc, simplify_line=False)
    assert len(leg["coordinates"]) == len(arc) and leg["minutes"] is None


# ---------------------------------------------------------------- G41 コミット済みの線路の図

@pytest.fixture(scope="module")
def rail():
    net = _read("rail_network.json")
    g = rg.build(net["lines"], net["snap_m"])
    stations = {s["name"]: s for s in _read("rail_stations.json")["stations"]}
    return g, net["lines"], stations


def test_spec17_g41_tokyo_to_kudara_follows_rails(rail):
    g, lines, st = rail
    a = g.node_for(st["東京貨物ターミナル"]["node_lon"], st["東京貨物ターミナル"]["node_lat"])
    b = g.node_for(st["百済貨物ターミナル"]["node_lon"], st["百済貨物ターミナル"]["node_lat"])
    r = rg.shortest_path(g, a, b)
    assert r is not None, "東京貨物ターミナル→百済貨物ターミナルが線路の図でつながっていません"
    km, coords = r
    assert 450 < km < 700  # 東海道線経由で約 530km
    assert len(coords) > 100
    for p in coords:
        assert rg.distance_to_lines_m((p[0], p[1]), lines, 50.0) <= 50.0


def test_spec17_g41_station_snap_distances_recorded(rail):
    _, _, st = rail
    assert len(st) >= 30
    assert all(s["snap_m"] is not None and s["snap_m"] <= 300 for s in st.values())


def test_spec17_g41_committed_rail_legs_lie_on_rails(rail):
    g, lines, _ = rail
    rec = _read("from_13104.json")["dests"]["大阪府"]["modes"]["rail"]
    assert rec is not None
    rail_legs = [l for l in rec["legs"] if l["mode"] == "rail"]
    assert len(rail_legs) == 1 and rail_legs[0]["minutes"] is None
    for p in rail_legs[0]["coordinates"]:
        assert rg.distance_to_lines_m((p[0], p[1]), lines, 50.0) <= 50.0
    assert [l["mode"] for l in rec["legs"]][0] == "truck" and rec["legs"][0]["minutes"] is not None


# ---------------------------------------------------------------- G42 どの線も 2 点だけではない

def _all_routes():
    for code in ("13104", "14104", "11107", "12101", "08201", "09201", "10201"):
        for pref, rec in _read(f"from_{code}.json")["dests"].items():
            yield f"{code}>{pref}", rec
    for key, rec in _read("pairs.json")["pairs"].items():
        yield f"pair {key}", rec
    for tid, rec in _read("trips.json")["trips"].items():
        yield f"trip {tid}", {"modes": {"truck": rec["truck"]}, "reasons": {}}


def test_spec17_g42_no_committed_leg_is_two_points():
    n = 0
    for name, rec in _all_routes():
        for mode, route in rec["modes"].items():
            if route is None:
                assert rec["reasons"].get(mode), f"{name} {mode}: null なのに理由がありません"
                continue
            for leg in route["legs"]:
                n += 1
                assert len(leg["coordinates"]) >= 3, f"{name} {mode} {leg['mode']} が 2 点だけです"
                assert leg["mode"] == "truck" or leg["minutes"] is None  # 電車・船・飛行機の時間は出さない
                if leg["mode"] == "truck":
                    assert leg["minutes"] is not None and leg["minutes"] >= 0
    assert n > 1000


def test_spec17_air_legs_have_arc_points_and_routes_are_complete():
    air = [r["modes"]["air"] for _, r in _all_routes() if r["modes"].get("air")]
    assert air, "飛行機の経路が 1 本もありません"
    for route in air:
        arcs = [l for l in route["legs"] if l["mode"] == "air"]
        assert len(arcs) == 1 and len(arcs[0]["coordinates"]) >= 64 and arcs[0]["km"] >= 300
    # 全 7 出発地 × 47 県庁所在地 + 上位 60 区間 + 架空の便 6 本
    assert sum(len(_read(f"from_{c}.json")["dests"]) for c in ("13104", "14104", "11107", "12101", "08201", "09201", "10201")) == 7 * 47
    assert len(_read("pairs.json")["pairs"]) == 60
    assert len(_read("trips.json")["trips"]) == 6


def test_spec17_committed_files_have_attribution_and_fit_the_size_limit():
    total = 0
    for p in ROUTES.glob("*.json"):
        total += p.stat().st_size
        d = _read(p.name)
        if p.name != "manifest.json":
            assert "license" in d and "attribution" in d, p.name
    assert total <= 15 * 1024 * 1024
    for name in ("from_13104.json", "rail_network.json"):
        text = (ROUTES / name).read_bytes()
        assert b"\r" not in text
    assert "国土数値情報" in _read("rail_network.json")["attribution"]
    assert "国土数値情報（鉄道データ）（国土交通省）を加工して作成" in _read("pairs.json")["attribution"]


# ---------------------------------------------------------------- OSRM の間隔と、その場の取得

class _Res(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_body(n_legs: int = 1) -> bytes:
    return json.dumps({"code": "Ok", "routes": [{
        "distance": 12345.0, "duration": 600.0,
        "geometry": {"coordinates": [[139.0, 35.0], [139.01, 35.01], [139.02, 35.0], [139.03, 35.01]]}}]}).encode()


class _Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _client(urlopen, clock):
    return rt.OsrmClient(min_interval=1.0, retries=2, retry_wait=0.5, sleep=clock.sleep, clock=clock.now,
                         urlopen=urlopen)


def test_spec17_osrm_client_waits_between_requests():
    clock, urls = _Clock(), []

    def fake(req, timeout=0):
        urls.append(req.full_url)
        assert req.headers["User-agent"].startswith("shipcompare-hackathon/0.3")
        return _Res(_ok_body())

    c = _client(fake, clock)
    c.route((139.0, 35.0), (139.1, 35.1))
    c.route((139.0, 35.0), (139.2, 35.2))
    assert "overview=full" in urls[0] and "geometries=geojson" in urls[0]
    assert c.requests == 2
    assert sum(clock.sleeps) >= 1.0  # 2 件目の前に 1 秒以上待った


def test_spec17_osrm_client_does_not_retry_no_route():
    clock, calls = _Clock(), []

    def fake(req, timeout=0):
        calls.append(1)
        return _Res(json.dumps({"code": "NoRoute", "routes": []}).encode())

    with pytest.raises(rt.NoRoute):
        _client(fake, clock).route((139.0, 35.0), (127.0, 26.0))
    assert len(calls) == 1


def test_spec17_route_on_demand_caches_and_never_asks_twice(tmp_path: Path):
    clock, calls = _Clock(), []

    def fake(req, timeout=0):
        calls.append(req.full_url)
        return _Res(_ok_body())

    c = _client(fake, clock)
    a, b = (139.0, 35.0), (139.3, 35.3)
    first = rt.route_on_demand(a, b, base=tmp_path, client=c)
    assert first is not None and first["cached"] is False and first["km"] == 12.3 and first["minutes"] == 10.0
    assert len(first["coordinates"]) >= 3
    assert (tmp_path / "routes" / "cache").is_dir()
    second = rt.route_on_demand(a, b, base=tmp_path, client=c)
    assert second["cached"] is True and second["coordinates"] == first["coordinates"]
    assert len(calls) == 1


def test_spec17_route_on_demand_returns_none_on_failure(tmp_path: Path):
    clock = _Clock()

    def fail(req, timeout=0):
        raise urllib.error.URLError("offline")

    c = _client(fail, clock)
    assert rt.route_on_demand((139.0, 35.0), (139.3, 35.3), base=tmp_path, client=c) is None
    assert not list((tmp_path / "routes" / "cache").glob("*.json")) if (tmp_path / "routes" / "cache").exists() else True
    arc = rt.fallback_arc((139.0, 35.0), (139.3, 35.3))
    assert arc["fallback"] is True and len(arc["coordinates"]) > 3


def test_spec17_cache_dir_is_git_ignored():
    root = Path(__file__).resolve().parents[1]
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "data/routes/cache/" in ignore and "data/raw/" in ignore
