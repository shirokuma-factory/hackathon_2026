"""tools/capture_web.py の edge_page に、デバイス倍率（DPR）を足した版。capture_web.py 本体は変更しない。

CSS の画面は w x h のまま、Page.captureScreenshot の出力が (w*scale) x (h*scale) になる。
使い方:
    from edge_page_hd import edge_page_hd
    with edge_page_hd(1920, 1080, scale=2) as page: ...
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import capture_web as cw  # noqa: E402


@contextlib.contextmanager
def edge_page_hd(w: int = 1920, h: int = 1080, scale: float = 1):
    with cw.edge_page(w, h) as page:
        if scale != 1:
            page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=scale, mobile=False)
        yield page
