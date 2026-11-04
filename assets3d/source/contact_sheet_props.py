"""build_props.py --tiles で出した画像を 1920x1080 の一覧にまとめる（Pillow）。

使い方: python assets3d/source/contact_sheet_props.py <tiles_dir> <out.png>
寸法・三角形数・KB は web/models/props/index.json から読む。
"""
import json
import os
import sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
INDEX = os.path.join(ROOT, "web", "models", "props", "index.json")
BG = (11, 18, 32)
W, H, COLS, ROWS = 1920, 1080, 5, 4
TOP = 66  # 各マスの上に文字用の帯を空ける。タイルは 768x408（build_props.py の既定）


def font(size):
    for f in (r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def main(tiles, out):
    items = json.load(open(INDEX, encoding="utf-8"))["props"]
    cw, ch = W // COLS, H // ROWS
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    f1, f2 = font(18), font(14)
    for i, it in enumerate(items):
        x, y = (i % COLS) * cw, (i // COLS) * ch
        im = Image.open(os.path.join(tiles, it["name"] + ".png")).convert("RGB").resize((cw, ch - TOP), Image.LANCZOS)
        sheet.paste(im, (x, y + TOP))
        L, Wd, Ht = it["dims_m"]
        dim = (f"{L*100:.0f} x {Wd*100:.0f} x {Ht*100:.0f} cm" if max(L, Wd, Ht) < 1.0
               else f"{L:.2f} x {Wd:.2f} x {Ht:.2f} m")
        d.text((x + 10, y + 8), f"{it['name']}  {it['label_ja']}", fill=(241, 245, 249), font=f1)
        d.text((x + 10, y + 32), f"長さ x 幅 x 高さ {dim}", fill=(148, 163, 184), font=f2)
        d.text((x + 10, y + 52), f"{it['tris']:,} 三角形   {it['glb_bytes'] / 1024:.1f} KB", fill=(148, 163, 184), font=f2)
    x, y = (len(items) % COLS) * cw, (len(items) // COLS) * ch
    d.text((x + 10, y + 8), "向きと色", fill=(241, 245, 249), font=f1)
    notes = ["前方 = Blender の -Y（glTF の +Z）", "扉は後ろ（+Y）、前面は閉じた端面",
             "原点 = 床の中心、単位 m", "視点は前方右上の 3/4 図", "Draco なし・テクスチャなし"]
    for j, t in enumerate(notes):
        d.text((x + 10, y + 40 + j * 22), t, fill=(148, 163, 184), font=f2)
    for j, (c, t) in enumerate([("#f59e0b", "トラック 橙"), ("#22c55e", "鉄道 緑"), ("#c4b5fd", "航空 薄紫"), ("#c8a97e", "段ボール")]):
        yy = y + 160 + j * 24
        d.rectangle((x + 10, yy, x + 34, yy + 16), fill=c)
        d.text((x + 44, yy), t, fill=(203, 213, 225), font=f2)
    for k in range(1, COLS):
        d.line((k * cw, 0, k * cw, H), fill=(30, 41, 59))
    for k in range(1, ROWS):
        d.line((0, k * ch, W, k * ch), fill=(30, 41, 59))
    sheet.save(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
