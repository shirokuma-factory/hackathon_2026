"""docs/video/motion/<名前>.html を、背景が透明な 1920x1080・30fps の動画（qtrle の .mov）にする。

使い方:
    uv run python tools/video/render_motion.py MG1 MG2 MG3 MG4 MG5 MG6
    uv run python tools/video/render_motion.py            # docs/video/motion/MG*.html のすべて

出力: outputs/video/motion/<名前>.mov と、確認用の <名前>_sheet.jpg（暗い背景に重ねた 12 コマの一覧）。
コマは実時間で撮らない。HTML に window.__seek(ms) があればそれを 1 コマずつ呼び、
なければ document.getAnimations() を止めて currentTime を進める。
長さは <body data-duration="秒"> で決める。Edge は tools/capture_web.py の edge_page で起動する。
"""

from __future__ import annotations

import base64
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import capture_web as cw  # noqa: E402

SRC = ROOT / "docs" / "video" / "motion"
OUT = ROOT / "outputs" / "video" / "motion"
W, H, FPS = 1920, 1080, 30
SUBTITLE_TOP = H - 300  # この線より下は字幕の場所。何も描かない
SHEET_FRAMES, SHEET_COLS, TILE_W = 12, 3, 640
SHEET_BG = (28, 39, 51)


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for cand in ("C:/ai/bin/ffmpeg.exe",):
        if Path(cand).exists():
            return cand
    raise SystemExit("ffmpeg が見つかりません。")


def capture_frames(page, name: str, frames_dir: Path) -> tuple[int, int]:
    """全コマを PNG で frames_dir に書く。(コマ数, 下 300px に描かれたコマ数) を返す。"""
    from PIL import Image

    page.goto((SRC / f"{name}.html").as_uri())
    page.send("Emulation.setDefaultBackgroundColorOverride", color={"r": 0, "g": 0, "b": 0, "a": 0})
    page.js("document.fonts.ready.then(()=>1)")
    duration = float(page.js("parseFloat(document.body.dataset.duration)"))
    n = round(duration * FPS)
    has_seek = bool(page.js("typeof window.__seek === 'function'"))
    if not has_seek:
        page.js("document.getAnimations().forEach(a=>a.pause())")
    dirty = 0
    for i in range(n):
        ms = i * 1000 / FPS
        if has_seek:
            page.js(f"window.__seek({ms!r})")
        else:
            page.js(f"document.getAnimations().forEach(a=>{{a.pause();a.currentTime={ms!r};}})")
        # 描画が済むのを 2 回の描画で待つ
        page.js("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(1))))")
        r = page.send("Page.captureScreenshot", format="png", fromSurface=True, captureBeyondViewport=False,
                      clip={"x": 0, "y": 0, "width": W, "height": H, "scale": 1})
        raw = base64.b64decode(r["data"])
        (frames_dir / f"{i + 1:04d}.png").write_bytes(raw)
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        if img.size != (W, H):
            raise RuntimeError(f"{name}: コマの大きさが {img.size} です")
        # 下 300px に不透明な画素があるか（アルファの最大値）
        if img.crop((0, SUBTITLE_TOP, W, H)).getchannel("A").getextrema()[1] > 0:
            dirty += 1
    return n, dirty


def encode(ffmpeg: str, frames_dir: Path, out: Path) -> None:
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-framerate", str(FPS), "-i", str(frames_dir / "%04d.png"),
         "-c:v", "qtrle", "-pix_fmt", "argb", "-r", str(FPS), str(out)],
        check=True)


def contact_sheet(frames_dir: Path, n: int, out: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("NotoSansJP-VF.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    tile_h = TILE_W * H // W
    rows = -(-SHEET_FRAMES // SHEET_COLS)
    pad = 6
    sheet = Image.new("RGB", (SHEET_COLS * TILE_W + (SHEET_COLS + 1) * pad, rows * tile_h + (rows + 1) * pad), (0, 0, 0))
    idx = [round(k * (n - 1) / (SHEET_FRAMES - 1)) for k in range(SHEET_FRAMES)]
    for k, i in enumerate(idx):
        fr = Image.open(frames_dir / f"{i + 1:04d}.png").convert("RGBA")
        base = Image.new("RGBA", fr.size, SHEET_BG + (255,))
        base.alpha_composite(fr)
        tile = base.convert("RGB").resize((TILE_W, tile_h), Image.LANCZOS)
        d = ImageDraw.Draw(tile)
        y = SUBTITLE_TOP * tile_h // H
        d.line([(0, y), (TILE_W, y)], fill=(120, 60, 60), width=1)  # 字幕の場所の上端
        d.rectangle([0, 0, 150, 30], fill=(0, 0, 0))
        d.text((6, 3), f"{i / FPS:5.2f}s #{i}", fill=(255, 255, 255), font=font)
        sheet.paste(tile, (pad + (k % SHEET_COLS) * (TILE_W + pad), pad + (k // SHEET_COLS) * (tile_h + pad)))
    sheet.save(out, "JPEG", quality=88)


def main() -> int:
    names = sys.argv[1:] or sorted(p.stem for p in SRC.glob("MG*.html"))
    ffmpeg = find_ffmpeg()
    OUT.mkdir(parents=True, exist_ok=True)
    with cw.edge_page(W, H) as page:
        for name in names:
            frames_dir = Path(tempfile.mkdtemp(prefix=f"motion_{name}_"))
            try:
                n, dirty = capture_frames(page, name, frames_dir)
                mov = OUT / f"{name}.mov"
                encode(ffmpeg, frames_dir, mov)
                contact_sheet(frames_dir, n, OUT / f"{name}_sheet.jpg")
            finally:
                shutil.rmtree(frames_dir, ignore_errors=True)
            print(f"保存: {mov}  {n} コマ ({n / FPS:.2f} 秒)  下300pxに描いたコマ={dirty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
