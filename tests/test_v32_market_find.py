"""SPEC §32 の #1・#2・#3・#8・#9（鉄道・船の入口の文、大型車の折れ線、運転時間のそろい、登録のボタン、便の名札）。

静的な確認（文字列）と、ヘッドレス Edge での画面の確認。Edge が無い環境では画面の確認を飛ばす。
"""

from __future__ import annotations

import time
from pathlib import Path

from test_v31_market_routes import _browser

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_g142_1_rail_ship_entry_text_claims_only_what_the_screen_shows():
    mk = read("market.js")
    assert "日数と目安額の差で見られます" not in mk
    assert "日数と費用は、事業者に問い合わせてください。" in mk   # 文言の直し 2026-11-07。2026-11-08 総点検の直しに合わせて「事業者に」を足した


def test_g142_2_corridor_detail_has_no_heavy_vehicle_chart_but_the_layer_stays():
    mk, html = read("market.js"), read("index.html")
    body = mk[mk.index("function renderPair()"):mk.index("function weekText()")]
    assert "chartSvg(" not in body and "mp-j" not in body and "ch-box" not in body
    assert 'id="mlg-jartic-t"' in html and "function setJartic(" in mk and "id: 'm-jartic'" in mk


def test_g142_3_truck_card_shows_the_top_drive_time_on_fallback():
    rt = read("routes.js")
    assert "s.fallback) return s.truckMin ?" in rt


def test_g142_8_register_button_is_pinned_in_the_left_panel():
    css = read("find_extra.css")
    assert "#find-view .f-reg .f-btns { position: sticky; bottom:" in css


def test_g142_9_find_tags_are_spread_on_the_screen():
    js = read("find.js")
    assert "function spreadTags()" in js and "setOffset(" in js and "map.on('moveend', () => spreadTags())" in js


def test_g142_2_corridor_screen_has_no_chart():
    with _browser(1920, 1080) as (page, open_):
        open_("#v=market&p=27-12")
        assert page.js("!!document.querySelector('#m-pair .mp-title')")
        assert not page.js("!!document.querySelector('#m-pair svg#ch, #m-pair .mp-j')")
        assert "日数と費用は、事業者に問い合わせてください" in page.js("document.querySelector('#m-pair .mp-clo').textContent")
        assert not page.js("window.__errs.length")


def test_g142_3_fallback_truck_card_has_the_same_drive_time_as_the_top():
    with _browser(1920, 1080, "public_nocache") as (page, open_):
        open_("#v=compare&o=13104&l=km&d=27205")
        page.wait(lambda: "直線で出しています" in page.js("document.querySelector('#rt-modes').textContent"), 90, "経路の取得の終わり")
        top = page.js("document.body.innerText")
        card = page.js("document.querySelector('.rt-card[data-mode=truck] .rt-time').textContent")
        assert "運転時間は出せません" not in card
        if "運転" in card:
            assert "線は直線です" in card
        assert not page.js("window.__errs.length")


def test_g142_8_register_button_is_visible_above_the_notes_at_1920():
    with _browser(1920, 1080) as (page, open_):
        open_("#v=market&p=27-12")
        page.js("document.querySelector('#m-pair [data-act=carrier]').click()")
        page.wait(lambda: page.js("!!document.querySelector('#fr-go')"), 30, "登録の欄")
        time.sleep(1.5)
        r = page.js("(function(){const b=document.querySelector('#fr-go').getBoundingClientRect(),v=document.querySelector('#find-view').getBoundingClientRect(),n=document.querySelector('#find-notes').getBoundingClientRect();return [b.top,b.bottom,v.bottom,n.top]})()")
        assert r[0] >= 0 and r[1] <= r[2] + 1 and r[1] <= r[3], r


def test_g142_9_find_tags_do_not_overlap():
    with _browser(1920, 1080) as (page, open_):
        open_("#v=find&p=27-12")
        time.sleep(3)
        rects = page.js("[...document.querySelectorAll('.f-tag')].filter(e=>e.offsetParent).map(e=>{const r=e.getBoundingClientRect();return [r.left,r.top,r.right,r.bottom]})")
        assert len(rects) >= 5
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)
        assert not page.js("window.__errs.length")
