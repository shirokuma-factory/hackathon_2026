"""ComfyUI で作った質感画像を、地図のパネル用の継ぎ目のないタイルにする。
低周波（大きな明暗むら）を除いてから、半分ずらした画像と縁でなめらかに混ぜる。python make_tiles_v2.py <comfy 出力先>"""
import os
import sys
import numpy as np
from PIL import Image, ImageFilter

C = sys.argv[1]


def lowpass_wrap(a, r):
    h, w, _ = a.shape
    big = np.tile(a, (3, 3, 1)).astype("uint8")
    im = Image.fromarray(big).filter(ImageFilter.GaussianBlur(r))
    return np.asarray(im, dtype=np.float32)[h:2 * h, w:2 * w]


def make(src, dst, size=512):
    im0 = Image.open(os.path.join(C, src)).convert("RGB")
    m = int(im0.width * 0.08)  # 縁の明るさのむらを切り落とす
    a = np.asarray(im0.crop((m, m, im0.width - m, im0.height - m)).resize((size, size), Image.LANCZOS), dtype=np.float32)
    mean = a.mean((0, 1))
    a = a - lowpass_wrap(a, size / 24) + mean  # 大きなむらを除く
    h, w, _ = a.shape
    sh = np.roll(np.roll(a, h // 2, 0), w // 2, 1)
    y = np.abs(np.linspace(-1, 1, h))[:, None]
    x = np.abs(np.linspace(-1, 1, w))[None, :]
    wg = np.clip(1 - np.maximum(y, x) ** 1.5, 0, 1)[..., None]
    out = a * wg + sh * (1 - wg)
    out = np.clip(out, 0, 255).astype("uint8")
    im = Image.fromarray(out)
    im.save(os.path.join(C, dst + ".webp"), quality=88, method=6)
    im.save(os.path.join(C, dst + ".png"))
    t = im.resize((384, 384))
    s = Image.new("RGB", (1152, 1152))
    for i in range(3):
        for j in range(3):
            s.paste(t, (i * 384, j * 384))
    s.save(os.path.join(C, dst + "_tiled3x3.jpg"), quality=82)
    print(dst, os.path.getsize(os.path.join(C, dst + ".webp")))


make("tex_paper_raw.png", "tex_paper_seamless")
make("tex_ground_raw.png", "tex_ground_seamless")
