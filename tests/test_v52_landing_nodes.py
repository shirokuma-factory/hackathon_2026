"""最初の画面の名前と場面の字幕、地図で選んだ県・地点の詳細カード（2026-11-07）。

- 最初の画面: Backfull の名前と一行が 1920 と 390 で見える。場面の字幕は scenes.json の時刻に合わせて出て、パネルと名前に重ならない。700px 以下では出さない。
- 全国の物流傾向: 県を押すと詳細カードが開き、出荷・入荷・上位 3 つの相手先が market.json の値と一致する。Esc で閉じる。
- 自社の運行: 地点を選ぶと詳細カードが開き、便数・空車で出た便・曜日が区間の数え直しと一致する。
ヘッドレス Edge は同時に 1 つだけ（conftest の edge_page）。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
READY = "!!window.__app && !document.querySelector('#loading')"
FAKE = {"segments": [
    {"key": "kanto", "t0": 0, "t1": 5, "anchor": {"x": 0.5, "y": 0.5}},     # パネルの真後ろ
    {"key": "kansai", "t0": 5, "t1": 10, "anchor": {"x": 0.08, "y": 0.1}},  # 名前の近く
    {"key": "kyushu", "t0": 10, "t1": 15, "anchor": {"x": 0.9, "y": 0.95}},  # 隅
]}


def _size(page, w, h):
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)


def _home(page, port, w, h):
    # 文言の直し 2026-11-07: 既定の背景（img/hero/）には scenes.json が無く、字幕は出ない。仕組みは HeroCaptions.load の試験用の入口で確かめる。
    # 映像の時刻で字幕が切り替わらないよう、映像は外す（update(t) を試験が直接呼ぶ）
    _size(page, w, h)
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 30, "最初の画面")
    time.sleep(1.2)
    page.js("(() => { const v = document.querySelector('.home-hero video'); if (v) v.remove(); })()")


WORDMARK_JS = """JSON.stringify((function(){
  const n = document.querySelector('.hb-name'), t = document.querySelector('.hb-tag'), r = n.getBoundingClientRect(), tr = t.getBoundingClientRect(), p = document.querySelector('.home-wrap').getBoundingClientRect(), cs = getComputedStyle(n);
  return {text: n.textContent, tag: t.textContent, l: r.left, b: tr.bottom, r: Math.max(r.right, tr.right), w: r.width, vis: cs.visibility, fw: cs.fontWeight, fs: cs.fontSize, pl: p.left, pt: p.top, vw: innerWidth};
})())"""


def test_wordmark_is_visible():
    port = server_port("local")
    for w, h in [(1920, 1080), (390, 844)]:
        with edge_page(w, h) as page:
            _home(page, port, w, h)
            m = json.loads(page.js(WORDMARK_JS))
            assert m["text"] == "Backfull" and "荷主の荷で埋める" in m["tag"], m
            assert m["vis"] == "visible" and m["w"] > 40 and m["l"] >= 0 and m["r"] <= m["vw"], m
            assert int(m["fw"]) >= 700 and m["fs"] in ("22px", "20px"), m
            assert m["b"] <= m["pt"] + 1 or m["r"] <= m["pl"], m   # パネルと重ならない


CAP_JS = """JSON.stringify((function(){
  const c = document.getElementById('home-cap'), r = c.getBoundingClientRect(), w = document.querySelector('.home-wrap').getBoundingClientRect(), b = document.getElementById('home-brand').getBoundingClientRect();
  const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  return {hidden: c.hidden, on: c.classList.contains('on'), label: c.querySelector('.hc-label').textContent, line: c.querySelector('.hc-line').textContent, panel: hit(r, w), brand: hit(r, b), inside: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight, disp: getComputedStyle(c).display};
})())"""


def test_captions_follow_scenes_and_avoid_the_panel():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _home(page, port, 1920, 1080)
        page.js(f"HeroCaptions.load({json.dumps(FAKE)})")
        seen = []
        for t in (2, 7, 12):
            page.js(f"HeroCaptions.update({t})")
            time.sleep(0.6)
            r = json.loads(page.js(CAP_JS))
            seen.append(r["label"])
            assert r["on"] and not r["hidden"] and not r["panel"] and not r["brand"] and r["inside"], (t, r)
        assert seen == ["関東", "関西 → 関東", "鹿児島 → 東京"]   # 文言の直し 2026-11-07
        page.js("HeroCaptions.update(20)")
        time.sleep(0.8)
        assert json.loads(page.js(CAP_JS))["hidden"]


def test_captions_hidden_on_phone_and_noop_without_json():
    port = server_port("local")
    with edge_page(390, 844) as page:
        _home(page, port, 390, 844)
        page.js(f"HeroCaptions.load({json.dumps(FAKE)}); HeroCaptions.update(2)")
        time.sleep(0.5)
        assert json.loads(page.js(CAP_JS))["disp"] == "none"
    with edge_page(1920, 1080) as page:
        _home(page, port, 1920, 1080)   # 既定の背景には scenes.json が無い → 字幕は出ない
        time.sleep(0.5)
        assert json.loads(page.js(CAP_JS))["hidden"]
        page.js("HeroCaptions.update(2)")   # json が無いまま時刻を進めても出ない
        time.sleep(0.5)
        assert json.loads(page.js(CAP_JS))["hidden"]
        assert page.js("fetch('img/hero/scenes.json').then(r => r.status)") != 200


def _app(page, port, hash_):
    _size(page, 1920, 1080)
    open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}", ready_js=READY, wait_loading=False, start_timeout=180, tag="v52_fail")
    time.sleep(1.0)


CARD_JS = """JSON.stringify((function(){
  const c = document.getElementById('pref-card');
  return {hidden: c.hidden, title: c.querySelector('.nc-title').textContent, nums: [...c.querySelectorAll('.nc-nums dd')].map(e => e.textContent), rows: [...c.querySelectorAll('.nc-list li')].map(e => e.textContent), sel: __app.Market.state.prefSel};
})())"""


def test_prefecture_card_matches_market_json():
    port = server_port("local")
    d = json.loads((ROOT / "web/data/market.json").read_text(encoding="utf-8"))
    i = d["prefs"].index("東京都")
    pairs = d["modes"]["truck"]["pairs"]
    out = sum(p[2] for p in pairs if p[0] == i) + sum(p[3] for p in pairs if p[1] == i)
    inn = sum(p[3] for p in pairs if p[0] == i) + sum(p[2] for p in pairs if p[1] == i)
    part = sorted(([p[1] if p[0] == i else p[0], p[5]] for p in pairs if i in (p[0], p[1])), key=lambda x: -x[1])[:3]
    for hash_ in ("#v=market", "#v=market&g=1"):
        with edge_page(1920, 1080) as page:
            _app(page, port, hash_)
            page.js("__app.Market.click({object:{properties:{n:'東京都'}}, layer:{id:'m-pref'}, x:900, y:500})")
            time.sleep(0.6)
            r = json.loads(page.js(CARD_JS))
            assert not r["hidden"] and r["title"] == "東京都" and r["sel"] == i, r
            num = lambda s: int(re.sub(r"[^\d]", "", s))
            assert [num(x) for x in r["nums"]] == [round(out), round(inn)], (r, out, inn)
            assert len(r["rows"]) == 3
            for row, (j, sh) in zip(r["rows"], part):
                assert d["prefs"][j] in row and f"{round(sh):,}" in row, (row, j, sh)
            assert page.js("!!document.querySelector('#pref-card .nc-close')")
            page.js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
            time.sleep(0.3)
            assert page.js("document.getElementById('pref-card').hidden") is True
            assert page.js("__app.Market.state.prefSel") is None


PLACE_JS = """JSON.stringify((function(){
  const c = document.getElementById('own-place-card');
  return {hidden: c.hidden, nums: [...c.querySelectorAll('.nc-nums dd')].map(e => e.textContent), wd: [...c.querySelectorAll('.nc-wd b')].map(e => Number(e.textContent)), days: [...c.querySelectorAll('.nc-wd small')].map(e => e.textContent).join(''), dest: c.querySelectorAll('.nc-list li').length};
})())"""

EXPECT_JS = """JSON.stringify((function(){
  const d = __app.Own.state.data, id = d.dock;
  const dep = d.legs.filter(l => l.frm === id), arr = d.legs.filter(l => l.to === id), emp = dep.filter(l => l.kind === 'empty');
  const wd = [0,1,2,3,4,5,6].map(w => dep.filter(l => l.kind === 'empty' && OwnCalc.legWd(l) === w).length);
  return {id, dep: dep.length, arr: arr.length, emp: emp.length, wd};
})())"""


def test_own_place_card_matches_legs():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _app(page, port, "#v=own")
        page.wait(lambda: page.js("!!(__app.Own.state.data && document.querySelector('.own-tag.pick'))"), 60, "自社の運行の地点")
        exp = json.loads(page.js(EXPECT_JS))
        assert exp["dep"] > 0, exp
        page.js(f"__app.Own.openPlace({json.dumps(exp['id'])})")
        time.sleep(0.5)
        r = json.loads(page.js(PLACE_JS))
        assert not r["hidden"] and r["days"] == "月火水木金土日" and len(r["wd"]) == 7, r
        assert r["nums"][0].startswith(f"{exp['dep']} ") and r["nums"][1].startswith(f"{exp['arr']} ") and r["nums"][2].startswith(f"{exp['emp']} "), (r, exp)
        assert r["wd"] == exp["wd"], (r, exp)
        assert 1 <= r["dest"] <= 2
        page.js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))")
        time.sleep(0.3)
        assert page.js("document.getElementById('own-place-card').hidden") is True
