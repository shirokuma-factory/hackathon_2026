"""SPEC §34.4・§34.5: 配置の探索を強くする。置き方が見つからないだけなら × と断定しない。4t の言葉を分ける。"""

from __future__ import annotations

import time

from shipcompare.estimate import estimate_all
from shipcompare.models import Item
from shipcompare.loadplan import (
    LoadItem, Container, TARIFF_4T_NOTE, container_by_id, plan_load, tariff_class_note,
)

from conftest import make_shipment, row_of
from test_v3_loadplan import check_geometry


def virtual(l: int, w: int, h: int, payload: int = 1000, basis: str = "公表例") -> Container:
    return Container("example", "example", l, w, h, payload, basis, "sample")


def test_repro4_places_all_three():
    """監査の再現 4: 4×4×1m に 3×2×1m を 2 個と 4×1×1m を 1 個（積み重ね不可）。"""
    r = plan_load([LoadItem(2, 300, 200, 100, 1, False), LoadItem(1, 400, 100, 100, 1, False)],
                  virtual(4000, 4000, 1000))
    assert r["summary"]["placed"] == 3 and r["summary"]["unplaced_count"] == 0
    assert r["summary"]["fits"] is True and r["summary"]["verdict"] != "×"  # 容積 100% なので △
    check_geometry(r)


def test_repro4_is_deterministic():
    items = [LoadItem(2, 300, 200, 100, 1, False), LoadItem(1, 400, 100, 100, 1, False)]
    a = plan_load(items, virtual(4000, 4000, 1000))
    b = plan_load(items, virtual(4000, 4000, 1000))
    assert a == b


def test_hard_but_area_sufficient_is_warn_not_ng():
    """風車の形（5×5m に 3×2m を 4 個と 1×1m を 1 個）は棚詰めでは解けない。床の合計は足りるので × にしない。"""
    r = plan_load([LoadItem(4, 300, 200, 100, 1, False), LoadItem(1, 100, 100, 100, 1, False)],
                  virtual(5000, 5000, 1000))
    s = r["summary"]
    assert s["fits"] is False and s["unplaced_count"] >= 1
    assert s["verdict"] == "△"
    assert "配置の確認が必要" in s["verdict_reason"]
    check_geometry(r)


def test_weight_over_is_ng():
    r = plan_load([LoadItem(1, 100, 100, 100, 2000, False)], virtual(4000, 4000, 1000, payload=1000))
    assert r["summary"]["verdict"] == "×" and "重さ" in r["summary"]["verdict_reason"]


def test_single_item_larger_than_hold_is_ng():
    r = plan_load([LoadItem(1, 500, 100, 100, 1, True)], virtual(4000, 400, 1000))
    assert r["summary"]["verdict"] == "×" and "寸法" in r["summary"]["verdict_reason"]


def test_non_stackable_floor_insufficient_is_ng():
    r = plan_load([LoadItem(5, 300, 200, 100, 1, False)], virtual(4000, 2000, 1000))  # 床 8m2 に 6m2 x5
    assert r["summary"]["verdict"] == "×" and "床" in r["summary"]["verdict_reason"]


def test_many_boxes_still_fast():
    t = time.perf_counter()
    r = plan_load([LoadItem(300, 50, 40, 30, 1, False)], container_by_id("wing_4t"))
    assert time.perf_counter() - t < 3.0
    assert r["summary"]["placed"] + r["summary"]["unplaced_count"] == 300


def test_4t_sentence_in_loadplan_and_kashikiri():
    wing = container_by_id("wing_4t")
    assert wing.payload_kg == 2700
    r = plan_load([LoadItem(3, 50, 50, 50, 1000, True)], wing)  # 3,000kg: 2,700 超、4,000 以内
    assert r["summary"]["tariff_note"] == TARIFF_4T_NOTE
    assert "約 2,700kg" in TARIFF_4T_NOTE and "車によって最大積載量が違います" in TARIFF_4T_NOTE
    assert tariff_class_note("wing_4t", 2000) == "" and tariff_class_note("wing_4t", 4500) == ""
    assert tariff_class_note("box_2t", 3000) == ""


def test_kashikiri_card_note_for_4t_over_wing_payload():
    rows = estimate_all(make_shipment([Item(1, 10, 10, 10, 3000)]))
    r4 = row_of(rows, "kashikiri_4t")
    assert r4.detail["class_label"] == "4t 車の区分（〜4,000kg）"
    assert r4.detail["load_note"] == TARIFF_4T_NOTE
    assert TARIFF_4T_NOTE in r4.notes
    # 2,000kg 以内なら文は出さない
    rows2 = estimate_all(make_shipment([Item(1, 10, 10, 10, 1500)]))
    assert "load_note" not in row_of(rows2, "kashikiri_4t").detail
