"""web/driver.html を tools/driver_page_template.html から作る（デザインの作り直し 2026-11-07）。

デザインキット（tools/ui_kit.css）を <style> の先頭に埋め込むだけで、データは読まない。使い方: uv run python tools/build_driver_page.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_build  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "tools" / "driver_page_template.html"
OUT = ROOT / "web" / "driver.html"


def build() -> str:
    return artifact_build.inline_kit(TEMPLATE.read_text(encoding="utf-8"))


def main() -> int:
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(build())
    print(f"保存: {OUT}  {OUT.stat().st_size} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
