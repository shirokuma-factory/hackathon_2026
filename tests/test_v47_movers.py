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


MOVER_POS = """(()=>{const a=window.__app,o=a.overlay(),ls=[];const fl=(x)=>Array.isArray(x)?x.forEach(fl):x&&ls.push(x);fl((o.props||o._props).layers);
const r={};for(const l of ls){const m=/^rt-veh-(truck|rail|ferry|air)(-3d)?$/.exec(l.id);if(m)r[m[1]]=(r[m[1]]||[]).concat(Array.from(l.props.data||[]).map(v=>[+v.p[0].toFixed(3),+v.p[1].toFixed(3)]));}
return r;})()"""


def test_guided_flow_vehicles_move_without_clicks(public_server):
    """案内（荷主 → 比べる → 届け先を選ぶ）の流れで、再生ボタンを押さなくても乗り物の位置が時間で変わる。2026-11-08「飛行機・船・電車が動かない」の確認用。"""
    with edge_page(1920, 1080, hooks=(RESOURCE_HOOK, CSP_HOOK)) as page:
        page.goto(f"http://127.0.0.1:{public_server}/index.html")
        page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 60, "最初の画面")
        page.js("document.querySelector('#role-shipper').click()")
        page.js("document.querySelector('#p-ship-compare').click()")
        page.wait(lambda: page.js("!!window.__app && __app.S.view==='compare' && !!__app.S.idx"), 240, "比べる画面")
        page.js("(()=>{const q=document.querySelector('#q');q.focus();q.value='福岡市中央区';q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        page.wait(lambda: page.js("!!document.querySelector('#suggest li[data-i]')"), 30, "候補")
        page.js("document.querySelector('#suggest li[data-i]').dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}))")
        page.wait(lambda: page.js("document.body.classList.contains('has-route')") and "air" in page.js(MOVER_POS), 60, "乗り物")
        assert page.js("__app.S.playing") is False
        a = page.js(MOVER_POS)
        time.sleep(1.5)
        b = page.js(MOVER_POS)
        for m in ("truck", "rail", "ferry", "air"):
            assert a.get(m) and b.get(m), (m, a, b)
            assert a[m] != b[m], f"{m} の位置が 1.5 秒で変わっていません: {a[m]}"
        assert page.js("window.__csp") == []


TINT = """(()=>{const o=__app.overlay(),ls=[];const fl=(x)=>Array.isArray(x)?x.forEach(fl):x&&ls.push(x);fl((o.props||o._props).layers);
const r={};for(const l of ls){const m=/^rt-veh-(truck|rail|ferry|air)(-3d)?$/.exec(l.id);if(!m||!/-3d$/.test(l.id))continue;const d=Array.from(l.props.data||[]);
const full=d.find(v=>(v.a??1)>=0.99)||d[0];r[m[1]]={op:l.props.opacity,col:full&&typeof l.props.getColor==='function'?l.props.getColor(full):null,ss:l.props.sizeScale};}
return r;})()"""


def test_guided_vehicle_layers_opaque_and_tinted(public_server):
    """案内の流れでは 3D の乗り物は濃さ 0.9 以上で、手段の色の掛け色（白ではない）が付く。灰色の素材が地図になじんで「薄くて見えない」と言われた件（2026-11-08）。"""
    with edge_page(1920, 1080, hooks=(RESOURCE_HOOK, CSP_HOOK)) as page:
        page.goto(f"http://127.0.0.1:{public_server}/index.html")
        page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 60, "最初の画面")
        page.js("document.querySelector('#role-shipper').click()")
        page.js("document.querySelector('#p-ship-compare').click()")
        page.wait(lambda: page.js("!!window.__app && __app.S.view==='compare' && !!__app.S.idx"), 240, "比べる画面")
        page.js("(()=>{const q=document.querySelector('#q');q.focus();q.value='福岡市中央区';q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        page.wait(lambda: page.js("!!document.querySelector('#suggest li[data-i]')"), 30, "候補")
        page.js("document.querySelector('#suggest li[data-i]').dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}))")
        page.wait(lambda: "rt-veh-air-3d" in _ids(page), 90, "3D の乗り物")
        assert page.js("document.body.classList.contains('guided')")
        t = page.js(TINT)
        assert t, t
        for m, v in t.items():
            assert v["op"] >= 0.9, (m, v)
            c = v["col"]
            assert c and (c[0], c[1], c[2]) != (255, 255, 255), (m, v)
