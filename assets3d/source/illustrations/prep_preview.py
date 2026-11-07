"""確認用ページ examples/illust_preview/ 用に、候補の絵を小さくして img/ へ複製する（Pillow）。
uv run python assets3d/source/illustrations/prep_preview.py
- 車: TripView の車の枠（90x56、2 倍で 180x112）に合わせた透過 PNG。真上・斜めの A 案は前が右になるよう回す。
- 役割カード（B）と手順（C）は 2x をそのまま使う。
"""
import os
import shutil
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(ROOT, "outputs", "illustrations_2026-11-07")
DST = os.path.join(ROOT, "examples", "illust_preview", "img")
os.makedirs(DST, exist_ok=True)
CW, CH = 180, 112


def fit(im, cw=CW, ch=CH):
    bb = im.getchannel("A").point(lambda v: 255 if v > 12 else 0).getbbox()
    im = im.crop(bb)
    s = min((cw - 4) / im.width, (ch - 4) / im.height)
    im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    out.paste(im, ((cw - im.width) // 2, (ch - im.height) // 2), im)
    return out


def save(im, name):
    im.save(os.path.join(DST, name), optimize=True)


A = os.path.join(SRC, "A_topdown", "png256")
KEYS = {"4t_loaded": "truck_4t_loaded", "4t_empty": "truck_4t_empty", "4t": "truck_4t_closed",
        "2t": "box_2t_closed", "kei": "kei_van_closed", "10t": "truck_closed"}
for style, (view, st) in {"topline": ("top", "line"), "tiltline": ("tilt", "line"), "topmono": ("top", "mono")}.items():
    for key, base in KEYS.items():
        im = Image.open(os.path.join(A, f"{base}_{view}_{st}.png")).convert("RGBA").rotate(-90, expand=True)  # 前を右へ
        save(fit(im), f"tv_{style}_{key}.png")

D = os.path.join(SRC, "D_side34", "raw")
D_NAMES = {"4t_loaded": "truck_4t_loaded", "4t_empty": "truck_4t_empty", "4t": "truck_4t_closed",
           "2t": "box_2t_closed", "kei": "kei_van_closed", "10t": "truck_closed"}
for st, tag in (("orig", "side"), ("line", "sideline")):
    for key, base in D_NAMES.items():
        save(fit(Image.open(os.path.join(D, f"{base}_{st}.png")).convert("RGBA")), f"tv_{tag}_{key}.png")
    # 全車種で同じ縮尺（4 車種の外接を共通にして、幅 180 に収める）
    ims = [Image.open(os.path.join(D, f"{D_NAMES[k]}_{st}.png")).convert("RGBA") for k in ("kei", "2t", "4t", "10t")]
    bbs = [i.getchannel("A").point(lambda v: 255 if v > 12 else 0).getbbox() for i in ims]
    box = (min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs))
    for k, i in zip(("kei", "2t", "4t", "10t"), ims):
        c = i.crop(box)
        s = (CW - 4) / c.width
        c = c.resize((CW - 4, round(c.height * s)), Image.LANCZOS)
        out = Image.new("RGBA", (CW, c.height + 4), (0, 0, 0, 0))
        out.paste(c, (2, 2), c)
        save(out, f"cs_{tag}_{k}.png")

for n in ("shipper", "carrier"):
    for st in ("line", "clay", "iso"):
        shutil.copy(os.path.join(SRC, "B_role_cards", "2x", f"{n}_{st}.png"), os.path.join(DST, f"role_{n}_{st}.png"))
for n in ("data", "bars", "consult"):
    for st in ("line", "iso"):
        shutil.copy(os.path.join(SRC, "C_steps", "2x", f"step_{n}_{st}.png"), os.path.join(DST, f"step_{n}_{st}.png"))
print(len(os.listdir(DST)), "files", sum(os.path.getsize(os.path.join(DST, f)) for f in os.listdir(DST)) // 1024, "KB")
