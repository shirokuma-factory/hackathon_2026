"""現行と v2 の比較シート（現行を上、v2 を下）。python assets3d/source/v2/sheet_vehicles_v2.py <stills> <出力先> [--lod]"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

FONT = "C:/Windows/Fonts/meiryo.ttc"
stills, out = sys.argv[1], sys.argv[2]
SETS = {
    "a_trucks": [("truck_10t", "10t トラック"), ("truck_4t", "4t トラック"), ("kei_van", "軽バン")],
    "b_ship_rail_air": [("container_ship", "コンテナ船"), ("roro_ship", "RoRo 船"), ("freight_train", "貨物列車"), ("cargo_plane", "貨物機")],
    "c_empty_loaded": [("truck_10t_empty", "10t 空車 / 実車"), ("truck_4t_empty", "4t 空車 / 実車")],
}


def tile(name, tag):
    p = lambda k: os.path.join(stills, "%s_%s_%s.png" % (name, tag, k))
    m = Image.open(p("med")).convert("RGB")
    s = Image.open(p("small")).convert("RGB")
    s3 = s.resize((s.width * 3, s.height * 3), Image.NEAREST)
    return m, s3


def build(key, items):
    f = ImageFont.truetype(FONT, 22)
    fs = ImageFont.truetype(FONT, 17)
    rows = []
    for name, label in items:
        if key == "c_empty_loaded":
            base = name.replace("_empty", "")
            cur_m, cur_s = tile("truck_10t_empty" if "10t" in name else "truck_4t_empty", "cur")
            ev_m, ev_s = tile(name, "v2")
            lo_m, lo_s = tile(name.replace("empty", "loaded"), "v2")
            rows.append((label, [("現行（空車・実車の区別なし）", cur_m, cur_s), ("v2 空車", ev_m, ev_s), ("v2 実車", lo_m, lo_s)]))
        else:
            cm, cs = tile(name, "cur")
            vm, vs = tile(name, "v2")
            rows.append((label, [("現行", cm, cs), ("v2", vm, vs)]))
    ncols = max(len(r[1]) for r in rows)
    cw = 480 + 360
    # 行 = 1 つのモデル、行内を上下（現行 → v2）に積む形にする。c は横に 3 つ
    imgs = []
    for label, cols in rows:
        if key == "c_empty_loaded":
            W = 480 * 3
            im = Image.new("RGB", (W, 360 + 270 + 40), (14, 19, 29))
            d = ImageDraw.Draw(im)
            d.text((10, 6), label, font=f, fill=(232, 238, 248))
            for i, (t, m, s) in enumerate(cols):
                im.paste(m, (i * 480, 40))
                im.paste(s.resize((360, 270)), (i * 480, 40 + 360))
                d.text((i * 480 + 10, 46), t, font=fs, fill=(255, 255, 255))
            imgs.append(im)
        else:
            im = Image.new("RGB", (cw, 2 * 360 + 40), (14, 19, 29))
            d = ImageDraw.Draw(im)
            d.text((10, 6), label, font=f, fill=(232, 238, 248))
            for j, (t, m, s) in enumerate(cols):
                im.paste(m, (0, 40 + j * 360))
                im.paste(s, (480, 40 + j * 360))
                d.text((10, 46 + j * 360), t, font=fs, fill=(255, 255, 255))
                d.text((490, 46 + j * 360), "%s 小（40px 相当を 3 倍）" % t, font=fs, fill=(255, 255, 255))
            imgs.append(im)
    cols_n = 3 if key == "a_trucks" else 2 if key == "b_ship_rail_air" else 1
    cols_n = len(imgs) if key == "a_trucks" else cols_n
    wi, hi = imgs[0].size
    nr = (len(imgs) + cols_n - 1) // cols_n
    sheet = Image.new("RGB", (wi * cols_n, hi * nr), (14, 19, 29))
    for i, im in enumerate(imgs):
        sheet.paste(im, ((i % cols_n) * wi, (i // cols_n) * hi))
    return sheet


suffix = "_lod" if "--lod" in sys.argv else ""
os.makedirs(out, exist_ok=True)
for key, items in SETS.items():
    sh = build(key, items)
    sh.save(os.path.join(out, "sheet_vehicles_%s%s.png" % (key, suffix)))
    w = 1800 if sh.width > 1800 else sh.width
    small = sh.resize((w, int(sh.height * w / sh.width)), Image.LANCZOS)
    small.save(os.path.join(out, "sheet_vehicles_%s%s.jpg" % (key, suffix)), quality=80)
    print(key, sh.size)
