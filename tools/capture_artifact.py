"""Artifact の版の画面写真を docs/assets/2026-11-05_v26_*.png に撮る（400px と 1440px、明るい・暗い）。使い方: uv run python tools/capture_artifact.py"""

from __future__ import annotations

import base64
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import artifact_build  # noqa: E402
import capture_web as cw  # noqa: E402

PAGES = {"waiting": ROOT / "web" / "publish" / "waiting_artifact.html", "compare": ROOT / "web" / "publish" / "compare_lite_artifact.html"}
OUT = ROOT / "docs" / "assets"


def shell_uri(path: Path, theme: str) -> str:
    html = artifact_build.wrap_for_test(path.read_text(encoding="utf-8"), theme)
    out = Path(tempfile.mkdtemp(prefix="artifact_shot_")) / f"{path.stem}_{theme}.html"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return out.as_uri() + "?example=1"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with cw.edge_page(1440, 900) as page:
        for name, path in PAGES.items():
            for theme in ("light", "dark"):
                for w, cap in ((400, 2600), (1440, 1500)):
                    page.goto(shell_uri(path, theme))
                    page.send("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
                    page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec, table.res')"), 30, "例")
                    h = min(int(page.js("document.documentElement.scrollHeight")), cap)
                    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
                    r = page.send("Page.captureScreenshot", format="png", fromSurface=True, clip={"x": 0, "y": 0, "width": w, "height": h, "scale": 1})
                    dest = OUT / f"2026-11-05_v26_{name}_{w}_{theme}.png"
                    dest.write_bytes(base64.b64decode(r["data"]))
                    print(f"保存: {dest}  {w}x{h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
