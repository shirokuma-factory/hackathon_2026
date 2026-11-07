"""ツアーのコンタクトシート。使い方: python sheet_tour_v5.py <out dir> [frames...]（省略時は場面ごとに 2 コマ）"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

out = os.path.abspath(sys.argv[1])
fr = [int(x) for x in sys.argv[2:]] or [72, 108, 216, 288, 360, 432, 540, 612, 684, 756, 792, 828]
labels = ["全国", "全国", "関東", "関東", "関西", "関西", "九州", "九州", "地方（東北）", "地方（東北）", "全国へ戻る", "全国へ戻る"]
F = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 20)
W, H = 640, 360
n = len(fr)
rows = (n + 1) // 2
s = Image.new("RGB", (W * 2, (H + 30) * rows), (18, 22, 30))
d = ImageDraw.Draw(s)
for k, f in enumerate(fr):
    im = Image.open(os.path.join(out, "final", "tour", "t%04d.png" % f)).convert("RGB").resize((W, H), Image.LANCZOS)
    if k == 3:
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); sc = W / 1920
        ImageDraw.Draw(ov).rounded_rectangle((600 * sc, 430 * sc, 1320 * sc, 940 * sc), radius=12, fill=(12, 16, 24, 150), outline=(255, 255, 255, 140), width=2)
        im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    x, y = (k % 2) * W, (k // 2) * (H + 30)
    s.paste(im, (x, y + 30))
    d.text((x + 8, y + 3), "%s  %.1f 秒" % (labels[k] if k < len(labels) else "", f / 24) + ("（欄の位置 目安）" if k == 3 else ""), font=F, fill=(236, 240, 248))
s.save(os.path.join(out, "sheet_tour.jpg"), quality=86)
print("sheet", s.size)
