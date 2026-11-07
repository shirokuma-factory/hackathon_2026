"""版2の追加（SPEC §13、受入条件 G11〜G13）。"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from shipcompare.models import Case, CostLine, QuoteResponse
from shipcompare.quotes import (
    STATE_COMPLETE, STATE_INCOMPLETE, evaluate_quote, line_amounts_ex_tax,
)
from shipcompare.request import build_request
from shipcompare.storage import (
    ORDER_NOTICE, case_from_dict, case_to_dict, demo_quotes_for, load_case, load_scenario, save_case, scenario_info,
)

from conftest import make_shipment

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
    case = Case()
    case.confirm(sc["shipment"], scenario=scenario_info(sc))
    case.quotes = demo_quotes_for(sc, 1, TODAY)
    case.quotes[0].id, case.quotes[1].id = "q1", "q2"
    path = tmp_path / "c.json"
    save_case(path, case, now=datetime(2026, 11, 3, 12, 0, 0))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2 and raw["notice"] == ORDER_NOTICE
    assert raw["scenario"] == {"id": "S5", "name": "計画の実演例", "label": "想定の荷主", "modified": False}
    est = raw["estimates"]
    assert est[0]["version"] == 1
    rosen = next(r for r in est[0]["rows"] if r["mode"] == "rosen")
    assert rosen["amount_ex_tax"] and rosen["source_name"] and rosen["checked_on"] and rosen["source_url"] is None
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


# ---------------------------------------------------------------- 13.1 / 13.4


# ---------------------------------------------------------------- 13.12 (G17, G18, F4)


def test_spec13_12_per_version_scenario_roundtrip_and_old_file():
    case = Case()
    case.confirm(make_shipment(), scenario={"id": "S1", "name": "n", "label": "l", "modified": False})
    case.confirm(make_shipment(monthly_shipments=2), scenario=None)
    back = case_from_dict(case_to_dict(case))
    assert back.versions[0].scenario["id"] == "S1" and back.versions[1].scenario is None and back.scenario is None
    d = case_to_dict(case)
    for v in d["versions"]:
        v.pop("scenario")
    assert case_from_dict(d).versions[0].scenario is None


def test_spec13_12_g18_resave_of_schema_v1_is_v2(tmp_path):
    v1 = {"schema_version": 1, "versions": [{"number": 1, "shipment": {"items": [], "route": None}}],
          "quotes": [], "chosen_quote_id": None, "reason": "", "open_issues": []}
    case = case_from_dict(v1)
    assert case_to_dict(case)["schema_version"] == 2
    path = tmp_path / "c.json"
    save_case(path, case)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2


# ---------------------------------------------------------------- 13.13 (G19, G20, G21)


def test_spec13_12_g20_top_level_only_scenario_moves_to_last_version():
    case = Case()
    case.confirm(make_shipment())
    case.confirm(make_shipment(monthly_shipments=2))
    d = case_to_dict(case)
    for v in d["versions"]:
        v.pop("scenario")
    d["scenario"] = {"id": "S5", "name": "計画の実演例", "label": "想定の荷主"}
    back = case_from_dict(d)
    assert back.versions[0].scenario is None
    assert back.scenario["id"] == "S5" and back.scenario["modified"] is False
    assert case_to_dict(back)["scenario"]["id"] == "S5"


