"""幅 390 の狭い画面で、下のパネルや下に留まる主ボタンの帯が内容を覆わないこと。

- 案内の全国の物流傾向: 下のパネルの上に九州（鹿児島の南端）の点が見える（地図の余白に下のパネルの高さを入れる）。
- 自社の運行の案内（帰り便の手順）: 下に留まる主ボタンの帯が、文面の入力欄とそのボタンを覆わない。
ヘッドレス Edge は 1 つだけ。画面写真は環境変数 NARROW_SHOTS_DIR の置き場に残す（無ければ残さない）。
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from conftest import edge_page, open_app

READY = "!!window.__app && !document.querySelector('#loading')"
W, H = 390, 844


def _shot(page, name):
    d = os.environ.get("NARROW_SHOTS_DIR")
    if not d:
        return
    Path(d).mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=60)
    (Path(d) / name).write_bytes(base64.b64decode(r["data"]))


def _size(page):
    page.send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=True)
    time.sleep(0.7)


def test_narrow_overlaps_390(local_server):
    with edge_page(W, H) as page:
        _size(page)
        for hash_, tag in (("#v=market&g=1", "market_guided"), ("#v=market", "market_default")):
            if tag == "market_guided":
                open_app(page, f"http://127.0.0.1:{local_server}/index.html{hash_}", ready_js=READY, wait_loading=False, tag="narrow_fail")
            else:
                page.js(f"location.hash = {json.dumps(hash_)}")
                page.js("location.reload()")
                page.wait(lambda: page.js(READY), 120, "再読み込み")
            page.wait(lambda: page.js("document.body.classList.contains('sheet-mode')"), 30, "狭い画面の表示")
            time.sleep(3.5)   # 地図の寄せの動きが終わるまで
            _shot(page, tag + "_390.jpg")
            r = page.js("""(function(){
              const m = window.__app.map(), el = document.querySelector('#m-guide.sh-on, #m-left.sh-on, #m-right.sh-on'), sh = el.getBoundingClientRect();
              const k = m.project([130.55, 30.99]);
              return {sheetTop: sh.top, kyushuY: k.y, kyushuX: k.x};
            })()""")
            assert r["kyushuY"] < r["sheetTop"] - 4, f"{tag}: 九州がパネルの下に隠れる: {r}"
            assert 0 <= r["kyushuX"] <= W, r

        # 自社の運行の案内: 文面の入力欄がボタンの帯に覆われない
        page.goto(f"http://127.0.0.1:{local_server}/index.html#v=own&g=empty")
        page.wait(lambda: page.js(READY) and page.js("!!document.querySelector('#neg-root')"), 120, "自社の運行")
        _size(page)
        page.js("document.querySelector('#neg-entry-sample').click()")
        time.sleep(0.4)
        for sel in ["#neg-go-cond", "#neg-g-conds", "#neg-go-draft"]:
            page.wait(lambda s=sel: page.js(f"!!document.querySelector({json.dumps(s)})"), 10, sel)
            page.js(f"document.querySelector({json.dumps(sel)}).click()")
            time.sleep(0.4)
        page.wait(lambda: page.js("!!document.querySelector('#neg-text')"), 10, "文面の入力欄")
        time.sleep(0.8)
        _shot(page, "own_draft_top_390.jpg")
        page.js("""(function(){const t = document.querySelector('#neg-text'); let e = t;
          while (e) { if (e.scrollHeight > e.clientHeight + 2 && /auto|scroll/.test(getComputedStyle(e).overflowY)) e.scrollTop = e.scrollHeight; e = e.parentElement; }
          window.scrollTo(0, document.documentElement.scrollHeight);})()""")
        time.sleep(0.5)
        _shot(page, "own_draft_390.jpg")
        r = page.js("""(function(){
          const t = document.querySelector('#neg-text').getBoundingClientRect(), b = document.querySelector('.neg-guided .neg-btns').getBoundingClientRect();
          const y = Math.min(innerHeight - 1, Math.floor(t.bottom) - 2), x = Math.floor(t.left + t.width / 2);
          const hit = document.elementFromPoint(x, y);
          return {textBottom: t.bottom, bandTop: b.top, hit: hit ? hit.id || hit.className || hit.tagName : null, h: getComputedStyle(document.querySelector('#neg-root')).getPropertyValue('--neg-btns-h'), bandH: b.height};
        })()""")
        assert r["hit"] == "neg-text", f"入力欄の下端が帯に覆われる: {r}"
        assert r["textBottom"] <= r["bandTop"] + 1, r
