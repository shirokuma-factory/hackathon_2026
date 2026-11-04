"""空きを探す・つなぐの画面（SPEC §16.2、G38・G39 の前提）。

画面（web/find.js）が頼るデータとの対応と、画面が API に送る本文の形を確かめる。通信はしない（Board を直接呼ぶ）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from shipcompare import spaceb
from shipcompare.spaceb import Board

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _json(name: str):
    return json.loads((WEB / "data" / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def index():
    d = _json("index.json")
    return {c: (d["prefs"][i], d["names"][i]) for i, c in enumerate(d["codes"])}


def _codes(t):
    return {t["origin_code"], t["dest_code"], *t["drop_points"]}


def test_spec16_2_ui_all_trip_places_exist_on_the_map(index):
    for t in spaceb.load_trips():
        missing = _codes(t) - set(index)
        assert not missing, (t["id"], missing)


def test_spec16_2_ui_trip_ends_match_route_label(index):
    """便の経路の文言（東京都→兵庫県）と、発着の市区町村コードの県が一致する。地図の線は発着の座標から引く。"""
    for t in spaceb.load_trips():
        head = t["route_label"].split("（")[0]
        assert head == f"{index[t['origin_code']][0]}→{index[t['dest_code']][0]}", t["id"]


def test_spec16_2_ui_every_trip_is_the_reverse_of_a_backhaul_pair(index):
    """6 本とも、市場を見るの区間（荷が多い向き）の逆向き。『この区間の空き便を探す』で必ず強調される便がある。"""
    m = _json("market.json")
    prefs = m["prefs"]
    pairs = {(prefs[p[0]], prefs[p[1]]) for p in m["modes"]["truck"]["pairs"]}
    for t in spaceb.load_trips():
        o, d = index[t["origin_code"]][0], index[t["dest_code"]][0]
        assert (d, o) in pairs, (t["id"], d, o)


def test_spec16_2_ui_reverse_trips_of_hyogo_to_tokyo(index):
    """画面の isReverse と同じ規則（荷が多い向き from→to の、to→from を走る便）。兵庫→東京なら T1・T2・T7（T7 は SPEC §21 で足した）。"""
    rev = [t["id"] for t in spaceb.load_trips()
           if index[t["origin_code"]][0] == "東京都" and index[t["dest_code"]][0] == "兵庫県"]
    assert rev == ["T1", "T2", "T7"]


def test_spec16_2_ui_drop_points_for_search_are_union_of_trips(index):
    drops = {c for t in spaceb.load_trips() for c in t["drop_points"]}
    assert len(drops) == 17 and "28110" in drops
    assert all(index[c][1] for c in drops)  # 検索の候補に市区町村名が出せる


S1_BODY = {  # 画面が確定済みの荷物（場面 S1）から作る本文と同じ形
    "shipment": {"items": [{"count": 3, "length_cm": 120, "width_cm": 50, "height_cm": 50, "weight_kg": 40}]},
    "dest_code": "28110", "category": None, "in_container": False, "fragile": False, "is_box": True,
}


def test_spec16_2_ui_s1_cargo_mixes_pass_and_fail():
    """画面写真の場面: S1 で受け取りは神戸市中央区。載るのは T6 だけで、ほかは理由が出る（1個の重さ 40kg が上限を超える便が多い）。"""
    b = Board()
    res = {r["trip_id"]: r for r in b.match_all(spaceb.parse_shipment(S1_BODY))}
    assert [k for k, r in res.items() if r["ok"]] == ["T6"]
    assert any("1個の重さ 40kg" in x for x in res["T1"]["reasons"])
    assert any("降ろせる地点" in x for x in res["T4"]["reasons"])
    assert all(res[k]["reasons"] for k in ("T1", "T2", "T3", "T4", "T5"))


def test_spec16_2_ui_hold_then_accept_changes_remaining_slack():
    b = Board()
    s = spaceb.parse_shipment(S1_BODY)
    h = b.hold("T6", s, 30)
    t6 = next(t for t in b.trips() if t["id"] == "T6")
    assert t6["remaining_slack_kg"] == 680
    assert b.decide(h["hold_id"], "accept")["state"] == "accepted"
    assert next(t for t in b.trips() if t["id"] == "T6")["remaining_slack_kg"] == 680
    b.reset()
    assert next(t for t in b.trips() if t["id"] == "T6")["remaining_slack_kg"] == 800


def test_spec16_2_ui_notices_always_in_page():
    """G38: 『架空』と運営の注意が、見方を開くたびにではなく、ページに常に置いてある（画面の初期の文言が API の文言と同じ）。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert spaceb.FICTION_NOTICE in html
    assert spaceb.BOOKING_NOTICE in html
    assert "決済、メール、通知の送信はしません" in html


def test_spec16_2_ui_find_js_uses_only_the_b_endpoints():
    js = (WEB / "find.js").read_text(encoding="utf-8")
    used = set(re.findall(r"'(/api/[^']+)'", js))
    # /api/scenarios は場面の追加項目（SPEC §21 の backhaul）を読むだけ。書き込みは b の API だけ
    assert used == {"/api/b/trips", "/api/b/match", "/api/b/hold", "/api/b/decide", "/api/b/reset", "/api/b/contract", "/api/scenarios"}
    assert "is_demo" not in js and "fetch(" not in js  # 通信は Work.api だけを通す（サーバーに書くのは b の API だけ）
