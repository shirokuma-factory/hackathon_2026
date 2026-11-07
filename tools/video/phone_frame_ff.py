"""スマートフォンの録画（縦長）を、窓の場面と同じ落ち着いた背景の左に置く（右の列は字幕のために空ける）。

r14 の採点（docs/reviews/2026-11-07_デモ動画の採点_r14.md）で、ボタンの列に寄った絵の下に字幕が重なり、
声が「出発の時刻を押すだけ」と言う所でそのボタンが見えなかった。画面全体を寄りなしで見せる。
名前に _L を付けると、build_r5.py が字幕を右の列（x=1320）へ移す。

使い方: uv run --no-project --with pillow --with numpy python tools/video/phone_frame_ff.py \
    --in outputs/video/rec7/DRIVER.mp4 --t0 0.2 --t1 15.9 --out outputs/video/bframe/r9/W_DRIVER_L.mp4 [--scale 2]
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_frame as bf  # noqa: E402
import browser_frame_ff as bff  # noqa: E402

FF = bf.FF


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--t0", type=float, default=0.0)
    ap.add_argument("--t1", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=int, default=1, choices=[1, 2])
    ap.add_argument("--speed", type=float, default=1.0, help="録画を速める倍率（押す瞬間を声に合わせる）")
    a = ap.parse_args()
    k = a.scale
    W, H = 1920 * k, 1080 * k
    _, sw, sh = bf.probe(a.inp)
    ph = int(H * 0.86) // 2 * 2
    pw = int(ph * sw / sh) // 2 * 2
    x, y = int(W * 0.30 - pw / 2) // 2 * 2, int((H - 120 * k) / 2 - ph / 2 + 20 * k) // 2 * 2
    rad = 46 * k
    bg = bff.make_bg(W, H, "cozy3")
    rng = np.random.default_rng(7)
    bg = bg + rng.normal(0, 1.2, (H, W)).astype(np.float32)[..., None]
    # 端末の縁（黒い枠）と影
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rounded_rectangle([x - 14 * k, y - 14 * k, x + pw + 14 * k, y + ph + 14 * k], radius=rad + 12 * k, fill=255)
    sh_ = np.asarray(m.resize((W // 8, H // 8)).resize((W, H), Image.BILINEAR), np.float32) / 255 * 0.45
    base = bg * (1 - sh_[..., None])
    body = np.asarray(m, np.float32) / 255
    base = base * (1 - body[..., None]) + np.array([14, 15, 18], np.float32) * body[..., None]
    tmp = Path(a.out).with_suffix("")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    base_png, mask_png = tmp.parent / f"_{tmp.name}_base.png", tmp.parent / f"_{tmp.name}_mask.png"
    Image.fromarray(np.clip(base + 0.5, 0, 255).astype(np.uint8)).save(base_png)
    mk = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mk).rounded_rectangle([0, 0, pw - 1, ph - 1], radius=rad, fill=255)
    mk.save(mask_png)
    T = f"{(a.t1 - a.t0) / a.speed:.3f}"
    fc = (f"[0:v]setpts=PTS/{a.speed},fps=30,scale={pw}:{ph}:flags=lanczos,format=rgba[c];[2:v]format=gray,scale={pw}:{ph}[m];[c][m]alphamerge[cm];"
          f"[1:v][cm]overlay={x}:{y}:shortest=1,format=yuv420p[v]")
    subprocess.run([FF, "-y", "-v", "error", "-ss", f"{a.t0}", "-t", f"{a.t1 - a.t0:.3f}", "-i", a.inp, "-loop", "1", "-framerate", "30", "-t", T, "-i", str(base_png),
                    "-loop", "1", "-framerate", "30", "-t", T, "-i", str(mask_png), "-filter_complex", fc, "-map", "[v]",
                    "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-r", "30", "-colorspace", "bt709", "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-t", T, "-movflags", "+faststart", a.out], check=True)
    base_png.unlink()
    mask_png.unlink()
    print(f"{a.out} {W}x{H} 端末 x={x} 幅={pw} 高さ={ph}")


if __name__ == "__main__":
    main()
