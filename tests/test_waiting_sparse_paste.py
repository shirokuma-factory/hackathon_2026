"""列の少ない貼り付け（日付・車番・到着・荷役開始・荷役終了の 5 列）でも、メールに事務向けの指示や矛盾した文が入らないこと（2026-11-08）。"""

from __future__ import annotations

import json
import sys
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


def test_sparse_paste_email_has_no_screen_only_text_and_handler_bulk_works():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _paste(page, PASTE5)
        t = page.js("draftText(S.res.by_partner[0])")
        for bad in ("再計算してください", "（場所の記入なし）", "荷役は合計0分", "渋滞や当社側の遅れによる待ちは含めていません", "運転者の荷役は合計"):
            assert bad not in t, bad
        assert "記録のすべての待ちを数えています" in t
        assert page.js("document.querySelector('#wait-doc').textContent.includes('再計算してください')") is False
        # 画面の確認欄
        assert "荷主等の都合の欄が貼り付けの記録に無かった" in page.js("document.querySelector('#fault-box').textContent")
        assert page.js("!!document.querySelector('#handler-box #bulk-handler')")
        before = page.js("S.res.by_partner[0].handling_fee")
        assert before == 0
        page.js("document.querySelector('#bulk-handler').value = '運転者'")
        page.js("document.querySelector('#b-handler-fill').click()")
        page.wait(lambda: page.js("!document.querySelector('#handler-box')"), 10, "荷役の一括")
        assert page.js("S.rows.filter(r => r.handler === '運転者').length") == 5
        assert page.js("S.res.by_partner[0].handling_fee") > 0
        t2 = page.js("draftText(S.res.by_partner[0])")
        assert "運転者の荷役は合計" in t2 and "再計算してください" not in t2 and "（場所の記入なし）" not in t2
        # 荷主等の都合を いいえ にすると、「含めていません」の文に戻る
        page.js("document.querySelector('#b-fault-no').click()")
        page.wait(lambda: page.js("!document.querySelector('#fault-box')"), 10, "都合の一括")
        assert "渋滞や当社側の遅れによる待ちは含めていません" in page.js("draftText(S.res.by_partner[0])")


def test_sparse_paste_consult_lines_without_place_phrase():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1366, 768) as page:
        _paste(page, PASTE5)
        t = page.js("draftText(S.res.by_partner[0])")
        assert "台に到着した" in t
        assert "場所の記入なし" not in t and "では、8時台" not in t
        assert "\t場所\t" not in t and "場所の記載なし" not in t
        html = page.js("document.querySelector('#wait-doc').textContent")
        assert "場所の記入なし" not in html


def test_full_example_email_keeps_its_wording():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1366, 768) as page:
        page.goto(WAITING.as_uri() + "?example=1&selftest=0")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "書面")
        t = page.js("draftText(S.res.by_partner[0])")
        assert "渋滞や当社側の遅れによる待ちは含めていません" in t and "運転者の荷役は合計" in t
        assert "再計算してください" not in t and "場所の記入なし" not in t and "では、8時台に到着した" in t
        assert page.js("!document.querySelector('#fault-box') && !document.querySelector('#handler-box')")


# 本文の合計・1 時間以上の回数は、画面の数字のタイルと添付の記録の表の合計と同じ数え方（2026-11-08）
TOTALS_JS = r"""(function(){
  const p = S.res.by_partner.find((x) => x.partner === S.partner) || S.res.by_partner[0];
  const body = draftText(p);
  const m = body.match(/合計で((?:\d+時間)?(?:\d+分)?)(?:（[^）]*）)?でした/), o = body.match(/1時間以上お待ちした回が(\d+)回/);
  const tile = document.querySelector('#stats .hero .stat-n').innerText.replace(/\s+/g, '');
  const tr = [...document.querySelectorAll('#wait-doc table.rec tr')].find((x) => x.children[0] && x.children[0].textContent.trim() === '合計');
  const tot = tr ? tr.children : [];
  return {body: m && m[1], over: o ? +o[1] : 0, tile, over_tile: p.over_1h_count, wait: p.wait_minutes,
          rec_total: [...tot].map((c) => c.textContent), rows: 0};
})()"""


def _check_totals(page):
    r = page.js(TOTALS_JS)
    assert r["body"] and r["body"] == (r["tile"][:-2] if r["tile"].endswith("時間0分") else r["tile"]), r
    assert r["over"] == r["over_tile"], r
    assert str(r["wait"]) in r["rec_total"], r   # 添付の記録の表の合計（待機の分）
    return r


def test_example_email_total_matches_tile_and_attached_table():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(WAITING.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "書面")
        r = _check_totals(page)
        assert r["body"] == "11時間22分" and r["wait"] == 682


def test_sparse_paste_email_total_matches_tile_and_attached_table():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _paste(page, PASTE5)
        _check_totals(page)
        page.js("document.querySelector('#b-fault-no').click()")
        page.wait(lambda: page.js("!document.querySelector('#fault-box')"), 10, "都合の一括")
        r = page.js(TOTALS_JS)
        assert r["over"] == r["over_tile"], r
