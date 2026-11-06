"""SPEC §39 書面を相手に出せる形に（受入条件 G180〜G184）。

- G180: 運送会社向けの注意（PROTECT[0]）は画面の書面の近くに出し、印刷とコピーする書面には入れない。
- G181: 差出人（会社名・担当者・連絡先）と交付日の欄。書面とコピーの文に入り、保存され、「保存した記録を消す」で消える。
  空なら書き入れの案内のまま。そろったら印刷とコピーの題から（下書き）を外す（画面には残す）。
- G182: 結びの一文は「上記の荷待ち・荷役」（記録の一覧は一文より上にある）。
- G183: 「例を入れる」と貼り付けのあとは、契約の相手ごとの表まで送る。?example=1 と自動の例では送らない。
- G184: 積込料・取卸料は荷役の時間全体を荷役開始から 30 分ごとに区切り、2 時間の境より後に始まる区切りだけ 2 時間超の額。
  回数は ceil(荷役の分 ÷ 30) を超えない。Python・JS・試験表がそろう。
画面の確認はヘッドレス Edge（環境変数 EDGE_PATH で Chrome も可）。無い環境では飛ばす。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import waiting  # noqa: E402

TEMPLATE = (ROOT / "tools" / "waiting_page_template.html").read_text(encoding="utf-8")
PAGE = ROOT / "web" / "waiting.html"
ART = ROOT / "web" / "publish" / "waiting_artifact.html"
HTML = PAGE.read_text(encoding="utf-8")
ART_HTML = ART.read_text(encoding="utf-8")
SHARED = (ROOT / "web" / "waiting_calc.js").read_text(encoding="utf-8")
VECTORS = json.loads((ROOT / "tests" / "data" / "waiting_vectors.json").read_text(encoding="utf-8"))
CAUTION = "請求するか、いくらにするかは運送会社が決めます"


def _fn(src: str, name: str) -> str:
    """function name(...) { ... } の本体を、次の行頭の } まで切り出す。"""
    i = src.index(f"function {name}(")
    return src[i:src.index("\n}\n", i)]


# ---------------------------------------------------------------- G180 注意を書面の外へ
@pytest.mark.parametrize("src", [HTML, ART_HTML], ids=["local", "artifact"])
def test_g144_caution_is_not_in_letter_html_or_copied_text(src):
    assert "printnote" not in src
    for name in ("draftHtml", "draftText"):
        body = _fn(src, name)
        assert "PROTECT" not in body, name
        assert "弁護士" not in body, name
    # 画面には書面の近く（#draft-area の中、書面の外）に出す
    rr = _fn(src, "renderResult")
    assert 'id="doc-caution"' in rr and "PROTECT[0]" in rr
    assert rr.index('id="doc-caution"') < rr.index('<article id="wait-doc">')


def test_g144_caution_and_sender_form_are_hidden_in_print():
    m = re.search(r"@media print \{(.*?)\n\}\n</style>", HTML, re.S)
    assert m
    hide = next(ln for ln in m.group(1).splitlines() if "display: none !important" in ln)
    for sel in ("#doc-caution", "#sender", ".protect"):
        assert sel in hide, sel
    assert "#wait-doc .draft-mark.ready { display: none; }" in m.group(1)


# ---------------------------------------------------------------- G181 差出人と交付日
def test_g145_sender_fields_and_storage():
    for k in ("company", "person", "contact", "date"):
        assert f'data-s="{k}"' in TEMPLATE, k
    for label in ("会社名", "担当者", "連絡先（任意）", "交付日"):
        assert f"<label>{label}<input" in TEMPLATE, label
    assert 'type="date" class="s-in" data-s="date"' in TEMPLATE
    # 保存は記録と別の鍵。消すときは両方。読み書きは try の中
    assert "const SENDER_KEY = 'waiting_page_sender_v1';" in TEMPLATE
    clear = TEMPLATE[TEMPLATE.index("function storeClear"):]
    clear = clear[:clear.index("\n")]
    assert "removeItem(STORE_KEY)" in clear and "removeItem(SENDER_KEY)" in clear
    for fn in ("function senderSave", "function senderLoad"):
        i = TEMPLATE.index(fn)
        assert "try {" in TEMPLATE[i:i + 80], fn
    assert "S.sender = { company: '', person: '', contact: '', date: todayIso() };" in _fn(TEMPLATE, "forgetRecords")
    # 空なら案内のまま
    assert "（運送会社の名称・担当者を書き入れてください）" in TEMPLATE and "（交付日を書き入れてください）" in TEMPLATE


def test_g145_letter_uses_sender_and_drops_draft_only_when_ready():
    h, t = _fn(TEMPLATE, "draftHtml"), _fn(TEMPLATE, "draftText")
    assert "senderText()" in h and "issueDateText()" in h
    assert "senderText()" in t and "issueDateText()" in t
    assert '<span class="draft-mark${senderReady() ? \' ready\' : \'\'}">（下書き）</span>' in h
    assert "DOC_TITLE + (senderReady() ? '' : '（下書き）')" in t
    assert "書き入れてください" not in h and "書き入れてください" not in t   # 固定の案内は書面の関数に直に書かない


# ---------------------------------------------------------------- G182 結びの一文
@pytest.mark.parametrize("src", [TEMPLATE, HTML, ART_HTML], ids=["template", "local", "artifact"])
def test_g146_closing_sentence_says_above(src):
    assert "下記の荷待ち・荷役" not in src
    assert src.count("上記の荷待ち・荷役について、標準的運賃の表に当てた参考額は計") == 2   # 画面の書面とコピーの文
    h = _fn(src, "draftHtml")
    assert h.index("<h4>記録の一覧</h4>") < h.index("上記の荷待ち・荷役")


# ---------------------------------------------------------------- G183 結果まで送る
def test_g147_jump_only_after_button_and_paste():
    assert "function jumpToResult()" in TEMPLATE
    assert "else { disarmExample(); putExample(); jumpToResult(); }" in TEMPLATE  # 例を入れる（§38 の 2 回押しのあと）
    assert "if (inp.length) jumpToResult();" in _fn(TEMPLATE, "pasteIn")
    init = _fn(TEMPLATE, "init")
    tail = init[init.index("qs.get('example') === '1'"):]
    assert "jumpToResult" not in tail   # ?example=1 と自動の例では送らない
    assert "prefers-reduced-motion" in _fn(TEMPLATE, "jumpToResult")


# ---------------------------------------------------------------- G184 境をまたぐ積込料・取卸料
def _one(**kw):
    base = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A運輸", "arrive": "09:00", "start": "10:00",
            "end": "", "handler": "運転者", "method": "フォークリフト等", "shipper_fault": "はい"}
    base.update(kw)
    res = waiting.calc([base], 2026)
    assert not res["errors"], res["errors"]
    return res["rows"][0]


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def test_g148_thirty_minute_task_across_boundary_is_one_unit():
    r = _one(start="10:50", end="11:20")
    assert (r["handling_normal_units"], r["handling_over_units"], r["handling_fee"]) == (1, 0, 2080)


def test_g148_units_never_exceed_ceil_total_and_over_only_after_boundary():
    for s in range(60, 180, 5):            # 荷役開始 = 到着 + s 分（境は +120 分）
        for d in range(1, 125, 7):
            r = _one(start=_hm(9 * 60 + s), end=_hm(9 * 60 + s + d))
            total = -(-d // 30)
            assert r["handling_normal_units"] + r["handling_over_units"] == total, (s, d)
            # 区切り k（荷役開始 + 30k から）の始まりが境より前なら通常の額
            normal = sum(1 for k in range(total) if s + 30 * k < 120)
            assert r["handling_normal_units"] == normal, (s, d)


def test_g148_python_js_and_vectors_agree():
    case = next(c for c in VECTORS["cases"] if c["name"] == "driver_handling_across_2h_boundary")
    row = case["expect"]["rows"][0]
    assert (row["handling_normal_units"], row["handling_over_units"], row["handling_fee"]) == (1, 0, 2080)
    assert "const totalUnits = ceilDiv(before + after, unit);" in SHARED
    assert "hnUnits = Math.min(totalUnits, ceilDiv(before, unit)); hoUnits = totalUnits - hnUnits;" in SHARED
    py = (ROOT / "src" / "shipcompare" / "waiting.py").read_text(encoding="utf-8")
    assert "hn_units = min(total_units, _ceil_div(before, unit))" in py
    # 同じ JS がページに埋め込まれている（ページの ?selftest=1 が試験表を通す）
    assert "const totalUnits = ceilDiv(before + after, unit);" in HTML and "const totalUnits = ceilDiv(before + after, unit);" in ART_HTML


@pytest.mark.parametrize("src", [HTML, ART_HTML], ids=["local", "artifact"])
def test_g148_rule_is_stated_in_basis(src):
    assert "const HANDLING_RULE = '積込料・取卸料は、荷役の時間全体を荷役開始から 30 分までごとに区切って数えます" in src
    assert "  HANDLING_RULE,\n];" in src


# ---------------------------------------------------------------- 画面（ヘッドレス Edge）
def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _wait(page, expr: str, timeout: float = 8.0):
    end = time.time() + timeout
    while time.time() < end:
        if page.js(expr):
            return
        time.sleep(0.1)
    raise AssertionError(f"待ちきれませんでした: {expr}")


def _type(page, key: str, val: str) -> None:
    page.js(f"(()=>{{const e=document.querySelector('.s-in[data-s={key}]'); e.value={json.dumps(val, ensure_ascii=False)}; e.dispatchEvent(new Event('input',{{bubbles:true}}));}})()")


def test_g144_g145_g147_in_headless_edge():
    _edge_or_skip()
    import capture_web as cw
    url = PAGE.as_uri()
    with cw.edge_page(1366, 900) as page:
        page.goto(url + "?example=1")
        _wait(page, "!!document.querySelector('#wait-doc table.rec')")
        time.sleep(0.5)
        assert page.js("window.scrollY") == 0          # ?example=1 では送らない
        page.js("localStorage.removeItem('waiting_page_sender_v1')")
        # 空のときは案内のまま、コピーの文の題に（下書き）
        txt = page.js("draftText(S.res.by_partner[0])")
        assert txt.startswith("荷待ち・荷役に関する協議のお願い（下書き）")
        assert "差出人: （運送会社の名称・担当者を書き入れてください）" in txt
        assert CAUTION not in txt and "上記の荷待ち・荷役" in txt and "下記の" not in txt
        assert CAUTION in page.js("document.querySelector('#doc-caution').textContent")
        assert CAUTION not in page.js("document.querySelector('#wait-doc').textContent")
        # 印刷の見た目: 注意も欄も出ず、（下書き）は残る
        page.send("Emulation.setEmulatedMedia", media="print")
        printed = page.js("document.body.innerText")
        assert CAUTION not in printed and "会社名" not in printed and "（下書き）" in printed
        page.send("Emulation.setEmulatedMedia", media="")
        # 差出人を入れる
        _type(page, "company", "テスト運送株式会社")
        _type(page, "person", "配車係 山田")
        _type(page, "contact", "06-0000-0000")
        assert page.js("document.querySelector('#doc-sender').textContent") == "テスト運送株式会社　担当 配車係 山田　連絡先 06-0000-0000"
        assert re.fullmatch(r"\d{4} 年 \d{1,2} 月 \d{1,2} 日", page.js("document.querySelector('#doc-date').textContent"))
        txt = page.js("draftText(S.res.by_partner[0])")
        assert txt.startswith("荷待ち・荷役に関する協議のお願い\n") and "差出人: テスト運送株式会社" in txt
        assert "（下書き）" in page.js("document.querySelector('#wait-doc h3').textContent")   # 画面には残す
        page.send("Emulation.setEmulatedMedia", media="print")
        printed = page.js("document.body.innerText")
        assert "（下書き）" not in printed and "テスト運送株式会社" in printed and CAUTION not in printed
        page.send("Emulation.setEmulatedMedia", media="")
        assert json.loads(page.js("localStorage.getItem('waiting_page_sender_v1')"))["company"] == "テスト運送株式会社"
        # 「例を入れる」で表まで送る。欄の値は残る
        page.js("window.scrollTo(0, 0)")
        page.js("document.querySelector('#b-example').click()")
        _wait(page, "Math.abs(document.querySelector('#partners').getBoundingClientRect().top) < 40")
        assert page.js("document.querySelector('.s-in[data-s=company]').value") == "テスト運送株式会社"
        # 開き直すと会社名が戻り、交付日は今日
        page.goto(url)
        _wait(page, "!!document.querySelector('.s-in[data-s=company]')")
        assert page.js("document.querySelector('.s-in[data-s=company]').value") == "テスト運送株式会社"
        assert page.js("window.scrollY") == 0          # 初めて開いたときの自動の例でも送らない
        # 保存した記録を消すと差出人も消える
        page.js("document.querySelector('#b-forget').click()")
        assert page.js("localStorage.getItem('waiting_page_sender_v1')") is None
        assert page.js("S.sender.company") == ""
        # 貼り付けのあとも表まで送る
        page.js("document.querySelector('#paste').value = '10/6\\t\\t中型\\tB商事\\t\\t8:00\\t8:40\\t9:10\\t9:30\\t運転者\\tフォークリフト等\\tはい'; window.scrollTo(0, 0)")
        page.js("document.querySelector('#b-paste').click()")
        _wait(page, "Math.abs(document.querySelector('#partners').getBoundingClientRect().top) < 40")


def test_g148_selftest_passes_in_headless_edge():
    _edge_or_skip()
    import capture_web as cw
    n = len(VECTORS["cases"]) + 4
    with cw.edge_page(1366, 900) as page:
        page.goto(PAGE.as_uri() + "?selftest=1")
        _wait(page, "document.title.startsWith('SELFTEST')")
        assert page.js("document.title") == f"SELFTEST PASS {n}/{n}"
