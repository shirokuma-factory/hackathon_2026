"""compare_lod.py の画像（左 = 高詳細、右 = 低ポリ）を 1 枚の確認用シートにまとめる（Pillow）。
使い方: python assets3d/source/compare_lod_sheet.py <tiles_dir> <out.png>
  web/models/lod.json の三角形数とバイト数を、各マスに書く。"""
import json
import os
import sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BG = (11, 18, 32)
COLS, TW, TH = 3, 640, 320


def font(size):
    for f in (r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def main(tiles, out):
    lod = json.load(open(os.path.join(ROOT, "web", "models", "lod.json"), encoding="utf-8"))
    names = list(lod)
    rows = -(-len(names) // COLS)
    head = 44
    sheet = Image.new("RGB", (COLS * TW, rows * (TH + head)), BG)
    d = ImageDraw.Draw(sheet)
    f1, f2 = font(18), font(15)
    for i, n in enumerate(names):
        x, y = (i % COLS) * TW, (i // COLS) * (TH + head)
        im = Image.open(os.path.join(tiles, n + ".png")).convert("RGB").resize((TW, TH), Image.LANCZOS)
        sheet.paste(im, (x, y + head))
        h, l = lod[n]["hi"], lod[n]["lo"]
        d.text((x + 10, y + 4), n, fill=(241, 245, 249), font=f1)
        d.text((x + 10, y + 26), f"左 高詳細 {h['tris']:,} 三角形 {h['bytes'] / 1024:.0f} KB   右 低ポリ {l['tris']:,} 三角形 "
               f"{l['bytes'] / 1024:.0f} KB ({l['tris'] / h['tris'] * 100:.0f}%)", fill=(148, 163, 184), font=f2)
    for k in range(1, COLS):
        d.line((k * TW, 0, k * TW, sheet.height), fill=(30, 41, 59))
    for k in range(1, rows):
        d.line((0, k * (TH + head), sheet.width, k * (TH + head)), fill=(30, 41, 59))
    sheet = sheet.convert("RGB").quantize(colors=96, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    sheet.save(out, optimize=True)
    print(out, os.path.getsize(out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
