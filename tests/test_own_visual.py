"""自社の運行: 2026-11-08 の画面の目視（docs/reviews/2026-11-08_画面の目視_自社の運行.md）X1〜X5 と軽い点の回帰試験。"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import edge_page, edge_path_or_skip

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from shipcompare import own  # noqa: E402

PAGE = ROOT / "web" / "own_lite.html"
WEB = ROOT / "web"


def J(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def x1_csv() -> str:
    """見出しが「稼働日」「号機」「出し元」…の配車表。14 日 × 往復 = 28 区間。"""
    rows = ["稼働日,号機,出し元,届け先,出庫,帰着,実績値,区分"]
    for d in range(1, 15):
        ds = f"2026/10/{d:02d}"
        rows.append(f"{ds},相模 100 あ 1111,厚木市,江東区,06:00,10:00,60,実")
        rows.append(f"{ds},相模 100 あ 1111,江東区,厚木市,12:00,15:00,60,空")
    return "\n".join(rows) + "\n"


ROLES = {"号機": "plate", "出し元": "from", "帰着": "time", "実績値": "distance_km"}


@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        page.js("""new Promise((ok, ng) => { let n = 0; for (const f of ['/own_calc.js', '/own_month.js']) {
          const s = document.createElement('script'); s.src = f; s.onload = () => { if (++n === 2) ok(); }; s.onerror = () => ng(new Error(f)); document.head.appendChild(s); } })""")
        assert page.js("typeof OwnCalc === 'object' && typeof OwnMonth === 'object'")
        yield page


def an(page, text: str, opts: dict | None = None) -> dict:
    return page.js(f"OwnCalc.analyze([{{ name: 'a.csv', text: {J(text)} }}], {J(opts or {})})")


# ---------------------------------------------------------------- X1: 「稼働日」は日付の見出し

def test_x1_kado_bi_is_a_date_header_and_the_file_reads_28_legs(calc):
    assert calc.js("OwnCalc.haishaRole ? OwnCalc.haishaRole('稼働日') : 'date'") == "date"
    d = an(calc, x1_csv(), {"column_roles": ROLES})
    assert d["failed"] is None and len(d["legs"]) == 28


def test_x1_other_kado_headers_are_still_ignored(calc):
    if calc.js("typeof OwnCalc.haishaRole") != "function":
        pytest.skip("haishaRole は公開されていない")
    assert calc.js("OwnCalc.haishaRole('稼働率')") == "skip" and calc.js("OwnCalc.haishaRole('稼働台数')") == "skip"


def test_x1_python_twin_knows_kado_bi():
    assert own.haisha_role("稼働日") == "date" and own.haisha_role("稼働率") == "skip"


# ---------------------------------------------------------------- X3: 「毎週」は 3 回以上かつ 8 割以上のときだけ

def x3_csv() -> str:
    """厚木 → 江東 → 厚木。帰りが空なのは土曜（毎週・2 台とも）と、水曜（4 日のうち 3 日だけ 1 台、8 便中 3 便）。"""
    rows = ["日付,車番,発地,着地,重量,実空"]
    plates = ["相模 100 あ 1111", "相模 100 あ 2222"]
    for d in range(1, 32):
        wd = date(2026, 10, d).weekday()
        if wd == 6:
            continue
        for k, p in enumerate(plates):
            ds = f"2026/10/{d:02d}"
            rows.append(f"{ds},{p},厚木市,江東区,3.5t,実")
            empty = wd == 5 or (wd == 2 and k == 0 and d != 28)
            rows.append(f"{ds},{p},江東区,厚木市,,空" if empty else f"{ds},{p},江東区,厚木市,2.0t,実")
    return "\n".join(rows) + "\n"


def test_x3_board_text_says_weekly_only_for_the_weekday_that_is_mostly_empty(calc):
    d = an(calc, x3_csv())
    assert d["failed"] is None
    texts = calc.js(f"OwnMonth.boardTexts({J(d)}).map((t) => t.text)")
    joined = "\n".join(texts)
    assert "毎週土曜" in joined
    assert "毎週土曜・水曜" not in joined and "毎週水曜" not in joined
    assert "水曜" not in joined   # 2026-11-08: 掲示には規則に合う曜日だけ書く。合わない水曜は並べない


def test_x3_day_text_rule_is_in_the_source():
    src = (WEB / "own_month.js").read_text(encoding="utf-8")
    assert "0.8" in src and "実績:" in src   # 日本語の直し 2026-11-08: 掲示に書く曜日は規則（8 割）に合うものだけ。合わないときは日付と「実績:」の行


# ---------------------------------------------------------------- X2・X4・軽い点: 文言（ソース）

def test_x2_claude_text_uses_japanese_role_names():
    src = (WEB / "own.js").read_text(encoding="utf-8")
    assert "'Claude の提案: ' + roleJa(c.role)" in src
    assert "vehicle_type: '車種・トン数'" in src and "place_name: '停車した場所'" in src


def test_x4_draft_note_does_not_claim_t7_came_from_the_saturday_night():
    src = (WEB / "own.js").read_text(encoding="utf-8")
    assert "11/13 の帰り便 T7" not in src and "この下書きから登録した" not in src
    carriers = json.loads((ROOT / "data" / "demo" / "carriers.json").read_text(encoding="utf-8"))
    t7 = next(t for t in json.dumps(carriers, ensure_ascii=False) and _trips(carriers) if t.get("id") == "T7")
    assert t7["depart_at"].startswith("2026-11-13")   # 金曜発。文は曜日と結びつけない


def _trips(o):
    if isinstance(o, dict):
        if "id" in o and "depart_at" in o:
            yield o
        for v in o.values():
            yield from _trips(v)
    elif isinstance(o, list):
        for v in o:
            yield from _trips(v)


def test_confirm_cards_show_the_sample_sentence_once_and_failed_message_once():
    src = (WEB / "own.js").read_text(encoding="utf-8")
    assert 'id="own-eff-common"' in src and "eff === common" in src
    assert "<p class=\"o-hint\">${esc(p.failed)}" not in src          # 役割を選ぶ欄は、上の欄と同じ失敗の文を繰り返さない
    tpl = (ROOT / "tools" / "own_page_template.html").read_text(encoding="utf-8")
    assert "setMsg(`${d.failed}。下で列の中身を選ぶと読めます。`" not in tpl


# ---------------------------------------------------------------- X5・消して入れ直す（1 枚のページ）

def _open_sample(page):
    page.goto(PAGE.as_uri())
    page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
    page.js("document.querySelector('[data-act=sample]').click()")
    page.wait(lambda: page.js("!!window.OwnLite.state.data"), 60, "サンプル")


def test_x5_lite_sample_says_fictional_sample_in_header_and_print_sheet():
    edge_path_or_skip()
    with edge_page(1920, 1080, webgl=False) as page:
        _open_sample(page)
        r = page.js("""({ kpi: document.querySelector('#sec-kpi h2 small').textContent, print: document.querySelector('#printsheet').textContent })""")
        assert "架空のサンプル・車両 8 台" in r["kpi"] and "自社のファイル" not in r["kpi"]
        assert "架空のサンプル・車両 8 台" in r["print"] and "自社のファイル" not in r["print"]


def test_lite_reset_clears_the_offer_section_and_copy_message():
    edge_path_or_skip()
    with edge_page(1920, 1080, webgl=False) as page:
        _open_sample(page)
        page.js("Negotiate.state.view = 'draft'; Negotiate.state.copyMsg = 'コピーしました'; Negotiate.state.type = 'returnable'")
        page.js("document.querySelector('[data-act=reset]').click()")
        r = page.js("({ v: Negotiate.state.view, c: Negotiate.state.copyMsg, t: Negotiate.state.type })")
        assert r == {"v": "result", "c": "", "t": "commodity"}
        page.js("document.querySelector('[data-act=sample]').click()")
        page.wait(lambda: page.js("!!window.OwnLite.state.data"), 60, "サンプル")
        assert page.js("(document.querySelector('#neg-copymsg') || { textContent: '' }).textContent") == ""
        assert page.js("document.querySelector('#toast').hidden") is True
