"""動く乗り物（電車・船・飛行機）が、公開版の配信（CSP つき）でも層として出ていることを確かめる回帰試験。2026-11-08 の報告「動いていない」に対して。
送り方を比べるで着地を選ぶと 4 手段の乗り物の層（rt-veh-*）が出る。全国の物流傾向は選んだ手段の先頭の乗り物（m-flow-head）が出る。"""

from __future__ import annotations

import json
import time

from conftest import RESOURCE_HOOK, edge_page, open_app

CSP_HOOK = "window.__csp=[];document.addEventListener('securitypolicyviolation',e=>window.__csp.push(e.violatedDirective+' '+e.blockedURI));"
LAYERS = "(()=>{const o=window.__app.overlay(),ls=[];const fl=(x)=>Array.isArray(x)?x.forEach(fl):x&&ls.push(x);fl((o.props||o._props).layers);return ls.map(l=>l.id);})()"


def _ids(page):
    return page.js(LAYERS)


def test_compare_selected_shows_all_four_movers(public_server):
    with edge_page(1920, 1080, hooks=(RESOURCE_HOOK, CSP_HOOK)) as page:
        open_app(page, f"http://127.0.0.1:{public_server}/#v=compare")
        page.js("(()=>{const i=__app.S.idx.names.indexOf('福岡市中央区');__app.select(i,{fly:false});})()")
        page.wait(lambda: "rt-veh-air" in _ids(page), 60, "乗り物の層")
        ids = _ids(page)
        for m in ("truck", "rail", "ferry", "air"):
            assert f"rt-veh-{m}" in ids and f"rt-trail-{m}" in ids, (m, ids)
        time.sleep(1)
        assert page.js("window.__csp") == [], page.js("window.__csp")
        assert page.js("window.__errs") == [], json.dumps(page.js("window.__errs"))


def test_market_shows_moving_head(public_server):
    with edge_page(1920, 1080, hooks=(RESOURCE_HOOK, CSP_HOOK)) as page:
        open_app(page, f"http://127.0.0.1:{public_server}/#v=market&m=truck&n=5&km=200")
        page.wait(lambda: "m-flow-head" in _ids(page), 60, "先頭の乗り物の層")
        ids = _ids(page)
        assert "m-flow-core" in ids and "m-flow-head" in ids, ids
        assert page.js("window.__csp") == []
