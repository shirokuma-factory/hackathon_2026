"""B・C 案のコンタクトシートと 1x/2x の書き出し（Pillow）。python sheet_bc.py"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw
from sheetlib import *
import sheet_b_merge
import sheet_c_merge

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
OUT = os.path.join(ROOT, "outputs", "illustrations_2026-11-07")
MOCK = r"C:/Users/user/.codex/visualizations/2026/11/05/01a1ac04-0efa-7f23-b06f-978b27c4a5ac/backfull-negotiation-screens.png"
INK = (30, 40, 36)
SUB = (90, 98, 94)


def clay_clean(im):
    """粘土風は影を受ける床の薄いもやが背景に残るので、透明度の低い部分を落とす（透明度 28 以下を 0 にして伸ばす）。"""
    a = im.getchannel("A").point(lambda v: 0 if v <= 28 else min(255, int((v - 28) * 255 / 227 * 1.1)))
    im = im.copy()
    im.putalpha(a)
    return im


def sheet_b():
    sheet_b_merge.merge()
    raw = os.path.join(OUT, "B_role_cards", "raw")
    for d_ in ("2x", "1x"):
        os.makedirs(os.path.join(OUT, "B_role_cards", d_), exist_ok=True)
    for f in os.listdir(raw):
        if f.endswith(".png") and "_main" not in f and "_acc" not in f:
            im = Image.open(os.path.join(raw, f)).convert("RGBA")
            if "clay" in f:
                im = clean = clay_clean(im)
            # 母版 640x400。2x（retina）= 320x200、1x = 160x100（CSS ピクセル）
            im.resize((320, 200), Image.LANCZOS).save(os.path.join(OUT, "B_role_cards", "2x", f), optimize=True)
            im.resize((160, 100), Image.LANCZOS).save(os.path.join(OUT, "B_role_cards", "1x", f), optimize=True)
            im.save(os.path.join(OUT, "B_role_cards", f), optimize=True)
    cw, ch, lab = 380, 237, 110
    W = lab + 2 * cw + 200
    rows = [("原案（画面案）", None), ("線画（Freestyle）", "line"), ("粘土風", "clay"), ("等角図（ベタ塗り）", "iso")]
    H = 70 + len(rows) * (ch + 20)
    sh = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sh)
    d.text((10, 8), "B_role_cards  ホームの役割カード（母版 640x400 を縮小して表示）", font=font(18, True), fill=INK)
    d.text((10, 34), "右端の小さい絵は 1x の実寸（160x100）。色は深緑を寸法線と矢印にだけ使う", font=font(13), fill=SUB)
    d.text((lab + 10, 52), "荷主の方", font=font(14, True), fill=INK)
    d.text((lab + cw + 10, 52), "運送会社の方", font=font(14, True), fill=INK)
    d.text((lab + 2 * cw + 10, 52), "1x 実寸", font=font(14, True), fill=INK)
    mock = Image.open(MOCK).convert("RGB")
    crops = [mock.crop((170, 225, 350, 345)), mock.crop((640, 235, 850, 330))]
    for r, (label, st) in enumerate(rows):
        y = 70 + r * (ch + 20)
        d.line((0, y - 6, W, y - 6), fill=(225, 228, 225))
        d.text((10, y + ch // 2 - 10), label, font=font(13), fill=INK)
        for c, role in enumerate(("shipper", "carrier")):
            x = lab + c * cw
            if st is None:
                paste_fit(sh, crops[c], (x, y, x + cw - 10, y + ch))
            else:
                im = Image.open(os.path.join(OUT, "B_role_cards", f"{role}_{st}.png"))
                paste_fit(sh, im, (x, y, x + cw - 10, y + ch))
                one = Image.open(os.path.join(OUT, "B_role_cards", "1x", f"{role}_{st}.png"))
                bg = Image.new("RGB", one.size, (255, 255, 255))
                bg.paste(one, (0, 0), one)
                sh.paste(bg, (lab + 2 * cw + 10, y + 5 + c * 110))
    sh.save(os.path.join(OUT, "B_role_cards.jpg"), quality=88, optimize=True)


def sheet_c():
    sheet_c_merge.merge()
    raw = os.path.join(OUT, "C_steps", "raw")
    for d_ in ("2x", "1x"):
        os.makedirs(os.path.join(OUT, "C_steps", d_), exist_ok=True)
    names = ["step_data", "step_bars", "step_consult", "chain_1_press", "chain_2_paste", "chain_3_doc", "chain_4_check"]
    titles = ["データを選ぶ", "空いた帰りを見る", "荷主に相談する", "1 押す", "2 貼る", "3 書面", "4 照合"]
    for n in names:
        for st in ("line", "iso"):
            im = Image.open(os.path.join(raw, f"{n}_{st}.png"))
            im.resize((160, 160), Image.LANCZOS).save(os.path.join(OUT, "C_steps", "2x", f"{n}_{st}.png"), optimize=True)
            im.resize((80, 80), Image.LANCZOS).save(os.path.join(OUT, "C_steps", "1x", f"{n}_{st}.png"), optimize=True)
    cell = 190
    lab = 110
    W = lab + cell * 7 + 10
    H = 78 + 2 * (cell + 12) + 130
    sh = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sh)
    d.text((10, 8), "C_steps  操作の流れの挿絵（左 3 枚）と、荷待ちの記録の流れ（右 4 枚。ドライバーが押す → 事務が貼る → 書面 → 荷主が照合）", font=font(17, True), fill=INK)
    d.text((10, 34), "母版 320x320 を縮小して表示。最下段は 1x の実寸（80px）。2x は 160px。現行の素材は無い。", font=font(13), fill=SUB)
    for i, t in enumerate(titles):
        d.text((lab + i * cell + 20, 58), t, font=font(14, True), fill=INK)
    for r, (label, st) in enumerate((("線画", "line"), ("等角図", "iso"))):
        y = 78 + r * (cell + 12)
        d.line((0, y - 4, W, y - 4), fill=(225, 228, 225))
        d.text((10, y + cell // 2), label, font=font(14), fill=INK)
        for i, n in enumerate(names):
            im = Image.open(os.path.join(raw, f"{n}_{st}.png"))
            paste_fit(sh, im, (lab + i * cell + 6, y, lab + (i + 1) * cell - 6, y + cell))
    y = 78 + 2 * (cell + 12) + 6
    d.text((10, y + 30), "実寸 80px", font=font(13), fill=INK)
    for r, st in enumerate(("line", "iso")):
        for i, n in enumerate(names):
            im = Image.open(os.path.join(raw, f"{n}_{st}.png")).resize((80, 80), Image.LANCZOS)
            sh.paste(im, (lab + i * cell + 20 + r * 90, y + 10), im)
    sh.save(os.path.join(OUT, "C_steps.jpg"), quality=88, optimize=True)


if __name__ == "__main__":
    sheet_b()
    sheet_c()
