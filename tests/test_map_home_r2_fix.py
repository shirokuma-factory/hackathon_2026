"""2026-11-08 の最終評価（map_home R2）の直しの確認。M1: 傾向の「市区町村（推定）」から「県」へ戻すと県の塗りが戻る。"""

from __future__ import annotations

import base64
import io
import time

from conftest import edge_page, open_app, server_port

ERR_HOOK = ("window.__errs=[];window.addEventListener('error',e=>window.__errs.push(e.message));"
            "window.addEventListener('unhandledrejection',e=>window.__errs.push(String(e.reason)));")


def _spread(page) -> float:
    """地図の中央付近の色のばらつき（標準偏差の和）。県の塗り分け（茶と緑）があると大きく、一色だと小さい。"""
    from PIL import Image, ImageStat
    r = page.send("Page.captureScreenshot", format="png", fromSurface=True)
    img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
    w, h = img.size
    box = img.crop((int(w * 0.30), int(h * 0.25), int(w * 0.60), int(h * 0.85)))
    return sum(ImageStat.Stat(box).stddev)


def test_m1_pref_choropleth_returns_after_muni_scope():
    port = server_port("public")
    with edge_page(1920, 1080, hooks=(ERR_HOOK,)) as page:
        page.send("Page.navigate", url="about:blank")
        time.sleep(0.4)
        open_app(page, f"http://127.0.0.1:{port}/index.html#v=market", ready_js="!!window.__app && !document.querySelector('#loading')",
                 wait_loading=False, start_timeout=180, tag="map_home_r2_fail")
        time.sleep(3)
        before = _spread(page)
        page.js("document.querySelector('#m-scope [data-scope=muni]').click()")
        time.sleep(3)
        page.js("document.querySelector('#m-scope [data-scope=pref]').click()")
        time.sleep(3)
        after = _spread(page)
        # 県の塗りが戻っていれば、色のばらつきは最初の画面の9 割以上ある（消えると一色になる）
        assert after > before * 0.9, (before, after)
        # 鉄道に切り替えても戻ったまま
        assert not page.js("window.__errs.length"), page.js("JSON.stringify(window.__errs)")
