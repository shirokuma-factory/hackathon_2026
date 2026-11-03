"""受入条件 G09: 保存して読み込むと条件の全版・回答・判断が一致する。"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from shipcompare.estimate import estimate_all
from shipcompare.models import Case, CostLine, Item, QuoteResponse
from shipcompare.storage import load_case, save_case

from conftest import make_shipment


def build_case() -> Case:
    case = Case()
    case.confirm(make_shipment([Item(3, 120, 50, 50, 40), Item(None, 10, None, 10, 1.5)]), "2026-11-03T10:00:00")
    case.confirm(make_shipment(destinations=2, cargo_name="木工品", fragile_note="割れ物", forklift=True),
                 "2026-11-03T11:00:00")
    case.quotes = [
        QuoteResponse("q1", "A社", "路線便", 1, date(2026, 11, 3), date(2026, 11, 30), "税込",
                      {"trunk": CostLine("amount", 1100), "pickup": CostLine("included"), "fuel": CostLine("unknown")}),
        QuoteResponse("q2", "B社", "貸切", 2, date(2026, 11, 4), None, "税抜", {}),
    ]
    case.chosen_quote_id = "q2"
    case.reason = "日程が合うため"
    case.open_issues = ["積付けの条件を確認", "高速代の扱い"]
    return case


def test_round_trip_equal(tmp_path):
    case = build_case()
    path = tmp_path / "case.json"
    save_case(path, case, now=datetime(2026, 11, 3, 12, 0, 0))
    loaded = load_case(path)
    assert loaded == case
    assert loaded.saved_at == "2026-11-03T12:00:00"
    assert [v.number for v in loaded.versions] == [1, 2]
    assert loaded.versions[0].shipment.items[1].count is None  # 不明が 0 にならない
    assert loaded.chosen_quote_id == "q2" and loaded.reason == "日程が合うため"
    assert loaded.open_issues == ["積付けの条件を確認", "高速代の扱い"]


def test_saved_file_uses_lf_and_utf8(tmp_path):
    path = tmp_path / "case.json"
    save_case(path, build_case())
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert "木工品".encode("utf-8") in raw


def test_same_display_after_load(tmp_path):
    case = build_case()
    path = tmp_path / "case.json"
    save_case(path, case)
    loaded = load_case(path)
    a = [(r.mode, r.status, r.amount) for r in estimate_all(case.versions[1].shipment)]
    b = [(r.mode, r.status, r.amount) for r in estimate_all(loaded.versions[1].shipment)]
    assert a == b


def test_unsupported_schema_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"schema_version": 99}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_case(path)
