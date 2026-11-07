"""受入条件 G05 / G08: 境界値と、超過・要確認の区別。"""

from __future__ import annotations

import pytest

from shipcompare.estimate import (
    ISSUE_LOADING, ISSUE_MULTI_DEST, ISSUE_OVER_1000KG, ISSUE_OVERSIZE, ISSUE_TAKUHAI_OVER,
    ST_CHECK, ST_NOCALC, ST_OK, ST_UNUSABLE, summarize,
)
from shipcompare.models import Item
from shipcompare.estimate import estimate_all as _estimate_all
from shipcompare.tariffs import load_sample_tariffs

SAMPLE = load_sample_tariffs()  # 金額は架空の見本（data/tariffs/sample_*.json）から決まる値。実在の表の有無に左右されない


def estimate_all(shipment, manual=None):
    return _estimate_all(shipment, manual, tariffs=SAMPLE)

from conftest import make_shipment, one, row_of


def rows(items, **kw):
    return estimate_all(make_shipment(items, **kw))


# ---- 宅配: 50kg / 260cm の境界
def test_takuhai_50kg_ok_and_50_1kg_unusable():
    r = row_of(rows([Item(1, 50, 50, 50, 50)]), "takuhai")
    assert r.status == ST_OK  # 150cm・50kg ちょうど → 170 サイズ
    r = row_of(rows([Item(1, 50, 50, 50, 50.1)]), "takuhai")
    assert r.status == ST_UNUSABLE
    assert ISSUE_TAKUHAI_OVER in r.codes()
    # 会社名を出さない文（SPEC §30.1）: 「1個の重さが50.1kgで、大手の宅配の上限（…50kg まで）を超える」
    assert any("1個の重さが50.1kg" in x and "50kg まで" in x for x in r.reasons)


def test_takuhai_260cm_ok_and_261cm_unusable():
    r = row_of(rows([Item(1, 100, 100, 60, 20)]), "takuhai")
    assert r.status == ST_OK and r.detail["sizes"][0]["size"] == 260
    r = row_of(rows([Item(1, 100, 100, 61, 20)]), "takuhai")
    assert r.status == ST_UNUSABLE
    assert any("3辺の合計が261cm" in x and "260cm" in x for x in r.reasons)  # 会社名なしの文（SPEC §30.1）


def test_takuhai_one_bad_piece_makes_row_unusable():
    r = row_of(rows([Item(1, 30, 30, 30, 5), Item(1, 100, 100, 100, 5)]), "takuhai")
    assert r.status == ST_UNUSABLE
    assert "荷物2行目" in r.reasons[0]


def test_takuhai_dropoff_discount_100_incl_tax():
    # 60 サイズ 1,000円（架空の表）。持込で 900円 → 税抜 818
    r = row_of(rows([Item(1, 20, 20, 20, 1)], pickup="持込"), "takuhai")
    assert r.amount == 818
    r = row_of(rows([Item(1, 20, 20, 20, 1)], pickup="集荷"), "takuhai")
    assert r.amount == 909


def test_takuhai_other_route_needs_input():
    r = row_of(rows(one(), route="kanto_tohoku"), "takuhai")
    assert r.status == ST_NOCALC and r.needs_input == "takuhai_ex_tax" and r.amount is None
    r = row_of(estimate_all(make_shipment(one(), route="kanto_tohoku"), manual={"takuhai_ex_tax": 1500}), "takuhai")
    assert r.status == ST_OK and r.amount == 1500


# ---- 路線便: 計算重量が段の境目
def rosen(kg, count=1, **kw):
    return row_of(rows([Item(count, 10, 10, 10, kg)], **kw), "rosen")


def test_rosen_band_boundary():
    r = rosen(250)
    assert r.detail["weight_band_kg"] == 250 and r.amount == 9000
    r = rosen(250.01)
    assert r.detail["weight_band_kg"] == 300 and r.amount == 10500


def test_rosen_volume_decides_chargeable():
    # 100×100×100cm = 1m³ → 280kg、重さ 10kg → 300kg の段
    r = row_of(rows([Item(1, 100, 100, 100, 10)]), "rosen")
    assert str(r.detail["chargeable_kg"]) == "280.00" and r.detail["weight_band_kg"] == 300


def test_rosen_1000kg_boundary():
    r = rosen(500, count=2)  # 1個500kgは2割増の対象外
    assert r.amount == 31500
    r = rosen(500.005, count=2)
    assert r.status == ST_NOCALC and r.amount is None
    assert ISSUE_OVER_1000KG in r.codes()
    assert "見積で確認" in r.reasons[0]


def test_rosen_min_max_plus_minus_10_percent():
    r = rosen(250)
    assert r.low == 8100 and r.high == 9900


def test_rosen_oversize_surcharge_boundaries():
    assert ISSUE_OVERSIZE not in rosen(10).codes()
    # 長さ 450cm ちょうどは対象外、451cm は 2割増
    ok = row_of(rows([Item(1, 450, 10, 10, 10)]), "rosen")
    assert ISSUE_OVERSIZE not in ok.codes()
    ng = row_of(rows([Item(1, 451, 10, 10, 10)]), "rosen")
    assert ISSUE_OVERSIZE in ng.codes()
    # 重さ 500kg ちょうどは対象外、500.01kg は 2割増
    assert ISSUE_OVERSIZE not in rosen(500).codes()
    r = rosen(500.01)
    assert ISSUE_OVERSIZE in r.codes()
    assert r.amount == round(r.detail["base_fare"] * 1.2)
    # 容積 2m³ ちょうど(100×100×200)は対象外、超えると対象
    assert ISSUE_OVERSIZE not in row_of(rows([Item(1, 100, 100, 200, 10)]), "rosen").codes()
    assert ISSUE_OVERSIZE in row_of(rows([Item(1, 100, 100, 201, 10)]), "rosen").codes()


def test_rosen_not_modeled_note_present():
    assert any("ほかに加算" in n for n in rosen(10).notes)


def test_rosen_other_route_needs_input():
    r = row_of(rows(one(), route="other", distance_km=700), "rosen")
    assert r.status == ST_NOCALC and r.needs_input == "rosen_base_fare"
    r = row_of(estimate_all(make_shipment(one(), route="other", distance_km=700), manual={"rosen_base_fare": 5000}), "rosen")
    assert r.amount == 5000
    r = row_of(rows(one(), route="other", distance_km=None), "rosen")
    assert r.status == ST_NOCALC and "距離 km" in r.missing


# ---- 不明値・0・負数
@pytest.mark.parametrize("bad", [None, 0, -1])
@pytest.mark.parametrize("field", ["count", "length_cm", "width_cm", "height_cm", "weight_kg"])
def test_bad_values_never_become_zero_yen(bad, field):
    item = Item(1, 30, 30, 30, 10)
    setattr(item, field, bad)
    for r in rows([item]):
        assert r.status == ST_NOCALC
        assert r.amount is None
    assert not summarize(make_shipment([item])).ok


def test_empty_items_not_calculable():
    for r in rows([]):
        assert r.amount is None and r.status == ST_NOCALC


def test_unknown_missing_listed():
    t = summarize(make_shipment([Item(None, 30, 30, 30, 10)]))
    assert "荷物1行目の個数" in t.missing
    r = row_of(rows([Item(None, 30, 30, 30, 10)]), "rosen")
    assert "不足" in r.reasons[0]


# ---- 届け先・種別・積付け
def test_multi_destination_makes_non_takuhai_check():
    rs = rows(one(), destinations=2)
    assert row_of(rs, "takuhai").status == ST_OK
    for r in rs:
        if r.mode != "takuhai":
            assert r.status == ST_CHECK and ISSUE_MULTI_DEST in r.codes()


def test_destinations_unknown_is_check_not_ok():
    r = row_of(rows(one(), destinations=None), "rosen")
    assert r.status == ST_CHECK


def test_jitbox_party_types():
    assert row_of(rows(one()), "jitbox").status == ST_OK
    r = row_of(rows(one(), sender_type="個人事業主"), "jitbox")
    assert r.status == ST_CHECK and r.amount == 20000 and "要確認" in r.issues[-1].message
    r = row_of(rows(one(), receiver_type="個人"), "jitbox")
    assert r.status == ST_UNUSABLE and r.amount is None


def test_jitbox_box_count_boundaries():
    # 重さ 500kg ちょうどは1本、500.01kg は2本
    assert row_of(rows([Item(1, 10, 10, 10, 500)]), "jitbox").detail["boxes"] == 1
    assert row_of(rows([Item(2, 10, 10, 10, 250.01)]), "jitbox").detail["boxes"] == 2  # 合計 500.02kg（1 個が 500kg を超えると使えない: 洗い出し E1）
    assert row_of(rows([Item(1, 10, 10, 10, 500.01)]), "jitbox").status == "使えない"
    # 容積 1.84m³ ちょうど(100×100×184)は1本、超えると2本
    assert row_of(rows([Item(1, 100, 100, 184, 10)]), "jitbox").detail["boxes"] == 1
    assert row_of(rows([Item(1, 100, 100, 185, 10)]), "jitbox").detail["boxes"] == 2


def test_loading_check_is_distinct_from_over_limit():
    j = row_of(rows(one()), "jitbox")
    assert ISSUE_LOADING in j.codes()
    assert ISSUE_TAKUHAI_OVER not in j.codes() and ISSUE_OVERSIZE not in j.codes()
    assert any("積付けは要確認" in n for n in j.notes)
    k = row_of(rows(one()), "kashikiri_2t")
    assert ISSUE_LOADING in k.codes()


# ---- 貸切
def test_kashikiri_vehicles_by_weight():
    def modes(kg):
        return {r.mode for r in rows([Item(1, 10, 10, 10, kg)]) if r.mode.startswith("kashikiri")}
    assert modes(2000) == {"kashikiri_2t", "kashikiri_4t", "kashikiri_10t"}
    assert modes(2000.01) == {"kashikiri_4t", "kashikiri_10t"}
    assert modes(10000.01) == {"kashikiri"}
    only = [r for r in rows([Item(1, 10, 10, 10, 10000.01)]) if r.mode == "kashikiri"][0]
    assert only.status == ST_UNUSABLE


def test_kashikiri_distance_steps_and_range():
    kw = dict(route="other", origin_in_kanto=True)
    assert row_of(rows(one(), distance_km=200, **kw), "kashikiri_2t").amount == 52300
    assert row_of(rows(one(), distance_km=201, **kw), "kashikiri_2t").amount == 52300 + 3830
    low = [x for x in rows(one(), distance_km=199, **kw) if x.mode == "kashikiri"][0]
    assert low.status == ST_NOCALC and "200km 未満" in low.reasons[0]   # 近距離の段は表に無い。使えないのではなく額を出せない（S09）
    unk = [x for x in rows(one(), route="other", distance_km=300, origin_in_kanto=None) if x.mode == "kashikiri"][0]
    assert unk.status == ST_NOCALC


def test_kashikiri_national_guideline_note():
    for r in rows(one()):
        if r.mode.startswith("kashikiri"):
            assert any("国の目安" in n for n in r.notes)


# ---- カゴ台車: 内寸に入らない荷（向きを変えて入るかを見る）
def test_jitbox_piece_fit_by_any_orientation():
    # 170×104×104cm ちょうどは入る（3辺を並べ替えてよい）
    assert row_of(rows([Item(1, 104, 170, 104, 10)]), "jitbox").status == ST_OK
    # 1辺でも内寸を超えたら使えない（1個は分けられない。S08）。額は出さない
    r = row_of(rows([Item(1, 105, 105, 100, 10)]), "jitbox")
    assert r.status == ST_UNUSABLE and r.amount is None
    assert any("1個がボックスの内寸を超えます" in x for x in r.reasons)
    r = row_of(rows([Item(1, 100, 100, 171, 10)]), "jitbox")
    assert r.status == ST_UNUSABLE


def test_no_route_says_route_missing_not_manual_input():
    for r in rows(one(), route=None):
        if r.mode in ("takuhai", "rosen"):
            assert r.status == ST_NOCALC and r.needs_input is None and "区間" in r.missing


def test_manual_rows_show_user_input_as_source():
    r = row_of(estimate_all(make_shipment(one(), route="kanto_tohoku"), manual={"takuhai_ex_tax": 1500}), "takuhai")
    assert r.source.name == "利用者の入力" and not r.source.url
    r = row_of(estimate_all(make_shipment(one(), route="other", distance_km=700), manual={"rosen_base_fare": 5000}), "rosen")
    assert r.source.name == "利用者の入力"
    # 表から引いた行は運送会社の出典のまま
    assert row_of(rows(one()), "rosen").source.name != "利用者の入力"
