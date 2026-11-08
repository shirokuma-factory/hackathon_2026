"""SPEC §46 実務の効き目 r4 と画面の出来 r1 を受けた、荷待ちのページ・ドライバーの 1 枚・送り方比べの修正。

- メモの読み取り: 「車1234」「車 1234」を車番、「大型」「4t」だけでも車種、日付の次の最初の語を相手、略字（受・呼・終・積込終）と時刻が先の書き方、
  受付が到着より前の行は確かめてもらう。読んだ内容を行ごとに見せ、その場で直せる。
- 書面: 協議に使う額を 1 つ太字にしてほかは折りたたむ。CSV は選んだ相手の行だけ。印刷に照らし合わせのページの欄。言葉の説明。
- 照らし合わせのあと: 自社の時刻で数え直した額と差額、運送会社への返信の文。
- 案内の入口（#g）: 3 つの選択と、選んだあとの欄だけの画面。最初の画面の文字数・操作部品の上限。
- ドライバーの 1 枚: 最初の画面へ戻る道、日報に写す表。
- 送り方比べ: 宅配に載る荷と載らない荷を分けた合計、見積の回答を 3 社まで、特別積合せの説明。
Edge の確認は 1 つずつ直列に動かす。Edge が無い環境では Edge の確認を飛ばす。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

from shipcompare import waiting

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

WAITING = ROOT / "web" / "waiting.html"
DRIVER = ROOT / "web" / "driver.html"
COMPARE = ROOT / "web" / "compare_lite.html"
DEFAULTS = {"vehicle_class": "中型", "method": "手積み"}

MEMO = ("11/2 東和電機 加古川工場 着7時40分 受付8:00 呼ばれ9:50 終わり10:40 車１２３４ 大型 フォークで手積み手伝った\n"
        "11/3 東和電機 加古川工場 6:10着 6:25受付 9:00呼 9:40終 車 5678 4t\n"
        "11/4 江東 着7:50 呼ばれ9:40 積込終10:30 車番 9999 中型\n"
        "11/5 尼崎 ○○物流DC 着7:00 受7:30 呼8:40 相手 ○○\n"
        "11/6 加古川 着7:00 受6:20 呼8:00\n")


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _wait_for(page, js, what):
    page.wait(lambda: page.js(js), 30, what)


# ---------------------------------------------------------------- メモの読み取り（Python）
def test_memo_plate_class_partner_and_abbreviations_are_read():
    d = waiting.parse_paste_detail(MEMO)
    r = d["rows"]
    assert len(r) == 5 and not [n for n in d["notes"] if n.startswith("読めなかった行")]
    assert r[0]["vehicle_no"] == "1234" and r[0]["vehicle_class"] == "大型" and r[0]["partner"] == "東和電機" and r[0]["place"] == "加古川工場"
    assert r[0]["arrive"] == "7時40分" and r[0]["reception"] == "8:00" and r[0]["start"] == "9:50" and r[0]["end"] == "10:40"
    assert "フォークで" not in r[0]["place"]                       # 相手・場所のあとの語は場所に混ぜない
    assert r[1]["vehicle_no"] == "5678" and r[1]["vehicle_class"] == "4t"      # 「車 5678」（空白あり）と「4t」だけの車種
    assert r[1]["arrive"] == "6:10" and r[1]["reception"] == "6:25" and r[1]["start"] == "9:00" and r[1]["end"] == "9:40"   # 時刻が先の書き方と略字
    assert r[2]["end"] == "10:30" and r[2]["vehicle_no"] == "9999"            # 積込終
    assert r[2]["place"] == "江東" and r[2]["partner"] == waiting.MEMO_NO_PARTNER       # 語が 1 つだけなら場所
    assert r[3]["partner"] == "○○" and r[3]["place"] == "尼崎 ○○物流DC"                # 「相手 ○○」があればそれが相手
    res = waiting.calc(r, 2026, defaults=DEFAULTS)
    assert res["errors"] == []
    by = {x["vehicle_no"]: x["vehicle_class"] for x in res["rows"]}
    assert by["1234"] == "大型" and by["5678"] == "中型"                    # 4t は中型の単価


def test_memo_read_lists_what_was_read_per_row_and_flags_reception_before_arrival():
    d = waiting.parse_paste_detail(MEMO)
    rd = d["memo_read"]
    assert len(rd) == 5
    assert rd[0]["partner"] == "東和電機" and rd[0]["vehicle_no"] == "1234" and rd[0]["vehicle_class"] == "大型" and rd[0]["unread"] == ["フォークで手積み手伝った"]
    assert rd[4]["check"] and "受付 6:20 が到着 7:00 より前です" in rd[4]["check"][0]
    assert any(n.startswith("確かめてください:") and "11/6" in n for n in d["notes"])


def test_memo_without_these_words_keeps_the_old_reading():
    d = waiting.parse_paste_detail("11/10 江東 着7:50 呼ばれ9:40 終わり10:30\n")
    assert d["rows"][0]["place"] == "江東" and "vehicle_no" not in d["rows"][0] and "vehicle_class" not in d["rows"][0]


# ---------------------------------------------------------------- ファイルの形
def test_driver_page_has_a_way_back_and_the_day_sheet_and_keeps_distribution_help():
    html = DRIVER.read_text(encoding="utf-8")
    assert 'href="/"' in html and "最初の画面に戻る" in html
    assert "事務の方へ" in html and "URL をコピー" in html   # 文言の直し 2026-11-07          # 配り方の説明は残す
    assert "日報に写す表" in html and "日報の表をコピー" in html and "荷待ち（分）" in html
    assert html.count("localStorage") == 3 and "fetch(" not in html
    assert "--bg: #070b12" in html                                      # 地図の画面の暗い地


def test_pages_use_the_map_apps_dark_tokens_and_print_on_white():
    for p in (WAITING, COMPARE):
        html = p.read_text(encoding="utf-8")
        assert html.count("--bg: #070b12; --card: #0d141f; --text: #e8eef8") == 2     # 暗い側の 2 か所（prefers-color-scheme と data-theme）
        assert "--accent: #5cc8ff" in html and "--radius: 14px" in html
        assert "@media print" in html and "--card: #ffffff" in html.split("@media print", 1)[1][:900] and "background: #fff" in html.split("@media print", 1)[1][:2500]


def test_waiting_and_compare_terms_are_explained():
    w = WAITING.read_text(encoding="utf-8")
    assert "荷主等の都合" in w and "荷主や荷受人の責任で車が待たされたこと" in w and "荷物を運ぶ以外にドライバーがした作業" in w
    c = COMPARE.read_text(encoding="utf-8")
    assert "特別積合せ…複数の荷主の荷物を 1 台に積み合わせ" in c


# ---------------------------------------------------------------- 荷待ちのページ（Edge）
def _budget(page):
    from viewport_text import COUNT_JS
    return json.loads(page.js(COUNT_JS))


@pytest.mark.parametrize("width,height,max_chars", [(1920, 1080, 450), (360, 800, 300)])
def test_guided_entry_screens_stay_within_the_budget(width, height, max_chars):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(width, height) as page:
        for hash_ in ("#g", "#g=driver", "#g=excel", "#g=shipper"):
            page.goto(WAITING.as_uri() + "?x=" + hash_.replace("#", "").replace("=", "_") + hash_)
            _wait_for(page, "!!document.querySelector('#b-paste') && document.body.classList.contains('guided')", "案内の入口 " + hash_)
            time.sleep(0.3)
            m = _budget(page)
            assert m["chars"] <= max_chars, (hash_, width, m["chars"], m["samples"])
            assert m["controls"] <= (8 if hash_ == "#g" else 9), (hash_, width, m["controls"])   # 入口の説明に、driver.html へのリンクか見本のボタンを 1 つ足した（2026-11-08）
            assert m["primary"] == 1, (hash_, width, m["primary"])
            wide = page.js("document.documentElement.scrollWidth")
            assert wide <= width, (hash_, wide)
        # 入口には 3 つの選択だけ
        page.goto(WAITING.as_uri() + "?x=choose#g")
        _wait_for(page, "document.body.classList.contains('g-choose')", "選択の画面")
        names = page.js("[...document.querySelectorAll('#guide [data-g]')].map((b) => b.querySelector('span').textContent)")
        assert names == ["ドライバーの LINE を貼る", "Excel の表を貼る", "届いた書面を受付簿と見比べる（荷主の方）"]   # 文言の直し 2026-11-07
        assert page.js("document.querySelector('main').getBoundingClientRect().height === 0 || getComputedStyle(document.querySelector('main')).display === 'none'")


def test_guided_step_shows_only_the_needed_part_and_folds_table_and_notes():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 900) as page:
        page.goto(WAITING.as_uri() + "?x=step#g=driver")
        _wait_for(page, "document.body.classList.contains('g-step')", "手順の画面")
        info = page.js("""({tf: document.querySelector('#table-fold').open, nf: document.querySelector('#notes-fold').open,
                            res: getComputedStyle(document.querySelector('#result')).display, rows: S.rows.filter((r) => Object.values(r).some((v) => String(v).trim())).length,
                            h1: document.querySelector('h1').textContent, back: document.querySelector('#g-home').getAttribute('href')})""")
        assert info["tf"] is False and info["nf"] is False and info["res"] == "none" and info["rows"] == 0, info   # 例は入れない・結果は貼るまで出さない
        assert info["h1"] == "ドライバーの LINE を貼る" and info["back"] == "index.html"
        page.js("document.querySelector('#paste').value = " + json.dumps(MEMO, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        _wait_for(page, "!!document.querySelector('#wait-doc table.rec')", "書面")
        after = page.js("({res: getComputedStyle(document.querySelector('#result')).display, nf: document.querySelector('#notes-fold').open, sum: document.querySelector('#notes-sum').textContent})")
        assert after["res"] != "none" and after["nf"] is True and "件" in after["sum"], after       # 貼ったあとは注意の欄が開く
        # 選んだ 5 行すべてが入り、荷主として使うではなく運送会社の書面
        assert page.js("document.body.classList.contains('shipper-mode')") is False
        # 全部を見る
        page.js("location.hash = '#all'")
        _wait_for(page, "!document.body.classList.contains('guided')", "全部の画面")
        assert page.js("document.querySelector('#table-fold').open") is True


def test_default_page_without_hash_is_unchanged_and_has_the_example():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 900) as page:
        page.goto(WAITING.as_uri() + "?x=default")
        _wait_for(page, "!!document.querySelector('#wait-doc table.rec')", "書面")
        info = page.js("({g: document.querySelector('#guide').hidden, tf: document.querySelector('#table-fold').open, sum: getComputedStyle(document.querySelector('#table-fold > summary')).display, rows: S.rows.length})")
        # 文言の直し 2026-11-07: 例を自動で入れた最初の画面は、表を閉じて見出し（summary）を出し、貼る欄だけを見せる
        assert info["g"] is True and info["tf"] is False and info["sum"] != "none" and info["rows"] >= 10, info
        assert page.js("document.body.classList.contains('demo') && document.querySelector('#result-d').open === true")   # デザインの作り直し 2026-11-07: 例の最初の画面でも結果（Claude で説明文を作る欄）を見せる
        page.js("document.querySelector('#b-add').click()")   # 触ると表が開き、結果も開く
        assert page.js("!document.body.classList.contains('demo') && document.querySelector('#table-fold').open && document.querySelector('#result-d').open")


def test_memo_paste_shows_what_was_read_and_fixes_apply_to_the_table():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        page.goto(WAITING.as_uri() + "?selftest=0&x=memo")
        _wait_for(page, "!!document.querySelector('#b-paste')", "荷待ちのページ")
        page.js("document.querySelector('#b-clear').click()")
        page.js("document.querySelector('#paste').value = " + json.dumps(MEMO, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        _wait_for(page, "document.querySelectorAll('#memo-read li').length === 5", "読み取りの一覧")
        first = page.js("""(function(){ const li = document.querySelector('#memo-read li');
          return [...li.querySelectorAll('input, select')].map((e) => e.dataset.mk + '=' + e.value).join(' / ') + ' | ' + li.textContent; })()""")
        assert "partner=東和電機" in first and "vehicle_no=1234" in first and "vehicle_class=大型" in first and "place=加古川工場" in first, first
        assert "読めなかった言葉: フォークで手積み手伝った" in first
        assert "確認: 受付 6:20 が到着 7:00 より前です" in page.js("document.querySelector('#memo-read').textContent")
        # 相手を直すと表と計算に反映される
        page.js("(function(){const e = document.querySelector('#memo-read input[data-mk=partner]'); e.value = '東和電機株式会社'; e.dispatchEvent(new Event('change', {bubbles: true}));})()")
        _wait_for(page, "S.rows.some((r) => r.partner === '東和電機株式会社') && S.res && S.res.by_partner.some((p) => p.partner === '東和電機株式会社')", "直した相手")
        # 書面: 協議に使う額は 1 つ太字、ほかは折りたたみ、照らし合わせのページの欄
        doc = page.js("""({big: document.querySelectorAll('#wait-doc .main-amt b.big').length, alt: !!document.querySelector('#doc-alt'), altOpen: document.querySelector('#doc-alt') ? document.querySelector('#doc-alt').open : null,
                           url: !!document.querySelector('#doc-url'), terms: document.querySelector('#doc-terms') ? document.querySelector('#doc-terms').textContent : ''})""")
        assert doc["big"] == 1 and doc["url"] and "荷主等の都合" in doc["terms"] and "附帯業務" in doc["terms"], doc
        if doc["alt"]:
            assert doc["altOpen"] is False


def test_csv_is_only_the_selected_partner_and_print_text_has_the_page_address():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        page.goto(WAITING.as_uri() + "?example=1&x=csv")
        _wait_for(page, "!!document.querySelector('#wait-doc table.rec')", "書面")
        info = page.js("""(function(){ const all = S.res.rows.length; const mine = S.res.rows.filter((r) => r.partner === S.partner).length;
          const lines = csvText().trim().split('\\r\\n').length - 1;
          return {all, mine, lines, scope: document.querySelector('#csv-scope').textContent, text: draftText(S.res.by_partner.find((x) => x.partner === S.partner))}; })()""")
        assert info["lines"] == info["mine"] < info["all"], info
        assert "だけです" in info["scope"]
        assert "照らし合わせのページ:" in info["text"] and "荷主等の都合とは" in info["text"]
        # 相手を切り替えると CSV もその相手の行
        page.js("document.querySelectorAll('#partners tr[data-pick]')[1].click()")
        n2 = page.js("csvText().trim().split('\\r\\n').length - 1")
        assert n2 == page.js("S.res.rows.filter((r) => r.partner === S.partner).length")
        # http で開いたときは、そのページの URL（荷主向けの入口つき）が書面に入る
        addr = page.js("(function(){ const keep = location; return crossPageAddress(); })()")
        assert "照らし合わせ" not in addr or "#g=shipper" in addr or "書き入れてください" in addr


def test_cross_check_recalculates_with_own_times_and_writes_a_reply():
    _edge_or_skip()
    import capture_web as cw
    log = "受付日\t運送会社\t施設\t車番\t受付時刻\t入場\n2026/10/5\tA運輸\t江東区\t1234\t8:00\t10:35\n2026/10/6\tA運輸\t江東区\t5678\t8:10\t9:00\n"
    pdf = "日付  車番  到着  荷役開始  荷役終了\n2026/10/5  1234  7:30  11:20  11:50\n2026/10/6  5678  8:10  9:00  9:30\n"
    with cw.edge_page(1280, 1000) as page:
        page.goto(WAITING.as_uri() + "?x=cross")
        _wait_for(page, "!!document.querySelector('#b-paste')", "荷待ちのページ")
        page.js("document.querySelector('#b-clear').click()")
        page.js("document.querySelector('#paste').value = " + json.dumps(log, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        _wait_for(page, "!!document.querySelector('#cross-ta')", "照合の欄")
        # 荷主として使うとき、運送会社向けの断り（荷役をした人が分からない）は出さない
        page.js("document.querySelector('#cross-ta').value = " + json.dumps(pdf, ensure_ascii=False))
        page.js("document.querySelector('#b-cross').click()")
        _wait_for(page, "!!document.querySelector('#cross-recalc')", "数え直した額")
        out = page.js("""({text: document.querySelector('#cross-recalc').textContent, reply: document.querySelector('#reply-ta').value,
                           r: S.cross.recalc, notes: document.querySelector('#errs').textContent})""")
        r = out["r"]
        assert r["n"] == 1 and r["own"] < r["carrier"] and r["diff"] == r["carrier"] - r["own"] > 0, r
        assert "受付簿の時刻で数えた額" in out["text"] and "書面のほうが多い" in out["text"]
        assert "当方の受付の時刻で数え直した参考額" in out["reply"] and "2026-10-05" in out["reply"] and "（運送会社の担当者名）" in out["reply"]
        assert "荷役をした人が分からない" not in out["notes"]


# ---------------------------------------------------------------- 送り方比べ（Edge）
def test_compare_lite_splits_courier_and_line_haul_and_keeps_three_quote_answers():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(COMPARE.as_uri() + "?example=1")
        _wait_for(page, "!!document.querySelector('table.res')", "判定")
        page.js("localStorage.removeItem('compare_lite_quotes_v1'); localStorage.removeItem('compare_lite_contract_v1')")
        page.js("S.rows = [{n: 12, l: 40, w: 30, h: 30, kg: 10}, {n: 1, l: 110, w: 110, h: 130, kg: 320}]; document.querySelector('#to').value = '宮城県'; document.querySelector('#from').value = '大阪府'; document.querySelector('#km').value = '700'; renderRows(); render();")
        # 契約単価を入れる前: 分けて送る方法があると知らせる
        assert "分けて送る方法があります" in page.js("document.querySelector('#cmp').textContent")
        page.js("""(function(){
          const set = (sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); };
          set('#c-name', 'A社'); set('#c-t-100', '2350'); set('#c-r-rate', '100');
        })()""")
        _wait_for(page, "document.querySelector('#cmp') && document.querySelector('#cmp').textContent.includes('宅配と路線便に分ける')", "分けた合計")
        text = page.js("document.querySelector('#cmp').textContent")
        # 宅配 12 個 × 2,350 = 28,200 円 + 路線便 1 個（計算重量 440.4kg × 100 円 = 44,044 円）= 72,244 円
        assert "72,244 円" in text and "宅配 12 個 28,200 円" in text and "路線便 1 個" in text and "44,044 円" in text, text
        assert page.js("!!document.querySelector('#terms')") and "特別積合せ" in page.js("document.querySelector('#terms').textContent")
        # 見積の回答: 3 社まで、会社名つき。安い順に並べ、ブラウザに残る
        page.js("""(function(){
          const set = (sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); };
          set('#ans-name-0', 'B運輸'); set('#ans-amt-0', '98,000'); set('#ans-name-1', 'C急送'); set('#ans-amt-1', '71000'); set('#ans-name-2', 'D便'); set('#ans-amt-2', '80000円');
        })()""")
        order = page.js("[...document.querySelectorAll('#ans-table tbody tr')].map((tr) => tr.children[1].textContent + ':' + tr.children[2].textContent.replace(/[^0-9]/g, ''))")
        assert order == ["C急送:71000", "D便:80000", "B運輸:98000"], order
        assert "C急送" in page.js("document.querySelector('#ans-best').textContent")
        assert "計算のほうが 14,860 円" in page.js("document.querySelector('#ans-vs').textContent")      # 計算の最安は荷全部を路線便にした 56,140 円、回答の最安は 71,000 円
        saved = page.js("JSON.parse(localStorage.getItem('compare_lite_quotes_v1'))")
        assert [s["name"] for s in saved] == ["B運輸", "C急送", "D便"]
        page.goto(COMPARE.as_uri() + "?x=again")
        _wait_for(page, "!!document.querySelector('#ans-name-0')", "再読み込み")
        assert page.js("document.querySelector('#ans-name-1').value") == "C急送" and page.js("document.querySelector('#ans-amt-1').value") == "71000"
        # 0 や文字は断る
        page.js("(function(){const e = document.querySelector('#ans-amt-0'); e.value = '0'; e.dispatchEvent(new Event('input', {bubbles: true}));})()")
        assert "0 より大きい数" in page.js("document.querySelector('#ans-out').textContent")


def test_compare_lite_without_a_mixed_load_has_no_split_row():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 900) as page:
        page.goto(COMPARE.as_uri() + "?x=nosplit")
        _wait_for(page, "!!document.querySelector('table.res')", "判定")
        page.js("localStorage.removeItem('compare_lite_quotes_v1')")
        page.js("S.rows = [{n: 3, l: 40, w: 30, h: 20, kg: 5}]; document.querySelector('#to').value = '大阪府'; document.querySelector('#from').value = '東京都'; renderRows(); render();")
        page.js("""(function(){
          const set = (sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); };
          set('#c-t-60', '1000'); set('#c-t-80', '1100'); set('#c-t-100', '1200'); set('#c-r-rate', '50');
        })()""")
        assert "宅配と路線便に分ける" not in page.js("document.querySelector('#cmp').textContent")      # 全部が宅配に載るので分けない


def test_compare_lite_selftest_still_passes():
    _edge_or_skip()
    import capture_web as cw
    n = len(json.loads((ROOT / "tests" / "data" / "compare_lite_vectors.json").read_text(encoding="utf-8"))["cases"])
    with cw.edge_page(1280, 900) as page:
        page.goto(COMPARE.as_uri() + "?selftest=1")
        _wait_for(page, "document.querySelector('#selftest') && document.querySelector('#selftest').textContent.length > 0", "自己確認")
        assert page.js("document.querySelector('#selftest').textContent") == f"SELFTEST PASS {n + 50}/{n + 50}"


# ---------------------------------------------------------------- ドライバーの 1 枚（Edge）
def test_driver_day_sheet_follows_the_paper_report_order_and_back_link_exists():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(390, 844) as page:
        page.goto(DRIVER.as_uri() + "?x=sheet")
        _wait_for(page, "!!document.querySelector('#sheet')", "ドライバーの 1 枚")
        page.js("""(function(){
          const mk = (id, date, place, a, s, e, d) => ({id, date, place, arrive: a, start: s, end: e, depart: d, vno: '1234', partner: '○○'});
          localStorage.setItem('driver_records_v1', JSON.stringify([mk('a', '10/5', '尼崎', '07:50', '09:40', '10:30', '10:40'), mk('b', '10/5', '江東 B', '23:30', '00:20', '', '')]));
        })()""")
        page.goto(DRIVER.as_uri() + "?x=sheet2")
        _wait_for(page, "!!document.querySelector('#day')", "日の選択")
        page.js("(function(){const s=document.querySelector('#day'); s.value='10/5'; s.dispatchEvent(new Event('input',{bubbles:true}));})()")
        rows = page.js("[...document.querySelectorAll('#sheet-body tr:not(.blank)')].map((tr) => [...tr.children].filter((td) => !td.classList.contains('pc')).map((td) => td.textContent).join('|'))")
        assert rows == ["10/5|尼崎|○○|7:50|9:40|10:30|10:40|110", "10/5|江東 B|○○|23:30|0:20|||50"], rows     # 日をまたぐ回は翌日として数える
        assert page.js("document.querySelector('#b-home').getAttribute('href')") == "index.html"
        assert page.js("document.documentElement.scrollWidth") <= 390
        small = page.js("[...document.querySelectorAll('button, input, select')].filter((b) => b.getBoundingClientRect().width > 0 && b.getBoundingClientRect().height < 43.5).map((b) => b.id || b.textContent)")
        assert small == [], small
