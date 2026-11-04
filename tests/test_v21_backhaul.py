"""帰り便で 1 件をつなぐ（SPEC §21、受入条件 G60〜G64）。

場面 S6（木工所）と便 T7（播磨ライン運輸）で、照合の追加条件、仮予約の締切と床の残り、書面の下書きを確かめる。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from shipcompare import geo, spaceb
from shipcompare.spaceb import Board, BoardError, BShipment, Piece

from test_v3_spaceb import Clock, trip, ship  # noqa: F401
from test_v3_web import get, post, srv  # noqa: F401  (srv は fixture)

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def s6():
    return json.loads((ROOT / "data" / "scenarios" / "S6.json").read_text(encoding="utf-8"))


def s6_body(**kw):
    sc = s6()
    bh = sc["backhaul"]
    body = {"shipment": {"items": sc["shipment"]["items"]}, "dest_code": bh["dest_code"], "is_box": bh["is_box"],
            "origin_code": bh["origin_code"], "window_from": bh["window_from"], "window_to": bh["window_to"]}
    body.update(kw)
    return body


def s6_ship(**kw):
    return spaceb.parse_shipment(s6_body(**kw))


def t7():
    return next(t for t in spaceb.load_trips() if t["id"] == "T7")


def t7_board(clock=None):
    return Board([t7()], clock=clock or Clock())


# ---------------------------------------------------------------- G60

def test_g60_s6_only_t7_matches_and_others_have_reasons():
    sh = s6_ship()
    res = {t["id"]: spaceb.match(sh, t) for t in spaceb.load_trips()}
    assert res["T7"] == (True, [])
    for tid in ("T1", "T2", "T3", "T4", "T5", "T6"):
        ok, reasons = res[tid]
        assert not ok and reasons and all(re.search(r"[ぁ-んァ-ン一-龥]", r) for r in reasons), tid
    assert [k for k, v in res.items() if v[0]] == ["T7"]


def test_g60_s6_data_matches_the_spec():
    sc = s6()
    assert sc["id"] == "S6" and "架空" in sc["name"] and sc["assumed_shipper"] is True
    items = sc["shipment"]["items"]
    assert [(i["count"], i["length_cm"], i["width_cm"], i["height_cm"], i["weight_kg"]) for i in items] == [
        (2, 240, 60, 200, 140), (2, 100, 60, 80, 60)]
    bh = sc["backhaul"]
    assert (bh["origin_code"], bh["dest_code"], bh["window_from"], bh["window_to"]) == ("13108", "27128", "2026-11-13", "2026-11-16")
    assert len(bh["shipper"]["corporate_number"]) == 13 and bh["shipper"]["corporate_number"].isdigit()
    assert sc["shipment"]["route"] == "kanto_kansai"
    sh = s6_ship()
    assert sh.total_floor_m2 == Decimal("4.08")


def test_g60_carrier_c4_and_trip_t7_fields():
    data = json.loads((ROOT / "data" / "demo" / "carriers.json").read_text(encoding="utf-8"))
    c4 = next(c for c in data["carriers"] if c["id"] == "C4")
    assert "架空" in c4["name"] and "架空" in c4["license_no"]
    for k in ("terms", "insurance", "payment_options", "cancel_terms"):
        assert c4[k]
    t = t7()
    assert t["carrier_id"] == "C4" and t["origin_code"] == "13108" and t["dest_code"] == "28210"
    assert (t["depart_at"], t["arrive_at"]) == ("2026-11-13T17:00", "2026-11-14T12:30")
    assert t["floor_free_m2"] == 6.0 and t["pickup_radius_km"] == 10 and t["accepts_unboxed"] is True
    assert t["drop_points"] == ["27128", "27127", "27120", "28210"]


# ---------------------------------------------------------------- G61

@pytest.mark.parametrize("wf,wt,ok", [
    ("2026-11-14", "2026-11-16", True),   # 到着日ちょうどが幅の先頭
    ("2026-11-10", "2026-11-14", True),   # 到着日ちょうどが幅の末尾
    ("2026-11-15", "2026-11-16", False),  # 到着の翌日から
    ("2026-11-10", "2026-11-13", False),  # 到着の前日まで
])
def test_g61_window_by_arrive_date(wf, wt, ok):
    r_ok, reasons = spaceb.match(s6_ship(window_from=wf, window_to=wt), t7())
    assert r_ok is ok
    if not ok:
        assert any("11/14 着" in r and "届けばよい日の幅" in r for r in reasons)


def test_g61_pickup_distance_boundary():
    pts = geo.load_points()
    far = "13111"  # 大田区
    a, b = pts["13108"], pts[far]
    km = geo.haversine_km(a["lon"], a["lat"], b["lon"], b["lat"])
    sh = s6_ship(origin_code=far)
    on = dict(t7(), pickup_radius_km=km)
    over = dict(t7(), pickup_radius_km=km - 0.001)
    assert spaceb.match(sh, on)[0] is True
    ok, reasons = spaceb.match(sh, over)
    assert not ok and any("集荷の地点が便の空く地点から" in r and "km で、この便の上限" in r for r in reasons)
    # 十分に遠い地点は T7 の 10km を超える
    ok, reasons = spaceb.match(s6_ship(origin_code="14104"), t7())
    assert not ok and any("上限 10km" in r for r in reasons)


def test_g61_floor_total_boundary():
    sh = s6_ship()  # 4.08m²
    assert spaceb.match(sh, dict(t7(), floor_free_m2=4.08))[0] is True
    ok, reasons = spaceb.match(sh, dict(t7(), floor_free_m2=4.07))
    assert not ok and any("床の合計 4.08m² がこの便の床の空き 4.07m² を超えます" in r for r in reasons)
    ok, reasons = spaceb.match(sh, dict(t7(), floor_free_m2=3.0))
    assert any("床の合計 4.08m² がこの便の床の空き 3.00m² を超えます" in r for r in reasons)
    # 盤の残りを渡すとそちらで比べる
    assert spaceb.match(sh, t7(), floor_m2=Decimal("4.07"))[0] is False


def test_g61_unboxed_only_when_trip_accepts():
    sh = s6_ship()
    assert spaceb.match(sh, t7())[0] is True
    ok, reasons = spaceb.match(sh, dict(t7(), accepts_unboxed=False))
    assert not ok and any("直方体の箱" in r for r in reasons)
    # 項目が無い便（accepts_unboxed なし）は従来どおり箱だけ
    legacy = {k: v for k, v in t7().items() if k != "accepts_unboxed"}
    assert any("直方体の箱" in r for r in spaceb.match(sh, legacy)[1])


def test_g61_shipment_without_origin_or_window_is_unchanged():
    """荷物に出発地・日の幅が無ければ、集荷の距離と日の幅は調べない（従来の結果と同じ）。床・箱でない荷は便に項目が無ければ調べない。"""
    plain = BShipment((Piece(1, Decimal(300), Decimal(300), Decimal(300), Decimal(10)),), "28110")
    t = trip()  # arrive_at・pickup_code・floor_free_m2 を持たない
    assert spaceb.match(plain, t) == (True, [])
    for tr in spaceb.load_trips():
        assert not any("集荷の地点" in r or "届けばよい日" in r or "床の合計" in r for r in spaceb.match(plain, tr)[1]), tr["id"]


def test_g61_pickup_and_window_apply_to_all_trips_with_defaults():
    """SPEC §21.2 の変更: 集荷の距離は便の pickup_code（無ければ origin_code）と上限（無ければ 20km）で全便に効く。日の幅も全便。"""
    s1_tokyo = BShipment((Piece(3, Decimal(1200), Decimal(500), Decimal(500), Decimal(40)),), "28110", origin_code="13104")
    ok, reasons = spaceb.match(s1_tokyo, next(t for t in spaceb.load_trips() if t["id"] == "T6"))  # 愛媛→兵庫の便
    assert not ok and any("集荷の地点が便の空く地点から" in r and "上限 20km" in r for r in reasons)
    # 出発地が便の出発地から 20km 以内なら通る（T1 は大田区発、新宿区から約 15km）
    t1 = next(t for t in spaceb.load_trips() if t["id"] == "T1")
    assert not any("集荷の地点" in r for r in spaceb.match(s1_tokyo, t1)[1])
    # 日の幅は pickup 項目の無い便にも効く（T6 は 11/22 着）
    win = BShipment(s1_tokyo.pieces, "28110", window_from=datetime(2026, 11, 13).date(), window_to=datetime(2026, 11, 16).date())
    assert any("11/22 着" in r and "届けばよい日の幅" in r for r in spaceb.match(win, next(t for t in spaceb.load_trips() if t["id"] == "T6"))[1])


def test_g61_parse_shipment_rejects_bad_window():
    for bad in (dict(window_from="2026-11-16", window_to="2026-11-13"), dict(window_from="2026-11-13", window_to=None),
                dict(window_from="来週", window_to="2026-11-16")):
        with pytest.raises(ValueError):
            s6_ship(**bad)


# ---------------------------------------------------------------- G62

def test_g62_hold_until_limit_is_72_hours():
    assert spaceb.MAX_HOLD_MINUTES == 72 * 60
    clock = Clock()
    b = t7_board(clock)
    sh = s6_ship()
    now = clock.now
    with pytest.raises(BoardError, match="72 時間以内"):
        b.hold("T7", sh, hold_until=(now + timedelta(hours=72, seconds=1)).isoformat())
    for bad in ((now - timedelta(minutes=1)).isoformat(), "あした", 123):
        with pytest.raises(BoardError):
            b.hold("T7", sh, hold_until=bad)
    h = b.hold("T7", sh, hold_until=(now + timedelta(hours=72)).isoformat())
    assert h["expires_at"] == (now + timedelta(hours=72)).isoformat(timespec="seconds")
    with pytest.raises(BoardError):
        t7_board(clock).hold("T7", sh, hold_minutes=72 * 60 + 1)


def test_g62_floor_remaining_drops_and_returns():
    clock = Clock()
    b = t7_board(clock)
    sh = s6_ship()
    assert b.trips()[0]["remaining_floor_m2"] == 6.0
    h1 = b.hold("T7", sh, hold_minutes=60)
    assert b.trips()[0]["remaining_floor_m2"] == pytest.approx(1.92) and h1["floor_m2"] == pytest.approx(4.08)
    # 残り 1.92m² には 2 件目が載らない
    with pytest.raises(BoardError, match="床の合計"):
        b.hold("T7", sh, hold_minutes=60)
    # 辞退で戻る
    b.decide(h1["hold_id"], "decline")
    assert b.trips()[0]["remaining_floor_m2"] == 6.0
    # 期限切れで戻る
    b.hold("T7", sh, hold_minutes=30)
    assert b.trips()[0]["remaining_floor_m2"] == pytest.approx(1.92)
    clock.advance(31)
    assert b.trips()[0]["remaining_floor_m2"] == 6.0
    # 承諾したら戻らない
    h3 = b.hold("T7", sh, hold_minutes=30)
    b.decide(h3["hold_id"], "accept")
    clock.advance(600)
    assert b.trips()[0]["remaining_floor_m2"] == pytest.approx(1.92)


def test_g62_api_hold_until_over_72h_is_400(srv):  # noqa: F811
    mod, port = srv
    post(port, "/api/b/reset", {})
    over = (datetime.now() + timedelta(hours=73)).isoformat(timespec="seconds")
    status, r = post(port, "/api/b/hold", dict(s6_body(), trip_id="T7", hold_until=over))
    assert status == 400 and "72 時間" in r["error"]
    ok_until = (datetime.now() + timedelta(hours=48)).isoformat(timespec="seconds")
    status, r = post(port, "/api/b/hold", dict(s6_body(), trip_id="T7", hold_until=ok_until))
    assert status == 200 and r["hold"]["expires_at"] == ok_until
    post(port, "/api/b/reset", {})


# ---------------------------------------------------------------- G63

SHIPPER = {"name": "東雲木工（架空）", "address": "東京都江東区（架空の番地）", "corporate_number": "1234567890123"}


def accepted_board():
    b = t7_board()
    h = b.hold("T7", s6_ship(), hold_minutes=60, shipper=SHIPPER)["hold_id"]
    b.decide(h, "accept")
    return b, h


def test_g63_contract_before_accept_is_error():
    b = t7_board()
    h = b.hold("T7", s6_ship(), hold_minutes=60, shipper=SHIPPER)["hold_id"]
    with pytest.raises(BoardError, match="承諾した仮予約だけ"):
        b.contract(h)
    with pytest.raises(BoardError):
        b.contract("H999")
    b.decide(h, "decline")
    with pytest.raises(BoardError):
        b.contract(h)


def test_g63_contract_has_six_legal_items_and_ready_flag():
    b, h = accepted_board()
    d = b.contract(h)
    legal = [i["key"] for i in d["items"] if i["legal"]]
    assert tuple(legal) == spaceb.LEGAL_KEYS and len(legal) == 6
    labels = [i["label"] for i in d["items"] if i["legal"]]
    assert labels == ["運送の内容と対価", "附帯業務の対価", "燃料サーチャージなどの料金", "当事者の名称と住所", "支払方法", "交付日"]
    # 運送会社の役が選ぶ前は未記入が 2 つで、確定できない
    assert d["ready"] is False
    assert d["missing"] == ["燃料サーチャージなどの料金", "支払方法"]
    by = {i["key"]: i["value"] for i in d["items"]}
    assert "45,000円（税抜）" in by["transport"] and "江東区" in by["transport"] and "陳列棚" not in by["transport"]
    assert "架空 播磨ライン運輸" in by["parties"] and "東雲木工（架空）" in by["parties"]
    assert "2 人" in by["handling"] and by["cancel"] and by["terms"] and by["insurance"] and "架空" in by["license"]
    assert by["corp_no"] == "1234567890123"
    # 選ぶと埋まる（「なし」も可）
    c4 = next(c for c in spaceb.load_demo()["carriers"] if c["id"] == "C4")
    d2 = b.contract(h, fuel_surcharge="なし", payment_method=c4["payment_options"][0])
    assert d2["ready"] is True and d2["missing"] == []
    d3 = b.contract(h, fuel_surcharge="なし")
    assert d3["ready"] is False and d3["missing"] == ["支払方法"]
    with pytest.raises(BoardError, match="選択肢"):
        b.contract(h, fuel_surcharge="なし", payment_method="現金で手渡し")


def test_g63_missing_shipper_or_carrier_fields_are_listed():
    b = t7_board()
    h = b.hold("T7", s6_ship(), hold_minutes=60)["hold_id"]  # 荷主の情報なし
    b.decide(h, "accept")
    d = b.contract(h, fuel_surcharge="なし", payment_method="前払い")
    assert d["ready"] is False
    assert "当事者の名称と住所" in d["missing"] and "荷主の法人番号（架空の番号）" in d["missing"]


def test_g63_api_contract_flow(srv):  # noqa: F811
    mod, port = srv
    post(port, "/api/b/reset", {})
    status, r = post(port, "/api/b/hold", dict(s6_body(), trip_id="T7", hold_minutes=60, shipper=SHIPPER))
    assert status == 200, r
    hid = r["hold"]["hold_id"]
    status, r = post(port, "/api/b/contract", {"hold_id": hid})
    assert status == 400 and "承諾した" in r["error"]
    assert post(port, "/api/b/decide", {"hold_id": hid, "decision": "accept"})[0] == 200
    status, r = post(port, "/api/b/contract", {"hold_id": hid})
    assert status == 200 and r["contract"]["ready"] is False and len(r["contract"]["missing"]) == 2
    assert r["contract"]["notice"] == spaceb.CONTRACT_NOTICE
    status, r = post(port, "/api/b/contract", {"hold_id": hid, "fuel_surcharge": "なし", "payment_method": "前払い"})
    assert status == 200 and r["contract"]["ready"] is True
    status, r = post(port, "/api/b/hold", dict(s6_body(), trip_id="T7", shipper="東雲"))
    assert status == 400
    post(port, "/api/b/reset", {})


def test_g63_api_scenarios_carry_backhaul(srv):  # noqa: F811
    mod, port = srv
    status, r = get(port, "/api/scenarios")
    sc = {s["id"]: s for s in r["scenarios"]}
    assert sc["S6"]["backhaul"]["window_to"] == "2026-11-16" and sc["S1"]["backhaul"] is None


# ---------------------------------------------------------------- G64

def test_g64_fiction_and_not_a_party_notices_are_always_in_the_page():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "find.js").read_text(encoding="utf-8")
    block = html[html.index('id="f-contract"'):html.index('<div id="hover-tag">')]
    assert spaceb.CONTRACT_NOTICE in block  # 書面の画面に静的に置いてある（JS が失敗しても出る）
    assert "架空" in block
    assert "Backfull は運送の当事者ではなく、運賃を受け取りません" in spaceb.CONTRACT_NOTICE
    # 書面の画面は find-view の見方の中にだけあり、ほかの見方では隠れる
    assert 'class="glass v-find"' in html[html.index('id="f-contract"') - 10:html.index('id="f-contract"') + 80]
    # JS は「確定」を、必要な項目がそろうまで押せなくする
    assert "b.disabled = !d.ready" in js
    assert "window.print()" in js


def test_g64_print_css_prints_only_the_contract():
    css = (WEB / "style.css").read_text(encoding="utf-8")
    i = css.index("@media print")
    block = css[i:]
    assert "#f-contract" in block and "visibility: hidden" in block and ".no-print" in block


def test_g64_deadline_and_declined_card_links_to_request_text():
    js = (WEB / "find.js").read_text(encoding="utf-8")
    assert "締切を過ぎました" in js and "送り方を比べるの見積依頼文で、ほかの手段に頼めます" in js
    assert "運送会社が辞退しました" in js and "Work.openWork(1)" in js


# ---------------------------------------------------------------- 送り方を比べる: 関東の7か所に無い出発地（S6 の江東区）

def test_s6_origin_outside_the_seven_uses_nearest_origin_table(srv):  # noqa: F811
    from shipcompare.storage import load_scenario, shipment_to_dict
    mod, port = srv
    ship_d = shipment_to_dict(load_scenario("S6")["shipment"])
    status, r = post(port, "/api/estimate", {"shipment": ship_d, "origin_code": "13108", "dest_code": "27128"})
    assert status == 200, r
    assert r["geo"]["origin_note"] == "距離は最寄りの出発地（新宿区）の値で代用" and r["geo"]["origin_used"] == "13104"
    _, base = post(port, "/api/estimate", {"shipment": ship_d, "origin_code": "13104", "dest_code": "27128"})
    assert base["geo"]["origin_note"] is None and base["geo"]["km"] == r["geo"]["km"]
    status, lay = post(port, "/api/layer", {"origin_code": "13108", "shipment": ship_d})
    assert status == 200 and "新宿区" in lay["origin_note"]
    # 市区町村コードそのものが無ければ従来どおり 400
    assert post(port, "/api/estimate", {"shipment": ship_d, "origin_code": "99999", "dest_code": "27128"})[0] == 400
