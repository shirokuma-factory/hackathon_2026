"""ローカルの単独ページ（web/waiting.html・web/compare_lite.html）から、claude.ai の Artifact に載せる版を作る（SPEC §26）。

Artifact の置き場の決まり: doctype・html・head・body の各タグを書かない。先頭は <title>、次に viewport-fit の meta、<style>、本文と <script>。
印刷の CSS（PRINT-START の印から </style> まで）と、印刷・ファイル保存の JS（LOCAL-ONLY の印の間）は落とし、ARTIFACT の旗を true にする。
"""

from __future__ import annotations

import re

VIEWPORT_META = '<meta name="claude:viewport-fit-sides" content="cover">'


def _cut(text: str, start: str, end: str) -> str:
    """start と end の印の間（印を含む）を全部取り除く。"""
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end) + r"\n?", re.S)
    return pat.sub("", text)


def local_flag(html: str) -> str:
    """ローカルの版: 旗の印を false にする。"""
    if html.count("/*__ARTIFACT__*/false") != 1:
        raise SystemExit("テンプレートに /*__ARTIFACT__*/false が 1 つありません")
    return html.replace("/*__ARTIFACT__*/false", "false")


def to_artifact(local_html: str, title: str) -> str:
    """ローカルの版（旗が false のもの）か、テンプレートを埋めたものから Artifact の版を作る。"""
    html = local_html
    html = html.replace("const ARTIFACT = false;", "const ARTIFACT = true;", 1)
    if "const ARTIFACT = true;" not in html:
        html = html.replace("/*__ARTIFACT__*/false", "true", 1)
    html = re.sub(r"/\*PRINT-START\*/.*?(?=</style>)", "", html, flags=re.S)
    html = _cut(html, "/*LOCAL-ONLY-START*/", "/*LOCAL-ONLY-END*/")
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    b = re.search(r"<body>\n?(.*)</body>", html, re.S)
    if not m or not b:
        raise SystemExit("style か body が見つかりません")
    style = m.group(1).strip("\n")
    body = b.group(1).rstrip()
    out = f"<title>{title}</title>\n{VIEWPORT_META}\n<style>\n{style}\n</style>\n{body}\n"
    if "window.print" in out or "download" in out.lower() or re.search(r"<(!doctype|html|head|body)[\s>]", out, re.I):
        raise SystemExit("Artifact の版に、使えないタグか印刷・保存の処理が残っています")
    return out


def wrap_for_test(artifact_html: str, theme: str | None = None) -> str:
    """試験と撮影のためだけに、最小の doctype の殻に包む（Artifact の版そのものには付けない）。"""
    attr = f' data-theme="{theme}"' if theme else ""
    return f'<!doctype html>\n<html lang="ja"{attr}>\n<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>\n<body>\n{artifact_html}</body>\n</html>\n'
