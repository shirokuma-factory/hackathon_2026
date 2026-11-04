"""build_models.py --tiles で出した画像を 1920x1080 の一覧にまとめる（Pillow）。

使い方: python assets3d/source/contact_sheet.py <tiles_dir> <out.png>
"""
import json
import os
import sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
GLB = os.path.join(ROOT, "web", "models")
ORDER = ["truck", "freight_train", "ferry", "cargo_plane", "warehouse", "terminal", "port_crane"]
LABEL = {"truck": "truck  10t ウイングボディ", "freight_train": "freight_train  EF210 + コンテナ車3両",
         "ferry": "ferry  長距離 RORO フェリー", "cargo_plane": "cargo_plane  双発 広胴貨物機",
         "warehouse": "warehouse  マルチ型倉庫 + ランプウェイ", "terminal": "terminal  トラックターミナル/貨物駅",
         "port_crane": "port_crane  ガントリークレーン"}
BG = (11, 18, 32)
W, H, COLS = 1920, 1080, 4


def font(size):
    for f in (r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def main(tiles, out):
    st = json.load(open(os.path.join(tiles, "stats.json"), encoding="utf-8"))
    cw, ch = W // COLS, H // 2
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    f1, f2 = font(21), font(16)
    for i, n in enumerate(ORDER):
        x, y = (i % COLS) * cw, (i // COLS) * ch
        im = Image.open(os.path.join(tiles, n + ".png")).convert("RGB").resize((cw, ch), Image.LANCZOS)
        sheet.paste(im, (x, y))
        s = st[n]
        kb = os.path.getsize(os.path.join(GLB, n + ".glb")) / 1024
        dm = s["dims_m"]
        d.text((x + 14, y + 12), LABEL[n], fill=(241, 245, 249), font=f1)
        d.text((x + 14, y + 40), f"幅{dm[0]:.0f} x 長{dm[1]:.0f} x 高{dm[2]:.0f} m   {s['tris']:,} 三角形   {kb:.0f} KB",
               fill=(148, 163, 184), font=f2)
    x, y = 3 * cw, ch
    d.text((x + 14, y + 12), "色と向き", fill=(241, 245, 249), font=f1)
    for j, (c, t) in enumerate([("#f59e0b", "トラック 橙"), ("#22c55e", "鉄道 緑"), ("#38bdf8", "船 水色"), ("#c4b5fd", "飛行機 薄紫")]):
        yy = y + 56 + j * 34
        d.rectangle((x + 14, yy, x + 44, yy + 22), fill=c)
        d.text((x + 56, yy), t, fill=(203, 213, 225), font=f2)
    notes = ["前方 = Blender の -Y（glTF の +Z）", "原点 = 地面（水面）の中心、単位 m",
             "視点は前方右上の 3/4 図", "Draco なし・テクスチャなし"]
    for j, t in enumerate(notes):
        d.text((x + 14, y + 210 + j * 26), t, fill=(148, 163, 184), font=f2)
    for k in range(1, COLS):
        d.line((k * cw, 0, k * cw, H), fill=(30, 41, 59))
    d.line((0, ch, W, ch), fill=(30, 41, 59))
    sheet.save(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
