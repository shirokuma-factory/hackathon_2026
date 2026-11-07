"""ComfyUI 仕上げの比較シート。Blender の元画像を一番上、変種を下に並べる。python sheet_hero_v2.py <assets_v2 出力先>"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

root = sys.argv[1]
H, C = os.path.join(root, "hero"), os.path.join(root, "comfy")
F = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 20)
items = [("Blender の元画像（原案）", os.path.join(H, "hero_1600x900.png")),
         ("A  img2img denoise 0.35 / seed 7（粘土の質感）", os.path.join(C, "style_d035_s7.png")),
         ("B  denoise 0.25 / seed 3（元に近い）", os.path.join(C, "style_d025_s3.png")),
         ("C  denoise 0.50 / seed 11（ガッシュ風）", os.path.join(C, "style_gouache_d050_s11.png"))]
W, Hh = 960, 540
sheet = Image.new("RGB", (W * 2, (Hh + 34) * 2), (14, 19, 29))
d = ImageDraw.Draw(sheet)
for i, (t, p) in enumerate(items):
    im = Image.open(p).convert("RGB").resize((W, Hh), Image.LANCZOS)
    x, y = (i % 2) * W, (i // 2) * (Hh + 34)
    sheet.paste(im, (x, y + 34))
    d.text((x + 10, y + 6), t, font=F, fill=(232, 238, 248))
sheet.save(os.path.join(root, "sheet_hero_style.png"))
sheet.resize((1600, int(1600 * sheet.height / sheet.width))).save(os.path.join(root, "sheet_hero_style.jpg"), quality=80)

tx = [("紙（ComfyUI 生成のまま）", "tex_paper_raw.png"), ("紙 シームレス処理後", "tex_paper_seamless.png"), ("紙 3x3 並べ", "tex_paper_seamless_tiled3x3.jpg"),
      ("地面（生成のまま）", "tex_ground_raw.png"), ("地面 シームレス処理後", "tex_ground_seamless.png"), ("地面 3x3 並べ", "tex_ground_seamless_tiled3x3.jpg")]
s2 = Image.new("RGB", (3 * 520, 2 * 554), (14, 19, 29))
d2 = ImageDraw.Draw(s2)
for i, (t, p) in enumerate(tx):
    im = Image.open(os.path.join(C, p)).convert("RGB").resize((512, 512), Image.LANCZOS)
    x, y = (i % 3) * 520, (i // 3) * 554
    s2.paste(im, (x + 4, y + 38))
    d2.text((x + 10, y + 6), t, font=F, fill=(232, 238, 248))
s2.save(os.path.join(root, "sheet_textures.jpg"), quality=82)
