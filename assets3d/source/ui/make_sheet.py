"""PNG から WebP（512/256/128）とコンタクトシートを作る。--publish で web/img/ui/ にもコピー。"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT = os.path.join(ROOT, "outputs", "ui_icons_2026-11-07")
NAMES = ["truck_loaded", "truck_empty", "clock_wait", "dock", "document", "phone_driver",
         "route_pin", "yen_estimate", "boxes_pallet", "empty_state"]
os.makedirs(os.path.join(OUT, "webp"), exist_ok=True)
for n in NAMES:
    im = Image.open(os.path.join(OUT, n + ".png")).convert("RGBA")
    # 影受けの薄いかぶりを消す
    import numpy as np
    a = np.array(im).astype(np.float32)
    a[..., 3] = np.clip((a[..., 3] - 6) / 249 * 255 * 1.0, 0, 255)
    a[..., 3][a[..., 3] < 4] = 0
    im = Image.fromarray(a.astype(np.uint8), "RGBA")
    for s in (512, 256, 128):
        im.resize((s, s), Image.LANCZOS).save(os.path.join(OUT, "webp", f"{n}_{s}.webp"), quality=90, alpha_quality=100, method=6)
try:
    font = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 18)
except Exception:
    font = ImageFont.load_default()
cell, cols = 330, 5
rows = 2
sheet = Image.new("RGB", (cols * cell, rows * cell * 2), "#0f1a2c")
for bi, bg in enumerate(("#0f1a2c", "#f4f6f8")):
    ImageDraw.Draw(sheet).rectangle([0, bi * rows * cell, cols * cell, (bi + 1) * rows * cell], fill=bg)
    for i, n in enumerate(NAMES):
        im = Image.open(os.path.join(OUT, "webp", f"{n}_256.webp")).convert("RGBA").resize((300, 300), Image.LANCZOS)
        x, y = (i % cols) * cell + 15, bi * rows * cell + (i // cols) * cell + 5
        sheet.paste(im, (x, y), im)
        ImageDraw.Draw(sheet).text((x + 4, y + 292), n, fill="#c8d2e0" if bi == 0 else "#33404f", font=font)
sheet.save(os.path.join(OUT, "sheet.jpg"), quality=90)
if "--publish" in sys.argv:
    import shutil
    d = os.path.join(ROOT, "web", "img", "ui")
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(os.path.join(OUT, "webp")):
        shutil.copy(os.path.join(OUT, "webp", f), os.path.join(d, f))
print("ok")
