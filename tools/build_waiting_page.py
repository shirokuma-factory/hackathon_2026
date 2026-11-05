"""web/waiting.html を tools/waiting_page_template.html から作る（SPEC §23.7）。

標準的運賃の表（data/tariffs/mlit_standard_fare_waiting.json）と試験表（tests/data/waiting_vectors.json）をページの中に埋め込む。
ページは通信をしないので、この 2 つはビルドのときにだけ読む。使い方: uv run python tools/build_waiting_page.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "tools" / "waiting_page_template.html"
TARIFF = ROOT / "data" / "tariffs" / "mlit_standard_fare_waiting.json"
VECTORS = ROOT / "tests" / "data" / "waiting_vectors.json"
OUT = ROOT / "web" / "waiting.html"


def _js(obj: object) -> str:
    # </script> が JSON の中に出ても閉じないようにする
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def build() -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    tariff = json.loads(TARIFF.read_text(encoding="utf-8"))
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    for key, val in (("/*__TARIFF__*/null", _js(tariff)), ("/*__VECTORS__*/null", _js(vectors))):
        if html.count(key) != 1:
            raise SystemExit(f"テンプレートに {key} が 1 つありません")
        html = html.replace(key, val)
    return html


def main() -> int:
    html = build()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print(f"保存: {OUT}  {OUT.stat().st_size} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
