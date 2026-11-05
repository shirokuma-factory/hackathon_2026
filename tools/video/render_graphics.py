"""docs/video/graphics/*.html を 1920x1080 の PNG にして outputs/video/graphics/ に書く。

使い方:
    uv run python tools/video/render_graphics.py            # すべて
    uv run python tools/video/render_graphics.py G2 G4      # 指定したものだけ

Edge（ヘッドレス）を tools/capture_web.py の edge_page で起動する。
回数は docs/video/graphics/counts.json の display を、data-count="キー" の要素に入れる。
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import capture_web as cw  # noqa: E402

SRC = ROOT / "docs" / "video" / "graphics"
OUT = ROOT / "outputs" / "video" / "graphics"
W, H = 1920, 1080


def main() -> int:
    from PIL import Image

    names = sys.argv[1:] or sorted(p.stem for p in SRC.glob("G*.html"))
    counts = json.loads((SRC / "counts.json").read_text(encoding="utf-8"))
    display = {k: v["display"] for k, v in counts.items() if isinstance(v, dict)}
    OUT.mkdir(parents=True, exist_ok=True)
    with cw.edge_page(W, H) as page:
        for name in names:
            page.goto((SRC / f"{name}.html").as_uri())
            page.js(
                "document.fonts.ready.then(()=>1)"
            )
            page.js(
                "(function(d){document.querySelectorAll('[data-count]').forEach(e=>{const v=d[e.dataset.count]; if(v!==undefined) e.textContent=v;});})("
                + json.dumps(display, ensure_ascii=False) + ")"
            )
            over = page.js("[document.documentElement.scrollWidth, document.documentElement.scrollHeight, document.body.scrollHeight]")
            r = page.send("Page.captureScreenshot", format="png", fromSurface=True,
                          clip={"x": 0, "y": 0, "width": W, "height": H, "scale": 1})
            img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
            path = OUT / f"{name}.png"
            img.save(path, "PNG")
            print(f"保存: {path}  {img.size[0]}x{img.size[1]}  scroll={over}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
