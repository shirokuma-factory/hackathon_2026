"""計画 08 の要件のうち、11/7 に消した Streamlit の版の試験が受け持っていた 2 つを、今のコードで確かめる。

tools/requirements.json の R04（test_spec13_1_*）と R18（test_spec13_4_*）がこの試験を見る。
"""
from __future__ import annotations

import json
from datetime import datetime

from shipcompare import estimate, request
from shipcompare.models import Case
from shipcompare.storage import ORDER_NOTICE, load_scenario, save_case, scenario_info


def test_spec13_1_public_match_and_acceptance_check_are_separate_names():
    # 公表の条件に合うこと（estimate の状態名）と、運送会社が受託できるかの確認（依頼文の確認事項）を別の言葉で出す
    assert estimate.ST_OK == "公開条件に合う"
    assert "この荷物をお引き受けいただけるか" in request.CONFIRM_ITEMS   # 日本語の直し 2026-11-08
    assert all("受託" not in s for s in (estimate.ST_OK, estimate.ST_CHECK, estimate.ST_UNUSABLE, estimate.ST_NOCALC))


def test_spec13_4_saved_case_says_the_order_is_not_confirmed(tmp_path):
    # 回答を選んでも発注は確定していないことを、文として持ち、保存する JSON にも入れる
    assert "発注は確定していません" in ORDER_NOTICE
    case = Case()
    sc = load_scenario("S5")
    case.confirm(sc["shipment"], scenario=scenario_info(sc))
    path = tmp_path / "c.json"
    save_case(path, case, now=datetime(2026, 11, 8, 12, 0, 0))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["notice"] == ORDER_NOTICE
