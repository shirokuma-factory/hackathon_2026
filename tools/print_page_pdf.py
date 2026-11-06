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


def _print_once(edge: str, url: str, pdf: Path, timeout: float) -> None:
    """Edge を 1 回起動して印刷する。時間切れのときは、プロファイルの Edge ごと止めてから例外を上げる。"""
    prof = cs.new_profile("edge_print_")
    proc = subprocess.Popen([edge, "--headless=new", "--disable-gpu", f"--user-data-dir={prof}", "--no-first-run", *cs.EDGE_CLEAN_FLAGS,
                             "--no-pdf-header-footer", f"--print-to-pdf={pdf}", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Edge の印刷が {timeout:.0f} 秒で終わりませんでした") from e
        for _ in range(40):  # Edge は終了してからファイルを書き終えることがある
            if pdf.exists() and pdf.stat().st_size > 0:
                return
            time.sleep(0.25)
        raise RuntimeError("PDF ができませんでした")
    finally:
        try:
            cs.stop_tree(proc)
        finally:
            cs.release_profile(prof)


def print_to_pdf(url: str, pdf: Path, retries: int = 3, timeout: float = 90) -> None:
    """ページを PDF に印刷する。拡張機能と同期を切った Edge で、失敗や時間切れのときは取り直す（retries 回まで）。"""
    edge = cs.find_edge()
    last: Exception | None = None
    for n in range(retries):
        pdf.unlink(missing_ok=True)
        try:
            _print_once(edge, url, pdf, timeout)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"印刷の試行 {n + 1}/{retries} が失敗: {e}", file=sys.stderr)
            time.sleep(2.0)
    raise RuntimeError(f"PDF を {retries} 回試しても作れませんでした: {last}")


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
