"""受入条件 G06: 運賃データに出典・確認日などのメタデータがある。"""

from __future__ import annotations

import pytest

from shipcompare.tariffs import REQUIRED_METADATA, TARIFF_FILES, load_all_tariffs, missing_metadata, source_of


@pytest.mark.parametrize("key", list(TARIFF_FILES))
def test_metadata_present(key):
    t = load_all_tariffs()[key]
    assert missing_metadata(t) == []
    if t.get("is_sample"):  # 架空の見本（実在の表が手元にないとき）
        assert t["checked_on"] == "2026-11-07" and "架空" in t["source"] and "架空" in t["carrier"]
        assert source_of(t).checked_on == "2026-11-07"
        return
    assert t["checked_on"] == "2026-11-03"
    assert t["source_url"].startswith("https://")
    assert source_of(t).checked_on == "2026-11-03"


def test_new_files_have_applies_from_key_and_tax_ex():
    ts = load_all_tariffs()
    for key in ("jitbox", "kashikiri"):
        assert "applies_from" in ts[key]
        assert ts[key]["tax"] == "税抜"
        if not ts[key].get("is_sample"):
            assert ts[key]["verified_by"] == "調査担当サブエージェントが一次資料で照合（2026-11-03）"
    assert "applies_from" in ts["rosen"]
    if not ts["rosen"].get("is_sample"):
        assert ts["rosen"]["applies_from"] == "2025-01-01"


def test_required_metadata_names():
    assert set(REQUIRED_METADATA) >= {"carrier", "service", "source_url", "checked_on", "tax", "scope_note", "verified_by"}
