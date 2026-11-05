"""web/waiting.html（SPEC §29、G127）を例の入った状態で撮る。1920x1080 と、400px 幅（ページの高さの全体、上限 4200px）。

使い方: uv run python tools/capture_waiting_v29.py
保存先: docs/assets/2026-11-05_v29_waiting_1920.png と 2026-11-05_v29_waiting_400.png
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import capture_web as cw  # noqa: E402

OUT = ROOT / "docs" / "assets"


def shot(page, dest: Path, w: int, h: int) -> None:
    r = page.send("Page.captureScreenshot", format="png", fromSurface=True, clip={"x": 0, "y": 0, "width": w, "height": h, "scale": 1})
    dest.write_bytes(base64.b64decode(r["data"]))
    print(f"保存: {dest}  {w}x{h}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    url = (ROOT / "web" / "waiting.html").as_uri()
    with cw.edge_page(1920, 1080) as page:
        # 1920x1080: 先頭の位置のまま撮る（表と書面が入る）
        page.send("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
        page.goto(url)
        page.wait(lambda: page.js("!!document.querySelector('#hotspots')"), 30, "例")
        shot(page, OUT / "2026-11-05_v29_waiting_1920.png", 1920, 1080)
        page.send("Emulation.setDeviceMetricsOverride", width=400, height=900, deviceScaleFactor=1, mobile=False)
        page.goto(url)
        page.wait(lambda: page.js("!!document.querySelector('#hotspots')"), 30, "例")
        h = min(int(page.js("document.documentElement.scrollHeight")), 4200)
        page.send("Emulation.setDeviceMetricsOverride", width=400, height=h, deviceScaleFactor=1, mobile=False)
        print("横スクロール:", page.js("document.documentElement.scrollWidth > document.documentElement.clientWidth"))
        shot(page, OUT / "2026-11-05_v29_waiting_400.png", 400, h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
