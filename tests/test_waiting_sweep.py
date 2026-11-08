"""公開版のどぶさらい（荷待ちとドライバー）2026-11-08 の指摘への直し。
W-01 荷主の画面で、書面に書かれた額の合計を入れても、自社の額（受付簿の時刻から数えた額）は変わらない。
W-02 返信の文が日本語として成り立つ。W-04 荷主ごとの表に待機の合計が出る。W-08 自社の画面に「貴社」と書かない。
W-09 受付の時刻つきの記録は、表に受付の列を足し、注記を重ねない。W-14・W-15 ドライバーの一覧: 消したあと元に戻せる、保存した時刻を直せる。
W-12 予約の枠の案の文。W-20 空欄のままコピーしようとすると知らせる。W-21 CSV のファイル名。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
WAITING = ROOT / "web" / "waiting.html"
DRIVER = ROOT / "web" / "driver.html"

LOG = "受付日\t運送会社\t施設\t車番\t受付時刻\t入場\n2026/10/5\tA運輸\t江東区\t1234\t8:00\t10:35\n2026/10/6\tA運輸\t江東区\t5678\t8:10\t9:00\n"
DOC = "日付  車番  到着  荷役開始  荷役終了\n2026/10/5  1234  7:30  11:20  11:50\n2026/10/6  5678  8:10  9:00  9:30\n"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _wait(page, expr, what):
    page.wait(lambda: page.js(expr), 30, what)


def _shipper_cross(page, doc, amount=None):
    page.goto(WAITING.as_uri() + "?x=sweep")
    _wait(page, "!!document.querySelector('#b-paste')", "ページ")
    page.js("document.querySelector('#b-clear').click()")
    page.js("document.querySelector('#paste').value = " + json.dumps(LOG, ensure_ascii=False))
    page.js("document.querySelector('#b-paste').click()")
    _wait(page, "!!document.querySelector('#cross-ta')", "照合の欄")
    page.js("document.querySelector('#cross-ta').value = " + json.dumps(doc, ensure_ascii=False))
    if amount is not None:
        page.js("(function(){ const e = document.querySelector('#cross-doc-amount'); e.value = '%s'; e.dispatchEvent(new Event('change', {bubbles: true})); })()" % amount)
    page.js("document.querySelector('#b-cross').click()")


def test_w01_document_total_does_not_replace_own_amount():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        _shipper_cross(page, DOC)
        _wait(page, "!!document.querySelector('#cross-recalc')", "数え直した額")
        base = page.js("({own: S.cross.recalc.own, carrier: S.cross.recalc.carrier})")
        assert base["own"] < base["carrier"]
        _shipper_cross(page, DOC, amount="9000")
        _wait(page, "!!document.querySelector('#cross-recalc') && S.cross.doc_amount === 9000", "手で入れた額")
        out = page.js("({own: S.cross.recalc.own, carrier: S.cross.recalc.carrier, doc: S.cross.recalc.doc, box: document.querySelector('#cross-recalc').textContent, reply: document.querySelector('#reply-ta').value})")
        assert out["own"] == base["own"] and out["carrier"] == base["carrier"], (base, out)   # 書面の合計 9000 円で、自社の額が変わらない
        assert out["doc"] == 9000 and "9,000 円" in out["reply"] and "差としては扱っていません" in out["reply"]
        assert f"{base['own']:,} 円" in out["reply"] and "9,000 円（税抜）" not in out["reply"]
        assert "9,000 円" in out["box"] and "この比べ方には使っていません" in out["box"]


def test_w02_reply_text_reads_as_japanese():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        _shipper_cross(page, DOC)
        _wait(page, "!!S.cross", "照合")
        same = page.js("""replyText({pairs: [], key_counts: {place: 0, time: 0}, limit: 15, differ: [], only_carrier: [],
            recalc: {carrier: 5280, own: 5280, diff: 0, doc: null}})""")
        assert "より 0 円" not in same and "（同じ額）" not in same
        assert "どちらも同じ額です。" in same and "5,280 円" in same
        less = page.js("""replyText({pairs: [], key_counts: {place: 0, time: 0}, limit: 15, differ: [], only_carrier: [],
            recalc: {carrier: 5280, own: 4000, diff: 1280, doc: null}})""")
        assert "当方の額は書面より 1,280 円少なくなります。" in less


def test_w04_partner_table_shows_wait_total_and_calls_party_shipper():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(WAITING.as_uri() + "?x=sweep4")
        _wait(page, "!!document.querySelector('#partners table.par')", "表")
        out = page.js("""(function(){
          const th = [...document.querySelectorAll('#partners table.par')[0].querySelectorAll('th')].filter((t) => t.offsetParent !== null).map((t) => t.textContent);
          const h2 = [...document.querySelectorAll('#partners h2')].map((e) => e.childNodes[0].textContent);
          const hs = [...document.querySelectorAll('#hotspots th')].map((t) => [t.textContent, getComputedStyle(t).textAlign]);
          return {th, h2, hs, text: document.body.textContent};
        })()""")
        assert "待機の合計" in out["th"] and "運転者の荷役の合計" not in out["th"], out["th"]
        assert out["th"][0] == "荷主" and "荷主ごと" in out["h2"] and "依頼元ごと" not in out["h2"]
        aligns = dict(out["hs"])
        assert aligns["場所"] == "left" and aligns["荷主"] == "left" and aligns["回数"] == "right"
        assert "貴社の書面のメール" not in out["text"]
        assert "全部の荷主を合わせると" in out["text"] and "全部の荷主を合わせた数字です" in out["text"]


def test_w09_reception_column_and_deduped_notes():
    _edge_or_skip()
    import capture_web as cw
    paste = ("日付\t契約の相手\t場所\t車種\t到着\t受付\t荷役開始\t荷役終了\t荷役をした人\n"
             "2026/10/5\tA商事\t江東DC\t中型\t7:50\t8:00\t10:00\t10:30\t運転者\n"
             "2026/10/6\tA商事\t江東DC\t中型\t7:55\t8:00\t10:00\t10:30\t運転者\n")
    with cw.edge_page(1280, 1000) as page:
        page.goto(WAITING.as_uri() + "?x=sweep9")
        _wait(page, "!!document.querySelector('#b-paste')", "ページ")
        page.js("document.querySelector('#b-clear').click()")
        page.js("document.querySelector('#paste').value = " + json.dumps(paste, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        _wait(page, "!!document.querySelector('#wait-doc table.rec')", "書面")
        out = page.js("""({head: [...document.querySelectorAll('#wait-doc table.rec')[0].querySelectorAll('th')].map((t) => t.textContent),
            notes: [...document.querySelectorAll('#rec-notes li')].map((l) => l.textContent), basis: (document.querySelector('#rec-basis') || {}).textContent || '',
            text: draftText(S.res.by_partner[0])})""")
        assert "受付" in out["head"] and out["head"].index("受付") == out["head"].index("到着") + 1
        assert not any("から数えると" in n for n in out["notes"]), out["notes"]
        assert len(out["notes"]) == len({n.split("は、", 1)[-1] for n in out["notes"]})   # 同じ文の注記は 1 つ
        assert "受付の時刻から数えています" in out["basis"] and "受付の時刻から数えています" in out["text"]   # 日本語の直し 2026-11-08


def test_w12_slot_proposal_sentence_has_no_double_node():
    js = (ROOT / "web" / "waiting_calc.js").read_text(encoding="utf-8")
    assert "を超えません。予約の枠を決めても動かす車はないので、枠の提案はありません。" in js
    assert "枠を決めても移す車は出ないので" not in js


def test_w20_w21_blank_placeholders_warn_before_copy_and_csv_name():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(WAITING.as_uri() + "?x=sweep20")
        _wait(page, "!!document.querySelector('#b-copydoc')", "例")
        page.js("window.__copied = []; Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: (t) => { window.__copied.push(t); return Promise.resolve(); }}});")
        page.js("document.querySelector('#b-copydoc').click()")
        assert page.js("window.__copied.length") == 0
        assert "会社名" in page.js("document.querySelector('#copymsg').textContent")
        page.js("document.querySelector('#b-copydoc').click()")
        _wait(page, "window.__copied.length === 1", "2 回目でコピー")
        name = page.js("csvFileName()")
        assert name.startswith("荷待ちの記録_") and name.endswith(".csv") and "waiting_calc" not in name, name
        dates = page.js("csvText().split('\\r\\n').slice(1).filter(Boolean).map((l) => l.split(',')[0])")
        assert dates == sorted(dates)


def test_w14_w15_driver_undo_delete_and_edit_saved_times():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(390, 844) as page:
        page.goto(DRIVER.as_uri() + "?x=sweep14")
        _wait(page, "!!document.querySelector('#sheet')", "ドライバーの 1 枚")
        page.js("""(function(){
          const mk = (id, date, place, a, s) => ({id, date, place, arrive: a, start: s, end: '', depart: '', vno: '1234', partner: '○○'});
          localStorage.setItem('driver_records_v1', JSON.stringify([mk('a', '11/8', '尼崎', '08:10', '09:00'), mk('b', '11/8', '東大阪', '12:44', '13:30')]));
          localStorage.setItem('driver_settings_v1', JSON.stringify({place: '', partner: '', vno: '1234', vclass: '', handler: ''}));
        })()""")
        page.goto(DRIVER.as_uri() + "?x=sweep14b")
        _wait(page, "document.querySelectorAll('#recs [data-del]').length === 2", "2 件")
        page.js("document.querySelector('#recs [data-del=\"a\"]').click()")
        out = page.js("({n: document.querySelectorAll('#recs [data-del]').length, undo: !document.querySelector('#undo-bar').hidden, msg: document.querySelector('#msg').textContent})")
        assert out["n"] == 1 and out["undo"] and out["msg"] == ""
        page.js("document.querySelector('#b-undo').click()")
        out = page.js("({ids: [...document.querySelectorAll('#recs [data-del]')].map((b) => b.dataset.del), stored: JSON.parse(localStorage.getItem('driver_records_v1')).map((r) => r.id)})")
        assert out["ids"] == ["a", "b"] and out["stored"] == ["a", "b"]
        # 保存した記録の時刻を直す
        page.js("document.querySelector('#recs [data-edit=\"b\"]').click()")
        page.js("(function(){ const i = document.querySelector('[data-rtime=\"start\"][data-rid=\"b\"]'); i.value = '14:00'; i.dispatchEvent(new Event('input', {bubbles: true})); i.dispatchEvent(new Event('change', {bubbles: true})); })()")
        out = page.js("({start: JSON.parse(localStorage.getItem('driver_records_v1')).find((r) => r.id === 'b').start, sheet: document.querySelector('#sheet-body').textContent})")
        assert out["start"] == "14:00" and "14:00" in out["sheet"] and "76" in out["sheet"]   # 12:44 から 14:00 まで 76 分
