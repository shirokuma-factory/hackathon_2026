"""公開版のどぶさらい P0 探し（2026-11-08）の入力の守りの試験。
A1 荷待ち: 同じ貼り付け・同じ CSV をもう一度入れても、聞かずに倍にならない（足さない／足す）。
compare_lite: 読めない行があっても読める行で計算する・出荷の記録の二重押し・1 件消す・全部消すは 2 回押し・契約単価を入れたときの宅配の欄・「12万円」。
driver: 時刻の逆転と暦にない日付は保存しない。own_lite: 大型の最大積載量の範囲、空きの重さと日付の確かめ。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from conftest import edge_page  # noqa: E402

WAITING = ROOT / "web" / "waiting.html"
LITE = ROOT / "web" / "compare_lite.html"
DRIVER = ROOT / "web" / "driver.html"
OWN = ROOT / "web" / "own_lite.html"

TABLE = "日付\t場所\t到着\t荷役開始\t荷役終了\t契約の相手\n10/5\t江東倉庫\t8:50\t9:35\t9:57\t○○商事\n10/6\t江東倉庫\t9:10\t9:50\t10:12\t○○商事\n"


def j(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def setv(page, sel: str, value: str, ev: str = "input") -> None:
    page.js(f"(function () {{ const e = document.querySelector({j(sel)}); e.value = {j(value)}; e.dispatchEvent(new Event({j(ev)}, {{bubbles: true}})); }})()")


def click(page, sel: str) -> None:
    page.js(f"document.querySelector({j(sel)}).click()")


# ---------------------------------------------------------------- 荷待ち A1
def _filled_rows(page) -> int:
    return page.js("S.rows.filter((r) => String(r.date).trim() !== '').length")


def _waiting(page):
    page.goto(WAITING.as_uri() + "?x=guard")
    page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "ページ")
    click(page, "#b-clear")


def test_waiting_same_paste_twice_asks_and_default_does_not_add():
    with edge_page(1280, 1000, webgl=False) as page:
        _waiting(page)
        setv(page, "#paste", TABLE)
        click(page, "#b-paste")
        assert _filled_rows(page) == 2
        click(page, "#b-paste")   # 同じ表をもう一度
        assert page.js("!!document.querySelector('#dup-skip') && !!document.querySelector('#dup-add')")
        assert "同じ記録がもう入っています（2 行）" in page.js("document.querySelector('#paste-msg').textContent")
        assert _filled_rows(page) == 2, "聞いている間は表を変えない"
        click(page, "#dup-skip")
        assert _filled_rows(page) == 2
        assert "同じ記録 2 行は足していません" in page.js("document.querySelector('#paste-msg').textContent")
        # 足す を選んだときだけ倍になる
        click(page, "#b-paste")
        click(page, "#dup-add")
        assert _filled_rows(page) == 4


def test_waiting_same_csv_twice_asks_and_default_does_not_add():
    with edge_page(1280, 1000, webgl=False) as page:
        _waiting(page)
        setv(page, "#paste", TABLE)
        click(page, "#b-paste")
        assert _filled_rows(page) == 2
        csv = page.js("csvText()")
        setv(page, "#csvpaste", csv)
        click(page, "#b-csvpaste")
        assert page.js("!!document.querySelector('#dup-skip')"), page.js("document.querySelector('#csvpaste-msg').textContent")
        assert "同じ記録がもう入っています（2 行）" in page.js("document.querySelector('#csvpaste-msg').textContent")
        assert _filled_rows(page) == 2
        click(page, "#dup-skip")
        assert _filled_rows(page) == 2
        click(page, "#b-csvpaste")
        click(page, "#dup-add")
        assert _filled_rows(page) == 4


def test_waiting_different_rows_are_added_without_asking():
    with edge_page(1280, 1000, webgl=False) as page:
        _waiting(page)
        setv(page, "#paste", TABLE)
        click(page, "#b-paste")
        setv(page, "#paste", "日付\t場所\t到着\t荷役開始\t荷役終了\t契約の相手\n10/7\t江東倉庫\t8:30\t10:05\t10:30\t○○商事\n")
        click(page, "#b-paste")
        assert not page.js("!!document.querySelector('#dup-skip')")
        assert _filled_rows(page) == 3


# ---------------------------------------------------------------- compare_lite
def _lite(page):
    page.goto(LITE.as_uri() + "?x=guard")
    page.wait(lambda: page.js("!!document.querySelector('#b-add') && typeof render === 'function'"), 30, "ページ")
    page.js("localStorage.clear()")


def test_lite_invalid_row_does_not_hide_valid_row():
    with edge_page(1280, 1000, webgl=False) as page:
        _lite(page)
        page.js("S.rows = [{n: '1.5', l: '30', w: '20', h: '10', kg: '5'}, {n: '2', l: '40', w: '30', h: '20', kg: '8'}]; S.exampleAuto = false; renderRows(); render();")
        assert page.js("S.res.totals && S.res.totals.count") == 2
        assert "荷物が入っていません" not in page.js("document.querySelector('#cargo-line').textContent")
        assert "読めない 1 行" in page.js("document.querySelector('#cargo-line').textContent")
        txt = page.js("document.querySelector('#row-errs').textContent")
        assert "1 行目" in txt and "個数は 1 以上の整数" in txt
        # 表の中の 1 行しかなく、それが読めないときは、読める行がないと言う
        page.js("S.rows = [{n: '1.5', l: '30', w: '20', h: '10', kg: '5'}]; renderRows(); render();")
        assert page.js("S.res.totals") is None
        assert "1 行目" in page.js("document.querySelector('#row-errs').textContent")
        assert "荷物が入っていません" not in page.js("document.querySelector('#cargo-line').textContent")


def test_lite_history_double_click_delete_one_and_delete_all_confirm():
    with edge_page(1280, 1000, webgl=False) as page:
        _lite(page)
        page.js("document.querySelector('#hist-d').open = true")
        setv(page, "#h-date", "2026-11-05", "change")
        setv(page, "#h-amt", "98000")
        click(page, "#b-hadd")
        click(page, "#b-hadd")   # 続けて 2 回
        assert page.js("loadHistory().length") == 1
        assert "足していません" in page.js("document.querySelector('#h-msg').textContent")
        assert "98,000" in page.js("document.querySelector('#h-month').textContent")
        setv(page, "#h-amt", "12万円")
        click(page, "#b-hadd")
        assert page.js("loadHistory().map((x) => x.amount)") == [98000, 120000]
        # 1 件消す
        click(page, "[data-hdel]")
        assert page.js("loadHistory().length") == 1
        # 全部消すは 2 回押す。1 回目では消えない
        click(page, "#b-hclear")
        assert page.js("loadHistory().length") == 1
        assert "もう一度押してください" in page.js("document.querySelector('#h-msg').textContent")
        click(page, "#b-hclear")
        assert page.js("loadHistory().length") == 0


def test_lite_quote_answer_accepts_man_yen_and_fullwidth():
    with edge_page(1280, 1000, webgl=False) as page:
        _lite(page)
        cases = {"12万円": 120000, "12万": 120000, "1.5万": 15000, "12万5000": 125000, "¥120,000": 120000, "１２０，０００円": 120000, "98000": 98000}
        for raw, want in cases.items():
            setv(page, "#ans-amt-0", raw)
            page.js("renderAnswers()")
            assert page.js("readAnswers()[0].amount") == want, raw
            assert not page.js("!!document.querySelector('#ans-out .errs')"), raw
        for raw in ("0", "-5", "abc", "12万円円円万"):
            setv(page, "#ans-amt-0", raw)
            page.js("renderAnswers()")
            assert page.js("readAnswers()[0].bad") is True, raw
            assert "12万円" in page.js("document.querySelector('#ans-out .errs').textContent")


def test_lite_contract_price_says_courier_example_is_not_in_the_cheapest():
    with edge_page(1280, 1000, webgl=False) as page:
        _lite(page)
        page.js("S.rows = [{n: '3', l: '50', w: '40', h: '30', kg: '20'}]; S.exampleAuto = false; renderRows();")
        setv(page, "#from", "東京都", "change")
        setv(page, "#to", "大阪府", "change")
        setv(page, "#c-r-rate", "100")
        page.js("render()")
        assert page.js("!!document.querySelector('#pub-amount')"), "宅配の例の額が出ていない"
        assert "最安」と額の比べには入れていません" in page.js("(document.querySelector('#pub-excluded') || {}).textContent || ''")


# ---------------------------------------------------------------- driver
def _driver(page):
    page.goto(DRIVER.as_uri() + "?x=guard")
    page.wait(lambda: page.js("!!document.querySelector('#b-save') && typeof saveCurrent === 'function'"), 30, "ページ")
    page.js("localStorage.clear()")


def test_driver_reversed_times_and_bad_dates_are_not_saved():
    with edge_page(400, 900, webgl=False) as page:
        _driver(page)
        # 着 9:00 から荷役開始 8:00 は 23 時間かかったことになる
        page.js("S.cur = {...blankCur(), date: '11/10', arrive: '09:00', start: '08:00'}; renderStamps(); renderList();")
        click(page, "#b-save")
        assert page.js("S.recs.length") == 0
        assert "より前です" in page.js("document.querySelector('#save-err').textContent")
        assert page.js("!document.querySelector('#save-err').hidden")
        # 暦にない日付
        for bad in ("13/45", "2/30", "0/5", "11/31"):
            page.js(f"S.cur = {{...blankCur(), date: {j(bad)}, arrive: '09:00', start: '09:40'}}; renderStamps(); renderList();")
            click(page, "#b-save")
            assert page.js("S.recs.length") == 0, bad
            assert "暦にない日付" in page.js("document.querySelector('#date-err').textContent"), bad
        # 夜中をまたぐ回: 明示しなければ保存しない。明示すれば保存し、LINE の文に余計な語は入れない
        page.js("S.cur = {...blankCur(), date: '11/10', arrive: '23:30', start: '00:20'}; renderStamps(); renderList();")
        click(page, "#b-save")
        assert page.js("S.recs.length") == 0
        assert "夜中の 0 時をまたいだ回" in page.js("document.querySelector('#save-err').textContent")
        click(page, "#overnight")
        click(page, "#b-save")
        assert page.js("S.recs.length") == 1
        assert page.js("S.recs[0].overnight") is None
        assert "overnight" not in page.js("recordLine(S.recs[0])")
        # ふつうの回は保存できる
        page.js("S.cur = {...blankCur(), date: '11/11', arrive: '09:00', start: '09:40', end: '10:05'}; renderStamps(); renderList();")
        click(page, "#b-save")
        assert page.js("S.recs.length") == 2
        assert page.js("document.querySelector('#save-err').hidden")


# ---------------------------------------------------------------- own_lite
def _own(page):
    page.goto(OWN.as_uri())
    page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
    click(page, "[data-act=sample]")
    page.wait(lambda: page.js("!!document.querySelector('[data-kpi=actual_rate]')"), 60, "数字")


def test_own_large_payload_range_and_post_text_guards():
    with edge_page(1280, 1000, webgl=False) as page:
        _own(page)
        if page.js("!!document.querySelector('#large-t')"):
            for bad in ("0", "-5", "1000", "31"):
                before = page.js("OwnLite.state.largeT")
                setv(page, "#large-t", bad, "change")
                assert "0.5〜30 t" in page.js("document.querySelector('#large-t-msg').textContent"), bad
                assert page.js("OwnLite.state.largeT") == before, bad
            setv(page, "#large-t", "13", "change")
            assert page.js("OwnLite.state.largeT") == 13
        # 掲示の文: 2t 車に 50 t の空きは作らない。過ぎた日付も作らない
        setv(page, "[data-tm=cls]", "2t", "change")
        setv(page, "[data-tm=kg]", "50", "change")
        setv(page, "[data-tm=date]", "2030-01-01", "change")
        click(page, "[data-act=tm-make]")
        assert "2t車の最大積載量 2 t を超えています" in page.js("document.querySelector('#tm-err').textContent")
        assert page.js("!document.querySelector('#tm-out')")
        setv(page, "[data-tm=kg]", "2", "change")
        setv(page, "[data-tm=date]", "2020-01-01", "change")
        click(page, "[data-act=tm-make]")
        assert "過ぎた日です" in page.js("document.querySelector('#tm-err').textContent")
        assert page.js("!document.querySelector('#tm-out')")
        setv(page, "[data-tm=date]", "2030-01-01", "change")
        click(page, "[data-act=tm-make]")
        assert page.js("!document.querySelector('#tm-err')")
        assert "最大 2 t まで" in page.js("document.querySelector('#tm-out').value")
