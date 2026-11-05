"""web/compare_lite.html を tools/compare_lite_template.html から作る（SPEC §24）。

貸切の運賃の表（関東・近畿）、試験表（tests/data/compare_lite_vectors.json）、各社の問い合わせ先（data/contacts.json）をページの中に埋め込む。
ページは通信をしないので、この 3 つはビルドのときにだけ読む。使い方: uv run python tools/build_compare_page.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "tools" / "compare_lite_template.html"
KANTO = ROOT / "data" / "tariffs" / "mlit_standard_fare_kanto.json"
KINKI = ROOT / "data" / "tariffs" / "mlit_standard_fare_kinki.json"
CONTACTS = ROOT / "data" / "contacts.json"
VECTORS = ROOT / "tests" / "data" / "compare_lite_vectors.json"
OUT = ROOT / "web" / "compare_lite.html"


def _js(obj: object) -> str:
    # </script> が JSON の中に出ても閉じないようにする
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def build() -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    tariffs = {"関東": json.loads(KANTO.read_text(encoding="utf-8")), "近畿": json.loads(KINKI.read_text(encoding="utf-8"))}
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    # 各社の公式の問い合わせ・申し込みページ（data/contacts.json）。ページは通信をしないので、ビルドのときに埋め込む
    raw = json.loads(CONTACTS.read_text(encoding="utf-8"))
    contacts = {"checked_on": raw["checked_on"], "contacts": {k: [{"name": c["name"], "url": c["url"], "condition": c["condition"]} for c in v] for k, v in raw["contacts"].items()}}
    for key, val in (("/*__TARIFFS__*/null", _js(tariffs)), ("/*__VECTORS__*/null", _js(vectors)), ("/*__CONTACTS__*/null", _js(contacts))):
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
