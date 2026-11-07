"""全体の一覧（各案の推し 1 組を 1 枚に並べる）。python sheet_index.py"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw
from sheetlib import *

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
OUT = os.path.join(ROOT, "outputs", "illustrations_2026-11-07")
INK = (30, 40, 36)
SUB = (90, 98, 94)

sh = Image.new("RGB", (1500, 730), BG)
d = ImageDraw.Draw(sh)
d.text((14, 8), "Backfull 説明用イラスト 一覧（推奨の組。詳しくは A_topdown / B_role_cards / C_steps の各シート）", font=font(18, True), fill=INK)

# A: 線画の真上
d.text((14, 44), "A 真上（線画、256px を表示）。空荷台と積載は深緑の荷で区別", font=font(14, True), fill=INK)
A = [("truck_4t_empty_top", "4t 空"), ("truck_4t_loaded_top", "4t 積載"), ("truck_closed_top", "10t"), ("box_2t_closed_top", "2t"),
     ("kei_van_closed_top", "軽バン"), ("pallet_t11_plain_top", "パレット"), ("unknown_symbol_top", "車格不明"), ("warehouse_docks_top", "倉庫")]
for i, (n, t) in enumerate(A):
    im = Image.open(os.path.join(OUT, "A_topdown", "png256", n + "_line.png"))
    x = 14 + i * 180
    paste_fit(sh, im, (x, 70, x + 170, 70 + 200))
    d.text((x + 50, 274), t, font=font(12), fill=SUB)

# B
d.text((14, 310), "B 役割カード（線画、2x = 320x200）", font=font(14, True), fill=INK)
for i, r in enumerate(("shipper", "carrier")):
    im = Image.open(os.path.join(OUT, "B_role_cards", "2x", f"{r}_line.png"))
    sh.paste(im, (14 + i * 340, 336), im)

# C
d.text((14, 560 - 18), "C 操作の流れと荷待ちの記録（線画、2x = 160px）", font=font(14, True), fill=INK)
names = ["step_data", "step_bars", "step_consult", "chain_1_press", "chain_2_paste", "chain_3_doc", "chain_4_check"]
for i, n in enumerate(names):
    im = Image.open(os.path.join(OUT, "C_steps", "2x", n + "_line.png"))
    sh.paste(im, (14 + i * 168, 548), im)
sh.save(os.path.join(OUT, "index.jpg"), quality=88, optimize=True)
