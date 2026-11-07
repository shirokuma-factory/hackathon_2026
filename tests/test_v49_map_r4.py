"""第 8 部 r4 の指摘の修正（地図の画面）。

- 空きを探す（案内）: 仮予約した便は消えず、仮予約中・締切・次の操作が出る。地図の便の札はタブで止まらず、日本語の読み上げ名を持つ。
- 全国の物流傾向（案内）: 番号の札が重ならない、区間を選ぶと選んだ番号だけ。段の表示、区間を選び直すリンク、押せない主ボタンは出さない。
- 同じ文書の中で見方を何度切り替えても、コンソールの誤り（deck.gl の PathLayer の初期化）が出ない。
- 最初の画面の文言、用途を選んだあとのフォーカス、行間。
ヘッドレス Edge は同時に 1 つだけ（conftest の edge_page）。Edge が無いときだけ飛ばす。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from conftest import edge_page, open_app, server_port
from viewport_text import COUNT_JS

ROOT = Path(__file__).resolve().parents[1]
READY = "!!window.__app && !document.querySelector('#loading')"
BUDGETS = {1920: 450, 360: 300}
SIZES = [(1920, 1080), (360, 800)]
CONSOLE_HOOK = (
    "window.__errs=window.__errs||[];(function(){const e=console.error;"
    "console.error=function(){window.__errs.push('console.error: '+[...arguments].map(String).join(' ').slice(0,300));return e.apply(console,arguments);};"
    "window.addEventListener('error',function(ev){if(ev.message)window.__errs.push('error: '+ev.message);});"
    "window.addEventListener('unhandledrejection',function(ev){window.__errs.push('rejection: '+String(ev.reason).slice(0,300));});})();"
)


def _size(page, w, h):
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)


def _open(page, port, hash_=""):
    page.send("Page.addScriptToEvaluateOnNewDocument", source=CONSOLE_HOOK)
    open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}", ready_js=READY, wait_loading=False, start_timeout=180, tag="v49_map_r4_fail")


def _count(page):
    return json.loads(page.js(COUNT_JS))


def _rows(page):
    page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 60, "上位 5 の一覧")


def _pick_and_go_find(page, idx=0):
    _rows(page)
    page.js(f"document.querySelectorAll('.mg-row')[{idx}].click()")
    page.wait(lambda: page.js("(function(){const b=document.getElementById('mg-next');return !!b && !b.hidden && !b.disabled;})()") is True, 60, "主ボタン")
    page.js("document.getElementById('mg-next').click()")
    page.wait(lambda: "v=find" in page.js("location.hash"), 30, "空きを探すへ移る")
    page.wait(lambda: page.js("document.querySelectorAll('#f-list .chip.ok').length") >= 1, 90, "載る便")


def _reset_holds(page):
    page.js("fetch('/api/b/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.status)")


# ---------------------------------------------------------------- 1・2 空きを探す（案内）
@pytest.mark.parametrize("w,h", SIZES)
def test_find_hold_keeps_the_card_and_markers_are_not_tab_stops(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=market&g=1")
        try:
            _pick_and_go_find(page, 1)  # 上位 5 の 1 位（東京都 → 兵庫県）には鹿児島版のサンプルの便が無いので 2 位を使う
            time.sleep(1.5)
            # 地図の便の札: タブで止まらず、日本語の読み上げ名（便の名前）を持つ
            tags = json.loads(page.js("JSON.stringify([...document.querySelectorAll('.f-tag')].map(e => [e.tabIndex, e.getAttribute('aria-label')]))"))
            assert tags and all(t[0] == -1 and t[1] and "Map marker" not in t[1] and re.search(r"[ぁ-んァ-ン一-龥]", t[1]) for t in tags), tags
            m0 = _count(page)
            assert m0["controls"] <= 8 and m0["primary"] <= 1 and m0["chars"] <= BUDGETS[w], m0
            assert page.js("document.querySelectorAll('#g-steps li').length") == 2
            assert page.js("document.querySelector('#g-steps [aria-current=step]').textContent.replace(/\\s+/g,'')") == "2空いた帰り便を探す"   # 文言の直し 2026-11-07
            # 仮予約すると、その便が残り「仮予約中」と次の操作が出る
            page.js("document.querySelector('#f-list .f-card.ok [data-act=hold]').click()")
            page.wait(lambda: page.js("document.querySelectorAll('#f-list .f-card.hold-mine').length") == 1, 60, "仮予約の札")
            time.sleep(0.8)
            card = json.loads(page.js("""JSON.stringify((function(){const c=document.querySelector('#f-list .f-card.hold-mine'); const r=c.getBoundingClientRect();
              return {shown: getComputedStyle(c).display !== 'none' && r.height > 0, text: c.innerText, visible: [...document.querySelectorAll('#f-list .f-card')].filter(e => getComputedStyle(e).display !== 'none').length};})())"""))
            assert card["shown"] and card["visible"] == 1, card
            assert "仮予約中" in card["text"] and "取り消されます" in card["text"]  # 文言の直し 2026-11-07 and "承諾" in card["text"], card
            m1 = _count(page)
            assert m1["controls"] <= 8 and m1["primary"] <= 1 and m1["chars"] <= BUDGETS[w], m1
            assert not page.js("window.__errs.length"), page.js("JSON.stringify(window.__errs)")
        finally:
            _reset_holds(page)


@pytest.mark.parametrize("w,h", SIZES)
def test_find_has_repick_link_that_returns_to_market_with_the_selection(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=market&g=1")
        _pick_and_go_find(page, 1)
        link = json.loads(page.js("""JSON.stringify((function(){const b=document.getElementById('f-repick'); const r=b.getBoundingClientRect(); return {text:b.textContent.trim(), shown: r.width>0 && r.height>0 && r.top>=0 && r.bottom<=innerHeight};})())"""))
        assert link["text"] == "行き先の県を選び直す" and link["shown"], link
        k = page.js("(function(){const p=__app.Find.state.pair, ix=__app.Market.state.prefIdx; return ix.get(p.from)+'-'+ix.get(p.to);})()")
        page.js("document.getElementById('f-repick').click()")
        page.wait(lambda: "v=market" in page.js("location.hash"), 30, "全国の物流傾向へ戻る")
        page.wait(lambda: page.js("__app.Market.state.sel") == k, 30, "選んだ区間のまま")
        assert page.js("document.querySelector('#g-steps [aria-current=step]').textContent.replace(/\\s+/g,'')") == "1行き先の県を選ぶ"   # 文言の直し 2026-11-07
        assert page.js("document.querySelector('.mg-row[aria-pressed=true]') !== null") is True


# ---------------------------------------------------------------- 3・5・6 全国の物流傾向（案内）
@pytest.mark.parametrize("w,h", SIZES)
def test_market_numbers_do_not_overlap_and_only_the_selected_one_stays(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=market&g=1")
        _rows(page)
        time.sleep(2.5)
        # 区間を選ぶ前: 押せない主ボタンは出さない。段の表示は「1 区間を選ぶ」
        assert page.js("document.getElementById('mg-next').hidden") is True
        assert page.js("document.querySelector('#g-steps [aria-current=step]').textContent.replace(/\\s+/g,'')") == "1行き先の県を選ぶ"   # 文言の直し 2026-11-07
        m0 = _count(page)
        assert m0["controls"] <= 8 and m0["primary"] == 0 and m0["chars"] <= BUDGETS[w], m0
        pos = json.loads(page.js("JSON.stringify([...document.querySelectorAll('.m-num')].map(e => {const r=e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2];}))"))
        assert len(pos) == 5, pos
        for i in range(len(pos)):
            for j in range(i + 1, len(pos)):
                d = ((pos[i][0] - pos[j][0]) ** 2 + (pos[i][1] - pos[j][1]) ** 2) ** 0.5
                assert d >= 20, (i + 1, j + 1, d, pos)   # 札の直径は 24px。中心が 20px 以上離れていれば、数字は読める
        page.js("document.querySelectorAll('.mg-row')[1].click()")
        time.sleep(3.2)
        assert page.js("document.querySelectorAll('.m-num').length") == 1
        assert page.js("document.querySelector('.m-num').textContent") == "2"
        m1 = _count(page)
        assert m1["controls"] <= 8 and m1["primary"] <= 1 and m1["chars"] <= BUDGETS[w], m1
        assert not page.js("window.__errs.length"), page.js("JSON.stringify(window.__errs)")


def test_market_selection_stays_top_down_and_vehicle_is_larger():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=market&g=1")
        _rows(page)
        page.js("document.querySelectorAll('.mg-row')[0].click()")
        time.sleep(3.0)
        assert page.js("Math.abs(__app.map().getPitch())") < 0.5 and page.js("Math.abs(__app.map().getBearing())") < 0.5
        js = (ROOT / "web" / "market.js").read_text(encoding="utf-8")
        assert "innerWidth < 600 ? 2.6 : 3.2" in js
    with edge_page(1920, 1080) as page:   # 動きを減らす設定でも真上のまま
        _size(page, 1920, 1080)
        page.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        _open(page, port, "#v=market&g=1")
        _rows(page)
        page.js("document.querySelectorAll('.mg-row')[0].click()")
        time.sleep(0.8)
        assert page.js("Math.abs(__app.map().getPitch())") < 0.5


# ---------------------------------------------------------------- 7 見方の切り替え
def test_switching_views_in_one_document_raises_no_console_errors():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=market")
        time.sleep(2.0)
        for _ in range(2):
            for v in ["find", "compare", "own", "market"]:
                page.js(f"__app.setView('{v}')")
                time.sleep(1.4)
        errs = page.js("JSON.stringify(window.__errs)")
        assert errs == "[]", errs


def test_static_layers_are_rebuilt_when_deck_has_finalized_them():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "Finalized" in app and "usedHere" in app


# ---------------------------------------------------------------- 8 凡例
def test_compare_guided_legend_keeps_the_rest_in_the_closed_toggle():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=compare&g=1")
        time.sleep(1.5)
        assert page.js("document.querySelectorAll('#g-legend .g-k:not(.g-more):not(.g-src)').length") <= 3
        assert page.js("[...document.querySelectorAll('#g-legend .g-more')].every(e => getComputedStyle(e).display === 'none')") is True
        assert page.js("document.getElementById('attr-toggle').textContent.trim()") == "色の見方と出典"   # 文言の直し 2026-11-07
        page.js("document.getElementById('attr-toggle').click()")
        assert page.js("[...document.querySelectorAll('#g-legend .g-more')].every(e => getComputedStyle(e).display !== 'none')") is True


# ---------------------------------------------------------------- 9・10・11 最初の画面とフォーカス・行間
def test_home_roles_use_short_heading_and_action_buttons():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert '<h1 id="home-h" tabindex="-1">どちらの作業をしますか</h1>' in html
    assert "荷物の条件を入れる</button>" in html and "運行記録を見る</button>" in html and "はこちら" not in html
    assert "　" not in re.search(r'<h1 id="home-h"[^>]*>(.*?)</h1>', html).group(1)


@pytest.mark.parametrize("role,sel,view", [("#role-shipper", "#p-ship-compare", "compare"), ("#role-carrier", "#p-car-empty", "own")])
def test_focus_moves_to_the_view_heading_after_choosing_a_purpose(role, sel, view):
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        page.send("Page.addScriptToEvaluateOnNewDocument", source=CONSOLE_HOOK)
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 30, "最初の画面")
        page.js(f"document.querySelector('{role}').click()")
        page.wait(lambda: page.js(f"!document.querySelector('{sel}').closest('[hidden]')"), 15, "用途の選択")
        page.js(f"document.querySelector('{sel}').click()")
        page.wait(lambda: page.js(f"document.body.classList.contains('view-{view}')"), 120, "画面")
        page.wait(lambda: page.js("(function(){const a=document.activeElement; return !!a && a !== document.body && /^H[1-3]$/.test(a.tagName);})()"), 90, "見出しにフォーカス")


def test_body_line_height_is_1_7_and_guided_notes_are_at_least_13px():
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    assert re.search(r"body \{[^}]*line-height: 1\.7;", css, re.S)
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=market&g=1")
        _rows(page)
        page.js("document.querySelectorAll('.mg-row')[0].click()")
        time.sleep(2.5)
        small = json.loads(page.js("""JSON.stringify((function(){const out=[]; for(const e of document.querySelectorAll('#m-guide *, #g-legend *, #g-steps *, #guide-bar *')){ if(e.closest('.m-num,.m-end')) continue; const r=e.getBoundingClientRect(); if(!r.width||!r.height) continue; if(![...e.childNodes].some(n=>n.nodeType===3&&n.nodeValue.trim())) continue; const f=parseFloat(getComputedStyle(e).fontSize); if(f<13) out.push([e.className||e.tagName, f, e.textContent.trim().slice(0,16)]);} return out;})())"""))
        assert not small, small


def test_guided_market_shows_no_survey_year_or_caveat_sentences():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=market&g=1")
        _rows(page)
        page.js("document.querySelectorAll('.mg-row')[0].click()")
        time.sleep(2.5)
        txt = page.js("document.body.innerText")
        for bad in ["2021", "3 日間", "年間の量", "募集中", "公開統計"]:
            assert bad not in txt, bad
        # 出典の 1 行は、閉じた「凡例・出典」の中だけ
        assert page.js("getComputedStyle(document.querySelector('#g-legend .g-src')).display") == "none"
        assert "物流センサス（国土交通省、e-Stat）を加工して作成" in page.js("document.querySelector('#g-legend .g-src').textContent")
