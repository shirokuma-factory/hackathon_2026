"""画面の見た目の直し（2026-11-08 の目視）の確認。値は画面の中で測る。Edge が無い環境だけ飛ばす。"""

from __future__ import annotations

import contextlib
import time

from conftest import edge_page, open_app, server_port

CONTRAST_JS = """(function(){
  const lum = (c) => { const v = c.map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const rgba = (s) => { const m = s.match(/[\\d.]+/g).map(Number); return [m[0], m[1], m[2], m.length > 3 ? m[3] : 1]; };
  const e = document.querySelector('.home-brand .hb-tag'); if (!e) return null;
  const fg = rgba(getComputedStyle(e).color);
  // 下地は .home-brand の背景を、最悪の場合の明るい背景（白）に重ねたものとして測る
  const bg0 = rgba(getComputedStyle(e.closest('.home-brand')).backgroundColor);
  const bg = [0, 1, 2].map((i) => bg0[i] * bg0[3] + 255 * (1 - bg0[3]));
  const a = lum(fg), b = lum(bg);
  return {ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05), bgA: bg0[3]};
})()"""


@contextlib.contextmanager
def _browser(width: int, height: int):
    port = server_port("public")
    with edge_page(width, height) as page:
        def open_(hash_: str, ready: str = "!!window.__app && !document.querySelector('#loading')") -> None:
            page.send("Page.navigate", url="about:blank")
            time.sleep(0.4)
            open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}", ready_js=ready,
                     wait_loading=False, start_timeout=180, tag="visual_fix_fail")
            time.sleep(2.5)
        yield page, open_


def test_home_tagline_contrast_is_at_least_4_5():
    with _browser(1920, 1080) as (page, open_):
        open_("", "!!document.querySelector('.home-brand') && document.body.classList.contains('view-home')")
        r = page.js(CONTRAST_JS)
        assert r and r["ratio"] >= 4.5, r


def test_opened_credits_toggle_is_topmost_and_panel_is_opaque_and_clear_of_legend():
    with _browser(1920, 1080) as (page, open_):
        open_("#v=market")
        page.js("(function(){const d = document.querySelector('#m-credits-d'); d.open = true; d.dispatchEvent(new Event('toggle'));})()")
        time.sleep(0.8)
        r = page.js("""(function(){
          const s = document.querySelector('#m-credits-d > summary'), c = document.querySelector('#m-credits'), lg = document.querySelector('#m-legend');
          const sr = s.getBoundingClientRect(), top = document.elementFromPoint(sr.left + sr.width / 2, sr.top + sr.height / 2);
          const cr = c.getBoundingClientRect(), lr = lg.getBoundingClientRect();
          const bg = getComputedStyle(c).backgroundColor.match(/[\\d.]+/g).map(Number);
          const overlap = !(cr.right <= lr.left || cr.left >= lr.right || cr.bottom <= lr.top || cr.top >= lr.bottom);
          return {hit: !!top && (top === s || s.contains(top)), alpha: bg.length > 3 ? bg[3] : 1, overlap, vw: innerWidth, l: cr.left, r: cr.right, t: cr.top};
        })()""")
        assert r["hit"], r
        assert r["alpha"] >= 0.95, r
        assert not r["overlap"], r
        assert r["l"] >= 0 and r["r"] <= r["vw"] and r["t"] >= 0, r


def test_hover_tag_stays_inside_the_viewport_at_390():
    with _browser(390, 844) as (page, open_):
        open_("#v=market")
        for x, y in ((20, 20), (380, 400), (200, 840)):
            page.js(f"Market.hover({{object: {{properties: {{n: '長野県'}}}}, layer: {{id: 'm-pref'}}, x: {x}, y: {y}}})")
            time.sleep(0.3)
            r = page.js("(function(){const t = document.querySelector('#hover-tag'), b = t.getBoundingClientRect(); return {on: t.classList.contains('on'), l: b.left, r: b.right, t: b.top, b: b.bottom, vw: innerWidth, vh: innerHeight};})()")
            assert r["on"], r
            assert r["l"] >= 7.5 and r["r"] <= r["vw"] - 7.5 and r["t"] >= 0 and r["b"] <= r["vh"], r


def test_find_view_map_credit_is_not_covered_at_1920():
    with _browser(1920, 1080) as (page, open_):
        open_("#v=find")
        r = page.js("""(function(){
          const c = document.querySelector('.maplibregl-ctrl-attrib'); if (!c) return null;
          const a = c.querySelector('a') || c, b = a.getBoundingClientRect();
          const top = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
          return {hit: !!top && (top === a || a.contains(top) || c.contains(top)), l: b.left, r: b.right, w: b.width, vw: innerWidth};
        })()""")
        assert r and r["w"] > 0 and r["hit"] and r["r"] <= r["vw"], r
