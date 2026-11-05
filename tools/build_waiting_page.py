"""web/waiting.html を tools/waiting_page_template.html から作る（SPEC §23.7）。

標準的運賃の表（data/tariffs/mlit_standard_fare_waiting.json）と試験表（tests/data/waiting_vectors.json）、地図の画面と共有する計算（web/waiting_calc.js）をページの中に埋め込む。
ページは通信をしないので、この 2 つはビルドのときにだけ読む。使い方: uv run python tools/build_waiting_page.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_build  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "tools" / "waiting_page_template.html"
TARIFF = ROOT / "data" / "tariffs" / "mlit_standard_fare_waiting.json"
VECTORS = ROOT / "tests" / "data" / "waiting_vectors.json"
SHARED = ROOT / "web" / "waiting_calc.js"   # 地図の画面（web/own.js）と共有する計算と文言（SPEC §30.4）
OUT_ARTIFACT = ROOT / "web" / "publish" / "waiting_artifact.html"
TITLE = "荷待ち協議の書面"
OUT = ROOT / "web" / "waiting.html"
OUT_TARIFF = ROOT / "web" / "data" / "waiting_tariff.json"   # 地図の画面が通信せずに計算するための告示の表（SPEC §31.1）


def _js(obj: object) -> str:
    # </script> が JSON の中に出ても閉じないようにする
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def public_tariff() -> dict:
    """公開するページに埋める告示の表。照合の記録からリポジトリの中のファイル名を外す（SPEC §31.5 #17）。"""
    tariff = json.loads(TARIFF.read_text(encoding="utf-8"))
    if tariff.get("verified_by"):
        tariff["verified_by"] = re.sub(r"（docs/[^、）]*、", "（", tariff["verified_by"])
    return tariff


def _filled() -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    tariff = public_tariff()
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    shared = SHARED.read_text(encoding="utf-8").strip("\n").replace("</", "<\\/")
    for key, val in (("/*__WAITING_CALC__*/", shared), ("/*__TARIFF__*/null", _js(tariff)), ("/*__VECTORS__*/null", _js(vectors))):
        if html.count(key) != 1:
            raise SystemExit(f"テンプレートに {key} が 1 つありません")
        html = html.replace(key, val)
    return html


def build() -> str:
    return artifact_build.local_flag(_filled())


def build_artifact() -> str:
    return artifact_build.to_artifact(_filled(), TITLE)


def main() -> int:
    OUT_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    for path, html in ((OUT, build()), (OUT_ARTIFACT, build_artifact())):
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        print(f"保存: {path}  {path.stat().st_size} B")
    OUT_TARIFF.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_TARIFF, "w", encoding="utf-8", newline="\n") as f:
        f.write(_js(public_tariff()) + "\n")
    print(f"保存: {OUT_TARIFF}  {OUT_TARIFF.stat().st_size} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
