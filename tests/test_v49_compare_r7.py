"""v4.9: 実務の効き目 r7 と画面の出来 r4 を受けた比べる画面の修正（宅配の区間、10t 貸切、距離の既定値、360px のカード、扇状の線）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import edge_path_or_skip

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

PAGE = ROOT / "web" / "compare_lite.html"
TEMPLATE = ROOT / "tools" / "compare_lite_template.html"


def _edge():
    import capture_web as cw
    return cw


def test_map_estimate_uses_kanto_table_from_saitama_to_osaka():
    # 埼玉(11107)→大阪(27128)でも、関東発・関西あての宅配の表で額が出る。以前は "other" に落ちて「表がない」だった
    import serve_web
    body = {"origin_code": "11107", "dest_code": "27128",
            "shipment": {"items": [{"count": 12, "length_cm": 50, "width_cm": 40, "height_cm": 50, "weight_kg": 20}]}}
    out = serve_web.api_estimate(body)
    assert out["route"] == "kanto_kansai"
    rows = {r["mode"]: r for r in out["rows"]}
    assert rows["takuhai"]["amount"] and rows["takuhai"]["amount"] > 0, rows["takuhai"]


def test_chart_does_not_preselect_the_only_priced_option():
    js = (ROOT / "web" / "compare_chart.js").read_text(encoding="utf-8")
    assert "unknownOthers" in js and "(r || !unknownOthers)" in js


def test_template_has_10t_pref_distance_cards_and_no_banner():
    t = TEMPLATE.read_text(encoding="utf-8")
    assert "evalKashikiri('10t'" in t and "DECK_10T" in t
    assert "PREFXY" in t and "KM_FACTOR = 1.25" in t and "直線距離の 1.25 倍" in t   # 2026-11-08 の直しに合わせて（「直線×1.25」→「直線距離の 1.25 倍」）
    assert "max-width: 600px" in t and "attr(data-l)" in t
    assert 'class="banner"' not in t and "BANNER" not in t
    for p in (PAGE, ROOT / "web" / "publish" / "compare_lite_artifact.html"):
        h = p.read_text(encoding="utf-8")
        assert 'class="banner"' not in h and h.count("北海道") > 0 and '"沖縄県":[' in h


def test_routes_and_app_hide_fan_before_destination():
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "(S.view === 'compare' && document.body.classList.contains('guided')) ? 0 : S.sel >= 0 ? 0.28 : 1" in app
    css = (ROOT / "web" / "compare.css").read_text(encoding="utf-8")
    assert "line-break: strict" in css


def _eval(page, expr):
    return page.js(expr)


def test_charter_10t_and_takuhai_message_and_auto_km():
    edge_path_or_skip()  # Edge の無い環境（GitHub Actions の Linux）では飛ばす
    cw = _edge()
    try:
        ctx = cw.edge_page(1280, 900)
    except Exception:  # noqa: BLE001
        pytest.skip("Edge がありません")
    with ctx as page:
        page.goto(PAGE.as_uri() + "?x=r7")
        page.wait(lambda: page.js("!!document.querySelector('#from')"), 30, "画面")
        # 5t のパレット 10 枚(500kg x 10): 10t の貸切の目安が出る
        r = page.js("""(function(){
          const res = evaluate({rows: [{n: 10, l: 110, w: 110, h: 120, kg: 500}], from: '埼玉県', to: '宮城県', km: 350, recv: 'business', flex: 0});
          const k = res.options.find((o) => o.id === 'kashikiri_10t');
          return {k: k && {s: k.status, a: k.amount}, k4: res.options.find((o) => o.id === 'kashikiri_4t').status};
        })()""")
        assert r["k"] and r["k"]["a"] and r["k"]["s"] in ("ok", "check"), r
        # 小さな荷には 10t の行を出さない
        r2 = page.js("evaluate({rows: [{n: 2, l: 40, w: 30, h: 20, kg: 5}], from: '東京都', to: '大阪府', km: 492, recv: 'business', flex: 0}).options.some((o) => o.id === 'kashikiri_10t')")
        assert r2 is False
        # 距離の既定値: 東京都→大阪府は 400〜560km、手で書き換えたら維持
        page.js("(function(){ const f = document.querySelector('#from'), t = document.querySelector('#to'); document.querySelector('#km').value = ''; f.value = '東京都'; f.dispatchEvent(new Event('change', {bubbles: true})); t.value = '大阪府'; t.dispatchEvent(new Event('change', {bubbles: true})); })()")
        km = int(page.js("document.querySelector('#km').value"))
        assert 400 <= km <= 560, km
        assert "直線距離の1.25倍" in page.js("document.querySelector('#km-note').textContent")   # 文言の直し 2026-11-07
        page.js("(function(){ const k = document.querySelector('#km'); k.value = '777'; k.dispatchEvent(new Event('input', {bubbles: true})); const t = document.querySelector('#to'); t.value = '京都府'; t.dispatchEvent(new Event('change', {bubbles: true})); })()")
        assert page.js("document.querySelector('#km').value") == "777"
        # 宅配の文言: 関東→関西で荷が載らないとき、区間の外という文を出さない
        page.js("""(function(){ const s = JSON.stringify({rows: [{n: 1, l: 240, w: 60, h: 200, kg: 140, pk: 'box'}, {n: 2, l: 40, w: 30, h: 20, kg: 5, pk: 'box'}], from: '東京都', to: '大阪府', km: '492', recv: 'business', flex: '0', fkFrom: 'unknown', fkTo: 'unknown', pickup: ''}); localStorage.setItem(PKEY, s); })()""")
        page.goto(PAGE.as_uri() + "?x=r7b")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        txt = page.js("(document.querySelector('tr[data-opt=takuhai]')||{textContent:''}).textContent")
        assert "関東発・関西あて（滋賀" not in txt, txt
        assert "宅配に載らない荷" in txt, txt


def test_item_table_becomes_cards_at_360():
    edge_path_or_skip()  # Edge の無い環境（GitHub Actions の Linux）では飛ばす
    cw = _edge()
    try:
        ctx = cw.edge_page(360, 800)
    except Exception:  # noqa: BLE001
        pytest.skip("Edge がありません")
    with ctx as page:
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        r = page.js("""(function(){ const tr = document.querySelector('#pbody tr'); const inp = tr.querySelector('input.pin'); const td = inp.closest('td');
          const b = getComputedStyle(td, '::before').content; return {disp: getComputedStyle(tr).display, w: inp.getBoundingClientRect().width, label: b, sw: document.documentElement.scrollWidth, cw: innerWidth}; })()""")
        assert r["disp"] == "grid" and r["w"] >= 70 and "個数" in r["label"], r
        assert r["sw"] <= r["cw"] + 1, r
