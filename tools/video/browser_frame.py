"""アプリ画面を、落ち着いた背景の上の縮小したブラウザ窓に入れて書き出す。

使い方（OpenCV が要るので uv で補う）:
  uv run --no-project --with opencv-python-headless --with pillow --with numpy python tools/video/browser_frame.py \
    --in outputs/video/rec6/focus/P4h.mp4 --side right --out outputs/video/bframe/x.mp4 [--scale 2] \
    [--url backfull] [--bg cozy1|cozy2] [--intro 0.6] [--in-start 0] [--dur 10] [--alpha]
--alpha を付けると、背景なしの窓だけを qtrle の .mov（アルファ付き）にも書き出す（--out の拡張子を .mov に替えた名前）。
窓の幅は画面の 60%、反対側の約 35% は字幕・図のために空ける。寸法は 1080p 基準で、--scale 2 で 4K。
"""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FF = "C:/ai/bin/ffmpeg.exe"
FONT = "C:/Windows/Fonts/NotoSansJP-VF.ttf"
FPS = 30
SS = 4  # 角丸・丸ドットの描画用の超解像倍率


def ease_out_expo(t):
    t = min(max(t, 0.0), 1.0)
    return 1.0 if t >= 1 else (1 - 2 ** (-10 * t)) / (1 - 2 ** -10)


def probe(path):
    r = subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    m = re.search(r"Video:.*?(\d{3,5})x(\d{3,5})", r.stderr)
    return dur, int(m[1]), int(m[2])


def hexrgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def make_bg(W, H, variant):
    """背景（float32, 0-255）。斜めのグラデーション + 角の暖かい光 + 周辺減光。"""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    u, v = xx / W, yy / H
    if variant == "cozy2":
        c0, c1 = hexrgb("#101722"), hexrgb("#221b1e")
        bloom_c, bloom_col, bloom_a, rad = (0.9, 0.04), hexrgb("#f0b060"), 0.08, 0.9
        t = 0.35 * u + 0.65 * v
    else:
        c0, c1 = hexrgb("#0d1420"), hexrgb("#1b1a22")
        bloom_c, bloom_col, bloom_a, rad = (0.06, 0.04), hexrgb("#f0c04a"), 0.06, 0.9
        t = 0.7 * u + 0.3 * v
    img = c0[None, None, :] * (1 - t[..., None]) + c1[None, None, :] * t[..., None]
    d = np.sqrt(((u - bloom_c[0]) * W / H) ** 2 + (v - bloom_c[1]) ** 2) / rad
    b = np.clip(1 - d, 0, 1) ** 2 * bloom_a
    img = img * (1 - b[..., None]) + bloom_col[None, None, :] * b[..., None]
    dc = np.sqrt(((u - 0.5) * W / H) ** 2 + (v - 0.55) ** 2)
    vig = 1 - 0.38 * np.clip((dc - 0.45) / 0.75, 0, 1) ** 1.6
    return (img * vig[..., None]).astype(np.float32)


def rounded_mask(w, h, r):
    m = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], r * SS, fill=255)
    return np.asarray(m.resize((w, h), Image.LANCZOS), np.float32) / 255.0


def build_chrome(ww, wh, tb, k, url):
    """窓の見た目（タイトルバー + 本体色）、角丸マスク、内側 1px の枠線。"""
    body = hexrgb("#16181d")
    bar = hexrgb("#24262c")
    img = Image.new("RGB", (ww * SS, wh * SS), tuple(int(x) for x in body))
    dr = ImageDraw.Draw(img)
    dr.rectangle([0, 0, ww * SS, tb * SS], fill=tuple(int(x) for x in bar))
    dr.line([0, tb * SS, ww * SS, tb * SS], fill=(0x10, 0x11, 0x14), width=SS)
    cy = tb / 2
    rad = 6.5 * k
    for i, col in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        cx = (20 + i * 20) * k
        dr.ellipse([(cx - rad) * SS, (cy - rad) * SS, (cx + rad) * SS, (cy + rad) * SS], fill=col)
    pw, ph = ww * 0.40, 26 * k
    px0, py0 = (ww - pw) / 2, cy - ph / 2
    dr.rounded_rectangle([px0 * SS, py0 * SS, (px0 + pw) * SS, (py0 + ph) * SS], ph / 2 * SS, fill="#16181d")
    f = ImageFont.truetype(FONT, int(13 * k * SS))
    dr.text((ww / 2 * SS, cy * SS), url, font=f, fill="#9aa3ad", anchor="mm")
    chrome = np.asarray(img.resize((ww, wh), Image.LANCZOS), np.float32)
    mask = rounded_mask(ww, wh, int(14 * k))
    inner = rounded_mask(ww - 2 * k, wh - 2 * k, max(1, int(14 * k) - k))
    pad = np.zeros_like(mask)
    pad[k:wh - k, k:ww - k] = inner
    ring = np.clip(mask - pad, 0, 1) * 0.08
    return chrome, mask, ring


def make_shadow(W, H, wx, wy, ww, wh, mask, k):
    """窓の影（alpha 0.45, blur 60, y+24）。"""
    s = np.zeros((H, W), np.float32)
    y0 = wy + 24 * k
    ys, xs = max(y0, 0), max(wx, 0)
    ye, xe = min(y0 + wh, H), min(wx + ww, W)
    s[ys:ye, xs:xe] = mask[ys - y0:ye - y0, xs - wx:xe - wx]
    s = cv2.GaussianBlur(s, (0, 0), 30 * k)  # CSS の blur 60 は sigma 30 相当
    return s * 0.45


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--in-start", type=float, default=0.0)
    ap.add_argument("--dur", type=float, default=None)
    ap.add_argument("--side", choices=["right", "left"], default="right")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=int, default=1, choices=[1, 2])
    ap.add_argument("--url", default="backfull")
    ap.add_argument("--bg", choices=["cozy1", "cozy2"], default="cozy1")
    ap.add_argument("--intro", type=float, default=0.6)
    ap.add_argument("--alpha", action="store_true", help="背景なしの窓だけの .mov も書き出す")
    a = ap.parse_args()

    k = a.scale
    W, H = 1920 * k, 1080 * k
    sdur, sw, sh = probe(a.inp)
    dur = a.dur if a.dur else max(0.5, sdur - a.in_start)
    n = int(round(dur * FPS))

    ww = int(W * 0.60) // 2 * 2
    tb = 44 * k
    asp = min(max(sw / sh, 16 / 10), 16 / 9)  # 内容欄の縦横比（16:10 から 16:9）
    ch_ = int(ww / asp) // 2 * 2
    wh = tb + ch_
    margin = 110 * k
    wx = W - margin - ww if a.side == "right" else margin
    avail = H - 120 * k  # 下の 120px は空ける
    wy = int(avail / 2 - wh / 2 - 10 * k)
    fit = min(ww / sw, ch_ / sh)
    cw, chh = int(sw * fit) // 2 * 2, int(sh * fit) // 2 * 2
    ox, oy = (ww - cw) // 2, tb + (ch_ - chh) // 2

    chrome, mask, ring = build_chrome(ww, wh, tb, k, a.url)
    shadow = make_shadow(W, H, wx, wy, ww, wh, mask, k)
    alpha_full = shadow.copy()
    alpha_full[wy:wy + wh, wx:wx + ww] = mask + shadow[wy:wy + wh, wx:wx + ww] * (1 - mask)
    body = hexrgb("#16181d")

    bg = make_bg(W, H, a.bg)
    rng = np.random.default_rng(7)
    grain_tiles = [rng.normal(0, 1.4, (H, W)).astype(np.float32) for _ in range(4)]

    dec = subprocess.Popen(
        [FF, "-v", "error", "-ss", str(a.in_start), "-i", a.inp, "-t", str(dur + 0.2),
         "-vf", f"fps={FPS},scale={cw}:{chh}:flags=lanczos", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE)
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    enc = subprocess.Popen(
        [FF, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-pix_fmt", "yuv420p",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-movflags", "+faststart", str(outp)],
        stdin=subprocess.PIPE)
    enc_a = None
    if a.alpha:
        enc_a = subprocess.Popen(
            [FF, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
             "-c:v", "qtrle", "-pix_fmt", "argb", str(outp.with_suffix(".mov"))], stdin=subprocess.PIPE)

    fsz = cw * chh * 3
    last = None
    t0 = time.time()
    cx_, cy_ = wx + ww / 2, wy + wh / 2
    for i in range(n):
        raw = dec.stdout.read(fsz)
        if len(raw) == fsz:
            last = np.frombuffer(raw, np.uint8).reshape(chh, cw, 3)
        win = np.empty((wh, ww, 3), np.float32)
        win[:] = body
        win[:tb] = chrome[:tb]
        if last is not None:
            win[oy:oy + chh, ox:ox + cw] = last
        win = win + (255 - win) * ring[..., None]
        layer = np.zeros((H, W, 4), np.float32)
        layer[wy:wy + wh, wx:wx + ww, :3] = win * mask[..., None]  # 事前乗算済み（影は黒）
        layer[..., 3] = alpha_full * 255.0

        t = i / FPS
        e = ease_out_expo(t / a.intro) if a.intro > 0 else 1.0
        s = (0.97 + 0.03 * e) * (1 + 0.002 * t)  # 登場の拡大 + 10 秒で 1.02 倍の寄り
        dy = (1 - e) * 30 * k
        op = min(1.0, e * 1.4)
        M = np.array([[s, 0, cx_ - s * cx_], [0, s, cy_ - s * cy_ + dy]], np.float32)
        layer = cv2.warpAffine(layer, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        layer = np.clip(layer, 0, 255)
        la = layer[..., 3:4] / 255.0 * op

        if enc_a:
            rgb = np.where(la > 1e-4, layer[..., :3] * op / np.maximum(la, 1e-4), 0)
            rgba = np.concatenate([np.clip(rgb, 0, 255), la * 255], -1).astype(np.uint8)
            enc_a.stdin.write(rgba.tobytes())

        g = np.roll(grain_tiles[i % 4], (i * 37) % W, axis=1)
        out = layer[..., :3] * op + (bg + g[..., None]) * (1 - la)
        enc.stdin.write(np.clip(out + 0.5, 0, 255).astype(np.uint8).tobytes())

    for p in (enc, enc_a):
        if p:
            p.stdin.close()
            p.wait()
    dec.stdout.close()
    dec.wait()
    print(f"{outp} {W}x{H} {n}フレーム {time.time() - t0:.1f}秒")


if __name__ == "__main__":
    sys.exit(main())
