"""SPEC §24 荷主が使う比べるページ web/compare_lite.html（G88〜G91）と、荷待ちのページの印刷・保存・CSV の読み戻し（G85〜G87）。"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_compare_page  # noqa: E402
import build_compare_vectors  # noqa: E402

PAGE = ROOT / "web" / "compare_lite.html"
WAITING = ROOT / "web" / "waiting.html"
VECTORS = ROOT / "tests" / "data" / "compare_lite_vectors.json"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


# ---------------------------------------------------------------- G85 印刷
def test_g85_waiting_print_is_a4_portrait_and_not_cut(tmp_path):
    _edge_or_skip()
    import print_page_pdf as pp
    pdf = tmp_path / "test_waiting_print.pdf"
    pp.print_to_pdf(WAITING.as_uri() + "?example=1", pdf)
    pages = pp.pdf_page_count(pdf)
    assert pages == 2, pages  # 書面の作り直し 2026-11-07: 1 枚目にメール（本文・記録の表・署名）、2 枚目に添付・参考
    info = pp.pdf_info(pdf)
    if info is None:
        pytest.skip("pymupdf が使えません（ページ数だけ確かめました）")
    assert len(info) == pages
    assert all(abs(p["w_pt"] - 595) < 2 and abs(p["h_pt"] - 842) < 2 for p in info), info  # A4 縦
    assert info[-1]["chars"] > 0, "最後のページが白紙"
    assert all(p["chars"] > 100 for p in info[:-1])


def test_g85_print_css_does_not_scroll_or_cut_the_record_table():
    html = WAITING.read_text(encoding="utf-8")
    m = re.search(r"@media print \{(.*?)\n\}\n</style>", html, re.S)
    assert m
    css = m.group(1)
    assert "@page { size: A4 portrait" in html
    assert "#wait-doc .rec-w { overflow: visible; }" in css
    assert "white-space: normal" in css
    assert "visibility: hidden" not in css  # 隠すのは display:none（余白の白紙ページを出さない）


# ---------------------------------------------------------------- G86・G87 保存と読み戻し
def test_g86_g87_waiting_page_has_storage_and_csv_import_texts():
    html = WAITING.read_text(encoding="utf-8")
    assert "記録はこのブラウザの中にだけ保存されます。別の PC には移りません" in html
    for t in ("保存した記録を消す", "CSV を読み込む", "localStorage"):
        assert t in html, t
    assert html.count("localStorage.") >= 3
    # localStorage の読み書きはすべて try の中（使えなくてもページが動く）
    for fn in ("function storeSave", "function storeLoad", "function storeClear"):
        i = html.index(fn)
        assert "try {" in html[i:i + 120], fn


def test_g86_storage_roundtrip_and_forget_in_headless_edge():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1600, 1000) as page:
        page.goto(WAITING.as_uri())
        page.wait(lambda: page.js("!!document.querySelector('#b-example')"), 30, "ページ")
        page.js("localStorage.removeItem('waiting_page_rows_v1')")
        page.js("document.querySelector('#b-example').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "下書き")
        page.wait(lambda: page.js("!!localStorage.getItem('waiting_page_rows_v1')"), 10, "保存")
        saved = page.js("JSON.parse(localStorage.getItem('waiting_page_rows_v1')).rows.length")
        assert saved == 18
        page.goto(WAITING.as_uri())  # 開き直すと前回の記録が戻る
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "復元")
        assert page.js("document.querySelectorAll('#tbody tr').length") == 18
        assert "33,550" in page.js("document.querySelector('#partners table.par tbody tr').textContent")
        assert "前回の記録 18 行を読み込みました" in page.js("document.querySelector('#keep-msg').textContent")
        page.js("document.querySelector('#b-forget').click()")
        assert page.js("localStorage.getItem('waiting_page_rows_v1')") is None
        assert page.js("document.querySelector('#wait-doc table.rec')") is None
        # 保存が使えないブラウザでもページは動く
        page.goto(WAITING.as_uri())
        page.js("Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }}); document.querySelector('#b-example').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "保存なしの下書き")
        assert "保存が使えません" in page.js("document.querySelector('#keep-msg').textContent")


# ---------------------------------------------------------------- G88〜G91 compare_lite
def test_g88_vectors_are_what_python_generates():
    assert json.loads(json.dumps(build_compare_vectors.build(), ensure_ascii=False)) == json.loads(VECTORS.read_text(encoding="utf-8"))
    cases = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
    by = {(c["region"], c["km"], c["kg"]): {e["class"]: e["amount"] for e in c["expect"]} for c in cases}
    assert by[("関東", "500", "1")]["4t"] == 126_060 and by[("関東", "556", "1")]["4t"] == 147_960 and by[("近畿", "556", "1")]["4t"] == 140_950


def test_page_is_up_to_date():
    assert PAGE.read_text(encoding="utf-8") == build_compare_page.build()


def test_g89_page_makes_no_network_calls():
    html = PAGE.read_text(encoding="utf-8")
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource", "importScripts", "import(", "navigator.serviceWorker"):
        assert bad not in html, bad
    # 契約単価（CKEY）と荷物の表（PKEY）の保存だけ localStorage を使う（このブラウザの中だけ。送信はしない。v4.1・v4.2）
    # 見積の回答（QKEY、会社名と額を 3 社まで。SPEC §46）も同じ扱い
    assert set(re.findall(r"localStorage\.(\w+)\(([A-Za-z_]+)", html)) == {("setItem", "CKEY"), ("getItem", "CKEY"), ("setItem", "PKEY"), ("getItem", "PKEY"), ("setItem", "QKEY"), ("getItem", "QKEY"), ("setItem", "HKEY"), ("getItem", "HKEY")}
    # 出荷の記録（HKEY、日付・区間・送り方・額。このブラウザの中だけ）も同じ扱い
    assert not re.search(r"<script[^>]+src\s*=", html, re.I)
    assert not re.search(r"<link(?![^>]*href\s*=\s*.data:)[^>]+href\s*=", html, re.I)
    assert not re.search(r"@import|url\(\s*['\"]?https?:", html, re.I)
    assert not re.search(r"<(iframe|embed|object|video|audio|source|form)\b", html, re.I)
    assert not re.search(r"<img(?![^>]*src=\"data:image/webp;base64,)", html, re.I)   # 絵は data URI の WebP だけ（通信しない。2026-11-07 第 2 回）


def test_g90_page_has_the_required_texts():
    html = PAGE.read_text(encoding="utf-8")
    for t in ("目安です。最後は各社の見積で確かめてください", "使える ○", "要確認 △", "使えない ×", "道路の距離（km）", "法人・店舗・事業所", "個人宅", "待てる日数",   # 文言の直し 2026-11-07
              
              "デモの帰り便の空き（重さの余り ", "見積を頼む文", "数字の出どころ",
              "会社あての配達ができるかは分かっていません", "荷台の大きさは公表されていません", "1,000kg を超えるので、見積が要ります", "会社から会社へだけです", "容積重量", "280kg"):
        assert t in html, t
    for pref in ("北海道", "東京都", "大阪府", "沖縄県"):
        assert pref in html


def test_g91_selftest_and_s6_in_headless_edge():
    _edge_or_skip()
    import capture_web as cw
    n = len(json.loads(VECTORS.read_text(encoding="utf-8"))["cases"])
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri() + "?selftest=1")
        page.wait(lambda: page.js("document.querySelector('#selftest') && document.querySelector('#selftest').textContent.length > 0"), 30, "自己確認")
        text = page.js("document.querySelector('#selftest').textContent")
        assert text == f"SELFTEST PASS {n + 50}/{n + 50}", text
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        got = page.js("""(function(){
          const o = {}; document.querySelectorAll('table.res tr[data-opt]').forEach((tr) => { o[tr.dataset.opt] = tr.className; });
          return {o, home: !!document.querySelector('.home'), quote: document.querySelector('#quote').value,
                  sum: document.querySelector('#sum').textContent, k4: document.querySelector('tr[data-opt=kashikiri_4t] .amount').textContent};
        })()""")
    assert got["o"] == {"takuhai": "o-ng", "kazai": "o-check", "akabane": "o-ng", "kei": "o-ng", "jitbox": "o-ng", "rosen": "o-check",
                        "kashikiri_2t": "o-check", "kashikiri_4t": "o-ok", "home": "o-ng"}   # 帰り便は T7 の区間（東京都→鹿児島県）だけ。東京→大阪は ×   # 2t は高さ 200cm の荷がショートに入らず、ロングなら入る（地図の積付けと同じ。SPEC §43）
    assert not got["home"] and "126,060" in got["k4"]
    assert "合計: 4 個" in got["sum"] and "400" in got["sum"]
    q = got["quote"]
    assert "240×60×200cm、140kg を 2 個" in q and "東京都" in q and "大阪府" in q and "492km" in q and "法人" in q and "4 日" in q
    assert "円" not in q  # 値段は約束しない


# ---------------------------------------------------------------- G92・G93 貼り付けの見出しと書面の文言
def test_g92_realistic_excel_header_in_other_order_is_read_by_header():
    sys.path.insert(0, str(ROOT / "src"))
    from shipcompare import waiting
    text = ("荷主\t運行日\t備考\t車両番号\t車格\t着時刻\t指定時刻\t積込開始時刻\t積込終了時刻\t出発時刻\t荷役をした人\t荷役の方法\t荷主等の都合\n"
            "B商事\t10/7\t雨\t神戸100あ1234\t4t\t08:10\t08:30\t10:15\t10:55\t11:05\t運転者\t手積み\tはい\n")
    rows = waiting.parse_paste(text)
    assert rows[0]["partner"] == "B商事" and rows[0]["date"] == "10/7" and rows[0]["vehicle_no"] == "神戸100あ1234" and rows[0]["arrive"] == "08:10"
    assert (rows[0]["appointed"], rows[0]["start"], rows[0]["end"], rows[0]["depart"]) == ("08:30", "10:15", "10:55", "11:05")
    res = waiting.calc(rows, 2026)
    assert not res["errors"] and res["rows"][0]["wait_from"] == "指定の到着時刻" and res["rows"][0]["wait_min"] == 105
    names = {c["name"] for c in json.loads((ROOT / "tests" / "data" / "waiting_vectors.json").read_text(encoding="utf-8"))["cases"]}
    assert "paste_realistic_excel_header_other_order" in names  # JS の自己確認も同じ表を通す


def test_g93_draft_has_wait_from_appointed_wording_clause21_quote_and_shipper_handling_mark():
    own = (ROOT / "web" / "own.js").read_text(encoding="utf-8")
    for src in (WAITING.read_text(encoding="utf-8"), own):
        assert "到着（指定の到着時刻があるときは、その時刻）から ${t.free_minutes} 分を超えた時間に" in src
        assert "貸切運賃料金適用方（参考例）第 21 項（Q&A 集に収録）は、待機時間に「荷主による積込み・取卸しの時間を含む」とし" in src
        assert "貴社側で荷役をしていただいた時間も、待ち時間に含めています" in src if src is not own else "荷主側の荷役を待機に含む" in src   # 書面の作り直し 2026-11-07（荷待ちのページは新しい文言）
