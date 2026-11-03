"""版2の追加（SPEC §13、受入条件 G11〜G13）。"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from shipcompare.models import Case, CostLine, QuoteResponse
from shipcompare.quotes import (
    STATE_COMPLETE, STATE_INCOMPLETE, compare_quotes, evaluate_quote, line_amounts_ex_tax,
)
from shipcompare.request import build_request
from shipcompare.storage import (
    ORDER_NOTICE, case_from_dict, case_to_dict, demo_quotes_for, load_case, load_scenario, save_case,
)

from conftest import make_shipment
from test_app import confirm, load_scenario as app_load, new_app

TODAY = date(2026, 11, 3)
KEYS = ["pickup", "trunk", "delivery", "handling", "fuel", "highway", "other"]


def quote(tax="税抜", **amounts) -> QuoteResponse:
    lines = {k: CostLine("amount", amounts[k]) if k in amounts else CostLine("included") for k in KEYS}
    return QuoteResponse("q1", "A社", "路線便", 1, TODAY, date(2026, 12, 1), tax, lines)


# ---------------------------------------------------------------- G12
@pytest.mark.parametrize("amounts", [
    {"trunk": 1100}, {"pickup": 1001, "trunk": 2003, "delivery": 3007},
    {"pickup": 5, "trunk": 5, "delivery": 5, "handling": 5, "fuel": 5, "highway": 5, "other": 5},
    {"pickup": 333, "trunk": 334, "other": 335},
])
@pytest.mark.parametrize("tax", ["税抜", "税込"])
def test_spec13_3_breakdown_sum_matches_total(amounts, tax):
    q = quote(tax, **amounts)
    ev = evaluate_quote(q, 1, TODAY)
    assert ev.state == STATE_COMPLETE
    assert sum(line_amounts_ex_tax(q).values()) == ev.total_ex_tax


def test_spec13_3_breakdown_tax_in_values():
    q = quote("税込", trunk=11000, pickup=2200)
    assert line_amounts_ex_tax(q) == {"pickup": 2000, "trunk": 10000}


def test_spec13_3_breakdown_omits_included_and_unknown():
    q = quote(trunk=18000)
    q.lines["delivery"] = CostLine("unknown")
    q.lines["fuel"] = CostLine("amount", 0)
    assert line_amounts_ex_tax(q) == {"trunk": 18000}
    assert sum(line_amounts_ex_tax(q).values()) == evaluate_quote(q, 1, TODAY).partial_ex_tax


# ---------------------------------------------------------------- 13.10
def test_spec13_10_included_amount_not_double_counted(tmp_path):
    q = quote(trunk=26000)
    q.lines["fuel"] = CostLine("included", 3000)  # 含むなのに金額が入っている
    assert evaluate_quote(q, 1, TODAY).total_ex_tax == 26000
    assert line_amounts_ex_tax(q) == {"trunk": 26000}
    case = Case()
    case.confirm(make_shipment())
    case.quotes = [q]
    path = tmp_path / "c.json"
    save_case(path, case)
    loaded = load_case(path)
    assert evaluate_quote(loaded.quotes[0], 1, TODAY).total_ex_tax == 26000


def test_spec13_10_all_included_is_incomplete():
    q = QuoteResponse("q1", "A社", "路線便", 1, TODAY, date(2026, 12, 1), "税抜",
                      {k: CostLine("included", 1000) for k in KEYS})
    ev = evaluate_quote(q, 1, TODAY)
    assert ev.state == STATE_INCOMPLETE and "金額の項目がありません" in ev.reasons


# ---------------------------------------------------------------- 13.2 / 13.5 / 13.6
def test_spec13_2_s5_scenario_and_demo_quotes():
    sc = load_scenario("S5")
    assert sc["name"] == "計画の実演例" and sc["label"] == "想定の荷主"
    qs = demo_quotes_for(sc, 3, TODAY)
    assert [q.carrier for q in qs] == ["架空A社", "架空B社"]
    assert all(q.is_demo and q.version == 3 and q.received_on == TODAY for q in qs)
    assert qs[0].valid_until == date(2026, 11, 17)
    a, b = (evaluate_quote(q, 3, TODAY) for q in qs)
    assert a.state == STATE_INCOMPLETE and a.partial_ex_tax == 18000
    assert b.state == STATE_COMPLETE and b.total_ex_tax == 26000


def test_spec13_5_json_has_notice_scenario_estimates(tmp_path):
    sc = load_scenario("S5")
    case = Case(scenario={"id": sc["id"], "name": sc["name"], "label": sc["label"]})
    case.confirm(sc["shipment"])
    case.quotes = demo_quotes_for(sc, 1, TODAY)
    case.quotes[0].id, case.quotes[1].id = "q1", "q2"
    path = tmp_path / "c.json"
    save_case(path, case, now=datetime(2026, 11, 3, 12, 0, 0))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2 and raw["notice"] == ORDER_NOTICE
    assert raw["scenario"] == {"id": "S5", "name": "計画の実演例", "label": "想定の荷主"}
    est = raw["estimates"]
    assert est[0]["version"] == 1
    rosen = next(r for r in est[0]["rows"] if r["mode"] == "rosen")
    assert rosen["amount_ex_tax"] and rosen["source_name"] and rosen["source_url"] and rosen["checked_on"]
    assert all(q["is_demo"] is True for q in raw["quotes"])
    assert load_case(path) == case


def test_spec13_5_loads_schema_v1():
    v1 = {"schema_version": 1, "saved_at": "2026-11-03T10:00:00",
          "versions": [{"number": 1, "confirmed_at": None, "shipment": {"items": [], "route": None}}],
          "quotes": [{"id": "q1", "carrier": "A社", "product": "x", "version": 1, "tax": "税抜",
                      "lines": {"trunk": {"kind": "amount", "amount": 100}}}],
          "chosen_quote_id": None, "reason": "", "open_issues": []}
    case = case_from_dict(v1)
    assert case.schema_version == 1 and case.scenario is None
    assert case.quotes[0].is_demo is False and case.versions[0].shipment.monthly_shipments is None
    assert case_to_dict(case)["scenario"] is None


def test_spec13_6_monthly_shipments_validation_and_version():
    case = Case()
    case.confirm(make_shipment())
    case.confirm(make_shipment(monthly_shipments=4))
    assert case.current_version == 2  # 月の出荷回数も版の対象
    assert case_from_dict(case_to_dict(case)).versions[1].shipment.monthly_shipments == 4
    for bad in (0, -1, 1.5, True, "3"):
        d = case_to_dict(case)
        d["versions"][1]["shipment"]["monthly_shipments"] = bad
        with pytest.raises(ValueError):
            case_from_dict(d)


def test_spec13_6_request_monthly_and_scenario_lines():
    d = build_request(make_shipment())
    assert "月の出荷回数" in d.missing_optional and "月の出荷回数の見込み" not in d.text
    d = build_request(make_shipment(monthly_shipments=4), {"id": "S5", "name": "計画の実演例", "label": "想定の荷主"})
    assert "月の出荷回数の見込み: 4 回" in d.text and "月の出荷回数" not in d.missing_optional
    assert "この条件は計画の実演例（想定の荷主）の例です" in d.text
    assert "の例です" not in build_request(make_shipment()).text


# ---------------------------------------------------------------- G11（画面）
def test_spec13_2_s5_demo_flow():
    at = confirm(app_load(new_app(), 5))
    assert not at.exception
    at.button(key="add_demo_quotes").click().run()
    assert not at.exception
    case = at.session_state["case"]
    assert [q.carrier for q in case.quotes] == ["架空A社", "架空B社"]
    text = "\n".join(d.value.to_string() for d in at.dataframe)
    assert "架空B社（架空）" in text and "部分合計" in text
    assert any("比べる相手がいません" in m.value for m in at.markdown)

    at.button(key="add_demo_quotes").click().run()  # 二重に足さない
    assert len(at.session_state["case"].quotes) == 2

    at.text_input(key="q_carrier").set_value("A社")
    at.text_input(key="q_product").set_value("路線便")
    for k, amt in {"pickup": 6000, "trunk": 18000, "delivery": 5000}.items():
        at.selectbox(key=f"q_kind_{k}").select("amount")
        at.number_input(key=f"q_amt_{k}").set_value(amt)
    for k in ("handling", "fuel", "highway", "other"):
        at.selectbox(key=f"q_kind_{k}").select("included")
    at.button(key="q_submit").click().run()
    assert not at.exception
    text = "\n".join(d.value.to_string() for d in at.dataframe)
    assert "26,000" in text and "29,000" in text and "3,000" in text
    case = at.session_state["case"]
    cmp = compare_quotes(case.quotes, 1, date.today())
    done = {e.quote.carrier: e for e in cmp.by_state(STATE_COMPLETE)}
    assert done["架空B社"].rank == 1 and done["架空B社"].total_ex_tax == 26000
    assert done["A社"].rank == 2 and done["A社"].diff_from_lowest == 3000
    assert case.scenario["id"] == "S5"
    assert any(ORDER_NOTICE in m.value for m in at.info)


def test_spec13_5_new_input_clears_scenario_and_hides_demo_button():
    at = confirm(app_load(new_app(), 5))
    assert at.session_state["scenario_info"]["id"] == "S5"
    at.selectbox(key="scenario_select").select("").run()
    assert at.session_state["scenario_info"] is None
    assert not [b for b in at.button if b.key == "add_demo_quotes"]


# ---------------------------------------------------------------- 13.1 / 13.4
def test_spec13_1_status_label():
    from shipcompare.estimate import ST_OK, estimate_all
    assert ST_OK == "公開条件に合う"
    rows = estimate_all(load_scenario("S1")["shipment"])
    assert {r.status for r in rows} >= {"公開条件に合う"} and "候補" not in {r.status for r in rows}
    at = confirm(app_load(new_app(), 1))
    assert any("その会社がこの出荷を受けるかは見積で確かめます" in c.value for c in at.caption)
    assert "公開条件に合う" in "\n".join(d.value.to_string() for d in at.dataframe)


def test_spec13_4_order_notice(tmp_path):
    at = confirm(app_load(new_app(), 5))
    assert any(m.value == ORDER_NOTICE for m in at.info)
    case = Case()
    case.confirm(make_shipment())
    path = tmp_path / "c.json"
    save_case(path, case)
    assert json.loads(path.read_text(encoding="utf-8"))["notice"] == ORDER_NOTICE
    assert "発注は運送会社と直接行ってください" in ORDER_NOTICE
