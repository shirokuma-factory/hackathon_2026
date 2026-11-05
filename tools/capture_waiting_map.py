"""地図の画面の「自社の運行」→「荷待ちを入れる」を、例を入れた状態で撮る（SPEC §30.4、G132）。1920x1080 と 400x800。

使い方: uv run python tools/capture_waiting_map.py
保存先: docs/assets/2026-11-05_v32_waiting_1920.jpg と 2026-11-05_v32_waiting_400.jpg（400 は上の地図と、下へ送った表の 2 枚）
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import capture_screens as cs  # noqa: E402
import capture_web as cw  # noqa: E402

OUT = ROOT / "docs" / "assets"


def shot(page, dest: Path, w: int, h: int) -> None:
    from PIL import Image
    r = page.send("Page.captureScreenshot", format="png", fromSurface=True, clip={"x": 0, "y": 0, "width": w, "height": h, "scale": 1})
    img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=88)
    print(f"保存: {dest}  {img.size[0]}x{img.size[1]}")


def open_wait(page, base: str, w: int, h: int) -> None:
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
    page.goto("about:blank")
    page.goto(base + "#v=own&ot=wait")
    page.wait(lambda: page.js("!!window.__app"), 180, "アプリの起動")
    page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み画面の終了")
    page.wait(lambda: page.js("!!document.querySelector('[data-w=example]')"), 60, "例を入れるボタン")
    time.sleep(1.0)
    page.js("document.querySelector('[data-w=example]').click()")
    page.wait(lambda: page.js("!!document.querySelector('#hotspots') && document.querySelectorAll('.wm-pt').length > 0"), 60, "表と点")
    time.sleep(3.0)  # 地図の移動が終わるまで


def session(base_port: int, w: int, h: int):
    """画面の大きさごとに、新しい Edge を起動して Page を返す（同じ Edge で大きさを変えると、狭い画面で起動が終わらないことがある）。"""
    edge = cs.find_edge()
    profile = tempfile.mkdtemp(prefix="edge_profile_")
    dbg = cs.free_port()
    proc = subprocess.Popen(
        [edge, "--headless=new", f"--remote-debugging-port={dbg}", f"--window-size={w},{h}", f"--user-data-dir={profile}", "--hide-scrollbars",
         "--force-device-scale-factor=1", "--no-first-run", "--enable-webgl", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader", *cs.EDGE_CLEAN_FLAGS, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    targets = []
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{dbg}/json/list", timeout=2) as r:
                targets = cs.blank_targets(json.load(r))
            if targets:
                break
        except Exception:
            pass
        time.sleep(0.5)
    cs.W, cs.H = w, h
    page = cs.Page(targets[0]["webSocketDebuggerUrl"])
    page.send("Page.addScriptToEvaluateOnNewDocument", source=cw.ERR_HOOK)
    return page, proc, profile


def close(proc, profile) -> None:
    cs.stop_tree(proc)
    cs.stop_edge_profile(profile)
    shutil.rmtree(profile, ignore_errors=True)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    port = cs.free_port()
    server = cw.serve(port)
    time.sleep(1.5)
    base = f"http://localhost:{port}/index.html"
    rc = 0
    try:
        page, proc, profile = session(port, 1920, 1080)
        try:
            open_wait(page, base, 1920, 1080)
            info = page.js("""JSON.stringify({pts: document.querySelectorAll('.wm-pt').length,
              unplaced: !!document.querySelector('#wm-unplaced'), legend: !!document.querySelector('.wm-lg'), errs: window.__errs})""")
            print("1920:", info)
            shot(page, OUT / "2026-11-05_v32_waiting_1920.jpg", 1920, 1080)
        finally:
            close(proc, profile)
        page, proc, profile = session(port, 400, 800)
        try:
            open_wait(page, base, 400, 800)
            print("400 横スクロール:", page.js("document.documentElement.scrollWidth > document.documentElement.clientWidth"), "エラー:", page.js("window.__errs"))
            shot(page, OUT / "2026-11-05_v32_waiting_400.jpg", 400, 800)
            page.js("document.querySelector('#hotspots').scrollIntoView({block:'start'})")
            time.sleep(0.6)
            shot(page, OUT / "2026-11-05_v32_waiting_400_table.jpg", 400, 800)
        finally:
            close(proc, profile)
    except Exception as e:  # noqa: BLE001
        print("失敗:", e)
        rc = 1
    finally:
        cs.stop_tree(server)
    return rc


if __name__ == "__main__":
    sys.exit(main())
