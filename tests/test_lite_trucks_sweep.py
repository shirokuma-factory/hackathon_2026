"""公開版のどぶさらい（2026-11-08 送り方と大型車 H05・H14〜H21）の修正の試験。compare_lite と trucks_now。"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

PAGE = ROOT / "web" / "compare_lite.html"
ARTIFACT = ROOT / "web" / "publish" / "compare_lite_artifact.html"
SHOT_DIR = os.environ.get("LT_SHOT_DIR")   # 指定したときだけ画像を残す
READY = "!!(window.__trucks && window.__trucks.ready && window.__trucks.frame >= 1)"
TOWNS = ROOT / "web" / "trucks_now.js"


def _shot(page, name: str):
    if not SHOT_DIR:
        return
    Path(SHOT_DIR).mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="png", captureBeyondViewport=True)
    (Path(SHOT_DIR) / name).write_bytes(base64.b64decode(r["data"]))


def _open_lite(page, path: Path):
    page.goto(path.as_uri() + "?example=1")
    page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")


def _set(page, sel: str, value: str):
    page.js("(() => { const e = document.querySelector(" + json.dumps(sel) + "); e.value = " + json.dumps(value, ensure_ascii=False) + "; e.dispatchEvent(new Event('change', {bubbles: true})); })()")


OKINAWA = "S.rows = [{n: 2, l: 110, w: 110, h: 120, kg: 350, pk: 'pallet'}, {n: 5, l: 60, w: 40, h: 30, kg: 12, pk: 'box'}]; renderRows();"


# ---------------------------------------------------------------- compare_lite
@pytest.mark.parametrize("path", [PAGE, ARTIFACT], ids=["page", "artifact"])
def test_h05_flex_only_when_the_user_chose_it(path):
    with edge_page(1200, 800, webgl=False) as page:
        _open_lite(page, path)
        assert page.js("document.querySelector('#flex').value") == ""
        txt = page.js("document.querySelector('#quote').value")
        assert "日ほどは遅れても構いません" not in txt and "この日に届く必要があります" not in txt, txt   # 日本語の直し 2026-11-08
        _set(page, "#flex", "4")
        txt = page.js("document.querySelector('#quote').value")
        assert "4 日ほどは遅れても構いません" in txt, txt
        _set(page, "#flex", "0")
        assert "この日に届く必要があります" in page.js("document.querySelector('#quote').value")


@pytest.mark.parametrize("path", [PAGE, ARTIFACT], ids=["page", "artifact"])
def test_h14_h17_okinawa_priced_first_ship_row_and_sea_distance(path):
    with edge_page(1200, 800, webgl=False) as page:
        _open_lite(page, path)
        page.js("(() => { " + OKINAWA + " })()")
        _set(page, "#to", "沖縄県")
        out = page.js("""(() => {
          const rows = [...document.querySelectorAll('table.res tbody tr')];
          return { ids: rows.map((r) => r.dataset.opt), top: rows.filter((r) => r.hasAttribute('data-top')).map((r) => r.dataset.opt),
                   ng: rows.filter((r) => r.classList.contains('o-ng')).map((r) => r.dataset.opt),
                   ship: (document.querySelector('tr[data-opt=ship]') || {}).innerText || '', label: document.querySelector('#km-label').textContent,
                   quote: document.querySelector('#quote').value };
        })()""")
        # 額の無い行は強調しない。使えない行は最後
        assert out["top"] == [], out
        assert out["ids"][-len(out["ng"]):] == out["ng"], out
        assert "船（フェリー・RORO）" in out["ship"] and "見積" in out["ship"] and "次にすること" in out["ship"], out
        assert "経路の距離（海を含む" in out["label"], out
        assert "道路の距離:" not in out["quote"] and "経路の距離（海を含む）" in out["quote"], out
        # 陸続きの区間では船の行も「経路」の言い方も出ない
        _set(page, "#to", "大阪府")
        out = page.js("({ ship: !!document.querySelector('tr[data-opt=ship]'), label: document.querySelector('#km-label').textContent, top: document.querySelectorAll('table.res tr[data-top]').length })")
        assert out["ship"] is False and "道路の距離" in out["label"] and out["top"] == 1, out


@pytest.mark.parametrize("path", [PAGE, ARTIFACT], ids=["page", "artifact"])
def test_h15_ng_badge_is_not_struck_through(path):
    with edge_page(1200, 800, webgl=False) as page:
        _open_lite(page, path)
        page.js("(() => { " + OKINAWA + " })()")
        _set(page, "#to", "沖縄県")
        out = page.js("(() => { const b = document.querySelector('.badge.ng'); return { t: b.textContent, d: getComputedStyle(b).textDecorationLine }; })()")
        assert out["t"] == "使えない ×" and out["d"] == "none", out


@pytest.mark.parametrize("path", [PAGE, ARTIFACT], ids=["page", "artifact"])
def test_h16_ranking_table_is_stacked_at_390(path):
    with edge_page(390, 844, webgl=False) as page:
        _open_lite(page, path)
        page.js("(() => { const set = (sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); };"
                "set('#c-r-rate', '40'); set('#c-r-min', '5000'); })()")
        page.wait(lambda: page.js("!!document.querySelector('#cmp-rank')"), 10, "順位の表")
        out = page.js("""(() => { const t = document.querySelector('#cmp-rank');
          const td = [...t.querySelectorAll('td.rn')].map((e) => ({ w: e.getBoundingClientRect().width, h: e.getBoundingClientRect().height }));
          return { td, scrollW: document.documentElement.scrollWidth }; })()""")
        _shot(page, "lite_h16_390.png")
        assert out["scrollW"] <= 392, out
        for c in out["td"]:
            assert c["w"] > 120 and c["h"] < 70, out   # 1 文字ずつの縦並びにならない


@pytest.mark.parametrize("path", [PAGE, ARTIFACT], ids=["page", "artifact"])
def test_h16_rank_table_cells_have_labels(path):
    t = path.read_text(encoding="utf-8")
    assert 'id="cmp-rank"' in t and 'data-l="送り方"' in t and "#cmp-rank td::before" in t


# ---------------------------------------------------------------- trucks_now
def _open_trucks(page, url_path="trucks_now.html"):
    port = server_port("local")
    open_app(page, f"http://127.0.0.1:{port}/{url_path}", ready_js=READY, wait_loading=False, start_timeout=90,
             what="いまの大型車の画面が起動しません", tag="trucks_now")
    page.wait(lambda: page.js("window.__trucks.map && window.__trucks.map.loaded()"), 60, "地図の読み込み")


def test_h18_static_snapshot_is_one_clear_line():
    with edge_page(1920, 1080) as page:
        _open_trucks(page)
        out = page.js("""(() => { const S = window.__trucks, d = S.data;
          const o = { static: S.api.isStatic ? S.api.isStatic(d) : null };
          const ago = (m) => new Date(Date.now() - m * 60000 + 9 * 3600000).toISOString().slice(0, 19) + '+09:00';
          // 取得から 14 時間: 写し
          d.fetched_at = ago(14 * 60); S.api.refresh();
          o.old = { fresh: document.querySelector('#fresh').innerText, pollHidden: document.querySelector('#poll').hidden, btnHidden: document.querySelector('#poll-now').hidden };
          // 取得から 10 分: 更新あり
          d.fetched_at = ago(10); S.api.refresh();
          o.live = { fresh: document.querySelector('#fresh').innerText, pollHidden: document.querySelector('#poll').hidden, btnHidden: document.querySelector('#poll-now').hidden };
          // 取得から 14 時間でも JSON が static:false なら更新ありとして扱う
          d.fetched_at = ago(14 * 60); d.static = false; S.api.refresh();
          o.flag = { pollHidden: document.querySelector('#poll').hidden };
          d.static = true; d.fetched_at = ago(5); S.api.refresh();
          o.flagStatic = { fresh: document.querySelector('#fresh').innerText, pollHidden: document.querySelector('#poll').hidden };
          return o; })()""")
        assert "取得の写しです" in out["old"]["fresh"] and out["old"]["pollHidden"] and out["old"]["btnHidden"], out
        assert "遅れ" not in out["old"]["fresh"] and "今すぐ" not in out["old"]["fresh"] and "古いデータです" not in out["old"]["fresh"], out
        import re
        assert re.search(r"20\d\d/\d\d/\d\d \d\d:\d\d 取得の写しです", out["old"]["fresh"]), out
        assert "取得の写しです" not in out["live"]["fresh"] and "遅れ" not in out["live"]["fresh"] and not out["live"]["pollHidden"] and not out["live"]["btnHidden"], out
        assert out["flag"]["pollHidden"] is False, out
        assert out["flagStatic"]["pollHidden"] is True and "取得の写しです" in out["flagStatic"]["fresh"], out
        assert page.js("window.__errs.length") == 0
        _shot(page, "trucks_1920.png")


def test_h19_rank_numbers_follow_the_selected_hour_and_diff_header_is_labelled():
    with edge_page(1920, 1080) as page:
        _open_trucks(page)
        read = """(() => { const S = window.__trucks, C = window.TrucksCore;
          const rows = [...document.querySelectorAll('#rank .rk')].map((r) => ({ pref: r.dataset.pref, n: r.querySelector('.n').textContent, v: parseFloat(r.querySelector('.v').textContent.replace(/,/g, '')) }));
          return { rows, head: document.querySelector('#rank-diff-h').textContent }; })()"""
        for t, head in ((23, "先週比"), (6, "1 時間前比")):
            page.js(f"window.__trucks.api.setT({t})")
            page.wait(lambda: page.js(f"window.__trucks.lastIdx === {t}"), 10, "時間の切り替え")
            page.wait(lambda: page.js("document.querySelector('#rank-diff-h').textContent") == head, 10, "見出し")
            out = page.js(read)
            assert out["head"] == head, out
            by_n = sorted((r for r in out["rows"] if r["n"].isdigit()), key=lambda r: int(r["n"]))
            vals = [r["v"] for r in by_n]
            assert vals == sorted(vals, reverse=True), (t, by_n[:12])   # 番号の順に値が下がる（番号が実際の順位）
            assert [int(r["n"]) for r in by_n] == list(range(1, len(by_n) + 1))
        _shot(page, "trucks_slider6_1920.png")


def test_h20_point_card_title_is_not_an_api_id():
    with edge_page(1920, 1080) as page:
        _open_trucks(page)
        out = page.js("""(() => { const S = window.__trucks; S.T = 23; const p = S.pts.find((q) => q.series[23] != null && q.pref);
          const full = S.api.cardHtml(p, true), tip = S.api.tipHtml(p); const t = (h) => (h.match(/<b>([^<]*)<\\/b>/) || [])[1];
          return { full: t(full), tip: t(tip), id: String(p.id), pref: p.pref, fullHtml: full }; })()""")
        for k in ("full", "tip"):
            assert out["pref"] in out[k] and "観測点" in out[k] and "地点 " not in out[k] and out["id"] not in out[k], out
        assert "上り・大型交通量" not in out["fullHtml"] and "ファイルには" not in out["fullHtml"], out
        assert "観測点コード" in out["fullHtml"]


def test_h21_back_link_visible_at_390_and_favicon():
    html = (ROOT / "web" / "trucks_now.html").read_text(encoding="utf-8")
    assert 'rel="icon"' in html
    with edge_page(390, 844) as page:
        _open_trucks(page)
        out = page.js("(() => { const a = document.querySelector('.top .back a'); const r = a.getBoundingClientRect(); return { disp: getComputedStyle(a.parentElement).display, w: r.width, h: r.height, x: r.x }; })()")
        assert out["disp"] != "none" and out["w"] > 40 and out["h"] > 8 and out["x"] >= 0, out
        _shot(page, "trucks_390.png")


def test_h14_okinawa_at_390_has_no_horizontal_scroll():
    with edge_page(390, 844, webgl=False) as page:
        _open_lite(page, PAGE)
        page.js("(() => { " + OKINAWA + " })()")
        _set(page, "#to", "沖縄県")
        assert page.js("document.documentElement.scrollWidth") <= 392
        _shot(page, "lite_h14_okinawa_390.png")


@pytest.mark.parametrize("w,h", [(1920, 1080), (390, 844)])
def test_licence_article5_sentence_is_visible_without_opening_anything(w, h):
    with edge_page(w, h) as page:
        _open_trucks(page)
        out = page.js("""(() => { const e = document.querySelector('#credit-svc'), c = document.querySelector('#credit'), r = e.getBoundingClientRect(), cs = getComputedStyle(e);
          return { t: e.textContent, w: r.width, h: r.height, vis: cs.visibility, disp: cs.display, inDetails: !!e.closest('details'), color: cs.color, creditT: c.textContent,
                   inView: r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth }; })()""")
        assert "国土交通省によって保証されたものではありません" in out["t"], out
        assert out["w"] > 100 and out["h"] > 8 and out["vis"] == "visible" and out["disp"] != "none" and not out["inDetails"], out
        assert "国土交通省" in out["creditT"], out
        _shot(page, f"trucks_credit_{w}.png")
