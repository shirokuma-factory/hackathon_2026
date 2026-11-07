"""静止画（2560 PNG）から webp 1920/1600/800 を作る。品質 88。使い方: python webp_v3.py <out dir>"""
import os
import sys
from PIL import Image

out = os.path.abspath(sys.argv[1])
dst = os.path.join(out, "webp")
os.makedirs(dst, exist_ok=True)
for v in ("golden", "morning", "dusk"):
    im = Image.open(os.path.join(out, "final", "still_%s_2560.png" % v)).convert("RGB")
    for w in (1920, 1600, 800):
        h = round(w * 9 / 16)
        p = os.path.join(dst, "hero_%s_%d.webp" % (v, w))
        im.resize((w, h), Image.LANCZOS).save(p, "WEBP", quality=88, method=6)
        print(p, os.path.getsize(p))
