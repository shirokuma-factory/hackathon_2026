"""地図の画面の評価（2026-11-08）の P1 の直し。

- 荷物の確定が失敗しても、全国の物流傾向と空きを探すは開く（誤りは送り方を比べるだけに出す）
- 送り方を比べるの凡例は、パレット（遠いほど明るい）と同じ向きで書く
- 全国の物流傾向で例の便が無い区間を選んでも、次に進むボタンが出る
- 空きを探すは、荷主が入れた荷物を例の荷物に差し替えない
- scenes.json は ?captions=1 のときだけ読む（無いので 404 になっていた）
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
READY = "!!window.__app && !document.querySelector('#loading')"
ERR_HOOK = ("window.__errs=[];window.addEventListener('error',e=>window.__errs.push(e.message));"
            "window.addEventListener('unhandledrejection',e=>window.__errs.push(String(e.reason)));")
FAIL_CONFIRM = ("(function(){const f=window.fetch;window.fetch=function(u,o){"
                "if(String(u).indexOf('/api/confirm')>=0) return Promise.resolve(new Response(JSON.stringify({error:'テスト用の失敗'}),{status:500}));"
                "return f.apply(this,arguments);};})();")


def test_sources_wire_the_fixes():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    work = (ROOT / "web" / "work.js").read_text(encoding="utf-8")
    find = (ROOT / "web" / "find.js").read_text(encoding="utf-8")
    assert "遠いほど明るい水色" in app and "濃いほど大きい" not in app
    assert "captions=1" in app
    assert "retryInitConfirm" in work and "W.initError" in work and "ownCargo" in work
    assert "Work.ownCargo()" in find


def test_failed_confirm_does_not_block_market_and_find():
    port = server_port("local")
    with edge_page(1920, 1080, hooks=(ERR_HOOK, FAIL_CONFIRM)) as page:
        open_app(page, f"http://127.0.0.1:{port}/index.html#v=market&g=1", ready_js=READY, wait_loading=False, start_timeout=180, tag="v53_fail")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 60, "上位 5 の一覧")
        page.js("location.hash = '#v=find&g=1'")
        page.wait(lambda: page.js("document.body.classList.contains('view-find')"), 30, "空きを探すが開く")
        page.wait(lambda: page.js("document.querySelectorAll('#f-list *').length") > 0, 30, "便の一覧")
        page.js("location.hash = '#v=compare&g=1'")
        page.wait(lambda: "荷物の条件を確定できませんでした" in page.js("document.body.innerText"), 30, "比べる画面だけに誤りが出る")
        assert not json.loads(page.js("JSON.stringify((window.__errs||[]).filter(m=>!/ResizeObserver/.test(m)))"))


def test_compare_legend_matches_palette_and_no_captions_404():
    port = server_port("local")
    with edge_page(1920, 1080, hooks=(ERR_HOOK,)) as page:
        open_app(page, f"http://127.0.0.1:{port}/index.html#v=compare&g=1", ready_js=READY, wait_loading=False, start_timeout=180, tag="v53_legend")
        page.wait(lambda: "遠いほど明るい" in page.js("document.getElementById('g-legend').textContent"), 30, "凡例")
        assert "濃いほど" not in page.js("document.getElementById('g-legend').textContent")
        assert page.js("performance.getEntriesByType('resource').filter(e=>e.name.indexOf('scenes.json')>=0).length") == 0


def test_top_pair_without_trip_still_offers_next_step_and_find_keeps_own_cargo():
    port = server_port("local")
    with edge_page(1920, 1080, hooks=(ERR_HOOK,)) as page:
        open_app(page, f"http://127.0.0.1:{port}/index.html#v=compare&g=1", ready_js=READY, wait_loading=False, start_timeout=180, tag="v53_own")
        page.wait(lambda: page.js("window.Work && Work.ownCargo()") is True, 60, "簡単な荷物で確定")
        sid = page.js("Work.scenarioId()")
        assert sid is None
        page.js("location.hash = '#v=market&g=1'")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 60, "上位 5 の一覧")
        page.js("document.querySelectorAll('.mg-row')[0].click()")
        page.wait(lambda: "まだありません" in page.js("document.getElementById('mg-sel').textContent"), 30, "理由")
        assert page.js("document.getElementById('mg-next').hidden") is False
        page.js("document.getElementById('mg-next').click()")
        page.wait(lambda: "v=find" in page.js("location.hash"), 30, "空きを探すへ進む")
        page.wait(lambda: page.js("document.getElementById('f-list').children.length") > 0, 30, "一覧")
        assert page.js("Work.scenarioId()") is None   # 入れた荷物を例に差し替えていない
