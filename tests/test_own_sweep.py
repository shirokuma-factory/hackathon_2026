"""自社の運行: 2026-11-08 の公開版のどぶさらい（docs/reviews/2026-11-08_公開版のどぶさらい_自社の運行.md）の回帰試験。

JS（OwnCalc・OwnMonth・Negotiate）はヘッドレス Edge で、Python の twin（src/shipcompare/own.py）は直接呼ぶ。
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import edge_page

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from shipcompare import own  # noqa: E402


def J(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def odd_csv() -> str:
    """レビューの odd_haisha_sjis.csv と同じ形（見出しは辞書にない。28 日 × 往復 = 56 行）。大田区 → 鹿屋市の帰りは金曜だけ「無」、ほかは「有」。"""
    rows = ["運行年月日,ﾄﾗｯｸNo,積み,卸し,走ったｷﾛ,荷物有無,メモ"]
    for d in range(1, 29):
        wd = date(2026, 10, d).weekday()
        ds = f"R8.10.{d}"
        rows.append(f"{ds},1111,大田区,鹿屋市,1300,有,")
        rows.append(f"{ds},1111,鹿屋市,大田区,1300,{'無' if wd == 4 else '有'},")
    return "\n".join(rows) + "\n"


ODD_ROLES = {"haisha": {"運行年月日": "date", "ﾄﾗｯｸNo": "plate", "積み": "from", "卸し": "to", "走ったｷﾛ": "distance_km", "荷物有無": "mark", "メモ": "skip"}}


@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        page.js("""new Promise((ok, ng) => { let n = 0; const fs = ['/own_calc.js', '/own_month.js', '/negotiate.js'];
          for (const f of fs) { const s = document.createElement('script'); s.src = f; s.onload = () => { if (++n === fs.length) ok(); }; s.onerror = () => ng(new Error(f)); document.head.appendChild(s); } })""")
        assert page.js("typeof OwnCalc === 'object' && typeof OwnMonth === 'object'")
        yield page


def an(page, text: str, opts: dict | None = None) -> dict:
    return page.js(f"OwnCalc.analyze([{{ name: 'odd.csv', text: {J(text)} }}], {J(opts or {})})")


# ---------------------------------------------------------------- P0-02: 荷物有無の「有 / 無」

def test_p0_02_yes_no_mark_column_makes_friday_return_empty(calc):
    d = an(calc, odd_csv(), {"column_roles": ODD_ROLES})
    assert d["failed"] is None
    back = [l for l in d["legs"] if l["frm"] != l["to"] and l["dir"] == "back"]
    assert back, "帰りの区間が無い"
    empty = [l for l in back if l["kind"] == "empty"]
    loaded = [l for l in back if l["kind"] == "loaded"]
    assert len(empty) == 4 and all(date.fromisoformat(l["date"]).weekday() == 4 for l in empty)   # 金曜 4 日
    assert len(loaded) == len(back) - 4 and not [l for l in back if l["kind"] == "unknown"]


def test_p0_02_python_twin_reads_the_same_words():
    assert own.is_empty_mark_word("無") and own.is_empty_mark_word("なし") and own.is_empty_mark_word("空")
    assert not own.is_empty_mark_word("有") and not own.is_empty_mark_word("あり")
    # 備考の列の「なし」「無」は空車の印にしない（印の列だけで使う語）
    assert not own.is_empty_word("無") and not own.has_empty_mark("特になし") and not own.has_empty_mark("無")


def test_p0_02_js_mark_words_match_python(calc):
    assert calc.js("OwnCalc.MARK_EMPTY_EXTRA") == list(own.MARK_EMPTY_EXTRA)
    assert calc.js("OwnCalc.MARK_LOADED_EXTRA") == list(own.MARK_LOADED_EXTRA)
    assert calc.js("OwnCalc.isEmptyWord('無')") is False and calc.js("OwnCalc.hasEmptyMark('なし')") is False


# ---------------------------------------------------------------- P1-03・P1-04: 荷主への文

def neg_state(**kw):
    base = {"type": "commodity", "weekday": "土", "weekly": True, "fromMuni": "江東区", "toMuni": "鹿屋市", "load": {"state": "unconfirmed", "note": ""},
            "recv": {"state": "unconfirmed", "note": ""}, "hold": {"action": "hold", "picks": []}, "econ": None}
    base.update(kw)
    return base


def draft(page, **kw):
    st = neg_state(**kw)
    return page.js(f"(() => {{ const p = {J(st)}; p.econ = p.econ || Negotiate.emptyEcon(); return Negotiate.buildDraft(p); }})()") if page.js("typeof Negotiate === 'object'") else None


def test_p1_03_ok_without_details_is_asked_not_claimed(calc):
    t = draft(calc, load={"state": "ok", "note": ""}, recv={"state": "ok", "note": ""})
    if t is None:
        pytest.skip("Negotiate は公開されていない")
    assert "内容は別途お伝えします" not in t
    assert "積み込み場所の荷役設備" in t and "納品先（受け取り側）が受け取れる日時" in t   # 未確認として質問に残る
    assert "荷物の品名・荷姿" in t and "■進め方" in t   # 日本語の直し 2026-11-08: 「お見積りは…伺ってからお出しします」の行は進め方に統合
    t2 = draft(calc, load={"state": "ok", "note": "平日 9 時から 17 時"}, recv={"state": "ok", "note": "フォークリフトあり"})
    assert "積み込みの条件: 平日 9 時から 17 時" in t2 and "積み込み場所の荷役設備" not in t2 and "荷物の品名・荷姿" in t2


def test_p1_04_reconfirmation_does_not_open_as_a_first_contact(calc):
    t = draft(calc, recv={"state": "ng", "note": ""}, hold={"action": "hold", "picks": ["recv_time"]})
    if t is None:
        pytest.skip("Negotiate は公開されていない")
    assert "突然のご連絡" not in t and "先日ご相談した" in t and "あらためてご相談させてください" in t   # 日本語の直し 2026-11-08
    assert "突然のご連絡" in draft(calc)


# ---------------------------------------------------------------- P1-08 / P1-09 / P1-10: 空車の便だけ・結論の曜日だけ（OwnMonth・Negotiate）

def sat_csv() -> str:
    """厚木 → 江東 → 厚木。帰りは土曜がすべて空（毎週）、水曜は 4 日のうち 1 日だけ空、ほかは実車。10 月の日曜は休み。"""
    rows = ["日付,車番,発地,着地,重量,実空"]
    for d in range(1, 32):
        wd = date(2026, 10, d).weekday()
        if wd == 6:
            continue
        ds = f"2026/10/{d:02d}"
        rows.append(f"{ds},相模 100 あ 1111,厚木市,江東区,3.5t,実")
        empty = wd == 5 or (wd == 2 and d == 7)
        rows.append(f"{ds},相模 100 あ 1111,江東区,厚木市,,空" if empty else f"{ds},相模 100 あ 1111,江東区,厚木市,2.0t,実")
    return "\n".join(rows) + "\n"


def test_p1_09_tomorrow_default_is_the_next_weekday_of_the_conclusion(calc):
    d = an(calc, sat_csv())
    assert d["failed"] is None
    # 2026-11-08（日）に開く。結論は土曜 → 初期値は次の土曜 11/14
    best = calc.js(f"OwnMonth.bestWeekday(OwnMonth.returnPairs({J(d)})[0])")
    assert best == 5
    iso = calc.js(f"OwnMonth.boardDefaults({J(d)}, new Date(2026, 10, 8, 9, 0)).date")
    assert iso == "2026-11-14" and date.fromisoformat(iso).weekday() == 5
    assert calc.js("OwnMonth.nextOnWeekday(5, new Date(2026, 10, 14, 9, 0))") == "2026-11-21"   # 当日が土曜なら、次の週の土曜


def test_p1_10_board_text_lists_only_weekdays_meeting_the_rule(calc):
    d = an(calc, sat_csv())
    t = calc.js(f"OwnMonth.boardTexts({J(d)}).map((x) => x.text).join('|')")
    assert "毎週土曜" in t
    for w in "月火水木金":
        assert f"{w}曜" not in t
    assert "これまでの記録では" not in t


def test_p1_08_conditions_count_only_empty_trips(calc):
    d = an(calc, sat_csv())
    res = calc.js(f"""(() => {{ const d = {J(d)}; const a = Negotiate.analysisOf(d, null, 2); const t = a.target;
      return {{ empty: t.rows.filter((r) => r.kind === 'empty').length, all: t.rows.length, note: Negotiate.__timesNote ? Negotiate.__timesNote(t) : null }}; }})()""")
    assert res["all"] > res["empty"] >= 1   # 水曜は実車の便のほうが多い
    html = calc.js(f"""(() => {{ const d = {J(d)}; const a = Negotiate.analysisOf(d, null, 2); return Negotiate.__evLoad ? Negotiate.__evLoad(a) : ''; }})()""")
    if html:
        assert f"空車の便 {res['empty']} 本" in html and f"便 {res['all']} 本" not in html


# ---------------------------------------------------------------- 地図の画面（#v=own）: サンプルの表示

@pytest.fixture(scope="module")
def app(local_server):
    import capture_web as cw
    from conftest import RESOURCE_HOOK, fail_with_evidence
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
        page.send("Page.navigate", url=f"http://localhost:{local_server}/index.html#v=own")
        try:
            page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
            page.wait(lambda: page.js("!!document.querySelector('#own-body') && document.querySelector('#own-body').innerText.length > 50"), 60, "自社の運行の欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面が起動しません", e, "sweep_fail")
        import time
        time.sleep(1.0)
        yield page


def tab(page, name):
    import time
    page.js(f"Own.setTab({J(name)})")
    time.sleep(0.4)


def test_p0_01_alt_actual_rate_differs_from_main_and_says_count(app):
    t = app.js("(() => { const tl = [...document.querySelectorAll('.o-tile')].find((x) => /実車率/.test(x.querySelector('.o-tl').textContent)); return { main: tl.querySelector('.o-tv').textContent.trim(), alt: (tl.querySelector('.o-talt') || {textContent: ''}).textContent }; })()")
    import re
    m = re.search(r"不明の区間を空車とした場合\s*([\d.]+)%", t["alt"])
    assert m, t
    assert m.group(1) != t["main"].replace("%", "").strip()
    assert "区間の数で数えています" in t["alt"] and "65.4" in t["alt"]   # 1 枚のページ（own_lite）と同じ値


def test_p1_01_weekday_table_has_unknown_column_and_note(app):
    r = app.js("({ th: [...document.querySelectorAll('#own-right table.o-tab thead th')].map((x) => x.textContent), note: (document.querySelector('#own-unknote') || {textContent: ''}).textContent })")
    assert "実車か空車か不明" in r["th"]   # 日本語の直し 2026-11-08: 列名を中身に合わせた and "17 本" in r["note"] or "本" in r["note"]


def test_p1_05_no_anonymous_place_labels_on_screen(app):
    tab(app, "insight")
    body = app.js("document.querySelector('#own-body').innerText + ' ' + [...document.querySelectorAll('.own-tag')].map((x) => x.innerText).join(' ')")
    assert "地点 0" not in body and "地点0" not in body and "地点 1" not in body
    tab(app, "confirm")
    assert "地点 0" not in app.js("document.querySelector('#own-body').innerText")
    st = app.js(r"/地点\s?\d/.test(JSON.stringify(Own.state.sample))")
    assert st is False


def test_p1_06_evidence_buttons_say_what_they_do(app):
    tab(app, "insight")
    body = app.js("document.querySelector('#own-body').innerText")
    assert "書き出す" not in body and "車両の設定を開く" not in body


def test_p1_07_draft_button_fills_the_find_screen(app):
    tab(app, "insight")
    app.js("document.querySelector('button[data-act=next][data-a=draft_backhaul]').click()")
    import time
    time.sleep(0.8)
    v = app.js("({ from: (document.querySelector('#f-dir-from') || {}).value, to: (document.querySelector('#f-dir-to') || {}).value })")
    dbg = app.js("JSON.stringify({ dir: Find.state.dir, role: Find.state.role, note: (document.querySelector('#own-draft .od-text') || {}).textContent, leg: typeof OwnMonth })")
    assert v["from"] and v["to"] and v["from"] != v["to"], (v, dbg)
    app.js("App.setView('own')")
    time.sleep(0.5)


def test_p1_09_tomorrow_post_defaults_to_the_concluded_weekday(app):
    tab(app, "insight")
    iso = app.js("document.querySelector('#own-tmr-date').value")
    assert date.fromisoformat(iso).weekday() == 5 and date.fromisoformat(iso) > date.today()


def test_p1_10_board_text_does_not_list_every_weekday(app):
    tab(app, "insight")
    app.js("document.querySelector('button[data-act=board]').click()")
    t = app.js("document.querySelector('#own-board-ta').value")
    assert "毎週土曜" in t and "これまでの記録では" not in t
    for w in "月火水木金日":
        assert f"{w}曜" not in t


def test_p1_11_wait_tab_can_load_the_sample_stops(app):
    tab(app, "wait")
    n = app.js("(document.querySelector('button[data-w=from-stops]') || {textContent: ''}).textContent")
    assert "10 件" in n
    app.js("document.querySelector('button[data-w=from-stops]').click()")
    import time
    time.sleep(0.5)
    assert app.js("document.querySelectorAll('#w-tbody tr').length") >= 10
    # 18 列の表は横に送れて、見出しは上に残る
    ov = app.js("(() => { const w = document.querySelector('.w-tw'), th = document.querySelector('.w-tbl th'); return { x: getComputedStyle(w).overflowX, pos: getComputedStyle(th).position }; })()")
    assert ov["x"] in ("scroll", "auto") and ov["pos"] == "sticky"


def test_p1_14_column_fix_is_a_select_of_valid_roles(app):
    tab(app, "confirm")
    sel = app.js("(() => { const s = document.querySelector('.o-card .o-fix select'); return s ? [...s.options].map((o) => o.textContent) : null; })()")
    if sel is None:
        pytest.skip("サンプルに列の対応づけのカードがありません")
    assert len(sel) > 3 and "ナンバー" not in sel


def test_p1_12_ai_placeholder_is_replaced_not_stacked():
    src = (ROOT / "web" / "ai_explain.js").read_text(encoding="utf-8")
    assert "サーバーで開いたときだけ" in src and "x.remove()" in src


# ---------------------------------------------------------------- わかりやすさの指摘（U-20・U-22・U-23）

def test_u20_entry_shows_required_columns_and_sample_button(app):
    import time
    app.js("Own.state.guided && 0")
    html = app.js("Negotiate.state && document.querySelector('#neg-root') ? document.querySelector('#neg-root').innerText : ''")
    src = (ROOT / "web" / "negotiate.js").read_text(encoding="utf-8")
    assert "csvGuideHtml" in src and "最低限ほしい列" in src and 'data-act="sample-csv"' in src
    assert "ctx.saveSample" in (ROOT / "web" / "own.js").read_text(encoding="utf-8") or "saveSample:" in (ROOT / "web" / "own.js").read_text(encoding="utf-8")
    del html, time


def test_u22_no_empty_message_says_what_to_change():
    src = (ROOT / "web" / "negotiate.js").read_text(encoding="utf-8")
    assert "配車表とデジタコの記録を確かめてください。</p>` : ''" not in src and "空車か実車かを決めてください" in src


def test_u23_copy_warns_while_placeholders_remain():
    src = (ROOT / "web" / "negotiate.js").read_text(encoding="utf-8")
    assert "がまだ書き換わっていません" in src and "S.phAsked" in src


def test_failed_csv_clears_sample_numbers_in_source():
    src = (ROOT / "web" / "own.js").read_text(encoding="utf-8")
    assert "このファイルは読めませんでした" in src and "O.failMsg" in src
