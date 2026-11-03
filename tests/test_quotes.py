"""受入条件 G02〜G04 と G08（期限切れ、税込/税抜の混在）。"""

from __future__ import annotations

from datetime import date

from shipcompare.models import COST_KEYS, Case, CostLine, QuoteResponse
from shipcompare.quotes import STATE_COMPLETE, STATE_HISTORY, STATE_INCOMPLETE, compare_quotes

from conftest import make_shipment

TODAY = date(2026, 11, 3)
REST = ("pickup", "delivery", "handling", "fuel", "highway", "other")


def quote(qid, amounts=None, version=1, tax="税抜", valid=date(2026, 11, 30), included=()):
    """amounts: {項目: 円}、included: 含む項目。残りは不明。"""
    lines = {k: CostLine("amount", v) for k, v in (amounts or {}).items()}
    for k in included:
        lines[k] = CostLine("included")
    return QuoteResponse(qid, qid, "商品", version, TODAY, valid, tax, lines)


FULL = {"pickup": 1000, "trunk": 5000, "delivery": 1000, "handling": 500, "fuel": 300, "highway": 200}


def full(qid, trunk=5000, **kw):
    return quote(qid, dict(FULL, trunk=trunk), included=("other",), **kw)


# ---- G02
def test_ranking_only_with_two_complete():
    c = compare_quotes([full("a")], 1, TODAY)
    assert not c.ranking_shown and "比べる相手がいません" in c.message
    assert c.evaluations[0].rank is None and c.evaluations[0].diff_from_lowest is None
    c = compare_quotes([full("a"), full("b", 6000)], 1, TODAY)
    assert c.ranking_shown
    a, b = c.evaluations
    assert (a.quote.id, a.rank, a.diff_from_lowest, a.total_ex_tax) == ("a", 1, 0, 8000)
    assert (b.quote.id, b.rank, b.diff_from_lowest) == ("b", 2, 1000)


def test_ranking_sorted_and_ties():
    c = compare_quotes([full("x", 7000), full("a", 5000), full("t", 5000)], 1, TODAY)
    assert c.evaluations[-1].quote.id == "x"
    assert [e.rank for e in c.evaluations] == [1, 1, 3]


def test_no_quotes_message():
    c = compare_quotes([], 1, TODAY)
    assert not c.ranking_shown and c.evaluations == []


# ---- G03
def test_incomplete_shows_partial_and_unknown_and_not_ranked():
    inc = quote("p", {"trunk": 5000, "fuel": 300, "highway": 200, "handling": 500}, included=("other",))
    c = compare_quotes([full("a"), full("b", 6000), inc], 1, TODAY)
    e = next(x for x in c.evaluations if x.quote.id == "p")
    assert e.state == STATE_INCOMPLETE and e.rank is None and e.total_ex_tax is None
    assert e.partial_ex_tax == 6000
    assert e.unknown_keys == ["pickup", "delivery"]
    assert e.summary.startswith("部分合計 6,000円") and "不明: 集荷・配達" in e.summary
    assert [x.quote.id for x in c.evaluations if x.rank] == ["a", "b"]
    # 完全 1件 + 不完全 1件でも順位は出ない
    assert not compare_quotes([full("a"), inc], 1, TODAY).ranking_shown


def test_zero_amount_is_not_treated_as_free():
    q = quote("z", dict(FULL, pickup=0), included=("other",))
    e = compare_quotes([q], 1, TODAY).evaluations[0]
    assert e.state == STATE_INCOMPLETE and "pickup" in e.unknown_keys


def test_negative_amount_incomplete():
    q = quote("n", dict(FULL, pickup=-5), included=("other",))
    assert compare_quotes([q], 1, TODAY).evaluations[0].state == STATE_INCOMPLETE


def test_all_unknown_listed_not_zero_total():
    e = compare_quotes([quote("u")], 1, TODAY).evaluations[0]
    assert e.total_ex_tax is None and len(e.unknown_keys) == len(COST_KEYS)


# ---- G04
def test_version_change_moves_old_quote_to_history():
    case = Case()
    case.confirm(make_shipment())
    assert case.current_version == 1
    case.quotes += [full("a"), full("b", 6000)]
    assert compare_quotes(case.quotes, case.current_version, TODAY).ranking_shown
    v2 = case.confirm(make_shipment(destinations=3))
    assert v2.number == 2
    c = compare_quotes(case.quotes, case.current_version, TODAY)
    assert not c.ranking_shown
    assert all(e.state == STATE_HISTORY and e.rank is None for e in c.evaluations)
    assert "条件変更前" in c.evaluations[0].reasons[0]


def test_confirm_same_shipment_does_not_bump_version():
    case = Case()
    case.confirm(make_shipment())
    case.confirm(make_shipment())
    assert [v.number for v in case.versions] == [1]


def test_new_version_quotes_compare_separately():
    qs = [full("old1", version=1), full("old2", 6000, version=1), full("n1", version=2), full("n2", 6500, version=2)]
    c = compare_quotes(qs, 2, TODAY)
    assert [e.quote.id for e in c.by_state(STATE_COMPLETE)] == ["n1", "n2"]
    assert {e.quote.id for e in c.by_state(STATE_HISTORY)} == {"old1", "old2"}


# ---- G08
def test_expired_quote_incomplete_not_ranked():
    c = compare_quotes([full("a"), full("b", 6000), full("e", 100, valid=date(2026, 11, 2))], 1, TODAY)
    e = next(x for x in c.evaluations if x.quote.id == "e")
    assert e.state == STATE_INCOMPLETE and "有効期限切れ" in e.reasons and e.rank is None
    # 有効期限が当日なら期限内
    assert compare_quotes([full("d", valid=TODAY)], 1, TODAY).evaluations[0].state == STATE_COMPLETE


def test_missing_validity_incomplete():
    assert compare_quotes([full("a", valid=None)], 1, TODAY).evaluations[0].state == STATE_INCOMPLETE


def test_tax_included_converted_before_compare():
    # 税込 8,800 → 税抜 8,000。税抜 8,100 より安い
    inc = quote("i", {"trunk": 8800}, tax="税込", included=REST)
    ex = quote("x", {"trunk": 8100}, included=REST)
    c = compare_quotes([ex, inc], 1, TODAY)
    assert [e.quote.id for e in c.evaluations] == ["i", "x"]
    assert c.evaluations[0].total_ex_tax == 8000 and c.evaluations[1].diff_from_lowest == 100


def test_tax_included_rounding():
    q = quote("i", {"trunk": 1005}, tax="税込", included=REST)
    assert compare_quotes([q], 1, TODAY).evaluations[0].total_ex_tax == 914  # 913.63...
