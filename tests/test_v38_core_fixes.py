"""洗い出し r1 の確認済み指摘（L1・L2・Q1・Q2）の回帰試験。"""

from __future__ import annotations

from datetime import date

import pytest

from shipcompare.loadplan import Container, LoadItem, plan_load
from shipcompare.models import CostLine, QuoteResponse
from shipcompare.quotes import STATE_HISTORY, evaluate_quote
from shipcompare.storage import case_from_dict, case_to_dict

from test_storage import build_case


def virtual(l: int, w: int, h: int, payload: int = 10000) -> Container:
    return Container("example", "example", l, w, h, payload, "公表例", "sample")


# ---- L1
def test_l1_stackable_remaining_after_nonstackable_fills_floor_is_x():
    # 荷室の床 4×4m。積み重ね不可の 4×4m が床を全部ふさぎ、積み重ねてよい荷が 1 個残る
    r = plan_load([LoadItem(1, 400, 400, 100, 10, False), LoadItem(1, 100, 100, 100, 1, True)],
                  virtual(4000, 4000, 3000))
    s = r["summary"]
    assert s["verdict"] == "×" and s["fits"] is False
    assert "積み重ね不可" in s["verdict_reason"]
    assert "高さまたは下の荷の広さ" not in r["unplaced"][0]["reason"]
    assert "積み重ね不可" in r["unplaced"][0]["reason"]


def test_l1_all_nonstackable_floor_total_still_x():
    r = plan_load([LoadItem(3, 300, 300, 100, 1, False)], virtual(4000, 4000, 1000))
    assert r["summary"]["verdict"] == "×"


def test_l1_with_room_on_floor_is_not_x():
    r = plan_load([LoadItem(1, 200, 200, 100, 10, False), LoadItem(1, 100, 100, 100, 1, True)],
                  virtual(4000, 4000, 3000))
    assert r["summary"]["fits"] is True


# ---- L2
def test_l2_upright_only_item_is_triangle_with_message():
    c = virtual(800, 600, 1700)
    r = plan_load([LoadItem(1, 150, 50, 50, 5, True)], c)
    s = r["summary"]
    assert s["verdict"] == "△" and s["fits"] is False
    assert "立てれば入る可能性があります（この手順では傾けません）" in s["verdict_reason"]


def test_l2_truly_oversize_stays_x():
    r = plan_load([LoadItem(1, 250, 250, 250, 5, True)], virtual(800, 600, 1700))
    assert r["summary"]["verdict"] == "×"
    assert "寸法が荷室より大きい" in r["summary"]["verdict_reason"]


# ---- Q2
def _q(version):
    return QuoteResponse("q", "A", "商品", version, date(2026, 11, 3), date(2026, 11, 30), "税抜",
                         {"trunk": CostLine("amount", 1000)})


def test_q2_newer_version_is_not_labeled_before_change():
    ev = evaluate_quote(_q(3), 2, date(2026, 11, 3))
    assert ev.state == STATE_HISTORY
    text = "".join(ev.reasons)
    assert "条件変更前" not in text and "新しい版" in text


def test_q2_older_version_keeps_label():
    ev = evaluate_quote(_q(1), 2, date(2026, 11, 3))
    assert "条件変更前" in "".join(ev.reasons)


# ---- Q1
def _mutate_line(d, **kw):
    line = d["quotes"][0]["lines"]["trunk"]
    line.update(kw)


@pytest.mark.parametrize("mutate,word", [
    (lambda d: _mutate_line(d, amount="10000"), "金額"),
    (lambda d: _mutate_line(d, amount=10000.5), "金額"),
    (lambda d: _mutate_line(d, amount=True), "金額"),
    (lambda d: _mutate_line(d, kind="bogus"), "種類"),
    (lambda d: d["quotes"][0].update(tax="免税"), "税区分"),
])
def test_q1_malformed_quote_lines_rejected_in_japanese(mutate, word):
    d = case_to_dict(build_case())
    mutate(d)
    with pytest.raises(ValueError) as e:
        case_from_dict(d)
    assert word in str(e.value) and any(ord(ch) > 0x3000 for ch in str(e.value))


def test_q1_valid_case_still_loads():
    case_from_dict(case_to_dict(build_case()))
