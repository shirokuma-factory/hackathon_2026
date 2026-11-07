"""画面の目視（2026-11-08、docs/reviews/2026-11-08_画面の目視_荷待ちとドライバー.md）で見つかった不具合の再現と、直したことの固定。
ドライバーの日報の印刷、0 分のときのメール、荷主の入口の見本どうしの照合、荷主に届く文、入口のボタンの文、説明文の自動作成の理由。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
WAITING = ROOT / "web" / "waiting.html"
DRIVER = ROOT / "web" / "driver.html"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _wait_for(page, js, what):
    page.wait(lambda: page.js(js), 30, what)


def _click(page, sel):
    page.js(f"document.querySelector({json.dumps(sel)}).click()")


# ---------------------------------------------------------------- ×1 ドライバーの日報の印刷
def test_driver_print_shows_only_the_report_table_on_one_a4_page():
    _edge_or_skip()
    import base64
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        page.goto(DRIVER.as_uri() + "?x=printvis")
        _wait_for(page, "!!document.querySelector('#sheet')", "ドライバーの 1 枚")
        page.js("""(function(){
          const mk = (id, a, s, e, d) => ({id, date: '10/5', place: '尼崎', arrive: a, start: s, end: e, depart: d, vno: '1234', partner: '○○'});
          localStorage.setItem('driver_records_v1', JSON.stringify([mk('a', '07:50', '09:40', '10:30', '10:40'), mk('b', '08:10', '09:10', '10:00', '10:10')]));
          localStorage.setItem('driver_settings_v1', JSON.stringify({place: '', partner: '', vno: '1234', vclass: '', handler: ''}));
        })()""")
        page.goto(DRIVER.as_uri() + "?x=printvis2")
        _wait_for(page, "!!document.querySelector('#day')", "日の選択")
        page.js("(function(){const s=document.querySelector('#day'); s.value='10/5'; s.dispatchEvent(new Event('input',{bubbles:true}));})()")
        page.send("Emulation.setEmulatedMedia", media="print")
        try:
            seen = page.js("""(function(){
              const vis = (e) => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e); return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
              const bad = [...document.querySelectorAll('button, .side-note, .app-header, #stamps, #copybar, #distribute, .lead')].filter(vis).map((e) => e.id || e.className || e.tagName);
              return {bad, sheet: vis(document.querySelector('#sheet')), head: vis(document.querySelector('.print-head'))};
            })()""")
            assert seen["bad"] == [] and seen["sheet"] and seen["head"], seen
            pdf = page.send("Page.printToPDF", paperWidth=8.27, paperHeight=11.69, printBackground=True, preferCSSPageSize=False)
            data = base64.b64decode(pdf["data"])
            pages = len(re.findall(rb"/Type\s*/Page(?![s\w])", data))
            assert pages == 1, f"印刷が {pages} 枚になります"
        finally:
            page.send("Emulation.setEmulatedMedia", media="")


# ---------------------------------------------------------------- ×4 荷主の入口: 見本どうしの照合
def test_shipper_entry_example_letter_pairs_with_the_sample_register():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        page.goto(WAITING.as_uri() + "?x=cross#g=shipper")
        _wait_for(page, "!!document.querySelector('#b-g-sample')", "見本のボタン")
        _click(page, "#b-g-sample")
        _click(page, "#b-paste")
        _wait_for(page, "!!document.querySelector('#b-cross-ex')", "例の書面のボタン")
        _click(page, "#b-cross-ex")
        _click(page, "#b-cross")
        _wait_for(page, "!!document.querySelector('#cross-result')", "照合の結果")
        text = page.js("document.querySelector('#cross-out').textContent")
        m = re.search(r"同じ回と見られる (\d+) 回のうち、時刻が \d+ 分以上違うのは (\d+) 回", text)
        assert m, text
        assert int(m.group(1)) == 2, text          # 見本の受付簿は 2 行
        assert int(m.group(2)) == 1, text          # 例の書面は 2 行目だけ 25 分遅らせてある
        assert "どの回か決められない行" not in text, text


# ---------------------------------------------------------------- ×2 待ちが 0 分のとき
def test_zero_counted_wait_gives_a_note_instead_of_a_mail_and_the_chart_legend_has_no_max_1():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        page.goto(WAITING.as_uri() + "?x=zero")
        _wait_for(page, "!!document.querySelector('#wait-doc table.rec')", "例の書面")
        assert "最大" in page.js("document.querySelector('#heat').textContent")
        _click(page, "#b-fault-no")
        _wait_for(page, "!!document.querySelector('#no-wait-note')", "0 分の知らせ")
        got = page.js("({note: document.querySelector('#no-wait-note').textContent, mail: !!document.querySelector('#mail-subject'), btn: !!document.querySelector('#b-copydoc'), heat: !!document.querySelector('#heat'), legend: document.querySelector('#partners').textContent.includes('最大 1 分')})")
        assert "荷主の都合で待った時間がないので、相談のメールは作りません" in got["note"], got
        assert not got["mail"] and not got["btn"] and not got["heat"] and not got["legend"], got
        _click(page, "#b-fault-yes")
        _wait_for(page, "!!document.querySelector('#mail-subject')", "メールが戻る")
        assert page.js("!document.querySelector('#no-wait-note')")


# ---------------------------------------------------------------- ×3 ×5 荷主に届く文
def test_attachment_and_copied_text_have_no_clerk_notes_and_point_to_a_real_field():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        page.goto(WAITING.as_uri() + "?x=attach")
        _wait_for(page, "!!document.querySelector('#wait-doc table.rec')", "例の書面")
        page.js("document.querySelector('#doc-attach').open = true")
        note = page.js("document.querySelector('#doc-cross-note').textContent")
        assert "「荷主として使う」の照合の欄" not in note, note
        assert "運送会社から届いた書面と見比べる" in note, note
        # 事務の人向けの文は画面だけ
        page.goto(WAITING.as_uri() + "?x=attach3")
        _wait_for(page, "!!document.querySelector('#wait-doc table.rec')", "例の書面")
        page.js("document.querySelector('#doc-attach').open = true")
        title = page.js("document.querySelector('#doc-pm-title').textContent")
        assert "ページ全体の表とは" not in title and "この相手の行だけ" not in title, title
        assert page.js("getComputedStyle(document.querySelector('#doc-pm-screen')).display") != "none"
        page.send("Emulation.setEmulatedMedia", media="print")
        try:
            assert page.js("getComputedStyle(document.querySelector('#doc-pm-screen')).display") == "none"
        finally:
            page.send("Emulation.setEmulatedMedia", media="")
        # その欄は荷主の画面に本当にある
        page.goto(WAITING.as_uri() + "?x=attach2#g=shipper")
        _wait_for(page, "!!document.querySelector('#b-g-sample')", "荷主の入口")
        _click(page, "#b-g-sample")
        _click(page, "#b-paste")
        _wait_for(page, "!!document.querySelector('#cross-ta')", "照合の欄")
        assert "運送会社から届いた書面と見比べる" in page.js("document.querySelector('#cross-ta').closest('section, div, form').parentElement.textContent") or \
            "運送会社から届いた書面と見比べる" in page.js("document.body.textContent")


# ---------------------------------------------------------------- ×6 入口のボタン、△ 表の見出し
def test_choice_buttons_separate_title_and_description_and_headers_do_not_break_per_char():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(390, 900) as page:
        page.goto(WAITING.as_uri() + "?x=choose#g")
        _wait_for(page, "!!document.querySelector('.g-choices button small')", "入口のボタン")
        got = page.js("""[...document.querySelectorAll('.g-choices button')].map((b) => { const s = b.querySelector('span').getBoundingClientRect(), m = b.querySelector('small').getBoundingClientRect();
          const rg = document.createRange(); rg.selectNodeContents(b.querySelector('small')); const rects = [...rg.getClientRects()];
          const lastLine = rects.length ? rects[rects.length - 1].width : 0;
          return {gap: m.top - s.bottom, lines: Math.round(m.height / parseFloat(getComputedStyle(b.querySelector('small')).lineHeight || 16)), lastLine}; })""")
        for g in got:
            assert g["gap"] >= 0, got                    # 題の下に説明が来る（同じ行に続かない）
            assert g["lastLine"] >= 24, got              # 最後の 1 字だけが次の行に落ちない
    with cw.edge_page(1100, 900) as page:
        page.goto(WAITING.as_uri() + "?x=hdr")
        _wait_for(page, "!!document.querySelector('#hotspots')", "上位 5 の表")
        h = page.js("[...document.querySelectorAll('#hotspots th')].map((t) => [t.textContent, Math.round(t.getBoundingClientRect().height)])")
        kai = [x for x in h if x[0] == "回数"][0]
        assert kai[1] < 60, h                              # 1 字ずつ縦に折れていない
        heads = page.js("[...document.querySelectorAll('#hotspots th, .par th')].map((t) => t.textContent)")
        assert "請求の目安" not in "".join(heads) and "請求の目安（円）" not in "".join(heads), heads
        assert "参考額" in "".join(heads)


# ---------------------------------------------------------------- ×7 説明文の自動作成の理由
def test_ai_explain_unavailable_reason_has_no_env_name():
    js = (ROOT / "web" / "ai_explain.js").read_text(encoding="utf-8")
    assert "AI_EXPLAIN_ENABLED" not in js
    assert "この画面では説明文の自動作成を止めています" in js


# ---------------------------------------------------------------- △ 受付が到着より前、コピーする表の荷役終了
def test_reception_before_arrival_warns_and_copied_table_keeps_end_column():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        page.goto(WAITING.as_uri() + "?x=order")
        _wait_for(page, "!!document.querySelector('#wait-doc table.rec')", "例の書面")
        page.js("""(function(){ document.querySelector('#paste').value = %s; document.querySelector('#b-paste').click(); })()""" % json.dumps(
            "日付\t相手\t到着\t受付\t荷役開始\t荷役終了\n11/10\t○○\t13:05\t7:41\t14:40\t15:10\n", ensure_ascii=False))
        _wait_for(page, "document.body.textContent.includes('より前です')", "入れ違いの知らせ")
        assert "受付の時刻 07:41 が到着 13:05 より前です" in page.js("document.body.textContent")
        # コピーする本文に、時刻のある荷役終了の列が入る
        text = page.js("draftText(S.res.by_partner[0])")
        assert "荷役終了" in text and "15:10" in text, text
    with cw.edge_page(400, 900) as page:
        page.goto(DRIVER.as_uri() + "?x=order")
        _wait_for(page, "!!document.querySelector('#order-warn')", "ドライバーの 1 枚")
        page.js("""(function(){ S.cur.arrive = '13:05'; S.cur.reception = '07:41'; renderStamps(); })()""")
        assert page.js("!document.querySelector('#order-warn').hidden")
        assert "より前です" in page.js("document.querySelector('#order-warn').textContent")
