"""SPEC §33 の #1・#3・#4・#7・#8・#10・#11・#13（下のパネルの高さ、横棒の図、favicon、手段の名前、画面の名前、市場の要約、入口のボタン）。

静的な確認（文字列）と、node での横棒の図の確認と、ヘッドレス Edge での画面の確認。
node や Edge が無い環境では、その確認を飛ばす。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from test_v31_market_routes import _browser

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

CONSOLE_JS = ("window.__con=[];['warn','error'].forEach(k=>{const o=console[k].bind(console);"
              "console[k]=(...a)=>{window.__con.push(k+': '+a.join(' '));o(...a)}});")


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _watch(page) -> None:
    """コンソールの警告・誤りを window.__con に集める（ページを開く前に呼ぶ）。"""
    page.send("Page.addScriptToEvaluateOnNewDocument", source=CONSOLE_JS)


# ---------------------------------------------------------------- 静的
def test_g143_1_sheet_grows_for_detail_tab_without_writing_body_classes():
    js, css = read("sheet.js"), read("style.css")
    assert "SHEET_RATIO_DETAIL = 0.6" in js and "document.documentElement.style.setProperty('--sheet-h'" in js
    assert "on[0] === 'detail'" in js
    assert "if (st0.ratio === r) return;" in js  # 値が同じなら書かない
    assert "max-height: calc(var(--sheet-h) - 8px - var(--tabs-h))" in css


def test_g143_4_favicon_is_a_data_uri():
    assert 'rel="icon" href="data:image/svg+xml' in read("index.html")


def test_g143_8_mode_labels_are_rail_and_ship():
    app = read("app.js")
    assert "{ key: 'rail', label: '鉄道' }, { key: 'ferry', label: '船' }" in app
    assert "label: '電車'" not in app and "label: 'フェリー'" not in app


def test_g143_10_app_name_is_backfull():
    html = read("index.html")
    assert "<title>Backfull - 帰り便と中小の荷主の荷物をつなぐ</title>" in html  # 文言の直し 2026-11-07
    assert '<h1 id="home-h" tabindex="-1">どちらの作業をしますか</h1>' in html   # 見出しは 1 つの短い問い（第 8 部 r4）。名前は title
    assert '<h1 class="p-title" tabindex="-1">送り方を比べる</h1>' in html and "スポット出荷マップ</b>" not in html


def test_g143_11_summary_separates_the_two_ranges():
    html = read("index.html")
    assert "営業用の計" in html and "38.97%" in html and "と比べるのはこの値" in html
    assert "営業用普通車だけの範囲で、別の値です" in html and 'id="sum-load-ref"' in html


def test_g143_13_entry_button_has_origin_destination_on_its_own_line():
    mk, css = read("market.js"), read("style.css")
    assert 'class="mp-go-sub"' in mk and ".mp-go-main, .mp-go-sub { display: block; white-space: nowrap;" in css


# ---------------------------------------------------------------- 横棒の図（Edge の中で部品を動かす）
def _card(label, amount=None, **kw):
    return {"label": label, "amount": amount, "status": "公開条件に合う" if amount else "問い合わせ", **kw}


def test_g143_3_7_chart_sentence_ratio_and_ship_row():
    one = [_card("宅配"), _card("路線便"), _card("カゴ台車"), _card("貸切", 52000),
           _card("鉄道", None, status="計算できない"), _card("船", None, status="計算できない")]
    two = [_card("宅配", 20000), _card("貸切", 52000)]
    base = [_card("貸切", 52000)]
    ship_none = base + [_card("船", None, status="計算できない", routeNone=True)]
    ship_ask = base + [_card("船", None, status="計算できない", routeNone=False)]
    with _browser(1920, 1080) as (page, open_):
        _watch(page)
        open_("#v=compare&o=13104&l=km")
        h = lambda cards: page.js(f"CompareChart.html({json.dumps(cards, ensure_ascii=False)})")  # noqa: E731
        html = h(one)
        assert "条件がそろって額が出るのは貸切だけです。宅配・路線便・カゴ台車は料金表がないため、問い合わせが要ります。" in html  # 文言の直し 2026-11-07
        assert "倍" not in html and "最安" not in html
        html = h(two)
        assert "約 2.6 倍" in html and "額が出るのは" not in html
        none_html = h(ship_none)   # S11: 経路の表に無いだけなので「船が無い」とは言わず、見積を頼む形で選べる
        assert "このデモの航路の表に入っていません。船会社に見積を頼んでください。" in none_html and "つながる航路がありません" not in none_html
        assert 'value="船"' in none_html
        ask = h(ship_ask)
        assert "つながる航路がありません" not in ask and "日数と額は見積で確認" in ask   # 鉄道・船は額を出さない（v5.0: 公表の運賃表を持たない）
        assert "金額は見積で確かめます" in ask   # 文言の直し 2026-11-07
        assert not page.js("window.__errs.length") and not page.js("window.__con.length"), page.js("window.__con")


# ---------------------------------------------------------------- 画面（Edge）
def test_g143_1_narrow_compare_detail_tab_grows_to_60_percent_and_map_keeps_35():
    with _browser(400, 800) as (page, open_):
        _watch(page)
        open_("#v=compare&o=13104&l=km&d=27205")
        page.wait(lambda: page.js("document.body.classList.contains('has-detail')"), 60, "着地の詳細")
        time.sleep(3)
        r = page.js("(function(){const d=document.querySelector('#detail').getBoundingClientRect();"
                    "return [d.top,d.bottom,innerHeight,document.querySelector('#detail').classList.contains('sh-on')]})()")
        assert r[3], r
        assert r[2] - r[0] >= r[2] * 0.5 and r[0] >= r[2] * 0.35, r  # パネルは高さの 50% 以上、地図は 35% 以上見える
        page.js("document.querySelector('#sheet-tabs [data-sh=panel]').click()")
        time.sleep(0.8)
        small = page.js("(function(){const d=document.querySelector('#panel').getBoundingClientRect();return innerHeight-d.top})()")
        assert small <= 800 * 0.46, small  # 他のタブは 44% のまま
        assert not page.js("window.__errs.length") and not page.js("window.__con.length"), (page.js("window.__errs"), page.js("window.__con"))


def test_g143_10_header_and_title_at_1920():
    with _browser(1920, 1080) as (page, open_):
        _watch(page)
        open_("#v=compare&o=13104&l=km")
        assert page.js("document.title").startswith("Backfull")
        assert page.js("document.querySelector('#panel .p-title').textContent") == "送り方を比べる"
        assert "荷物と届け先を入れると" in page.js("document.querySelector('.brand-sub').textContent")
        assert not page.js("window.__errs.length") and not page.js("window.__con.length"), page.js("window.__con")


def test_g143_11_13_market_summary_and_entry_button_at_1920():
    with _browser(1920, 1080) as (page, open_):
        _watch(page)
        open_("#v=market&p=27-12")
        t = page.js("document.querySelector('#m-summary .sum-gap').textContent")
        assert "営業用の計" in t and "38.97%" in t and "別の値" in t
        r = page.js("(function(){const b=document.querySelector('#m-pair [data-act=clo]');"
                    "const m=b.querySelector('.mp-go-main').getBoundingClientRect(),s=b.querySelector('.mp-go-sub').getBoundingClientRect();"
                    "return [m.height,s.height,b.scrollWidth,b.clientWidth]})()")
        assert r[0] < 24 and r[1] < 20 and r[2] <= r[3] + 1, r  # 各行が 1 行のまま、はみ出さない
        assert not page.js("window.__errs.length") and not page.js("window.__con.length"), page.js("window.__con")
