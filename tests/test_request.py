"""見積依頼文（SPEC §7）。"""

from __future__ import annotations

from shipcompare.models import Item
from shipcompare.request import STATUS_DRAFT, STATUS_READY, build_request
from shipcompare.storage import load_scenario

from conftest import make_shipment


def test_draft_when_required_missing():
    s = load_scenario("S1")["shipment"]  # 日付なし
    d = build_request(s)
    assert d.status == STATUS_DRAFT
    assert "出荷日" in d.missing_required and "希望着日" in d.missing_required
    assert "まだ決まっていない項目" in d.text and "  - 出荷日"+chr(10) in d.text and "（必須）" not in d.text   # 日本語の直し 2026-11-08


def test_ready_when_complete():
    d = build_request(make_shipment())
    assert d.status == STATUS_READY and d.missing_required == []
    assert "フォークリフトの有無" in d.missing_optional
    for word in ("合計重量", "合計容積", "計算重量", "お引き受けいただけるか", "燃料", "高速", "税抜か税込か", "有効期限", "積付け", "お世話になっております", "よろしくお願いいたします", "（荷主の会社名）"):
        assert word in d.text


def test_unknown_item_value_in_draft():
    d = build_request(make_shipment([Item(None, 30, 30, 30, 10)]))
    assert d.status == STATUS_DRAFT and "荷物1行目の個数" in d.missing_required
    assert d.text.endswith("\n") and "\r" not in d.text
