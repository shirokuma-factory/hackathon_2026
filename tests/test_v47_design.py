"""第 8 部 r2（画面の出来と印象）の指摘のうち、地図・最初の画面・比べる・空きを探すの修正（SPEC §47）。

- 案内の地図も真上から見る（pitch 0・bearing 0。作者の指示）。乗り物は v2 のモデル。国外の地名と県名の名札は出さない。
- 全国 → 空きを探すで、選んだ区間をアドレスで渡し、受け取り場所を入れる。合う便が無いときは「合う架空の便はありません」と「区間を選び直す」。
- 区間を Enter で選んだ後のフォーカスは主ボタンへ。案内の欄は地図の canvas と出典のリンクより前の Tab 順。
- 版のお知らせは送り方を比べるの画面だけ。送り方を比べる: 結果で一番安い行を初めから選び、見積依頼文の段は未入力の項目を上に出し本文を開いておく。
- 最初の画面: 目的の 1 行、360 で日本地図の絵が見える、サムネイルは絵と同じ画風。検索で大阪市中央区が出る。出発地の制限を最初に書く。
ヘッドレス Edge は同時に 1 つだけ（conftest の edge_page）。Edge が無いときだけ飛ばす。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
READY = "!!window.__app && !document.querySelector('#loading')"
BUDGETS = {1920: 450, 360: 300}
SIZES = [(1920, 1080), (360, 800)]


def _size(page, w, h):
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)


def _open(page, port, hash_):
    open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}", ready_js=READY, wait_loading=False, start_timeout=180, tag="v47_design_fail")


def _count(page):
    from viewport_text import COUNT_JS
    r = json.loads(page.js(COUNT_JS))
    return {k: v for k, v in r.items() if k != "samples"}


def _rows(page):
    page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 60, "上位 5 の一覧")


# ---------------------------------------------------------------- 傾けた 3D の地図
@pytest.mark.parametrize("w,h", SIZES)
def test_guided_market_is_top_down_and_selecting_moves_slowly(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=market&g=1")
        _rows(page)
        time.sleep(1.0)
        assert page.js("Math.abs(window.__app.map().getPitch())") < 0.5   # 案内の地図も真上から見る
        page.js("""(function(){window.__mv=[]; const m=window.__app.map(); m.on('movestart',()=>{window.__t0=performance.now();}); m.on('moveend',()=>{window.__mv.push(performance.now()-window.__t0);});})()""")
        page.js("document.querySelector('.mg-row').click()")
        page.wait(lambda: page.js("window.__mv.length") >= 1, 20, "カメラの移動")
        assert page.js("window.__mv[0]") <= 2100   # 2 秒以内
        # 真上のまま（傾けない・回さない）
        assert page.js("Math.abs(window.__app.map().getPitch())") < 0.5 and page.js("Math.abs(window.__app.map().getBearing())") < 0.5
        # 乗り物は v2 のモデル（読み込めたら 3D だけで出す）
        assert page.js("M3D.skin('truck')") == "v2_truck" and page.js("M3D.skin('freight_train')") == "v2_freight_train"
        page.wait(lambda: page.js("M3D.modelReady('truck')"), 30, "v2 のモデルの読み込み")
        ids = json.loads(page.js("JSON.stringify(window.__app.Market.layers(performance.now(), 6).map(l => l.id))"))
        assert "m-pref-3d" in ids and "m-sel-veh" in ids and "m-sel-veh-sh" in ids, ids   # 県を立ち上げる層と、大きな乗り物と影
        # 全部を見るは真上から（今までどおり）
        page.js("document.getElementById('g-toggle').click()")
        page.wait(lambda: page.js("window.__app.map().getPitch()") < 1, 10, "真上に戻る")
        ids = json.loads(page.js("JSON.stringify(window.__app.Market.layers(performance.now(), 6).map(l => l.id))"))
        assert "m-pref-3d" not in ids, ids


def test_full_market_stays_top_down():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=market")
        time.sleep(1.5)
        assert page.js("window.__app.map().getPitch()") < 1
        assert page.js("M3D.skin('truck')") == "truck"


def test_guided_hides_foreign_labels_and_prefecture_name_labels():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=market&g=1")
        time.sleep(1.0)
        r = json.loads(page.js("""JSON.stringify((function(){const m=window.__app.map(); const out={};
          for (const l of m.getStyle().layers) { if (l.type !== 'symbol' || !/^label_/.test(l.id) || /water/.test(l.id)) continue;
            out[l.id] = l.id === 'label_state' ? m.getLayoutProperty(l.id, 'visibility') : JSON.stringify(m.getFilter(l.id) || null).includes('within'); }
          return out;})())"""))
        assert r, r
        for k, v in r.items():
            assert (v == "none") if k == "label_state" else v is True, (k, v)
        # 全部を見るに戻すと元に戻る
        page.js("document.getElementById('g-toggle').click()")
        time.sleep(1.0)
        r2 = json.loads(page.js("""JSON.stringify((function(){const m=window.__app.map(); return {st: m.getLayoutProperty('label_state','visibility'), c: JSON.stringify(m.getFilter('label_city')||null).includes('within')};})())"""))
        assert r2["st"] != "none" and r2["c"] is False, r2


# ---------------------------------------------------------------- 全国 → 空きを探すの引き継ぎ
@pytest.mark.parametrize("w,h", SIZES)
def test_handoff_passes_the_selected_section_and_fills_pickup(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=market&g=1")
        _rows(page)
        page.js("document.querySelector('.mg-row').click()")   # 兵庫県 → 東京都（荷が多い向き）
        time.sleep(2.2)
        label = page.js("document.getElementById('mg-next').innerText.replace(/\\s+/g,' ').trim()")
        assert label.startswith(("この向きで帰り便を探す", "この向きで空きを探す")) and "架空のデモ" in label, label   # 文言の直し 2026-11-07
        if w == 360:   # 折れない（ボタンの高さは 2 行まで）
            assert page.js("document.getElementById('mg-next').getBoundingClientRect().height") < 70
            assert page.js("document.querySelector('#mg-next .mg-badge').getBoundingClientRect().height") < 20
        page.js("document.getElementById('mg-next').click()")
        page.wait(lambda: "v=find" in page.js("location.hash"), 20, "空きを探すへ")
        hs = page.js("decodeURIComponent(location.hash)")
        assert "pf=兵庫県" in hs and "pt=東京都" in hs, hs
        page.wait(lambda: page.js("document.querySelector('#f-dest-q').value").startswith("兵庫県"), 60, "受け取り場所が選んだ区間の県になる")
        dest = page.js("document.querySelector('#f-dest-q').value")
        assert dest.startswith("兵庫県"), dest   # 受け取りは選んだ区間の荷が多い向きの発地の県
        page.wait(lambda: page.js("!document.getElementById('f-pairline').hidden"), 20, "区間の表示")
        line = page.js("document.getElementById('f-pairline').textContent")
        assert "東京都" in line and "兵庫県" in line, line
        # 一覧は空のまま止まらない: 合う便の札が出るか、「この区間に載せられる便はありません」と「別の区間を選ぶ」が出る（文言の直し 2026-11-07）
        page.wait(lambda: page.js("document.getElementById('f-list').innerText.trim().length") > 0, 60, "一覧が空のまま止まらない")
        lst = page.js("document.getElementById('f-list').innerText")
        assert ("この区間に載せられる便はありません" in lst and "別の区間を選ぶ" in lst) or page.js("!!document.querySelector('#f-list .f-card.ok')"), lst
        # 最大の画面でも誘導の上限を守る
        m = _count(page)
        assert m["chars"] <= BUDGETS[w] and m["primary"] <= 1 and m["controls"] <= 8, m


def test_handoff_via_url_works_when_opened_directly():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=find&g=1&pf=%E5%85%B5%E5%BA%AB%E7%9C%8C&pt=%E6%9D%B1%E4%BA%AC%E9%83%BD")
        page.wait(lambda: page.js("document.querySelector('#f-dest-q').value") != "", 60, "受け取り場所")
        assert page.js("document.querySelector('#f-dest-q').value").startswith("兵庫県")
        assert "兵庫県" in page.js("document.getElementById('f-pairline').textContent")
        assert page.js("Math.abs(window.__app.map().getPitch())") < 0.5   # 空きを探すも真上から


def test_zero_matching_demo_trips_shows_message_and_repick():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        # 北海道 → 沖縄県 の逆向き（沖縄県 → 北海道）の架空の便は無い（上位 5 区間の逆向きにはどれも架空の便がある）
        _open(page, port, "#v=find&g=1&pf=%E5%8C%97%E6%B5%B7%E9%81%93&pt=%E6%B2%96%E7%B8%84%E7%9C%8C")
        page.wait(lambda: page.js("!!document.querySelector('#f-list .f-nopair')"), 60, "0 件のとき")
        assert "この区間に載せられる便はありません" in page.js("document.querySelector('#f-list .f-nopair').textContent")   # 文言の直し 2026-11-07
        btn = page.js("[...document.querySelectorAll('#f-list .f-nopair button')].map(b => b.textContent.trim()).join('|')")
        assert "別の区間を選ぶ" in btn, btn
        page.js("document.querySelector('#f-list [data-act=repick]').click()")
        page.wait(lambda: "v=market" in page.js("location.hash"), 20, "全国の物流傾向へ戻る")
        assert page.js("document.body.classList.contains('view-market') && document.body.classList.contains('guided')") is True


# ---------------------------------------------------------------- フォーカスと Tab の順
def test_focus_moves_to_next_primary_after_selecting_a_section_by_enter():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=market&g=1")
        _rows(page)
        page.js("document.querySelectorAll('.mg-row')[1].focus()")   # 1 位（東京都 → 兵庫県）には鹿児島版のサンプルの便が無く主ボタンが出ないので 2 位を使う（2026-11-07）
        page.key("Enter")
        time.sleep(1.0)
        assert page.js("document.activeElement.id") == "mg-next", page.js("document.activeElement.tagName + '.' + document.activeElement.className")
        assert page.js("document.getElementById('mg-next').disabled") is False


@pytest.mark.parametrize("hash_,guide_id", [("#v=market&g=1", "mg-next"), ("#v=compare&g=1", "q"), ("#v=find&g=1", "f-dest-q")])
def test_guide_panel_comes_before_map_in_tab_order(hash_, guide_id):
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, hash_)
        time.sleep(1.5)
        # DOM の順: 案内の欄 → 地図（canvas と出典のリンクは地図の中）
        assert page.js(f"!!(document.getElementById('{guide_id}').compareDocumentPosition(document.getElementById('map')) & Node.DOCUMENT_POSITION_FOLLOWING)") is True
        assert page.js("!!(document.getElementById('guide-bar').compareDocumentPosition(document.getElementById('map')) & Node.DOCUMENT_POSITION_FOLLOWING)") is True
        # 実際に Tab で進めて、地図の中に入る前に案内の欄のボタンか欄に届く
        page.js("document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0)")
        seen = []
        for _ in range(30):
            page.key("Tab")
            seen.append(page.js("(function(){const e=document.activeElement; return (e.closest('#map') ? 'MAP:' : '') + (e.id || e.tagName);})()"))
            if seen[-1].startswith("MAP:"):
                break
        first_map = next((i for i, s in enumerate(seen) if s.startswith("MAP:")), len(seen))
        first_guide = next((i for i, s in enumerate(seen) if s in ("mg-next", "q", "f-dest-q", "origin") or s.startswith("mg-") or s == "BUTTON"), len(seen))
        assert first_guide < first_map or first_map == len(seen), seen


def test_map_is_last_in_body_dom_order_and_below_panels():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=market&g=1")
        r = json.loads(page.js("""JSON.stringify((function(){const m=document.getElementById('map'); const panels=['m-left','guide-bar','find-view','panel','own-view'].map(i=>document.getElementById(i)).filter(Boolean);
          return {after: panels.every(p => !!(p.compareDocumentPosition(m) & Node.DOCUMENT_POSITION_FOLLOWING)), z: getComputedStyle(m).zIndex,
                  pz: panels.map(p => getComputedStyle(p).zIndex)};})())"""))
        assert r["after"] is True and r["z"] == "0" and all(z not in ("auto", "0") for z in r["pz"]), r


# ---------------------------------------------------------------- 版のお知らせは送り方を比べるだけ
def test_version_notice_only_on_compare_screen():
    port = server_port("local")
    spy = "window.__t=[]; const o=window.toast; window.toast=function(m){window.__t.push(m); return o(m);};"
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=own&g=empty")
        page.wait(lambda: page.js("document.body.classList.contains('own-guided')"), 90, "自社の運行の案内")
        page.js(spy)
        page.js("document.querySelector('#ed-confirm').click()")
        time.sleep(4.0)
        assert not [m for m in page.js("window.__t") if "荷物の条件を変えました" in m or "条件は変わっていません" in m], page.js("window.__t")
    with edge_page(1920, 1080) as page:
        _size(page, 1920, 1080)
        _open(page, port, "#v=compare&g=1")
        page.wait(lambda: page.js("!!document.querySelector('#ed-confirm')"), 60, "条件の確定ボタン")
        page.js(spy)
        page.js("document.querySelector('#ed-confirm').click()")
        page.wait(lambda: any(("荷物の条件を変えました" in m or "条件は変わっていません" in m) for m in (page.js("window.__t") or [])), 20, "送り方を比べるでは出る")


# ---------------------------------------------------------------- 送り方を比べる（案内）
@pytest.mark.parametrize("w,h", SIZES)
def test_compare_step2_preselects_cheapest_and_step3_shows_needed_inputs(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=compare&g=1")
        # 1 条件: 出発地の制限を最初に書き、地図なしの版へ案内する
        note = page.js("document.getElementById('p-origin').textContent")
        assert "関東の 7 か所" in note and page.js("document.querySelector('#p-origin a').getAttribute('href')") == "compare_lite.html", note
        # 案内の最初の画面では集荷地の注記は出さず、荷物の編集欄（ed-origin-note）に移した（compare.css）
        assert page.js("document.getElementById('p-origin').getClientRects().length > 0") is True   # 2026-11-08 U-03: 関東以外の荷主向けの案内は最初の画面に見せる
        assert "ed-origin-note" in (ROOT / "web" / "work.js").read_text(encoding="utf-8")
        assert "右の結果欄" not in page.js("document.body.innerText")
        m = _count(page)
        assert m["chars"] <= BUDGETS[w] and m["primary"] <= 1 and m["controls"] <= 8, m
        # 検索: 入力欄の例の「大阪市中央区」が出る（デモの区間を候補から外さない）
        page.js("""(function(){const q=document.getElementById('q'); q.focus(); q.value='大阪市中央区'; q.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        page.wait(lambda: page.js("document.querySelectorAll('#suggest li[data-i]').length") >= 1, 10, "候補")
        assert "大阪市中央区" in page.js("document.querySelector('#suggest li[data-i]').textContent")
        page.js("document.querySelector('#suggest li[data-i]').dispatchEvent(new MouseEvent('mousedown',{bubbles:true}))")
        page.wait(lambda: page.js("!!document.querySelector('#cards .cc-row')"), 90, "結果の表")
        time.sleep(1.0)
        # 届け先を選んだあとのフォーカスは「見積依頼文を作る」へ
        page.wait(lambda: page.js("document.activeElement.id") == "make-request", 10, "フォーカスが主ボタンへ")
        # 2 結果: 一番安い行が選ばれ、「見積依頼文を作る」が主ボタン
        assert page.js("!!document.querySelector('#cards input[name=cc-pick]:checked')") is True
        assert page.js("document.querySelector('#cards .cc-row:has(input:checked)').classList.contains('off')") is False
        assert page.js("document.getElementById('make-request').hidden") is False
        assert page.js("document.getElementById('make-request').classList.contains('btn-primary')") is True
        # 内容の高さの欄（下半分が空ではない）
        if w == 1920:
            r = json.loads(page.js("JSON.stringify(document.getElementById('detail').getBoundingClientRect())"))
            assert r["height"] < 700, r
        m2 = _count(page)
        assert m2["chars"] <= BUDGETS[w] and m2["primary"] <= 1 and m2["controls"] <= 8, m2
        # 3 見積依頼文: 未入力の出荷日・希望着日の欄が本文の上、本文の最初の部分が開いている
        page.js("document.getElementById('make-request').click()")
        page.wait(lambda: page.js("!!document.getElementById('req-copy')"), 60, "依頼文")
        time.sleep(1.0)
        r = json.loads(page.js("""JSON.stringify((function(){const need=document.querySelector('.w-need'); const t=document.getElementById('req-text');
          return {ins: need ? [...need.querySelectorAll('input[type=date]')].map(i=>i.dataset.need) : null, needTop: need ? need.getBoundingClientRect().top : -1,
                  textTop: t.getBoundingClientRect().top, textH: t.getBoundingClientRect().height, textShown: t.getClientRects().length > 0};})())"""))
        assert r["ins"] == ["ship_date", "desired_date"], r
        assert r["needTop"] < r["textTop"] and r["textShown"] and r["textH"] > 60, r
        m3 = _count(page)
        assert m3["chars"] <= BUDGETS[w] and m3["primary"] <= 1 and m3["controls"] <= 8, m3
        # 日付を入れて作り直すと、未入力の欄が消える
        page.js("""(function(){const a=document.querySelector('[data-need=ship_date]'), b=document.querySelector('[data-need=desired_date]'); a.value='2026-12-01'; b.value='2026-12-03'; a.dispatchEvent(new Event('change',{bubbles:true})); b.dispatchEvent(new Event('change',{bubbles:true}));})()""")
        # 2026-11-08 の直しに合わせて: 日付が入ると必須の欄は消え、任意の日付欄（w-need w-dates）が同じ場所に出る。必須の欄だけが無くなったことを見る
        page.wait(lambda: page.js("!document.querySelector('.w-need:not(.w-dates)') && !!document.querySelector('.w-dates [data-opt-date=ship_date]')"), 30, "必須項目が埋まる")


# ---------------------------------------------------------------- 最初の画面
@pytest.mark.parametrize("w,h", SIZES)
def test_home_has_purpose_line_and_hero_visible(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 30, "最初の画面")
        time.sleep(1.0)
        txt = page.js("document.getElementById('home-purpose').textContent.trim()")
        assert txt == "帰り便の空きを探す、送り方と費用を比べる、荷待ちを記録して荷主と話し合う、の 3 つに使う画面です。", txt   # 日本語の直し 2026-11-08
        assert page.js("document.getElementById('home-purpose').getBoundingClientRect().top") < h * 0.6   # パネルは画面の高さの 34〜40% から始まる（第 9 部 r1）。絵はパネルの周りに見える
        r = json.loads(page.js("""JSON.stringify((function(){const h=document.querySelector('.home-hero').getBoundingClientRect(), t=document.getElementById('home-h').getBoundingClientRect(), b=document.getElementById('role-shipper').getBoundingClientRect();
          return {hh:h.height, ht:h.top, hw:h.width, title:t.top, btnBottom:b.bottom, img: document.querySelector('.home-hero img').complete};})())"""))
        assert r["img"] is True
        if w == 360:   # 絵が上に見え、見出しは絵の下の暗い部分に重なる。主ボタンは最初の画面に入る
            assert r["hh"] >= 220 and r["title"] >= 150 and r["btnBottom"] <= h, r
        m = _count(page)
        assert m["chars"] <= BUDGETS[w], m


def test_home_thumbnail_matches_hero_style():
    from PIL import Image
    thumb = Image.open(ROOT / "web" / "img" / "home_map.jpg").convert("L")
    hero = Image.open(ROOT / "web" / "img" / "hero" / "hero_800.webp").convert("L")
    mean = lambda im: sum(im.getdata()) / (im.size[0] * im.size[1])   # noqa: E731
    # 暗い地図の写真（平均 40 前後）ではなく、ジオラマの明るい絵（平均 100 以上）の切り抜き
    assert mean(thumb) > 90 and abs(mean(thumb) - mean(hero)) < 60, (mean(thumb), mean(hero))
    assert thumb.size == (208, 188)


def test_v2_models_are_present_and_small():
    d = ROOT / "web" / "models" / "v2"
    names = {"truck_4t_empty", "truck_4t_loaded", "truck_10t_empty", "truck_10t_loaded", "roro_ship", "freight_train", "cargo_plane", "container_ship"}
    have = {p.stem for p in d.glob("*.glb")}
    assert names <= have, names - have
    assert all(p.stat().st_size < 300_000 for p in d.glob("*.glb"))


@pytest.mark.parametrize("w,h", SIZES)
def test_market_guided_budgets_and_legend_clear_of_attribution(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        _size(page, w, h)
        _open(page, port, "#v=market&g=1")
        _rows(page)
        page.js("document.querySelector('.mg-row').click()")
        time.sleep(2.4)
        m = _count(page)
        assert m["chars"] <= BUDGETS[w] and m["primary"] <= 1 and m["controls"] <= 8, m
        if w == 1920:
            r = json.loads(page.js("""JSON.stringify((function(){const l=document.getElementById('g-legend').getBoundingClientRect(), a=document.querySelector('.maplibregl-ctrl-attrib').getBoundingClientRect();
              return {legBottom:l.bottom, attrTop:a.top, legRight:l.right, w:innerWidth};})())"""))
            assert r["legBottom"] <= r["attrTop"] + 1, r   # 凡例が出典を隠さない
