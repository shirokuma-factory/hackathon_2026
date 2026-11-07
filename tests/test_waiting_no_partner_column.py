"""契約の相手の列が無い表を貼っても、全行を仮の相手で計算し、画面で相手を入れると全行に入ること（2026-11-07）。

小さな運送会社の表には契約の相手の列が無いことが多い。以前は全行が「契約の相手がありません」の誤りで、0 行しか計算されなかった。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
WAITING = ROOT / "web" / "waiting.html"

PASTE5 = ("日付\t車番\t到着\t荷役開始\t荷役終了\n"
          "2026/10/5\t1234\t8:00\t9:10\t9:50\n"
          "2026/10/6\t1234\t8:30\t10:00\t10:40\n"
          "2026/10/7\t5678\t9:00\t10:20\t11:00\n"
          "2026/10/8\t5678\t7:50\t8:40\t9:10\n"
          "2026/10/9\t1234\t8:10\t9:30\t10:05\n")
PROV = "（相手を入れてください）"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _paste(page, text):
    page.goto(WAITING.as_uri() + "?selftest=0")
    page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "ページ")
    page.js("document.querySelector('#b-clear').click()")
    page.js("document.querySelector('#paste').value = " + json.dumps(text, ensure_ascii=False))
    page.js("document.querySelector('#b-paste').click()")
    page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "下書き")


def test_five_column_paste_is_calculated_and_name_fills_all_rows():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _paste(page, PASTE5)
        assert page.js("S.res.rows.length") == 5
        assert page.js("S.res.errors.length") == 0
        assert page.js("[...new Set(S.res.rows.map(r => r.partner))]") == [PROV]
        # 画面: 仮の相手の説明と入力欄、メールの宛先は編集用の仮の文
        assert page.js("!!document.querySelector('#prov-box #prov-name') && document.querySelector('#b-prov-fill').textContent") == "全部の行に入れる"
        assert "この記録の荷主（契約の相手）" in page.js("document.querySelector('#prov-box').textContent")
        assert page.js("document.querySelector('#mail-to').textContent") == "（荷主の会社名） 物流ご担当者様"
        assert page.js("!!document.querySelector('#mail-to .fill.is-blank')")
        # 相手を入れる
        page.js("document.querySelector('#prov-name').value = 'テスト商事株式会社'")
        page.js("document.querySelector('#b-prov-fill').click()")
        page.wait(lambda: page.js("!document.querySelector('#prov-box')"), 10, "入力欄が消える")
        assert page.js("S.res.rows.length") == 5
        assert page.js("S.rows.filter(r => r.partner === 'テスト商事株式会社').length") == 5
        assert page.js("[...new Set(S.res.rows.map(r => r.partner))]") == ["テスト商事株式会社"]
        assert page.js("document.querySelector('#mail-to').textContent") == "テスト商事株式会社 物流ご担当者様"
        csv = page.js("csvText()")
        lines = [ln for ln in csv.strip().split("\r\n")][1:]
        assert len(lines) == 5 and all("テスト商事株式会社" in ln and PROV not in ln for ln in lines)


def test_partially_empty_partner_cells_use_the_same_treatment():
    _edge_or_skip()
    import capture_web as cw
    text = ("日付\t契約の相手\t到着\t荷役開始\n"
            "2026/10/5\tA商事\t8:00\t9:10\n"
            "2026/10/6\t\t8:30\t10:00\n"
            "2026/10/7\t\t9:00\t10:20\n")
    with cw.edge_page(1366, 768) as page:
        _paste(page, text)
        assert page.js("S.res.rows.length") == 3 and page.js("S.res.errors.length") == 0
        assert page.js("S.res.rows.filter(r => r.partner_provisional).length") == 2
        page.js("document.querySelector('#prov-name').value = 'B運輸'")
        page.js("document.querySelector('#b-prov-fill').click()")
        page.wait(lambda: page.js("!document.querySelector('#prov-box')"), 10, "入力欄が消える")
        assert sorted(page.js("S.res.rows.map(r => r.partner)")) == ["A商事", "B運輸", "B運輸"]


def test_example_numbers_are_unchanged_and_have_no_prompt():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1366, 768) as page:
        page.goto(WAITING.as_uri() + "?example=1&selftest=0")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "書面")
        assert page.js("!document.querySelector('#prov-box')")
        assert page.js("S.res.rows.every(r => !r.partner_provisional)")
