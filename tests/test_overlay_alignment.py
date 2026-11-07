"""地図（MapLibre）の描画バッファと deck.gl の層が、DPR 2 でも同じ解像度比でそろうこと。

作成時の pixelRatio が描画バッファに反映されず canvas.width が DPR 倍のままだと、deck の層が左上基準で拡大して地図に重なり、日本が二重に見えた。
deck は描画バッファの横幅 ÷ useDevicePixels の倍率で層を描くので、その倍率のずれ × 画面上の位置が、層と地図の位置のずれ（CSS px）になる。
ヘッドレス Edge は 1 つ（conftest の edge_page）。deviceScaleFactor 2、1920×1080。
"""

from __future__ import annotations

import json
import time

import pytest
from conftest import edge_page, open_app, server_port

PROBE = """JSON.stringify((function(){
  const m = __app.map(), d = __app.overlay()._deck, c = m.getCanvas(), gl = c.getContext('webgl2') || c.getContext('webgl');
  const vp = d.getViewports()[0];
  const buf = gl.drawingBufferWidth / c.clientWidth;               // 地図の描画バッファの実際の解像度比
  const deckRatio = d.props.useDevicePixels === true ? devicePixelRatio : d.props.useDevicePixels;
  const k = buf / deckRatio - 1;                                    // deck の層の拡大のずれ（左上基準）
  const pts = [[139.7, 35.7], [135.5, 34.7], [130.4, 33.6], [141.3, 43.1]];
  const proj = pts.map(p => { const a = m.project(p), b = vp.project(p); return Math.max(Math.abs(a.x - b[0]), Math.abs(a.y - b[1])); });
  const off = pts.map(p => { const a = m.project(p); return Math.max(Math.abs(a.x * k), Math.abs(a.y * k)); });
  return {dpr: devicePixelRatio, mpr: m.getPixelRatio(), buf, deckRatio, projDiff: Math.max(...proj), renderOffset: Math.max(...off),
          bufMismatch: Math.abs(gl.drawingBufferWidth - Math.round(c.clientWidth * m.getPixelRatio()))};
})())"""


def _check(m, tag):
    assert m["projDiff"] <= 1, (tag, m)
    assert m["bufMismatch"] <= 1, (tag, m)          # canvas の大きさ = 表示の幅 × 地図の解像度比
    assert m["renderOffset"] <= 1, (tag, m)          # deck の層と地図の描画位置のずれ（CSS px）


@pytest.mark.parametrize("hash_", ["#v=market", "#v=find"])
def test_deck_and_map_share_pixel_ratio_at_dpr2(hash_):
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=2, mobile=False)
        open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}")
        time.sleep(1.5)
        m = json.loads(page.js(PROBE))
        assert m["dpr"] == 2
        _check(m, "start")
        for pref in ("low", "mid", "high"):             # 画質の段が変わったあと（再描画の前後）
            page.js(f"PERF.setPref('{pref}')")
            time.sleep(1.0)
            _check(json.loads(page.js(PROBE)), pref)
        page.js("__app.map().flyTo({center: [133, 35], zoom: 6.5, duration: 1200, essential: true}), 0")
        time.sleep(0.5)                                  # カメラ移動の途中
        _check(json.loads(page.js(PROBE)), "flyTo")
        time.sleep(1.5)
        _check(json.loads(page.js(PROBE)), "after flyTo")
        assert page.js("__app.map().getPitch()") == 0
