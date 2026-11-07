"""A 案のコンタクトシートと 256/128 の書き出し（Pillow）。python sheet_a.py"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw
from sheetlib import *

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
OUT = os.path.join(ROOT, "outputs", "illustrations_2026-11-07")
RAW = os.path.join(OUT, "A_topdown", "raw")
THUMB = os.path.join(ROOT, "web", "img", "models")

ROWS = [("4t 空荷台", "truck_4t", "empty", None), ("4t 積載", "truck_4t", "loaded", None),
        ("4t ウイング閉", "truck_4t", "closed", "truck_4t"), ("10t", "truck", "closed", "truck"),
        ("2t", "box_2t", "closed", "box_2t"), ("軽バン", "kei_van", "closed", "kei_van"),
        ("倉庫（ドック）", "warehouse", "docks", "warehouse"), ("倉庫 ドック拡大", "warehouse", "docks", None), ("パレット T11", "pallet_t11", "plain", "pallet_t11"),
        ("車格不明", "unknown", "symbol", None)]
COLS = [("元の色 真上", "top", "orig"), ("単色 真上", "top", "mono"), ("線画 真上", "top", "line"),
        ("単色 斜め", "tilt", "mono"), ("線画 斜め", "tilt", "line")]


def raw(name, var, view, style):
    p = os.path.join(RAW, f"{name}_{var}_{view}_{style}.png")
    return Image.open(p) if os.path.exists(p) else None


def export():
    for sz in (256, 128):
        os.makedirs(os.path.join(OUT, "A_topdown", f"png{sz}"), exist_ok=True)
    n = 0
    for f in sorted(os.listdir(RAW)):
        if f.endswith(".png"):
            im = Image.open(os.path.join(RAW, f))
            for sz in (256, 128):
                trim(im, 0.04, size=sz).save(os.path.join(OUT, "A_topdown", f"png{sz}", f), optimize=True)
            n += 1
    return n


def sheet(path, cell=170):
    lab = 120
    W = lab + cell + cell * len(COLS) + 150
    H = 60 + cell * len(ROWS) + 20
    sh = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sh)
    f1, f2 = font(15), font(13)
    d.text((10, 8), "A_topdown  既存モデルの真上・斜め（透過 PNG）。左端が現在の素材（サムネイル）", font=font(18, True), fill=(30, 40, 36))
    d.text((10, 34), "右端の列は 64px と 32px に縮めたときの見え方（単色・線画の真上）", font=f2, fill=(90, 98, 94))
    heads = ["現在の素材"] + [c[0] for c in COLS] + ["縮小 64 / 32"]
    xs = [lab + i * cell for i in range(len(COLS) + 2)]
    for x, h in zip(xs, heads):
        d.text((x + 6, 58 - 22 + 18), h, font=f2, fill=(60, 70, 66))
    for r, (label, name, var, thumb) in enumerate(ROWS):
        y = 60 + r * cell
        d.line((0, y, W, y), fill=(225, 228, 225))
        d.text((10, y + cell // 2 - 10), label, font=f1, fill=(30, 40, 36))
        if thumb and os.path.exists(os.path.join(THUMB, thumb + ".webp")):
            paste_fit(sh, Image.open(os.path.join(THUMB, thumb + ".webp")), (xs[0] + 6, y + 10, xs[0] + cell - 6, y + cell - 10), CHECK)
        else:
            d.text((xs[0] + 10, y + cell // 2 - 8), "（元の素材なし）", font=f2, fill=(150, 156, 152))
        for c, (cn, view, style) in enumerate(COLS):
            im = raw(name, var, view, style)
            if label == "倉庫 ドック拡大":
                im = raw(name, "docks", "zoom", style) if view == "top" else None
            x = xs[c + 1]
            if im is not None:
                paste_fit(sh, trim(im, 0.02), (x + 6, y + 6, x + cell - 6, y + cell - 6))
        # 縮小
        x = xs[-1]
        for k, (st, sz) in enumerate((("mono", 64), ("line", 64), ("mono", 32), ("line", 32))):
            im = raw(name, var, "top", st)
            if im is not None:
                t = trim(im, 0.02, size=sz)
                sh.paste(t, (x + 8 + (k % 2) * (sz + 8) * (1 if sz == 64 else 1), y + 8 + (0 if k < 2 else 74)), t)
    sh.convert("RGB").save(path, quality=88, optimize=True)


if __name__ == "__main__":
    print("exported", export())
    sheet(os.path.join(OUT, "A_topdown.jpg"))
