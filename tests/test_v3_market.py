"""版3の3段目: 市場を見る（SPEC §16.1、受入条件 G34・G35）と、最寄りの貨物駅・港（SPEC §15.5、G33）。

数字は資料 08（docs/plan/2026-11-03_最新版のまとめ_v4.md §08）と突き合わせる。通信はしない。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from shipcompare import geo, market

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def data():
    return market.load_backhaul()


# ---------------------------------------------------------------- G34: 資料 08 の数字

def test_spec16_1_interpref_and_shortfall_totals(data):
    assert data["modes"]["truck"]["interpref_t"] == pytest.approx(5_910_459.4, abs=0.1)
    assert data["modes"]["truck"]["shortfall_total_t"] == pytest.approx(1_648_207.7, abs=0.1)
    assert data["modes"]["truck"]["shortfall_far_t"] == pytest.approx(699_657.0, abs=0.1)
    assert round(market.shortfall_share(data) * 100) == 28


def test_spec16_1_top5_far_pairs_order_and_hyogo_tokyo(data):
    top = market.top_pairs(data, "truck", min_km=200, n=5)
    assert [(p["from"], p["to"]) for p in top] == [
        ("兵庫県", "東京都"), ("東京都", "三重県"), ("神奈川県", "愛知県"), ("兵庫県", "愛媛県"), ("宮城県", "青森県"),
    ]
    p = top[0]
    assert p["go_t"] == pytest.approx(37_325.7, abs=0.1)
    assert p["back_t"] == pytest.approx(3_126.7, abs=0.1)
    assert p["shortfall_t"] == pytest.approx(34_199.0, abs=0.1)
    assert all(q["km"] >= 200 for q in top)


def test_spec16_1_top_pairs_respects_min_km_and_n(data):
    assert len(market.top_pairs(data, "truck", min_km=200, n=30)) == 30
    assert market.top_pairs(data, "truck", min_km=0, n=1)[0]["from"] == "神奈川県"  # 近い県どうしでは最大
    assert all(p["km"] >= 500 for p in market.top_pairs(data, "truck", min_km=500, n=50))
    shorts = [p["shortfall_t"] for p in market.top_pairs(data, "truck", min_km=200, n=30)]
    assert shorts == sorted(shorts, reverse=True)


def test_spec16_1_tokyo_balance(data):
    assert data["modes"]["truck"]["balance_out_minus_in_t"]["東京都"] == pytest.approx(-190_569.2, abs=0.1)


def test_spec16_1_balance_sums_to_zero(data):
    """県の出と入の差は、全国で足すと 0（県の中の流動は除く）。"""
    for m in market.MODES:
        assert sum(data["modes"][m]["balance_out_minus_in_t"].values()) == pytest.approx(0, abs=1.0)


# ---------------------------------------------------------------- G35: S と実車率

def test_spec16_1_scale_s_matches_document(data):
    s = market.scale_s(data["modes"]["truck"]["pairs"])
    assert s == pytest.approx(0.3447, abs=0.0005)
    assert s == pytest.approx(0.344, abs=0.002)  # 受入条件 G35


@pytest.mark.parametrize("x_pct, expected", [(1, 62.36), (5, 63.22), (10, 64.29)])
def test_spec16_1_actual_rate_after(data, x_pct, expected):
    s = market.scale_s(data["modes"]["truck"]["pairs"])
    assert market.actual_rate_after(x_pct / 100, s) == pytest.approx(expected, abs=0.02)


def test_spec16_1_actual_rate_zero_x_is_current(data):
    s = market.scale_s(data["modes"]["truck"]["pairs"])
    assert market.actual_rate_after(0, s) == market.ACTUAL_RATE_PCT


def test_spec16_1_rate_curve_is_monotonic_and_starts_at_current(data):
    s = market.scale_s(data["modes"]["truck"]["pairs"])
    curve = market.rate_curve(s)
    assert curve[0] == [0.0, market.ACTUAL_RATE_PCT]
    assert curve[-1][0] == 20.0
    assert all(b[1] > a[1] for a, b in zip(curve, curve[1:]))
    assert len(curve) == 41


# ---------------------------------------------------------------- web/data（画面が読む JSON）が Python の計算と一致する

def _web(name):
    with open(ROOT / "web" / "data" / name, encoding="utf-8") as f:
        return json.load(f)


def test_spec16_1_web_market_json_matches_python(data):
    w = _web("market.json")
    assert w["scale_s"] == pytest.approx(market.scale_s(data["modes"]["truck"]["pairs"]), abs=0.00006)
    assert w["summary"] == {"load_factor_pct": 39.08, "actual_rate_pct": 62.15, "shortfall_share_pct": 28}
    prefs = w["prefs"]
    first = w["modes"]["truck"]["pairs"][0]
    assert (prefs[first[0]], prefs[first[1]]) == ("神奈川県", "東京都")
    far = [p for p in w["modes"]["truck"]["pairs"] if p[6] >= 200][:5]
    assert [(prefs[p[0]], prefs[p[1]]) for p in far] == [(p["from"], p["to"]) for p in market.top_pairs(data, "truck", 200, 5)]
    assert w["modes"]["truck"]["balance"][prefs.index("東京都")] == pytest.approx(-190_569.2, abs=0.1)
    assert dict(map(tuple, w["curve"]))[5.0] == pytest.approx(63.221, abs=0.001)


def test_spec16_4_jartic_pair_label_maps_to_larger_direction(data):
    p = market.pair_from_label("兵庫–東京", data["modes"]["truck"]["pairs"])
    assert (p["from"], p["to"]) == ("兵庫県", "東京都")
    p = market.pair_from_label("東京–三重", data["modes"]["truck"]["pairs"])
    assert (p["from"], p["to"]) == ("東京都", "三重県")
    assert market.pair_from_label("兵庫東京", data["modes"]["truck"]["pairs"]) is None
    w = _web("jartic.json")
    assert {(x["from"], x["to"]) for x in w["points"]} == {("兵庫県", "東京都"), ("東京都", "三重県"), ("神奈川県", "愛知県")}
    assert all(len(x["up"]) == 168 == len(x["down"]) for x in w["points"])


def test_spec16_4_web_jartic_keeps_missing_as_null():
    """欠測を 0 台にしていない（G40）。web 側の折れ線は null で線を切る。"""
    src = json.load(open(ROOT / "data" / "traffic" / "heavy_updown.json", encoding="utf-8"))
    w = _web("jartic.json")
    for a, b in zip(src["points"], w["points"]):
        assert a["up"] == b["up"] and a["down"] == b["down"]
        gaps = sum(1 for u, d in zip(b["up"], b["down"]) if u is None or d is None)
        assert gaps == b["missing_hours"] and b["observed_hours"] == 168 - gaps
    assert any(x["missing_hours"] > 0 for x in w["points"])  # 欠測のある地点が実際に含まれている


# ---------------------------------------------------------------- 出典の表示（G33・G38）

def test_spec16_credits_are_always_in_the_page():
    js = (ROOT / "web" / "market.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "自動車輸送統計年報" in js and "自動車輸送統計年報" in html
    assert "ODbL" in js and "OpenStreetMap contributors" in js
    assert "e-Stat" in js
    assert 'id="m-credits"' in html
    jartic = _web("jartic.json")
    assert jartic["attribution"] == "国土交通省 API 機能による交通量(参考値)を加工して作成"
    assert "M.j.attribution" in js
    market_json = _web("market.json")
    assert "を加工して作成" in market_json["source"] and "e-Stat" in market_json["source"]
    # 上り・下りは道路の定義で、区間の行き帰りと一致しない旨が画面に出る
    # 区間の詳細の折れ線は外した（SPEC §32 #2）。注意の文は地図の層の凡例に残る
    assert "荷の行き帰りとは別です" in html   # 文言の直し 2026-11-07
    # 鉄道コンテナの注記
    assert "調べた件数が少ない" in html


def test_spec16_view_switcher_in_page():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    labels = re.findall(r'<button data-view="(\w+)"[^>]*>([^<]+)(?:<small[^>]*>[^<]*</small>)?</button>', html)
    assert labels == [("market", "全国の物流傾向"), ("find", "空きを探す・つなぐ"), ("compare", "送り方を比べる"), ("own", "自社の運行")]  # 4 つ目は SPEC §22
    assert '<body class="view-home">' in html  # 最初に開くのは役割を選ぶ画面（SPEC §44）。地図の見方は、選ばれてから開く
    assert "次の段階で作ります" not in html  # 空きを探す・つなぐは作り込み済み（tests/test_v3_spaceb_ui.py）
    assert 'id="find-right"' in html


# ---------------------------------------------------------------- SPEC §15.5: 最寄りの貨物駅・港

HUBS = [
    {"name": "A", "lon": 139.0, "lat": 35.0},
    {"name": "B", "lon": 139.0, "lat": 36.0},
    {"name": "C", "lon": 140.0, "lat": 35.0},
]


def test_spec15_5_haversine_known_values():
    assert geo.haversine_km(139.0, 35.0, 139.0, 35.0) == 0
    assert geo.haversine_km(139.0, 35.0, 139.0, 36.0) == pytest.approx(111.2, abs=0.2)  # 緯度 1 度
    assert geo.haversine_km(139.0, 0.0, 140.0, 0.0) == pytest.approx(111.2, abs=0.2)  # 赤道では経度 1 度も同じ


def test_spec15_5_nearest_hub_picks_closest():
    i, km = geo.nearest_hub(139.01, 35.2, HUBS)
    assert HUBS[i]["name"] == "A" and km == pytest.approx(geo.haversine_km(139.01, 35.2, 139.0, 35.0))
    i, km = geo.nearest_hub(139.0, 35.9, HUBS)
    assert HUBS[i]["name"] == "B" and km == pytest.approx(11.1, abs=0.1)


def test_spec15_5_nearest_hub_tie_and_empty():
    i, _ = geo.nearest_hub(139.0, 35.5, HUBS[:2])  # A と B のちょうど真ん中は、番号の小さい A
    assert i == 0
    assert geo.nearest_hub(139.0, 35.0, []) is None


def test_spec15_5_nearest_real_freight_station_for_origins():
    stations = geo.load_hubs("freight_stations")
    assert len(stations) == 37
    pts = geo.load_points()
    shinjuku = pts["13104"]
    i, km = geo.nearest_hub(shinjuku["lon"], shinjuku["lat"], stations)
    assert stations[i]["name"] == "田端信号場" and km == pytest.approx(6.3, abs=0.1)
    osaka = pts["27128"]
    i, km = geo.nearest_hub(osaka["lon"], osaka["lat"], stations)
    assert stations[i]["name"] == "百済貨物ターミナル" and km == pytest.approx(5.5, abs=0.1)


def test_spec15_5_major_terminals_exclude_sightseeing_piers():
    terminals = geo.load_hubs("ferry_terminals")
    routes = geo.load_hubs("ferry_routes")
    major = geo.major_terminals(terminals, routes)
    assert 0 < len(major) < len(terminals)
    names = [terminals[i]["name"] for i in major]
    assert not any(w in n for n in names for w in geo.PASSENGER_ONLY_WORDS)


def test_spec15_5_freight_ferry_ports_are_long_route_ends_only():
    """最寄りの港の候補は 100km 以上の航路の端の 3km 以内。竹芝のウォーターズ・ユニバーサルシティポートなどの旅客・遊覧の点を含まない。"""
    terminals = geo.load_hubs("ferry_terminals")
    routes = geo.load_hubs("ferry_routes")
    ports = geo.freight_ferry_ports(terminals, routes)
    names = {terminals[i]["name"] for i in ports}
    assert 0 < len(ports) < len(geo.major_terminals(terminals, routes))
    assert not names & {"ウォーターズ竹芝前", "ユニバーサルシティポート", "Hi-NODE", "明石町・聖路加ガーデン前", "東京"}
    assert {"東京港フェリーターミナル", "大阪港国際フェリーターミナル", "南港フェリーターミナル"} <= names
    ends = [e for r in routes if r["km"] >= 100 for e in (r["path"][0], r["path"][-1])]
    for i in ports:
        assert min(geo.haversine_km(terminals[i]["lon"], terminals[i]["lat"], e[0], e[1]) for e in ends) <= 3.0


def test_spec15_5_nearest_port_for_tokyo_and_osaka():
    """OSM のデータに実際にある港のうち、新宿区は東京港フェリーターミナル（有明の東、約 12km）、大阪市中央区は大阪南港の港。"""
    terminals = geo.load_hubs("ferry_terminals")
    ports = geo.freight_ferry_ports(terminals, geo.load_hubs("ferry_routes"))
    cand = [terminals[i] for i in ports]
    pts = geo.load_points()
    i, km = geo.nearest_hub(pts["13104"]["lon"], pts["13104"]["lat"], cand)
    assert cand[i]["name"] == "東京港フェリーターミナル" and km == pytest.approx(12.2, abs=0.2)
    i, km = geo.nearest_hub(pts["27128"]["lon"], pts["27128"]["lat"], cand)
    assert cand[i]["name"] in ("大阪港国際フェリーターミナル", "南港フェリーターミナル") and km < 12


def test_spec15_5_web_hubs_json_matches_python():
    w = _web("hubs.json")
    stations = geo.load_hubs("freight_stations")
    assert len(w["stations"]) == len(stations)
    pts = geo.load_points()
    for oc in geo.ORIGINS:
        i, km = geo.nearest_hub(pts[oc]["lon"], pts[oc]["lat"], stations)
        assert w["origin"][oc]["st"] == [i, round(km, 1)]
    codes = sorted(pts)
    k = codes.index("27128")
    i, km = geo.nearest_hub(pts["27128"]["lon"], pts["27128"]["lat"], stations)
    assert (w["dest"]["st"][k], w["dest"]["st_km"][k]) == (i, round(km, 1))
    # 最寄りの港は、航路の端の港（レベル 1 以上）の中から選んでいる
    assert w["terminals"][w["dest"]["pt"][k]][3] >= 1
    # 新宿区の最寄りの港は、旅客用の竹芝ではなく東京港フェリーターミナル
    assert w["terminals"][w["origin"]["13104"]["pt"][0]][0] == "東京港フェリーターミナル"
    assert w["terminals"][w["dest"]["pt"][k]][0] in ("大阪港国際フェリーターミナル", "南港フェリーターミナル")
    assert "OpenStreetMap contributors" in w["attribution"] and "ODbL" in w["attribution"]
