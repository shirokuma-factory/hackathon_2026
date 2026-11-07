"""最初の画面の背景を three.js の 3D にする（web/hero3d.js）、乗り物の動きのなめらかさ、柱の表示の真上、サムネイル。

- 使える端末（WebGL あり・幅 700 超・動きを減らす設定でない）では、キャンバスが静止画の上に出て 20fps 以上で動く。
  使えない端末（WebGL なし・360 幅・動きを減らす設定）では 3D は出ず、映像（または静止画）のまま。最初の画面を離れるとキャンバスは外れる。
- 外部の CDN に頼らない（three.js は web/vendor/three/）。
- 地図の乗り物は経路の弧長に沿って一定の速さで進み、向きを滑らかに回す。柱の表示（市区町村）も真上のまま。
ヘッドレス Edge は同時に 1 つだけ（conftest の edge_page）。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
NO_WEBGL = "(function(){const g=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(t){if(/webgl/i.test(t))return null;return g.apply(this,arguments);};})();"


def _home(page, port, w, h, settle=3.5):
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
    page.goto(f"http://127.0.0.1:{port}/index.html?hero3d=1")  # 3D の背景は ?hero3d=1 のときだけ（2026-11-07）
    page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 30, "最初の画面")
    time.sleep(settle)


def _state(page):
    return json.loads(page.js("""JSON.stringify({active: !!(window.Hero3D && Hero3D.active()), fps: window.Hero3D ? Hero3D.fps() : 0,
      canvas: !!document.querySelector('.home-hero canvas'), on: !!document.querySelector('.home-hero canvas.on'),
      video: !!document.querySelector('.home-hero video'), img: document.querySelector('.home-hero img').complete})"""))


def test_assets_are_local_and_small():
    for f in ("vendor/three/three.module.min.js", "vendor/three/GLTFLoader.js", "vendor/three/BufferGeometryUtils.js", "vendor/three/LICENSE",
              "models/v3/japan_diorama_v3.glb", "models/v3/routes.json", "models/v3/sea_sdf.png", "hero3d.js"):
        assert (WEB / f).is_file(), f
    assert (WEB / "models/v3/japan_diorama_v3.glb").stat().st_size < 4_200_000
    js = (WEB / "hero3d.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js   # 実行時に外部へ取りにいかない
    for f in ("GLTFLoader.js", "BufferGeometryUtils.js"):
        assert not re.search(r"from 'three'", (WEB / "vendor/three" / f).read_text(encoding="utf-8")), f   # 相対の読み込みに書き換えてある
    assert "hero3d.js" in (WEB / "index.html").read_text(encoding="utf-8")


def test_home_thumbnail_is_a_crop_of_the_golden_still():
    from PIL import Image
    im = Image.open(WEB / "img" / "home_map.jpg")
    assert im.size == (208, 188) and im.format == "JPEG"
    assert (WEB / "img" / "home_map.jpg").stat().st_size < 60_000


def test_home_runs_three_hero_on_a_capable_device():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _home(page, port, 1920, 1080)
        s = _state(page)
        assert s["active"] and s["canvas"] and s["on"] and not s["video"] and s["img"], s
        time.sleep(2.0)
        assert _state(page)["fps"] >= 20, _state(page)
        # キャンバスは絶対配置で、押せる所を増やさない
        r = json.loads(page.js("""JSON.stringify((function(){const c=document.querySelector('.home-hero canvas'), b=c.getBoundingClientRect();
          return {w: b.width, h: b.height, aria: c.getAttribute('aria-hidden'), tab: c.tabIndex, pe: getComputedStyle(c.parentNode).pointerEvents};})())"""))
        assert r["w"] >= 1919 and r["h"] >= 1079 and r["aria"] == "true" and r["tab"] == -1 and r["pe"] == "none", r
        # 最初の画面を離れたらキャンバスは外れ、描画は止まる
        page.js("location.hash = '#v=market&g=1'")
        page.wait(lambda: page.js("!document.querySelector('.home-hero canvas')"), 30, "キャンバスが外れる")
        assert page.js("Hero3D.active()") is False


def test_home_falls_back_without_webgl_small_screen_or_reduced_motion():
    port = server_port("local")
    with edge_page(1920, 1080, hooks=(NO_WEBGL,)) as page:   # WebGL なし → 映像
        _home(page, port, 1920, 1080, settle=1.0)
        page.wait(lambda: page.js("!!document.querySelector('.home-hero video')"), 15, "映像")
        s = _state(page)
        assert not s["canvas"] and s["video"] and s["img"], s
    with edge_page(360, 800) as page:   # 幅 700 以下 → 静止画のまま
        _home(page, port, 360, 800, settle=2.5)
        s = _state(page)
        assert not s["canvas"] and not s["video"] and s["img"], s
    with edge_page(1920, 1080) as page:   # 動きを減らす → 静止画のまま
        page.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        _home(page, port, 1920, 1080, settle=2.5)
        s = _state(page)
        assert not s["canvas"] and not s["video"] and s["img"], s


def test_vehicle_motion_code_is_constant_speed_with_smoothed_heading():
    js = (WEB / "market.js").read_text(encoding="utf-8")
    assert "easeTrap" in js and "headState" in js and "turn(st.ang, want" in js and "MapFx.reduced()" in js.split("function headData")[1][:300]
    muni = (WEB / "muni.js").read_text(encoding="utf-8")
    assert "pitch: 55" not in muni and "map.easeTo({ pitch: 0," in muni   # 柱の表示も真上から見る


def test_market_vehicle_moves_at_a_steady_speed():
    port = server_port("local")
    ready = "!!window.__app && !document.querySelector('#loading')"
    with edge_page(1920, 1080) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
        open_app(page, f"http://127.0.0.1:{port}/index.html#v=market&g=1", ready_js=ready, wait_loading=False, start_timeout=180, tag="v50_vehicle_fail")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") > 0, 60, "区間の一覧")
        page.js("document.querySelectorAll('.mg-row')[0].click()")
        time.sleep(3.5)
        d = json.loads(page.js("""(async()=>{
          const M=__app.Market, map=__app.map(), sel=M.selected();
          let c=M.headData(performance.now()/1000).filter(d=>!sel||d.route.r.k===sel.k); c.sort((a,b)=>Math.min(b.u,1-b.u)-Math.min(a.u,1-a.u));
          const key=c[0].route.r.k, u0=c[0].u, dur=c[0].route.dur, pts=[];
          for(let i=0;i<=10;i++){ const want=u0+i*0.1/dur;
            const h=M.headData(performance.now()/1000).filter(d=>d.route.r.k===key).sort((a,b)=>Math.abs(a.u-want)-Math.abs(b.u-want))[0];
            const q=map.project(h.p); pts.push([q.x,q.y,h.ang,performance.now()]); await new Promise(r=>setTimeout(r,100)); }
          return JSON.stringify({pts, pitch: map.getPitch()});})()"""))
        steps = [((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 / ((a[3] - b[3]) / 100) for a, b in zip(d["pts"][1:], d["pts"])]
        mean = sum(steps) / len(steps)
        assert mean > 2 and all(abs(x - mean) <= mean * 0.2 for x in steps), steps   # 100ms ごとの移動量が平均の ±20% 以内
        turns = [abs(((b[2] - a[2] + 540) % 360) - 180) for a, b in zip(d["pts"], d["pts"][1:])]
        assert max(turns) < 45, turns   # 向きは 0.1 秒で 45 度以上は回らない
        assert abs(d["pitch"]) < 0.5
