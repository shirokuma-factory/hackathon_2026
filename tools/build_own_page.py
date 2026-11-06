"""web/own_lite.html を tools/own_page_template.html から作る（自社の運行を、サーバーなしで開ける 1 枚のページ）。

地図の画面（web/own.js）と同じ計算（web/own_calc.js）と月の保存・文面（web/own_month.js）を、ビルドのときにそのままページの中へ埋め込む。
ほかの担当が own_calc.js や own_month.js を直したあとは、このスクリプトをもう一度実行すれば、ページに反映される。
市区町村の表（web/data/index.json と muni.json の代表点）と、サンプルの CSV（examples/own/ の 3 つ）もページの中に入れる。ページは通信をしない。
使い方: uv run python tools/build_own_page.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_build  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "tools" / "own_page_template.html"
OWN_CALC = ROOT / "web" / "own_calc.js"
OWN_MONTH = ROOT / "web" / "own_month.js"
MUNI_INDEX = ROOT / "web" / "data" / "index.json"
MUNI_PT = ROOT / "web" / "data" / "muni.json"
EXAMPLES = ROOT / "examples" / "own"
SAMPLE_FILES = ("haisha_2026-10.csv", "digitaco_1234.csv", "digitaco_5678.csv")
OUT = ROOT / "web" / "own_lite.html"
OUT_ARTIFACT = ROOT / "web" / "publish" / "own_lite_artifact.html"
TITLE = "自社の運行を分析する"


def _js(obj: object) -> str:
    # </script> が JSON の中に出ても閉じないようにする
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def munis() -> list[list]:
    """市区町村の表 [コード, 名前, 都道府県, 経度, 緯度]。地図の画面（own.js の munisList）と同じ作り方: 代表点は muni.json の pt、無ければ index.json。"""
    ix = json.loads(MUNI_INDEX.read_text(encoding="utf-8"))
    pt = json.loads(MUNI_PT.read_text(encoding="utf-8")).get("pt") or []
    out = []
    for i, code in enumerate(ix["codes"]):
        p = pt[i] if i < len(pt) and pt[i] else [ix["lon"][i], ix["lat"][i]]
        out.append([code, ix["names"][i], ix["prefs"][i], p[0], p[1]])
    return out


def sample_files() -> list[dict]:
    return [{"name": n, "text": (EXAMPLES / n).read_text(encoding="utf-8")} for n in SAMPLE_FILES]


def _inline_js(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip("\n").replace("</", "<\\/")


def _filled() -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    for key, val in (
        ("/*__OWN_CALC__*/", _inline_js(OWN_CALC)),
        ("/*__OWN_MONTH__*/", _inline_js(OWN_MONTH)),
        ("/*__MUNIS__*/null", _js(munis())),
        ("/*__SAMPLE_FILES__*/null", _js(sample_files())),
    ):
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
