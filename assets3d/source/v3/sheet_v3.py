"""比較シート。使い方: python sheet_v3.py <out dir>
sheet.jpg: 今の版 + 3 つの照明（全体と等倍の切り出し）。sheet_loop.jpg: 循環動画の 8 コマ。"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

out = os.path.abspath(sys.argv[1])
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
F = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 22)
BG = (18, 22, 30)
TW, TH = 960, 540
items = [
    ("今の版（web/img/hero/hero_1920.webp）", Image.open(os.path.join(ROOT, "web", "img", "hero", "hero_1920.webp")).convert("RGB"), (500, 500)),
]
for v, lab in (("golden", "夕方（golden hour）"), ("morning", "晴れた朝（clear morning）"), ("dusk", "夕暮れ（dusk、街の明かり）")):
    items.append((lab, Image.open(os.path.join(out, "final", "still_%s_2560.png" % v)).convert("RGB"), (640, 640)))
sheet = Image.new("RGB", (TW * 2, (TH + 40) * len(items)), BG)
d = ImageDraw.Draw(sheet)
for i, (lab, im, (cx, cy)) in enumerate(items):
    y = i * (TH + 40)
    d.text((12, y + 8), lab + "  全体", font=F, fill=(236, 240, 248))
    d.text((TW + 12, y + 8), lab + "  等倍の切り出し（関西〜関東、%dx%d）" % (TW, TH), font=F, fill=(236, 240, 248))
    sheet.paste(im.resize((TW, TH), Image.LANCZOS), (0, y + 40))
    sheet.paste(im.crop((cx, cy, cx + TW, cy + TH)), (TW, y + 40))
sheet.save(os.path.join(out, "sheet.jpg"), quality=88)

fr = [0, 24, 48, 72, 96, 120, 144, 168]
fd = os.path.join(out, "final", "loop_golden")
s2 = Image.new("RGB", (TW * 2, (TH + 40) * 4), BG)
d2 = ImageDraw.Draw(s2)
for k, f in enumerate(fr):
    x, y = (k % 2) * TW, (k // 2) * (TH + 40)
    im = Image.open(os.path.join(fd, "f%04d.png" % f)).convert("RGB").resize((TW, TH), Image.LANCZOS)
    s2.paste(im, (x, y + 40))
    d2.text((x + 12, y + 8), "コマ %d / 192（%.2f 秒）" % (f, f / 24), font=F, fill=(236, 240, 248))
s2.save(os.path.join(out, "sheet_loop.jpg"), quality=88)
print("sheets done")
