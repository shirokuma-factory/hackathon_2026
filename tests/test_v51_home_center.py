"""最初の画面の選択パネルを画面の中央に置く（第 9 部 r1）。

- パネルは横の中央（±2px）、幅は min(720, 画面幅 - 48)。上の端は画面の高さの 34%（900px 以上の高さでは 40%）。phone（700px 以下）では下に寄せたボトムシート。
- 1 段目と 2 段目（荷主・運送会社）で、パネルの中身が互いに重ならず、横スクロールがなく、パネルの外にはみ出さない。1280×720 と 1366×768 でもスクロールなしで収まる。
- 背景は画面いっぱい。側面の暗いグラデーションはなく、中央の放射状の暗がりだけ。映像の読み込みでパネルの位置が動かない。
- 文字は 4.5:1 以上（パネルの下の背景の明るい側で測る）。
ヘッドレス Edge は同時に 1 つだけ（conftest の edge_page）。
"""

from __future__ import annotations

import base64
import io
import json
import time

import pytest
from conftest import edge_page, server_port

SIZES = [(1920, 1080), (1440, 900), (1366, 768), (1280, 720), (390, 844), (360, 800)]
ROLES = ["", "#r=shipper", "#r=carrier"]

MEASURE = """JSON.stringify((function(){
  const wrap = document.querySelector('.home-wrap'), p = wrap.getBoundingClientRect();
  const vis = [...document.querySelectorAll('#home h1, #home p, #home h2, #home button, #home a, #home small, #home b, #home img')].filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && !e.closest('.home-hero'));
  const rs = vis.map(e => { const r = e.getBoundingClientRect(); return {id: e.id || e.tagName, l: r.left, t: r.top, r: r.right, b: r.bottom}; });
  const outside = rs.filter(r => r.l < p.left - 0.5 || r.r > p.right + 0.5 || r.t < p.top - 0.5 || r.b > p.bottom + 0.5).map(r => r.id);
  const btn = [...wrap.querySelectorAll('button, a')].filter(e => e.getClientRects().length).map(e => ({e, r: e.getBoundingClientRect()}));
  const overlap = [];
  for (let i = 0; i < btn.length; i++) for (let j = i + 1; j < btn.length; j++) {
    const a = btn[i], b = btn[j];
    if (a.e.contains(b.e) || b.e.contains(a.e)) continue;
    if (a.r.left < b.r.right - 0.5 && b.r.left < a.r.right - 0.5 && a.r.top < b.r.bottom - 0.5 && b.r.top < a.r.bottom - 0.5) overlap.push([a.e.id, b.e.id]);
  }
  const hero = document.querySelector('.home-hero').getBoundingClientRect();
  return {cx: (p.left + p.right) / 2 - innerWidth / 2, top: p.top, bottom: p.bottom, width: p.width, vw: innerWidth, vh: innerHeight,
          scroll: wrap.scrollHeight - wrap.clientHeight, hs: document.scrollingElement.scrollWidth - innerWidth, vs: document.scrollingElement.scrollHeight - innerHeight,
          outside, overlap, hero: [hero.width, hero.height]};
})())"""


def _open(page, port, w, h, hash_):
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
    page.goto(f"http://127.0.0.1:{port}/index.html{hash_}")
    page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 30, "最初の画面")
    time.sleep(1.4)   # 入場の動き（400ms + 遅れ）が終わるまで


@pytest.mark.parametrize("w,h", SIZES)
@pytest.mark.parametrize("hash_", ROLES)
def test_panel_is_centred_inside_the_viewport_without_overlap(w, h, hash_):
    port = server_port("local")
    with edge_page(w, h) as page:
        _open(page, port, w, h, hash_)
        m = json.loads(page.js(MEASURE))
        assert abs(m["cx"]) <= 2, m
        assert m["hs"] <= 0 and m["vs"] <= 0, m          # 横にも縦にもスクロールしない
        assert not m["outside"] and not m["overlap"], m
        assert m["hero"][0] >= w - 1 and m["hero"][1] >= h - 1, m   # 背景は画面いっぱい
        if w > 700:
            assert abs(m["width"] - min(720, w - 48)) <= 1, m
            assert m["scroll"] <= 0, m                   # 1280×720 でもパネルの中はスクロールなし
            want = 0.4 if h >= 900 else 0.34
            assert abs(m["top"] / h - want) <= 0.01, m
            assert m["bottom"] <= h - 8, m
        else:
            assert abs(m["width"] - (w - 32)) <= 1, m     # 左右 16px
            assert abs((h - m["bottom"]) - 16) <= 1, m    # 下から 16px
            assert m["bottom"] - m["top"] <= h * 0.62 + 1 or m["scroll"] > 0, m   # 62% を超えるならパネルの中で動く


def test_side_gradient_is_gone_and_glass_panel_style():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _open(page, port, 1920, 1080, "")
        r = json.loads(page.js("""JSON.stringify((function(){const w=getComputedStyle(document.querySelector('.home-wrap')), a=getComputedStyle(document.querySelector('.home-hero'), '::after');
          return {bg: w.backgroundColor, bf: w.backdropFilter || w.webkitBackdropFilter, radius: w.borderTopLeftRadius, after: a.backgroundImage, pad: w.paddingLeft};})())"""))
        assert r["bg"].startswith("rgba(10, 16, 28, 0.58") and "blur(18px)" in r["bf"] and r["radius"] == "20px", r
        assert "radial-gradient" in r["after"] and "90deg" not in r["after"], r
        assert 32 <= float(r["pad"][:-2]) <= 40, r


def test_hero_video_start_does_not_move_the_panel():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _open(page, port, 1920, 1080, "")
        before = page.js("JSON.stringify(document.querySelector('.home-wrap').getBoundingClientRect())")
        time.sleep(2.5)
        assert page.js("JSON.stringify(document.querySelector('.home-wrap').getBoundingClientRect())") == before


def test_hero_position_is_retunable_with_custom_properties():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _open(page, port, 1920, 1080, "")
        assert page.js("getComputedStyle(document.querySelector('.home-hero img')).objectPosition") == "50% 50%"
        page.js("document.documentElement.style.setProperty('--hero-pos-y', '20%')")
        assert page.js("getComputedStyle(document.querySelector('.home-hero img')).objectPosition") == "50% 20%"


@pytest.mark.parametrize("w,h", [(1920, 1080), (1280, 720), (360, 800)])
@pytest.mark.parametrize("hash_", ROLES)
def test_text_contrast_on_the_panel_is_at_least_4_5(w, h, hash_):
    import numpy as np
    from PIL import Image
    port = server_port("local")
    js = """JSON.stringify([...document.querySelectorAll('#home h1, #home p, #home h2, #home b, #home small, #home .btn-link, #home .purpose')].filter(e => e.getClientRects().length).map(e => { const r = e.getBoundingClientRect(); return {c: getComputedStyle(e).color, x: r.left, y: r.top, w: r.width, h: r.height}; }))"""

    def lum(c):
        c = np.asarray(c, float) / 255
        c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]

    with edge_page(w, h) as page:
        _open(page, port, w, h, hash_)
        els = json.loads(page.js(js))
        # 文字を透明にして背景だけを撮り、背景の明るい側（上位 3%）に対する文字色の比を測る
        page.js("(()=>{const s=document.createElement('style');s.textContent='#home .home-wrap *{color:transparent!important;text-shadow:none!important}';document.head.appendChild(s)})()")
        time.sleep(0.3)
        im = np.asarray(Image.open(io.BytesIO(base64.b64decode(page.send("Page.captureScreenshot", format="png")["data"]))).convert("RGB"))
        assert els
        for e in els:
            reg = im[int(e["y"]):int(e["y"] + e["h"]), int(e["x"]):int(e["x"] + e["w"])]
            if reg.size == 0:
                continue
            fg = [int(v) for v in e["c"][e["c"].index("(") + 1:-1].split(",")[:3]]
            bg = np.percentile(lum(reg.reshape(-1, 3)), 97)
            lf = float(lum(np.array(fg)))
            cr = (lf + 0.05) / (bg + 0.05) if lf > bg else (bg + 0.05) / (lf + 0.05)
            assert cr >= 4.5, (e, cr)


def test_motion_is_off_under_reduced_motion():
    port = server_port("local")
    with edge_page(1280, 720) as page:
        page.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        page.send("Emulation.setDeviceMetricsOverride", width=1280, height=720, deviceScaleFactor=1, mobile=False)
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 30, "最初の画面")
        assert page.js("getComputedStyle(document.querySelector('.home-wrap')).animationName") == "none"
        assert page.js("getComputedStyle(document.querySelector('.home-wrap h1')).animationName") == "none"
