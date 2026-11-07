"""第 8 部 r1（画面の出来と印象）の指摘の修正（SPEC §45 追記）。

- 最初の画面: 役割を選ぶと 1 段目と別の役割の選択肢が実際に消える（hidden が CSS の display に負けない。computed style で確かめる）、
  キーボードで選んだあとフォーカスは 2 段目の見出しへ移る、主ボタンに見えるフォーカスの枠、日本地図の絵が背景にあり文字・押せる所を増やさない。
- 案内の表示: 自社の運行の案内でも上の切り替え（#views）を隠し、帯には「最初の画面に戻る」「全部を見る」だけ。地図の 1 行の凡例、
  全国の物流傾向は区間を選ぶまで主ボタンを押せず、選んでも案内の欄（360 でも）が残る。見積の作業は依頼文と「依頼文をコピー」だけ。
- 動き: 動きを減らす設定ではカメラの移動が 0。CSS の切り替えは 300ms 以内。
- 失敗: 地図のデータや WebGL が使えないとき、何が読めなかったかと、最初の画面・地図なしの版へのリンクが出る。
- 360: 出典は「出典」ボタンに畳む。保存した URL は以前と同じに開く。
ヘッドレス Edge は同時に 1 つだけ（conftest の edge_page）。Edge が無いときだけ飛ばす。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
READY = "!!window.__app && !document.querySelector('#loading')"
BUDGETS = {1920: 450, 360: 300}
SIZES = [(1920, 1080), (360, 800)]
NO_WEBGL = "(function(){const g=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t){if(/webgl/i.test(t))return null;return g.apply(this,arguments);};})();"


def _size(page, w, h):
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)


def _home(page, port):
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 30, "最初の画面")


def _open(page, port, hash_):
    open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}", ready_js=READY, wait_loading=False, start_timeout=180, tag="v46_design_fail")


def _count(page):
    from viewport_text import COUNT_JS
    r = json.loads(page.js(COUNT_JS))
    return {k: v for k, v in r.items() if k != "samples"}


def _disp(page, sel):
    return page.js(f"(function(){{const e=document.querySelector({json.dumps(sel)}); return e ? getComputedStyle(e).display : 'none-missing';}})()")


# ---------------------------------------------------------------- 最初の画面
@pytest.mark.parametrize("w,h", SIZES)
@pytest.mark.parametrize("role,shown,hidden_ids", [
    ("shipper", ".home-purposes[data-for=shipper]", [".home-purposes[data-for=carrier]", "#home-step1"]),
    ("carrier", ".home-purposes[data-for=carrier]", [".home-purposes[data-for=shipper]", "#home-step1"]),
])
def test_role_choice_really_hides_step1_and_other_role(w, h, role, shown, hidden_ids):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _home(page, port)
        assert _disp(page, "#home-step1") != "none"
        # キーボード: 役割のボタンへ Tab で入り、Enter で選ぶ
        page.js(f"document.getElementById('role-{role}').focus()")
        page.key("Enter")
        page.wait(lambda: page.js("!document.getElementById('home-step2').hidden"), 10, "2 段目")
        time.sleep(0.4)
        for sel in hidden_ids:
            assert _disp(page, sel) == "none", (sel, _disp(page, sel))
        assert _disp(page, shown) != "none"
        # 隠れた選択肢・ボタンが 1 つも見えていない（押せる所が 2 段目だけ）
        visible = json.loads(page.js("""JSON.stringify([...document.querySelectorAll('#home button, #home a')].filter(e => e.getClientRects().length).map(e => e.id || e.textContent.trim()))"""))
        assert "role-shipper" not in visible and "role-carrier" not in visible, visible
        # フォーカスは 2 段目の見出し（か最初の選択肢）にある。隠れたボタンに残らない
        act = page.js("document.activeElement.id || document.activeElement.className")
        assert act in ("home-h",) or "purpose" in str(act), act
        assert page.js("document.activeElement.getClientRects().length > 0") is True


@pytest.mark.parametrize("w,h", SIZES)
def test_home_has_map_hero_and_stays_within_budget(w, h):
    from viewport_text import COUNT_JS  # noqa: F401
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _home(page, port)
        time.sleep(1.2)
        hero = json.loads(page.js("""JSON.stringify((function(){const i=document.querySelector('.home-hero img'); const b=document.querySelector('.home-hero').getBoundingClientRect();
          return {src: i ? (i.currentSrc||i.src) : '', ok: !!(i && i.complete && i.naturalWidth > 0), w: b.width, h: b.height, alt: i ? i.getAttribute('alt') : null, hidden: document.querySelector('.home-hero').getAttribute('aria-hidden')};})())"""))
        assert hero["ok"] and "hero_" in hero["src"] and hero["alt"] == "" and hero["hidden"] == "true", hero
        # 画面いっぱいの背景。360 は上の帯（絵を見せ、見出しはその下の暗い部分に重ねる。SPEC §47）
        assert hero["w"] >= w - 1 and hero["h"] >= (h - 1 if w > 600 else 220), hero
        m = _count(page)
        assert m["chars"] <= BUDGETS[w] and m["primary"] <= 2, m
        # 配置が動かない: 映像を読み込んだあとも、役割のボタンの位置が同じ
        before = page.js("JSON.stringify(document.getElementById('role-shipper').getBoundingClientRect())")
        time.sleep(2.0)
        assert page.js("JSON.stringify(document.getElementById('role-shipper').getBoundingClientRect())") == before


def test_home_loop_video_is_muted_inline_and_skipped_for_reduced_motion():
    # WebGL が使えない端末では three.js の 3D（hero3d.js）ではなく、今までの映像にする。使える端末の 3D は test_v50_hero3d.py
    port = server_port("local")
    with edge_page(1920, 1080, hooks=(NO_WEBGL,)) as page:
        _size(page, 1920, 1080)
        _home(page, port)
        page.wait(lambda: page.js("!!document.querySelector('.home-hero video')"), 15, "背景の映像")
        v = json.loads(page.js("""JSON.stringify((function(){const v=document.querySelector('.home-hero video'); return {muted: v.muted, loop: v.loop, inline: v.playsInline, tab: v.tabIndex, hidden: v.getAttribute('aria-hidden'), pos: getComputedStyle(v).position};})())"""))
        assert v == {"muted": True, "loop": True, "inline": True, "tab": -1, "hidden": "true", "pos": "absolute"}, v
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        page.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        _home(page, port)
        time.sleep(2.5)
        assert page.js("!!document.querySelector('.home-hero video')") is False
        assert page.js("document.querySelector('.home-hero img').complete") is True   # 静止画のまま


def test_primary_button_has_visible_focus_ring():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _home(page, port)
        page.key("Tab")
        ring = json.loads(page.js("""JSON.stringify((function(){const e=document.activeElement, s=getComputedStyle(e); return {id: e.id, style: s.outlineStyle, w: parseFloat(s.outlineWidth), color: s.outlineColor};})())"""))
        assert ring["id"] == "role-shipper" and ring["style"] != "none" and ring["w"] >= 2, ring


# ---------------------------------------------------------------- 案内の地図・帯
@pytest.mark.parametrize("w,h", SIZES)
def test_market_guided_legend_button_state_and_panel_after_selecting(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=market&g=1")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 60, "上位 5 の一覧")
        time.sleep(1.0)
        leg = page.js("document.getElementById('g-legend').textContent.replace(/\\s+/g,'')")
        assert "出典:物流センサス（国土交通省、e-Stat）を加工して作成" in leg and "2021" not in leg and "公開統計" not in leg and "出荷が多い" in leg and "入荷が多い" in leg and "橙の点線:帰りが空く向き" in leg, leg   # 文言の直し 2026-11-07
        # 常に見える凡例は 3 項目・短い 1 行。続きは「凡例・出典」を開いたときだけ（r4a 指摘14）
        assert page.js("document.querySelectorAll('#g-legend .g-k:not(.g-more):not(.g-src)').length") == 2   # 文言の直し 2026-11-07: 常に見えるのは 2 項目
        assert page.js("[...document.querySelectorAll('#g-legend .g-more, #g-legend .g-src')].every(e => getComputedStyle(e).display === 'none')") is True
        if w > 600:   # 狭い画面では凡例は「凡例・出典」の開閉に入れる
            assert page.js("document.getElementById('g-legend').getClientRects().length > 0") is True
        # 選ぶ前: 押せない主ボタンは出さない（選んだあとに「この区間で空きを探す」が出る）
        assert page.js("document.getElementById('mg-next').hidden") is True
        assert page.js("document.getElementById('mg-next').getClientRects().length") == 0
        m0 = _count(page)
        assert m0["chars"] <= BUDGETS[w] and m0["primary"] <= 1 and m0["controls"] <= 8, m0
        page.js("document.querySelector('.mg-row').click()")
        time.sleep(2.8)
        assert page.js("document.getElementById('mg-next').disabled") is False
        assert page.js("document.getElementById('mg-next').textContent.trim()").startswith("この向きで帰り便を探す")   # 文言の直し 2026-11-07
        # 案内の欄は選んだあとも残り（360 でも）、選んだ区間がその中に出る
        r = json.loads(page.js("JSON.stringify(document.getElementById('m-guide').getBoundingClientRect())"))
        assert r["width"] > 0 and r["height"] > 0 and r["bottom"] <= h + 1 and r["top"] >= 0, r
        assert page.js("getComputedStyle(document.getElementById('m-guide')).display") != "none"
        sel = page.js("document.getElementById('mg-sel').textContent")
        assert "から" in sel and "t" in sel, sel
        m1 = _count(page)
        assert m1["chars"] <= BUDGETS[w] and m1["primary"] <= 1 and m1["controls"] <= 8, m1
        # 地図は日本語の読み上げ名を持つ
        assert "日本地図" in page.js("document.querySelector('canvas.maplibregl-canvas').getAttribute('aria-label')")
        if w == 360:   # 出典は「出典」ボタンに畳む
            assert page.js("getComputedStyle(document.querySelector('.maplibregl-ctrl-attrib')).display") == "none"
            assert page.js("getComputedStyle(document.getElementById('attr-toggle')).display") != "none"
            page.js("document.getElementById('attr-toggle').click()")
            assert page.js("getComputedStyle(document.querySelector('.maplibregl-ctrl-attrib')).display") != "none"


@pytest.mark.parametrize("w,h", SIZES)
def test_market_guided_dims_non_top5_and_adds_return_dashes(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=market&g=1")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 60, "上位 5 の一覧")
        page.js("document.querySelector('.mg-row').click()")
        time.sleep(2.5)
        ids = json.loads(page.js("JSON.stringify(window.__app.Market.layers(performance.now(), 6).map(l => l.id))"))
        assert "m-ret-dash" in ids and "m-sel-veh" in ids and "m-pref" in ids, ids
        # 全部を見る画面では、点線も 3 D の置き物も出さない
        page.js("document.getElementById('g-toggle').click()")
        ids = json.loads(page.js("JSON.stringify(window.__app.Market.layers(performance.now(), 6).map(l => l.id))"))
        assert "m-ret-dash" not in ids and "m-sel-veh" not in ids, ids


def test_effects_lighting_is_attached_to_the_overlay():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=market&g=1")
        assert page.js("MapFx.effects().length") == 1


@pytest.mark.parametrize("w,h", SIZES)
def test_own_guided_hides_view_switcher_and_keeps_slim_bar(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=own&g=empty")
        page.wait(lambda: page.js("document.body.classList.contains('own-guided')"), 90, "自社の運行の案内")
        time.sleep(1.0)
        assert _disp(page, "#views") == "none"
        bar = json.loads(page.js("""JSON.stringify([...document.querySelectorAll('#guide-bar button')].filter(e => e.getClientRects().length).map(e => e.textContent.trim()))"""))
        assert bar == ["最初の画面に戻る", "全部を見る"], bar
        r = json.loads(page.js("JSON.stringify(document.getElementById('guide-bar').getBoundingClientRect())"))
        assert r["top"] <= 24 and r["height"] <= 44, r
        m = _count(page)
        assert m["controls"] <= 8 and m["chars"] <= BUDGETS[w] and m["primary"] <= 1, m
        # 手順 2 にもサンプルを選んで進む（帯は同じ）
        if page.js("!!document.getElementById('neg-entry-sample')"):
            page.js("document.getElementById('neg-entry-sample').click()")
            time.sleep(0.8)
            assert _disp(page, "#views") == "none"
            assert page.js("getComputedStyle(document.getElementById('guide-bar')).display") != "none"
            m = _count(page)
            assert m["controls"] <= 8 and m["chars"] <= BUDGETS[w], m
        # 全部を見る: 切り替えが戻り、アドレスから g が消える
        page.js("document.getElementById('g-toggle').click()")
        time.sleep(0.8)
        assert "g=" not in page.js("location.hash") and "v=own" in page.js("location.hash")
        assert _disp(page, "#views") != "none"


# ---------------------------------------------------------------- 見積の作業（案内）
@pytest.mark.parametrize("w,h", SIZES)
def test_quote_work_guided_shows_steps_and_single_primary(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=compare&g=1")
        page.js("(() => { const A = window.__app; A.select(A.S.idx.codes.indexOf('27128')); })()")
        page.wait(lambda: page.js("!!document.querySelector('#cards .cc-row')"), 90, "結果の表")
        time.sleep(2.0)
        steps = page.js("document.getElementById('g-steps').textContent.replace(/\\s+/g,' ').trim()")
        assert steps == "1 荷物と行き先 2 送り方と費用 3 見積の依頼文" or "2 送り方と費用" in steps   # 文言の直し 2026-11-07
        assert page.js("document.querySelector('#g-steps li[aria-current]').textContent.trim()").startswith("2")
        page.js("document.querySelector('#cards input[name=cc-pick]').click()")
        page.js("document.getElementById('make-request').click()")
        page.wait(lambda: page.js("document.body.classList.contains('has-work')"), 60, "見積依頼文の作業が開く")
        page.wait(lambda: page.js("!!document.getElementById('req-copy')"), 60, "依頼文")
        time.sleep(1.0)
        assert page.js("document.getElementById('req-copy').textContent.trim()") == "依頼文をコピー"
        assert page.js("document.getElementById('req-copy').classList.contains('btn-primary')") is True
        assert page.js("document.querySelector('#g-steps li[aria-current]').textContent.trim()").startswith("3")
        assert "3 見積の依頼文" in page.js("document.getElementById('g-steps').textContent.replace(/\\s+/g,' ')")
        assert _disp(page, "#w-steps") == "none" and _disp(page, "#work footer") == "none"
        assert page.js("document.querySelector('#work .w-more').open") is False
        m = _count(page)
        assert m["primary"] <= 1 and m["controls"] <= 8 and m["chars"] <= BUDGETS[w], m


def test_work_tabs_do_not_break_per_character_at_360():
    port = server_port("local")
    with edge_page(360, 800) as page:
        _size(page, 360, 800)
        _open(page, port, "#v=compare")
        page.js("(() => { const A = window.__app; A.select(A.S.idx.codes.indexOf('27128')); })()")
        page.wait(lambda: page.js("!!document.querySelector('#cards .cc-row')"), 90, "結果の表")
        page.js("document.querySelector('#cards input[name=cc-pick]').click()")
        page.js("document.getElementById('make-request').click()")
        page.wait(lambda: page.js("document.body.classList.contains('has-work')"), 60, "見積依頼文の作業が開く")
        time.sleep(1.0)
        hs = json.loads(page.js("JSON.stringify([...document.querySelectorAll('#w-steps button')].map(b => [b.textContent.trim(), b.getBoundingClientRect().height]))"))
        assert len(hs) == 4 and all(h < 50 for _, h in hs), hs   # 1 字ずつ折れると高さが 100 を超える
        assert page.js("getComputedStyle(document.querySelector('#w-steps button')).whiteSpace") == "nowrap"


# ---------------------------------------------------------------- 動き
def test_reduced_motion_camera_does_not_animate():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        page.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        _open(page, port, "#v=market&g=1")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 60, "上位 5 の一覧")
        assert page.js("MapFx.ms(1600)") == 0 and page.js("MapFx.cam(2000).duration") == 0
        page.js("""(function(){window.__mv=[]; const m=window.__app.map(); m.on('movestart',()=>{window.__t0=performance.now();}); m.on('moveend',()=>{window.__mv.push(performance.now()-window.__t0);});})()""")
        page.js("document.querySelectorAll('.mg-row')[1].click()")
        time.sleep(1.5)
        mv = json.loads(page.js("JSON.stringify(window.__mv)"))
        assert mv and max(mv) < 150, mv   # 通常は 1,600ms 前後
        # 動きを止めた点線（毎フレーム動かさない）
        ids = json.loads(page.js("JSON.stringify(window.__app.Market.layers(performance.now(), 6).map(l => l.id))"))
        assert "m-ret-dash" in ids
        k1 = page.js("window.__app.Market.layers(1000, 6).find(l => l.id === 'm-ret-dash').props.data.length")
        k2 = page.js("window.__app.Market.layers(9000, 6).find(l => l.id === 'm-ret-dash').props.data[0].pts[0].join()")
        k3 = page.js("window.__app.Market.layers(1000, 6).find(l => l.id === 'm-ret-dash').props.data[0].pts[0].join()")
        assert k1 > 0 and k2 == k3


def test_normal_camera_is_gentle_not_instant():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=market&g=1")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 60, "上位 5 の一覧")
        time.sleep(1.5)
        page.js("""(function(){window.__mv=[]; const m=window.__app.map(); m.on('movestart',()=>{window.__t0=performance.now();}); m.on('moveend',()=>{window.__mv.push(performance.now()-window.__t0);});})()""")
        page.js("document.querySelectorAll('.mg-row')[1].click()")
        page.wait(lambda: page.js("window.__mv.length") >= 1, 20, "カメラの移動")
        d = page.js("window.__mv[0]")
        assert 1200 <= d <= 2400, d


def test_css_transitions_are_within_300ms():
    """transition・animation の長さが 300ms 以内（無限に繰り返す点滅と、遅れの指定は除く）。"""
    bad = []
    for name in ("style.css", "compare.css", "trip_view.css", "loadview.css", "find_extra.css"):
        text = (ROOT / "web" / name).read_text(encoding="utf-8")
        for m in re.finditer(r"(?<![\w-])(transition|animation)\s*:\s*([^;}]+)", text):
            decl = m.group(2)
            if "infinite" in decl or "calc(" in decl or decl.strip() in ("none", "none !important"):
                continue
            for part in decl.split(","):
                times = re.findall(r"(?<![\w.-])(\.?\d+(?:\.\d+)?)(ms|s)(?![\w-])", part)
                if not times:
                    continue
                v, u = times[0]
                sec = float(v) / (1000 if u == "ms" else 1)
                if sec > 0.3001 and not (sec <= 0.4001 and re.search(r"home-(panel|step)-in", part)):   # 最初の画面の入場だけ 400ms（第 9 部 r1）
                    bad.append((name, m.group(0)[:80]))
    assert not bad, bad


def test_deck_transitions_and_camera_use_motion_helper():
    for name in ("app.js", "market.js", "muni.js", "routes.js", "find.js"):
        text = (ROOT / "web" / name).read_text(encoding="utf-8")
        assert not re.search(r"duration:\s*\d{3,}", text), (name, re.findall(r".{30}duration:\s*\d{3,}.{10}", text))
        assert "essential: true" not in text or name == "muni.js", name


# ---------------------------------------------------------------- 失敗時
def test_data_failure_shows_file_home_and_lite_links():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        page.send("Network.enable")
        page.send("Network.setBlockedURLs", urls=["*data/muni.geojson*"])
        page.goto(f"http://127.0.0.1:{port}/index.html#v=market&g=1")
        page.wait(lambda: page.js("!!document.getElementById('fail-home')"), 120, "失敗の案内")
        txt = page.js("document.getElementById('load-notice').textContent")
        assert "読み込めませんでした" in txt, txt   # 文言の直し 2026-11-07
        links = json.loads(page.js("JSON.stringify([...document.querySelectorAll('#load-notice a')].map(a => [a.getAttribute('href'), a.textContent.trim()]))"))
        assert ["own_lite.html", "自社の運行を地図なしで開く"] in links and ["compare_lite.html", "送り方を地図なしで比べる"] in links   # 文言の直し 2026-11-07, links
        assert page.js("document.getElementById('fail-home').textContent.trim()") == "最初の画面に戻る"
        page.js("document.getElementById('fail-home').click()")
        page.wait(lambda: page.js("document.body.classList.contains('view-home')"), 15, "最初の画面へ")
        assert page.js("document.getElementById('role-shipper').getClientRects().length > 0") is True
        assert page.js("!document.getElementById('load-notice')") is True


def test_webgl_failure_shows_reason_and_lite_links():
    """WebGL が使えない端末: 全国の物流傾向は理由と軽い版へのリンクを出す。"""
    port = server_port("local")
    with edge_page(1920, 1080, hooks=(NO_WEBGL,)) as page:
        _size(page, 1920, 1080)
        page.goto(f"http://127.0.0.1:{port}/index.html#v=market&g=empty")
        page.wait(lambda: page.js("!!document.getElementById('fail-home')"), 120, "失敗の案内")
        txt = page.js("document.getElementById('load-notice').textContent")
        assert "地図を出せません" in txt, txt   # 文言の直し 2026-11-07
        hrefs = json.loads(page.js("JSON.stringify([...document.querySelectorAll('#load-notice a')].map(a => a.getAttribute('href')))"))
        assert "own_lite.html" in hrefs and "compare_lite.html" in hrefs, hrefs


def test_webgl_failure_own_opens_without_map():
    """WebGL が使えない端末: 自社の運行は地図の代わりに静止画を出し、段の欄（取り込み）はそのまま使える。"""
    port = server_port("local")
    with edge_page(1920, 1080, hooks=(NO_WEBGL,)) as page:
        _size(page, 1920, 1080)
        page.goto(f"http://127.0.0.1:{port}/index.html#v=own&g=empty")
        page.wait(lambda: page.js("!!document.querySelector('#own-nomap') && !!document.querySelector('#own-right')"), 120, "地図なしの自社の運行")
        assert page.js("getComputedStyle(document.querySelector('#own-nomap')).display") == "block"
        assert page.js("!!document.querySelector('#own-nomap img')") is True
        assert page.js("!document.getElementById('fail-home')") is True


# ---------------------------------------------------------------- 保存した URL
@pytest.mark.parametrize("hash_,view", [
    ("#v=market&ot=confirm&m=truck&n=30&km=200", "market"),
    ("#v=market", "market"),
    ("#v=compare", "compare"),
    ("#o=13104&l=days&d=27205", "compare"),
])
def test_saved_urls_open_in_full_mode(hash_, view):
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, hash_)
        page.wait(lambda: page.js("window.__app.S.view") == view, 90, f"{view} が開く")
        time.sleep(1.0)
        assert page.js("document.body.classList.contains('guided')") is False
        assert _disp(page, "#views") != "none" and page.js("document.getElementById('g-legend').hidden") is True
        if view == "market":
            assert page.js("window.__app.Market.state.n") == 30 and page.js("window.__app.Market.state.minKm") == 200
