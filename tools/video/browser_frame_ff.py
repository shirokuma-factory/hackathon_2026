"""browser_frame.py の速い版。背景と窓の枠を 1 枚の絵にしておき、映像の縮小と重ね合わせは ffmpeg で行う。

browser_frame.py は 1 コマごとに Python で 4K の絵を作るので、15 秒で約 6 分かかる。窓は動かさない
（登場の動き・ゆっくりした寄りは付けない。場面の切り替えは組み立て側の線の転換で行う）。

使い方:
  uv run --no-project --with opencv-python-headless --with pillow --with numpy python tools/video/browser_frame_ff.py \
    --in outputs/video/rec7/focus/F_POST_4k.mp4 --out outputs/video/bframe/r8/F_POST_h_4k.mp4 [--side right] [--scale 2] [--bg cozy3]
窓は画面幅の 60%。反対側の約 500px（1080p 基準）は字幕のために空ける。
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_frame as bf  # noqa: E402

FF = bf.FF
FPS = bf.FPS


def make_bg(W, H, variant):
    """cozy3: cozy1 より明るく温かい。左上から暖色の光、周辺は弱めに落とす。"""
    if variant != "cozy3":
        return bf.make_bg(W, H, variant)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    u, v = xx / W, yy / H
    c0, c1 = bf.hexrgb("#1c2230"), bf.hexrgb("#2b2328")
    t = 0.6 * u + 0.4 * v
    img = c0[None, None, :] * (1 - t[..., None]) + c1[None, None, :] * t[..., None]
    for (cx, cy), col, a, rad in (((0.08, 0.10), "#f2b866", 0.16, 1.0), ((0.95, 0.95), "#c98a5a", 0.07, 0.8)):
        d = np.sqrt(((u - cx) * W / H) ** 2 + (v - cy) ** 2) / rad
        b = np.clip(1 - d, 0, 1) ** 2 * a
        img = img * (1 - b[..., None]) + bf.hexrgb(col)[None, None, :] * b[..., None]
    dc = np.sqrt(((u - 0.5) * W / H) ** 2 + (v - 0.55) ** 2)
    vig = 1 - 0.25 * np.clip((dc - 0.5) / 0.75, 0, 1) ** 1.6
    return (img * vig[..., None]).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--side", choices=["right", "left"], default="right")
    ap.add_argument("--scale", type=int, default=1, choices=[1, 2])
    ap.add_argument("--url", default="backfull")
    ap.add_argument("--bg", choices=["cozy1", "cozy2", "cozy3"], default="cozy3")
    a = ap.parse_args()

    k = a.scale
    W, H = 1920 * k, 1080 * k
    sdur, sw, sh = bf.probe(a.inp)
    ww = int(W * 0.60) // 2 * 2
    tb = 44 * k
    asp = min(max(sw / sh, 16 / 10), 16 / 9)
    ch_ = int(ww / asp) // 2 * 2
    wh = tb + ch_
    margin = 110 * k
    wx = W - margin - ww if a.side == "right" else margin
    wy = int((H - 120 * k) / 2 - wh / 2 - 10 * k)
    fit = min(ww / sw, ch_ / sh)
    cw, chh = int(sw * fit) // 2 * 2, int(sh * fit) // 2 * 2
    ox, oy = (ww - cw) // 2, tb + (ch_ - chh) // 2

    chrome, mask, ring = bf.build_chrome(ww, wh, tb, k, a.url)
    shadow = bf.make_shadow(W, H, wx, wy, ww, wh, mask, k)
    bg = make_bg(W, H, a.bg)
    rng = np.random.default_rng(7)
    bg = bg + rng.normal(0, 1.2, (H, W)).astype(np.float32)[..., None]  # 帯状のむらを防ぐ弱い粒子（止まった粒子）
    base = bg * (1 - shadow[..., None])
    win = np.empty((wh, ww, 3), np.float32)
    win[:] = bf.hexrgb("#16181d")
    win[:tb] = chrome[:tb]
    win = win + (255 - win) * ring[..., None]
    reg = base[wy:wy + wh, wx:wx + ww]
    base[wy:wy + wh, wx:wx + ww] = win * mask[..., None] + reg * (1 - mask[..., None])

    tmp = Path(a.out).with_suffix("")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    base_png = tmp.parent / f"_{tmp.name}_base.png"
    mask_png = tmp.parent / f"_{tmp.name}_mask.png"
    Image.fromarray(np.clip(base + 0.5, 0, 255).astype(np.uint8)).save(base_png)
    Image.fromarray((mask[oy:oy + chh, ox:ox + cw] * 255).astype(np.uint8)).save(mask_png)

    fc = (f"[0:v]fps={FPS},scale={cw}:{chh}:flags=lanczos,format=rgba[c];"
          f"[2:v]format=gray,scale={cw}:{chh}[m];[c][m]alphamerge[cm];"
          f"[1:v][cm]overlay={wx + ox}:{wy + oy}:shortest=1,format=yuv420p[v]")
    T = f"{sdur:.3f}"
    cmd = [FF, "-y", "-v", "error", "-i", a.inp, "-loop", "1", "-framerate", str(FPS), "-t", T, "-i", str(base_png),
           "-loop", "1", "-framerate", str(FPS), "-t", T, "-i", str(mask_png), "-filter_complex", fc, "-map", "[v]",
           "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-r", str(FPS),
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-t", T, "-movflags", "+faststart", a.out]
    subprocess.run(cmd, check=True)
    base_png.unlink()
    mask_png.unlink()
    print(f"{a.out} {W}x{H} 窓 x={wx} 幅={ww}")


if __name__ == "__main__":
    sys.exit(main())
