"""版3の地図の画面（SPEC §14、受入条件 G24〜G28）。通信は差し替え、地図の描画は画面写真で確かめる。"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from shipcompare.estimate import estimate_all
from shipcompare.geo import (
    GRAY, ORIGINS, break_minutes, class_color, class_of, color_for, drive_days, kashikiri_for_distance, load_osrm, load_points,
    load_routes, quantile_edges, representative_point, route_for,
)
from shipcompare.tariffs import load_tariff

from conftest import make_shipment, one

ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    spec = importlib.util.spec_from_file_location("fetch_osrm", ROOT / "tools" / "fetch_osrm.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fetch_osrm"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- データ（14.1〜14.3）
def test_spec14_1_data_files_have_source_fetch_date_and_license():
    for code in ORIGINS:
        raw = json.loads((ROOT / "data" / "geo" / f"osrm_{code}.json").read_text(encoding="utf-8"))
        assert raw["license"].startswith("ODbL") and raw["fetched_on"] and raw["source"]
        assert list(raw)[0] == "source"  # 出典などはファイルの先頭
    routes = json.loads((ROOT / "data" / "geo" / "routes.json").read_text(encoding="utf-8"))
    assert routes["license"].startswith("ODbL") and routes["fetched_on"]
    assert set(load_routes()) == {"13104-27128", "13104-04101", "01101-13104"}


def test_spec14_2_points_exclude_blank_codes_and_cover_origins():
    pts = load_points()
    assert len(pts) == 1902  # 境界 1,906 件のうちコードが空の 4 件には代表点を作らない
    assert all(code in pts for code in ORIGINS)
    assert all(122 < p["lon"] < 154 and 20 < p["lat"] < 46 for p in pts.values())


def test_spec14_3_representative_point_inside_and_fallback_to_vertex():
    square = {"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]}
    assert representative_point(square) == (2.0, 2.0)
    # 面積が最大の多角形を選ぶ
    multi = {"type": "MultiPolygon", "coordinates": [
        [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        [[[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]],
    ]}
    assert representative_point(multi) == (15.0, 15.0)
    # 重心が外に出る C 字形は、その多角形の頂点で重心に最も近い点
    c_shape = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 1], [1, 1], [1, 9], [10, 9], [10, 10], [0, 10], [0, 0]]]}
    x, y = representative_point(c_shape)
    assert (x, y) in [tuple(p) for p in c_shape["coordinates"][0]]


# ---------------------------------------------------------------- G24: 経路がない市区町村は灰色
def test_spec14_4_null_destination_is_gray_not_zero_or_minimum():
    osrm = load_osrm("13104")
    assert osrm["42209"] == {"km": None, "minutes": None}  # 対馬市: 経路なし。0 で埋めていない
    assert osrm["13104"]["km"] == 0.0  # 出発地そのものは 0（これは本当の 0）
    present = [d["km"] for d in osrm.values() if d["km"] is not None]
    vmin, vmax = min(present), max(present)
    assert color_for(None, vmin, vmax) == GRAY
    assert color_for(None, vmin, vmax) != color_for(vmin, vmin, vmax)
    assert color_for(None, 0, 0) == GRAY


def test_spec14_5_color_scale_is_single_hue_without_red():
    lo, mid, hi = (color_for(v, 0, 100) for v in (0, 50, 100))
    assert lo[0] > mid[0] > hi[0] and lo[1] > mid[1] > hi[1]  # 小さい値ほど薄い
    for v in range(0, 101, 5):
        r, g, b, a = color_for(v, 0, 100)
        assert b >= r and b >= g  # 青系。赤が最大の色は出ない
    assert color_for(1000, 0, 100) == hi and color_for(-5, 0, 100) == lo  # 範囲外は端の色


# ---------------------------------------------------------------- G25: 日数
@pytest.mark.parametrize("minutes,days", [
    (1, 1), (240, 1), (240.1, 1), (539, 1), (540, 1), (541, 2), (1080, 2), (1081, 3),
])
def test_spec14_6_drive_days_boundaries(minutes, days):
    assert drive_days(minutes) == days


def test_spec14_7_drive_days_none_and_breaks():
    assert drive_days(None) is None and break_minutes(None) is None
    assert break_minutes(240) == 0  # 4時間ちょうどでは休憩が要らない
    assert break_minutes(241) == 30
    assert break_minutes(480) == 30 and break_minutes(481) == 60
    assert break_minutes(540) == 60  # 9時間ちょうど（4h + 4h + 1h）


# ---------------------------------------------------------------- G26: 貸切の目安額
def test_spec14_8_kashikiri_formula_and_boundaries():
    t = load_tariff("kashikiri")
    assert kashikiri_for_distance(199.9, 100, t) is None  # 200km 未満は範囲外
    assert kashikiri_for_distance(None, 100, t) is None
    assert kashikiri_for_distance(200, 100, t) == ("2t", 52300)
    assert kashikiri_for_distance(200.1, 100, t) == ("2t", 52300 + 3830)  # 20km までごとに加算
    assert kashikiri_for_distance(220, 100, t) == ("2t", 52300 + 3830)
    assert kashikiri_for_distance(220.1, 100, t) == ("2t", 52300 + 2 * 3830)
    assert kashikiri_for_distance(491.8, 100, t) == ("2t", 52300 + 15 * 3830)


def test_spec14_9_kashikiri_vehicle_is_smallest_that_fits():
    t = load_tariff("kashikiri")
    assert kashikiri_for_distance(300, None, t) == ("2t", 52300 + 5 * 3830)  # 荷物が未入力なら 2t
    assert kashikiri_for_distance(300, 2000, t)[0] == "2t"
    assert kashikiri_for_distance(300, 2001, t) == ("4t", 60360 + 5 * 4380)
    assert kashikiri_for_distance(300, 4001, t) == ("10t", 80110 + 5 * 5850)
    assert kashikiri_for_distance(300, 10001, t) is None


@pytest.mark.parametrize("km,kg", [(200, 10), (333.3, 2500), (491.8, 4500), (1086.3, 9000)])
def test_spec14_10_kashikiri_matches_screen2_formula(km, kg):
    """版2の目安額（estimate_kashikiri）と同じ式であること。式を二重に書いていない。"""
    s = make_shipment(one(1, 100, 100, 100, kg), route="other", distance_km=km, origin_in_kanto=True)
    rows = [r for r in estimate_all(s) if r.mode.startswith("kashikiri") and r.amount is not None]
    name, amount = kashikiri_for_distance(km, kg, load_tariff("kashikiri"))
    assert rows[0].detail["vehicle"] == name and rows[0].amount == amount


def test_spec14_11_route_for_mapping():
    assert route_for("13104", "27128") == "kanto_kansai"  # 大阪府
    assert route_for("13104", "27100") == "kanto_kansai"
    assert route_for("14104", "04101") == "kanto_tohoku"  # 宮城県
    assert route_for("13104", "01101") == "other"
    assert route_for("13104", "13101") == "other"
    assert route_for("01101", "13104") is None  # 出発地が関東の7か所でない
    assert route_for("13104", None) is None and route_for("13104", "") is None


# ---------------------------------------------------------------- G28: OSRM の取得（通信を差し替える）
class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, sec: float):
        self.sleeps.append(sec)
        self.now += sec


class FakeResponse:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class FakeNet:
    """urlopen の代わり。要求の時刻・URL・User-Agent を記録する。fail_first 回は通信エラーにする。"""

    def __init__(self, clock: FakeClock, fail_first: int = 0, always_fail: bool = False):
        self.clock = clock
        self.log: list[tuple[float, str, str]] = []
        self.fail_first = fail_first
        self.always_fail = always_fail

    def __call__(self, req, timeout=None):
        self.log.append((self.clock.now, req.full_url, req.get_header("User-agent")))
        if self.always_fail or len(self.log) <= self.fail_first:
            raise urllib.error.URLError("接続できません（試験）")
        url = req.full_url
        if "/table/" in url:
            n = len(url.split("/driving/")[1].split("?")[0].split(";"))
            dist = [0.0] + [1000.0 * (j + 1) for j in range(n - 1)]
            dur = [0.0] + [60.0 * (j + 1) for j in range(n - 1)]
            if n > 2:
                dist[2] = dur[2] = None  # 2番目の着地は経路なし
            return FakeResponse({"code": "Ok", "distances": [dist], "durations": [dur]})
        return FakeResponse({"code": "Ok", "routes": [{"distance": 123456.0, "duration": 6000.0,
                                                       "geometry": {"coordinates": [[139.7, 35.7], [135.5, 34.7]]}}]})


def make_geo_dir(tmp_path: Path) -> Path:
    geo = tmp_path / "geo"
    geo.mkdir()
    codes = ["13104", "14104", "27128", "04101", "01101"] + [f"90{i:03d}" for i in range(245)]
    feats = []
    for i, code in enumerate(codes):
        x, y = 130 + (i % 20) * 0.5, 31 + (i // 20) * 0.5
        feats.append({"type": "Feature", "properties": {"N03_001": "試験県", "N03_003": None, "N03_004": f"区{i}", "N03_007": code},
                      "geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + 0.4, y], [x + 0.4, y + 0.4], [x, y + 0.4], [x, y]]]}})
    feats.append({"type": "Feature", "properties": {"N03_001": "試験県", "N03_004": "所属未定地", "N03_007": None},
                  "geometry": {"type": "Polygon", "coordinates": [[[120, 30], [121, 30], [121, 31], [120, 30]]]}})
    (geo / "municipalities_N03-21_s0010.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    return geo


def run_tool(geo: Path, net: FakeNet, clock: FakeClock, **kw):
    tool = load_tool()
    client = tool.Client(sleep=clock.sleep, urlopen=net)
    summary = tool.run(geo, origins=["13104", "14104"], client=client, today=date(2026, 11, 3), log=lambda s: None, **kw)
    return tool, summary


def test_spec14_20_requests_are_spaced_by_at_least_1_1_seconds(tmp_path):
    clock = FakeClock()
    net = FakeNet(clock)
    _, summary = run_tool(make_geo_dir(tmp_path), net, clock)
    # 代表点 250 件 → 1回 99 点なので 1 出発地あたり 3 回。2 出発地 + デモの区間 3 回
    assert len(net.log) == 3 * 2 + 3
    times = [t for t, _, _ in net.log]
    assert all(b - a >= 1.1 - 1e-9 for a, b in zip(times, times[1:]))
    assert clock.sleeps == [1.1] * len(net.log)  # 要求のたびに 1.1 秒。再試行の待ちは無い
    assert all(ua == "shipcompare-hackathon/0.3 (non-commercial demo)" for _, _, ua in net.log)
    assert all(len(u.split("/driving/")[1].split("?")[0].split(";")) <= 100 for _, u, _ in net.log if "/table/" in u)
    assert summary["nulls"] == {"13104": 3, "14104": 3}  # 1要求に1件ずつ経路なし
    out = json.loads((tmp_path / "geo" / "osrm_13104.json").read_text(encoding="utf-8"))
    codes = sorted(out["destinations"])
    assert out["destinations"][codes[0]] == {"km": 1.0, "minutes": 1.0}  # 1件目の着地（メートル→km、秒→分）
    assert out["destinations"][codes[99]] == {"km": 1.0, "minutes": 1.0}  # 2つ目の塊の1件目も対応が合う
    nulls = [c for c, v in out["destinations"].items() if v["km"] is None]
    assert len(nulls) == 3 and all(out["destinations"][c]["minutes"] is None for c in nulls)  # 0 で埋めない
    assert out["license"].startswith("ODbL") and out["fetched_on"] == "2026-11-03"
    assert b"\r" not in (tmp_path / "geo" / "osrm_13104.json").read_bytes()


def test_spec14_21_existing_files_are_not_fetched_again_unless_forced(tmp_path):
    geo = make_geo_dir(tmp_path)
    c1 = FakeClock()
    run_tool(geo, FakeNet(c1), c1)
    before = (geo / "osrm_13104.json").read_bytes()

    c2 = FakeClock()
    net2 = FakeNet(c2)
    _, summary = run_tool(geo, net2, c2)
    assert net2.log == [] and c2.sleeps == []  # 通信も待ちも無い
    assert set(summary["skipped"]) == {"13104", "14104", "routes"} and summary["fetched"] == []
    assert (geo / "osrm_13104.json").read_bytes() == before

    (geo / "osrm_14104.json").unlink()  # 無いものだけ取る
    c3 = FakeClock()
    net3 = FakeNet(c3)
    _, summary = run_tool(geo, net3, c3)
    assert summary["fetched"] == ["14104"] and len(net3.log) == 3

    c4 = FakeClock()
    net4 = FakeNet(c4)
    _, summary = run_tool(geo, net4, c4, force=True)
    assert len(net4.log) == 9 and set(summary["fetched"]) == {"13104", "14104", "routes"}


def test_spec14_22_retries_three_times_with_5_second_wait_then_stops(tmp_path):
    geo = make_geo_dir(tmp_path)
    clock = FakeClock()
    net = FakeNet(clock, fail_first=2)  # 2回失敗して3回目で成功
    run_tool(geo, net, clock)
    assert clock.sleeps[:5] == [1.1, 5.0, 1.1, 5.0, 1.1]
    assert all(b - a >= 1.1 - 1e-9 for (a, _, _), (b, _, _) in zip(net.log, net.log[1:]))

    tool = load_tool()
    clock2 = FakeClock()
    net2 = FakeNet(clock2, always_fail=True)
    (tmp_path / "x").mkdir()
    geo2 = make_geo_dir(tmp_path / "x")
    with pytest.raises(tool.FetchError):
        tool.run(geo2, origins=["13104"], client=tool.Client(sleep=clock2.sleep, urlopen=net2),
                 today=date(2026, 11, 3), log=lambda s: None)
    assert len(net2.log) == 3  # 3回で諦める
    assert not (geo2 / "osrm_13104.json").exists()  # 失敗した出発地のファイルを中途半端に残さない


# ---------------------------------------------------------------- 色の階級（7分位）
def test_spec14_23_seven_quantile_classes_are_balanced_and_distinct():
    values = [float(v) for v in range(1, 701)]
    edges = quantile_edges(values)
    assert len(edges) == 8 and edges[0] == 1 and edges[-1] == 700
    counts = [0] * 7
    for v in values:
        counts[class_of(v, edges)] += 1
    assert max(counts) - min(counts) <= 1  # 各階級の件数がほぼ同じ
    colors = [tuple(class_color(i, 7)) for i in range(7)]
    assert len(set(colors)) == 7 and all(c[3] >= 170 for c in colors)  # 7色すべて違い、タイルの上でも見える濃さ
    assert all(c[2] >= c[0] and c[2] >= c[1] for c in colors)  # 赤を使わない


def test_spec14_24_quantile_edges_with_few_distinct_values():
    assert quantile_edges([]) == []
    edges = quantile_edges([1, 1, 1, 2, 2, 3])
    assert edges[0] == 1 and edges[-1] == 3 and len(edges) - 1 <= 3  # 同じ値の境界はまとめる
    assert quantile_edges([5, 5]) == [5, 5] and class_of(5, [5, 5]) == 0


