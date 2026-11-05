"""ページを A4 の PDF に印刷し、各ページを PNG にする（SPEC §24 G85）。

ヘッドレスの Edge の --print-to-pdf を使う。PNG と文字数の読み取りには pymupdf を使う（uv の環境には入れていないので、
無ければシステムの python を呼ぶ）。使い方:
  uv run python tools/print_page_pdf.py web/waiting.html?example=1 docs/assets/2026-11-05_v24_print_p
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_screens as cs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

_READER = r"""
import json, sys
try:
    import pymupdf as fz
except ImportError:
    import fitz as fz
doc = fz.open(sys.argv[1])
out = sys.argv[2] if len(sys.argv) > 2 else ""
info = []
for i, pg in enumerate(doc):
    info.append({"page": i + 1, "chars": len(pg.get_text().strip()), "w_pt": round(pg.rect.width, 1), "h_pt": round(pg.rect.height, 1)})
    if out:
        pg.get_pixmap(dpi=100).save(f"{out}{i + 1}.png")
print(json.dumps(info))
"""


def print_to_pdf(url: str, pdf: Path) -> None:
    edge = cs.find_edge()
    pdf.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as prof:
        subprocess.run([edge, "--headless=new", "--disable-gpu", f"--user-data-dir={prof}", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", url], check=True, timeout=90, capture_output=True)
    for _ in range(40):  # Edge は終了してからファイルを書き終えることがある
        if pdf.exists() and pdf.stat().st_size > 0:
            break
        time.sleep(0.25)
    if not pdf.exists() or pdf.stat().st_size == 0:
        raise RuntimeError("PDF ができませんでした")


def pdf_page_count(pdf: Path) -> int:
    """pymupdf が無くても数えられる（/Type /Page の個数）。"""
    return len(re.findall(rb"/Type\s*/Page(?![a-z])", pdf.read_bytes()))


def pdf_info(pdf: Path, png_prefix: str = "") -> list[dict] | None:
    """各ページの文字数と用紙の大きさ。pymupdf が使えなければ None。"""
    try:
        import pymupdf as fz  # type: ignore
    except ImportError:
        fz = None
    if fz is not None:
        doc = fz.open(str(pdf))
        res = []
        for i, pg in enumerate(doc):
            res.append({"page": i + 1, "chars": len(pg.get_text().strip()), "w_pt": round(pg.rect.width, 1), "h_pt": round(pg.rect.height, 1)})
            if png_prefix:
                pg.get_pixmap(dpi=100).save(f"{png_prefix}{i + 1}.png")
        return res
    base = Path(sys.base_prefix) / ("python.exe" if sys.platform == "win32" else "bin/python3")
    py = str(base) if base.exists() else shutil.which("python3")
    if not py:
        return None
    r = subprocess.run([py, "-c", _READER, str(pdf), png_prefix], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout.strip().splitlines()[-1])


def main() -> int:
    src, prefix = sys.argv[1], sys.argv[2]
    path, _, query = src.partition("?")
    url = (ROOT / path).resolve().as_uri() + (f"?{query}" if query else "")
    pdf = Path(tempfile.gettempdir()) / "print_page.pdf"
    print_to_pdf(url, pdf)
    Path(prefix).parent.mkdir(parents=True, exist_ok=True)
    info = pdf_info(pdf, str(ROOT / prefix))
    print(pdf_page_count(pdf), info)
    return 0


if __name__ == "__main__":
    sys.exit(main())
