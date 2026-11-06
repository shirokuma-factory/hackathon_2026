"""SPEC §41 自社の画面（#v=own）を仕事で使えるようにする直し。

- 県内の往復（加古川 ⇄ 姫路・神戸）の配車表が取り込める（地図の線の向きの印が 2 点の線で止まらない）
- 帰り荷 1 便の売上の目安（標準的運賃の距離制。data/tariffs の数と同じ）と、空車の費用の「帰り荷を取っても減らない」の表示
- 求荷求車に貼る文、デジタコの停車から荷待ちの下書き、月の保存と前の月との並び、印刷用の欄
- 列の役割を選ぶ欄、年を仮に置いた表示、日本語の列の理由、用語の説明、24px の欄、400px の表
Edge が無い環境だけ、画面の試験を飛ばす（起動の時間切れは失敗）。
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "own"
EX_FILES = [EXAMPLES / "haisha_2026-10.csv", EXAMPLES / "digitaco_1234.csv", EXAMPLES / "digitaco_5678.csv"]
SHOT_DIR = ROOT / "docs" / "assets"

# 県内の往復: 加古川 ⇄ 姫路（どちらも兵庫県）と 加古川 ⇄ 神戸
INTRA_HAISHA = ("車番,車種,2026/10/1,2026/10/2,2026/10/3,2026/10/4,2026/10/5,2026/10/6\n"
                "1234,大型,加古川→姫路 着5:30 4.6t,姫路→加古川 着8:00 3.2t,加古川→姫路 着5:30 4.0t,姫路→加古川（空）,加古川→姫路 着5:30 5.1t,姫路→加古川（空）\n"
                "5678,大型,加古川→神戸 着6:00 4.6t,神戸→加古川 着9:00（空）,加古川→神戸 着6:00 3.0t,神戸→加古川 着9:00（空）,加古川→神戸 着6:00 4.4t,神戸→加古川（空）\n")

# 11 月の別のファイル（前の月と並べる試験用）
NOV_HAISHA = ("車番,車種,2026/11/2,2026/11/3,2026/11/4,2026/11/5\n"
              "1234,大型,加古川→江東 着5:30 4.6t,江東→加古川（空）,加古川→江東 着5:30 4.0t,江東→加古川 着4:00 3.0t\n"
              "5678,大型,加古川→江東 着5:30 5.0t,江東→加古川（空）,加古川→江東 着5:30 4.1t,江東→加古川（空）\n")


@pytest.fixture(scope="module")
def app(local_server):
    import capture_web as cw
    from conftest import RESOURCE_HOOK
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
        page.send("Page.navigate", url=f"http://localhost:{local_server}/index.html#v=own")
        try:
            page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
            page.wait(lambda: page.js("!!document.querySelector('#own-file')"), 60, "ファイルを落とす欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面が起動しません", e, "own_v41_fail")
        time.sleep(2.0)
        yield page


def _set_files(page, paths):
    doc = page.send("DOM.getDocument", depth=1)
    nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#own-file")["nodeId"]
    page.send("DOM.setFileInputFiles", files=[str(p) for p in paths], nodeId=nid)


def _src(page) -> str:
    return page.js("(document.querySelector('#own-src') || {textContent: ''}).textContent")


def _to_sample(page):
    page.js("App.setView('own')")
    if page.js("Own.state.mode") != "sample":
        page.js("document.querySelector('#own-src [data-act=reset]').click()")
    page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _import(page, paths, want="自社のファイル"):
    _to_sample(page)
    page.js("Own.setTab('import')")
    _set_files(page, paths)
    page.wait(lambda: want in _src(page), 120, "取り込み")
    time.sleep(0.5)


def _import_examples(page):
    _import(page, EX_FILES, "自社のファイル: 3 件")


def _shot(page, name, quality=60):
    import base64
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=quality)
    (SHOT_DIR / name).write_bytes(base64.b64decode(r["data"]))


# ---------------------------------------------------------------- 標準的運賃の表の照合（Python の式と同じ答え）

def _py_fare(km: float, cls: str, region: str) -> int | None:
    t = json.loads((ROOT / "data" / "tariffs" / f"mlit_standard_fare_{region}.json").read_text(encoding="utf-8"))
    if km < t["base_km"]:
        return None
    v = next(x for x in t["vehicles"] if x["class"] == cls)
    if km <= t["over_500_km"]:
        steps, over = math.ceil((km - t["base_km"]) / t["step_km"]), 0
    else:
        steps = math.ceil((t["over_500_km"] - t["base_km"]) / t["step_km"])
        over = math.ceil((km - t["over_500_km"]) / t["over_500_step_km"])
    return v["base_fare"] + steps * v["add_per_step"] + over * v["add_per_step_over_500"]


def test_fare_table_in_js_matches_the_tariff_json_and_the_python_formula(app):
    page = app
    fare = json.loads(page.js("JSON.stringify(OwnMonth.FARE)"))
    for region in ("kanto", "kinki"):
        t = json.loads((ROOT / "data" / "tariffs" / f"mlit_standard_fare_{region}.json").read_text(encoding="utf-8"))
        assert (fare["base_km"], fare["step_km"], fare["over_500_km"], fare["over_500_step_km"]) == (t["base_km"], t["step_km"], t["over_500_km"], t["over_500_step_km"])
        for v in t["vehicles"]:
            assert fare["regions"][region]["v"][v["class"]] == [v["base_fare"], v["add_per_step"], v["add_per_step_over_500"]]
    for km in (200, 200.1, 219.9, 220, 499.9, 500, 500.1, 551.8, 668.0, 1000):
        for cls in ("2t", "4t", "10t"):
            for pref, region in (("東京都", "kanto"), ("大阪府", "kinki")):
                got = page.js(f"(function(){{const f = OwnMonth.fare({km}, '{cls}', '{pref}'); return f && f.amount;}})()")
                assert got == _py_fare(km, cls, region), (km, cls, pref)
    assert page.js("OwnMonth.fare(199.9, '10t', '東京都')") is None  # 200 km 未満は表が無いので出さない


# ---------------------------------------------------------------- 1. 県内の往復（P0）

def test_intra_prefecture_round_trip_imports_and_draws_arrows(app, tmp_path):
    page = app
    page.js("window.__errs = []")
    _import(page, [_write(tmp_path, "haisha_hyogo.csv", INTRA_HAISHA)])
    assert page.js("Own.state.mode") == "import", page.js("Own.state.dropMsg")
    d = json.loads(page.js("JSON.stringify({legs: Own.state.data.legs.length, path: Own.state.data.route.path, marks: Own.state.geo.marks.length, items: Own.state.geo.items.length, base: Own.state.data.places[Own.state.data.base].pref, dock: Own.state.data.places[Own.state.data.dock].pref})"))
    assert d["legs"] >= 10 and d["base"] == d["dock"] == "兵庫県", d
    assert d["path"] is not None and len(d["path"]) == 2, d  # 県内は 2 点の線
    assert d["marks"] >= 3 and d["items"] >= 1, d  # 向きの印が出る
    errs = page.js("(window.__errs || []).filter(e => !/favicon|404/.test(String(e)))")
    assert not errs, errs
    _shot(page, "2026-11-06_v41_own_intra_1920.jpg")
    _to_sample(page)


# ---------------------------------------------------------------- 3. 帰り荷 1 便の売上の目安

def test_return_load_revenue_estimate_is_shown_with_formula_and_matches_the_tariff(app):
    page = app
    _import_examples(page)
    page.js("(function(){const e = document.querySelector('#own-perkm'); e.value = '30'; e.dispatchEvent(new Event('input', {bubbles: true}));})()")

    cost = page.js("document.querySelector('#own-cost-out').innerText")
    assert "帰り荷を取っても減らない費用" in cost, cost
    rev = page.js("document.querySelector('#own-rev').innerText")
    assert "帰り荷 1 便の売上の目安" in rev and "目安" in rev and "標準的運賃" in rev and "×" in rev, rev
    d = json.loads(page.js("JSON.stringify(OwnMonth.revenue(Own.state.data))"))
    assert d["cls"] == "10t" and d["trips"] > 0 and d["fare"], d
    assert d["fare"]["regionKey"] == "kanto"  # 帰りの発地（江東区）の運輸局の表
    assert d["fare"]["amount"] == _py_fare(d["km"], "10t", "kanto")
    assert f"{d['fare']['amount']:,} 円" in rev and f"{d['total']:,} 円" in rev, rev
    assert d["fare"]["formula"] in rev
    page.js("(function(){const e = document.querySelector('#own-perkm'); e.value = ''; e.dispatchEvent(new Event('input', {bubbles: true}));})()")

    assert "円" not in page.js("document.querySelector('#own-cost-out').innerText")  # 単価が空のときは、費用の欄に円を出さない
    _to_sample(page)


def test_revenue_is_not_estimated_below_200_km_and_says_so(app, tmp_path):
    page = app
    _import(page, [_write(tmp_path, "haisha_hyogo.csv", INTRA_HAISHA)])
    rev = page.js("(document.querySelector('#own-rev') || {innerText: ''}).innerText")
    assert "200 km" in rev and "目安を出しません" in rev, rev
    _to_sample(page)


# ---------------------------------------------------------------- 4. 求荷求車の文

def test_board_text_has_the_fields_and_leaves_the_contact_blank(app):
    page = app
    _import_examples(page)
    page.js("Own.setTab('insight')")
    btn = page.js("!!document.querySelector('[data-act=board]')")
    assert btn
    page.js("document.querySelector('[data-act=board]').click()")
    txt = page.js("document.querySelector('#own-board-ta').value")
    for k in ("発地:", "着地:", "曜日・時刻:", "車格:", "空き:", "連絡先: （"):
        assert k in txt, txt
    assert re.search(r"毎週.曜", txt) and "江東" in txt and "加古川" in txt
    assert not re.search(r"\d{2,4}-\d{2,4}-\d{3,4}", txt)  # 電話番号は入れない
    page.js("document.querySelector('[data-act=board-copy]').click()")
    page.wait(lambda: page.js("document.querySelector('#own-board-msg').textContent") != "", 10, "コピーの結果")
    msg = page.js("document.querySelector('#own-board-msg').textContent")
    assert "コピーしました" in msg or "Ctrl+C" in msg, msg  # どちらでも、結果を画面に出す
    page.js("Own.setTab('import')")
    _to_sample(page)


# ---------------------------------------------------------------- 2. 荷待ちの下書き

def test_waiting_draft_rows_come_from_the_long_stops(app):
    page = app
    _import_examples(page)
    n = page.js("Own.state.data.stops.filter(s => s.minutes >= 30).length")
    assert n > 0
    page.js("Own.setTab('wait')")
    page.js("document.querySelector('[data-w=from-stops]').click()")
    page.wait(lambda: page.js("document.querySelectorAll('#w-tbody tr').length") >= n, 10, "下書きの行")
    rows = page.js("[...document.querySelectorAll('#w-tbody tr')].filter(tr => tr.querySelector('[data-k=place]').value)")
    first = json.loads(page.js("""JSON.stringify((function(){const tr = [...document.querySelectorAll('#w-tbody tr')].find(t => t.querySelector('[data-k=place]').value);
      const g = k => tr.querySelector('[data-k=' + k + ']'); return {date: g('date').value, vehicle: g('vehicle_no').value, place: g('place').value, arrive: g('arrive').value, depart: g('depart').value, partner: g('partner').value, ph: g('partner').placeholder};})())"""))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", first["date"]) and first["vehicle"] and first["place"], first
    assert re.fullmatch(r"\d{2}:\d{2}", first["arrive"]) and re.fullmatch(r"\d{2}:\d{2}", first["depart"]), first
    assert first["partner"] == "" and first["ph"] == "相手を入れてください", first
    count = page.js("[...document.querySelectorAll('#w-tbody tr')].filter(tr => tr.querySelector('[data-k=place]').value).length")
    assert count == n, (count, n)
    note = page.js("document.querySelector('#w-paste-note').innerText")
    assert "相手を入れてください" in note
    page.wait(lambda: "契約の相手を入れてください" in page.js("document.querySelector('#w-errs-box').innerText"), 10, "相手が空の案内")
    # 契約の相手・荷役開始・荷主等の都合は停車の記録に無いので、入れるまで計算に入らない（読めなかった行に出る）
    errs = page.js("document.querySelector('#w-errs-box').innerText")
    assert "荷役開始がありません" in errs and "荷主等の都合の待機か" in errs, errs
    page.js("document.querySelector('#w-draft-partner').value = '架空 元請X'")
    page.js("document.querySelector('[data-w=draft-partner]').click()")
    page.js("""(function(){for (const tr of document.querySelectorAll('#w-tbody tr')) { const s = tr.querySelector('[data-k=start]'); if (!s || s.value) continue;
      s.value = tr.querySelector('[data-k=arrive]').value; s.dispatchEvent(new Event('input', {bubbles: true})); }})()""")
    page.js("document.querySelector('[data-w=fault-yes]').click()")
    page.wait(lambda: "架空 元請X" in page.js("(document.querySelector('#own-right .w-par') || {innerText: ''}).innerText"), 15, "相手ごとの表")
    before = page.js("document.querySelectorAll('#w-tbody tr').length")
    page.js("document.querySelector('#w-tbody .w-del').click()")
    assert page.js("document.querySelectorAll('#w-tbody tr').length") == before - 1
    page.js("document.querySelector('[data-w=clear]').click()")
    page.js("Own.setTab('import')")
    _to_sample(page)


# ---------------------------------------------------------------- 5. 月の保存と比べ、印刷

def test_month_is_saved_compared_and_deleted_only_by_the_button(app, tmp_path):
    page = app
    page.js("OwnMonth.clearAll()")
    _import_examples(page)
    page.js("Own.setTab('import')")
    assert "保存しました" in page.js("document.querySelector('#own-month').innerText")
    raw = page.js("localStorage.getItem('backfull.own.v41')")
    assert raw and "KBN" not in raw and "ST_TIME" not in raw and "架空 東和電機" not in raw  # CSV の中身は保存しない
    assert len(json.loads(raw)["entries"]) == 1
    assert not page.js("!!document.querySelector('[data-act=month-cmp]')")  # 1 つだけのときは並べる相手がいない
    # 11 月のファイルを落とすと、前の月と並べられる
    _import(page, [_write(tmp_path, "haisha_2026-11.csv", NOV_HAISHA)])
    page.wait(lambda: page.js("Own.state.data && Own.state.data.mon") == 11, 60, "11 月の取り込み")
    page.wait(lambda: page.js("!!document.querySelector('[data-act=month-cmp]')"), 10, "並べる button")
    assert page.js("document.querySelector('[data-act=month-cmp]').textContent") == "前の月と並べる"
    page.js("document.querySelector('[data-act=month-cmp]').click()")
    rows = json.loads(page.js("JSON.stringify([...document.querySelectorAll('#own-month .o-cmp tbody tr')].map(tr => [...tr.children].map(c => c.textContent)))"))
    assert [r[0] for r in rows] == ["実車率", "大型の積載率", "空車 km", "荷待ち（30 分以上の停車）"], rows
    assert all(len(r) == 4 and r[3] for r in rows)
    heads = page.js("[...document.querySelectorAll('#own-month .o-cmp thead th')].map(t => t.textContent)")
    assert "2026 年 10 月" in heads and "2026 年 11 月" in heads, heads
    # 印刷用の欄に、比べも入る
    assert "前の月と並べる" in page.js("document.querySelector('#own-printsheet').textContent")
    # 開き直し: 同じ 10 月のファイルでは、確認の決定と地名の対応を戻すか聞く（黙って戻さない）
    _import_examples(page)
    page.js("Own.setTab('confirm')")
    first = page.js("document.querySelector('.o-card .o-btns [data-act=ok]') && document.querySelector('.o-card .o-btns [data-act=ok]').click()")
    page.wait(lambda: page.js("Own.state.data && Object.keys(Own.state.dec).length") >= 1, 10, "決定")
    time.sleep(1.0)
    _import_examples(page)
    page.js("Own.setTab('import')")
    assert page.js("!!document.querySelector('[data-act=month-restore]')")
    assert page.js("Object.keys(Own.state.dec).length") == 0  # 戻すと言うまで戻さない
    page.js("document.querySelector('[data-act=month-restore]').click()")
    page.wait(lambda: page.js("Object.keys(Own.state.dec).length") >= 1, 20, "決定を戻す")
    # 消すのは、消すボタンだけ
    page.js("Own.setTab('import')")
    assert page.js("localStorage.getItem('backfull.own.v41') !== null")
    page.js("document.querySelector('[data-act=month-clear]').click()")
    assert page.js("localStorage.getItem('backfull.own.v41')") is None
    time.sleep(0.5)
    page.js("Own.setTab('import')")
    assert page.js("localStorage.getItem('backfull.own.v41')") is None  # 消したあとは、次のファイルを落とすまで保存しない
    _to_sample(page)


def test_print_sheet_is_one_page_of_numbers_and_insights(app):
    page = app
    _import_examples(page)
    css = (ROOT / "web" / "own.css").read_text(encoding="utf-8")
    assert "@media print" in css and "own-print-sum" in css and "@page" in css
    assert page.js("getComputedStyle(document.querySelector('#own-printsheet')).display") == "none"  # 画面では隠す
    page.js("document.body.classList.add('own-print-sum')")
    page.send("Emulation.setEmulatedMedia", media="print")
    try:
        disp = page.js("getComputedStyle(document.querySelector('#own-printsheet')).display")
        txt = page.js("document.querySelector('#own-printsheet').innerText")
        h = page.js("document.querySelector('#own-printsheet').getBoundingClientRect().height")
    finally:
        page.send("Emulation.setEmulatedMedia", media="")
        page.js("document.body.classList.remove('own-print-sum')")
    assert disp == "block"
    for k in ("実車率", "大型の積載率", "空車 km", "荷待ち", "気づき"):
        assert k in txt, txt
    assert h < 1000, h  # A4 の 1 ページ（約 1000px）に収まる
    _to_sample(page)


# ---------------------------------------------------------------- 6. 列の役割・年・日本語の理由

def test_columns_are_explained_in_plain_japanese(app):
    page = app
    _import_examples(page)
    page.js("Own.setTab('import')")
    body = page.js("document.querySelector('#own-body').innerText")
    cols = page.js("document.querySelector('.o-cols').innerText")
    assert "辞書" not in cols and "形 100%" not in cols and not re.search(r"0\.\d\d", cols), cols
    assert ("列の名前" in cols or "列名" in cols) and not re.search(r"\b(plate|date|distance|vehicle_type)\b", cols)
    _to_sample(page)


def test_role_chooser_reruns_with_column_roles_and_remembers_the_choice(app):
    page = app
    page.js("OwnMonth.clearAll()")
    _to_sample(page)
    page.js("""(function(){
      window.__roleCalls = []; const orig = OwnCalc.analyze; window.__origAnalyze = orig;
      OwnCalc.analyze = function(files, opts){ window.__roleCalls.push(opts && opts.column_roles); return orig.apply(this, arguments); };
    })()""")
    try:
        _import_examples(page)
        page.js("Own.setTab('import')")
        # 例のデジタコの「X9」は列の名前から役割が決まらない列。選ぶ欄が日本語で出る
        need = page.js("(Own.state.data.column_roles_needed || []).map(x => x.column)")
        assert "X9" in need, need
        txt = page.js("document.querySelector('#own-roles').innerText")
        assert "X9" in txt and "選んでください" in txt
        labels = page.js("[...document.querySelectorAll('#own-roles select')][0].querySelectorAll('option') && [...[...document.querySelectorAll('#own-roles select')][0].querySelectorAll('option')].map(o => o.textContent)")
        assert labels[0] == "役割を選んでください" and "使わない" in labels and "日付" in labels, labels
        assert not re.search(r"\b(skip|distance_km|loaded_flag|start_time)\b", txt), txt  # 英語の役割名は出さない
        assert page.js("document.querySelector('#own-roles select').value") == ""  # 黙って選ばない
        page.js("(function(){const s = document.querySelector('#own-roles select[data-role-col=\"X9\"]'); s.value = 'skip'; s.dispatchEvent(new Event('change', {bubbles: true}));})()")
        page.wait(lambda: any(c and (c.get("digitaco") or {}).get("X9") == "skip" for c in page.js("window.__roleCalls")), 30, "役割を渡して計算し直し")
        page.wait(lambda: "X9" not in page.js("(Own.state.data.column_roles_needed || []).map(x => x.column)"), 30, "決めた列が選ぶ欄から消える")
        # 見出しの形が同じファイルを次に落としたときのために、覚えている（ファイルの中身ではなく、列の名前と役割だけ）
        saved = page.js("localStorage.getItem('backfull.own.v41.roles')")
        assert saved and "skip" in saved and "X9" in saved and "KBN" not in saved, saved
        # もう一度落とすと、聞かずに同じ役割で計算する
        page.js("window.__roleCalls = []")
        _import_examples(page)
        assert any(c and (c.get("digitaco") or {}).get("X9") == "skip" for c in page.js("window.__roleCalls"))
        assert "X9" not in page.js("(Own.state.data.column_roles_needed || []).map(x => x.column)")
    finally:
        page.js("OwnCalc.analyze = window.__origAnalyze")
        _to_sample(page)
        page.js("OwnMonth.clearAll()")


def test_year_assumed_is_shown_with_a_selector(app):
    page = app
    _import_examples(page)
    page.js("Own.state.data.year_assumed = true; Own.setTab('import')")
    t = page.js("document.querySelector('.o-assumed').innerText")
    assert "2026 年としました（変えられます）" in t, t
    assert page.js("document.querySelectorAll('.o-assumed select option').length") >= 3
    _to_sample(page)


# ---------------------------------------------------------------- 7. 説明・24px・用語

def test_explanations_checkbox_size_and_jargon_tips(app):
    page = app
    _import_examples(page)
    right = page.js("document.querySelector('#own-right').innerText")
    assert "13,500 kg" in right and "仮定" in right, right  # 10 t の仮定と 13,500 kg の違い
    assert "77.6%" in right and "辰巳" in right  # 例のファイルの実車率がサンプルと違う理由
    box = json.loads(page.js("JSON.stringify(document.querySelector('#own-mkt').getBoundingClientRect())"))
    assert box["width"] >= 24 and box["height"] >= 24, box
    # 用語: 実車率・積載率・積載効率の「?」は、押すと開き、Esc で閉じる
    for term in ("実車率", "積載率", "積載効率"):
        assert page.js(f"!!document.querySelector('.o-tip[aria-label=\"{term}とは\"]')"), term
    page.js("document.querySelector('.o-tip[aria-label=\"実車率とは\"]').click()")
    assert page.js("document.querySelector('.o-tip[aria-label=\"実車率とは\"]').getAttribute('aria-expanded')") == "true"
    assert page.js("getComputedStyle(document.querySelector('.o-tipw.open .o-tipb')).display") == "block"
    page.send("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
    page.send("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27)
    assert page.js("document.querySelectorAll('.o-tipw.open').length") == 0
    # 6 つの用語の説明が、用語の説明の欄にある
    page.js("document.querySelector('#own-gloss').open = true")
    g = page.js("document.querySelector('#own-gloss').innerText")
    for term in ("実車率", "積載率", "積載効率", "空車 km", "1口", "基準積載率"):
        assert term in g, g
    # キーボード: フォーカスして Enter（ボタンの標準の動き）で開く
    page.js("document.querySelector('.o-tip[aria-label=\"積載率とは\"]').focus()")
    page.send("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r")
    page.send("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)
    assert page.js("document.querySelector('.o-tip[aria-label=\"積載率とは\"]').parentElement.classList.contains('open')")
    _shot(page, "2026-11-06_v41_own_1920.jpg")
    _to_sample(page)


# ---------------------------------------------------------------- 400px の幅（Edge は同時に 1 つだけなので、同じ Edge の表示の幅を変えて見る）

def test_narrow_weekday_table_stays_inside_the_frame_and_costs_go_under_the_row(app):
    page = app
    _import_examples(page)
    page.send("Emulation.setDeviceMetricsOverride", width=400, height=860, deviceScaleFactor=1, mobile=False)
    time.sleep(1.5)
    page.js("(function(){for (const [id, v] of [['own-perkm', '30'], ['own-perhour', '3000']]) { const e = document.querySelector('#' + id); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); }})()")

    page.js("document.querySelector('#own-tw').scrollIntoView()")
    m = json.loads(page.js("""JSON.stringify((function(){const w = document.querySelector('#own-tw'), t = w.querySelector('table');
      const tr = [...t.querySelectorAll('tbody tr:not(.grp)')][0], tds = [...tr.children];
      const cost = tds.find(td => td.dataset.l === '空車の費用'), first = tds[0];
      return {frame: w.clientWidth, scroll: w.scrollWidth, table: t.getBoundingClientRect().width, doc: document.documentElement.scrollWidth, win: window.innerWidth,
              costTop: cost.getBoundingClientRect().top, firstBottom: first.getBoundingClientRect().bottom};})())"""))
    assert m["scroll"] <= m["frame"] + 1 and m["table"] <= m["frame"] + 1 and m["doc"] <= m["win"] + 1, m
    assert m["costTop"] >= m["firstBottom"] - 1, m  # 費用の欄は行の下
    # 説明と保存の欄も横にはみ出さない
    for sel in ("#own-right", "#own-view"):
        assert page.js(f"document.querySelector('{sel}').scrollWidth <= document.querySelector('{sel}').clientWidth + 1"), sel
    _shot(page, "2026-11-06_v41_own_400.jpg")
    page.js("document.querySelector('#own-body').scrollTop = 0; Own.setTab('confirm')")
    time.sleep(0.5)
    _shot(page, "2026-11-06_v41_own_400_left.jpg")
    page.js("Own.setTab('import')")
    page.js("(function(){for (const id of ['own-perkm', 'own-perhour']) { const e = document.querySelector('#' + id); e.value = ''; e.dispatchEvent(new Event('input', {bubbles: true})); }})()")
    page.send("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
    time.sleep(1.0)
    _to_sample(page)


# ---------------------------------------------------------------- 読み取り側の新しい項目（own_calc.js）を画面に出す

def test_new_fixture_files_import_and_show_layout_notes_and_role_choices(app):
    page = app
    page.js("window.__errs = []")
    # 県内の往復の配車表（examples/own/haisha_intra_pref.csv）: 取り込めて、線の向きの印が出る
    _import(page, [EXAMPLES / "haisha_intra_pref.csv"])
    assert page.js("Own.state.mode") == "import", page.js("Own.state.dropMsg")
    assert page.js("Own.state.data.legs.length") >= 10
    # 神戸は区の名前が無いので市役所のある区（中央区）に置く。主な往復の道が引かれ、置き方の注記が地図に置けない地点の欄に出る（SPEC §41）
    d = page.js("Own.state.data")
    assert d["places"][d["dock"]]["code"] == "28110" and d["route"]["path"] and len(d["route"]["path"]) >= 2
    assert [u["name"] for u in d["unplaced"]] == ["神戸"] and "市役所のある区に置きました" in d["unplaced"][0]["reason"]
    assert page.js("document.querySelector('#own-body').innerText").count("市役所のある区に置きました") >= 1
    page.js("Own.setTab('import')")
    assert "読み方" in page.js("document.querySelector('#own-body').innerText") or page.js("Own.state.data.files.some(f => f.layout_note_ja)") is False
    _shot(page, "2026-11-06_v41_own_intra_file_1920.jpg")
    # 列の名前から役割が決まらないデジタコ（digitaco_unknown_cols_9012.csv）: 選ぶ欄が出る
    _import(page, [EXAMPLES / "haisha_2026-10.csv", EXAMPLES / "digitaco_unknown_cols_9012.csv"])
    assert page.js("Own.state.mode") == "import", page.js("Own.state.dropMsg")
    n = page.js("(Own.state.data.column_roles_needed || []).length")
    assert n == 0 or page.js("!!document.querySelector('#own-roles select')")
    errs = page.js("(window.__errs || []).filter(e => !/favicon|404/.test(String(e)))")
    assert not errs, errs
    _to_sample(page)
