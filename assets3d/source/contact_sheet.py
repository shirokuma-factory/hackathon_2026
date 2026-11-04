"""build_models.py --tiles で出した画像を 1920x1080 の一覧にまとめる（Pillow）。

使い方: python assets3d/source/contact_sheet.py <tiles_dir> <out.png> [--set 2]
  --set を省くと 1 枚目（最初の 7 種）、--set 2 で 2 枚目（あとから足した 11 種）。
  --anim を付けると、アニメーションする 5 種の中間の姿勢の一覧にする（tiles_dir は <出力先>/anim）。
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
         "port_crane": "port_crane  ガントリークレーン",
         "kei_van": "kei_van  軽バン", "box_2t": "box_2t  2t 箱車（キャブオーバー）",
         "truck_4t": "truck_4t  4t ウイング車", "trailer": "trailer  トラクター + 40ft 海上コンテナ",
         "container_12ft": "container_12ft  12ft 鉄道コンテナ", "container_ship": "container_ship  内航コンテナ船",
         "roro_ship": "roro_ship  貨物専用 RORO 船", "passenger_jet": "passenger_jet  旅客機（床下貨物）",
         "cold_storage": "cold_storage  冷凍冷蔵倉庫", "air_cargo_terminal": "air_cargo_terminal  空港貨物ターミナル",
         "container_yard": "container_yard  コンテナヤード + RTG"}
ORDER2 = ["kei_van", "box_2t", "truck_4t", "trailer", "container_12ft", "container_ship", "roro_ship",
          "passenger_jet", "cold_storage", "air_cargo_terminal", "container_yard"]
# アニメーション付きモデル: 名前 -> (ラベル, 中間の姿勢の説明, ループ秒)
ORDER3 = ["truck", "truck_4t", "port_crane", "container_yard", "air_cargo_terminal"]
ANIM_INFO = {
    "truck": ("truck  10t ウイングボディ", "翼が上に開いた姿勢（4 秒時点）", 8),
    "truck_4t": ("truck_4t  4t ウイング車", "翼が上に開いた姿勢（4 秒時点）", 8),
    "port_crane": ("port_crane  ガントリークレーン", "トロリーが海側へ出て吊り具が降りた姿勢（6.75 秒時点）", 12),
    "container_yard": ("container_yard  コンテナヤード + RTG", "RTG が列に沿って走行した姿勢（8 秒時点）", 16),
    "air_cargo_terminal": ("air_cargo_terminal  空港貨物ターミナル", "ULD ドーリー 2 列が逆向きに動いた姿勢（5 秒時点）", 10),
}
BG = (11, 18, 32)
W, H, COLS = 1920, 1080, 4


def font(size):
    for f in (r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def main_anim(tiles, out):
    cols = 3
    rows = 2
    cw, ch = W // cols, H // rows
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    f1, f2 = font(21), font(16)
    for i, n in enumerate(ORDER3):
        x, y = (i % cols) * cw, (i // cols) * ch
        im = Image.open(os.path.join(tiles, n + ".png")).convert("RGB").resize((cw, ch), Image.LANCZOS)
        sheet.paste(im, (x, y))
        label, mid, sec = ANIM_INFO[n]
        d.rectangle((x, y, x + cw - 1, y + 66), fill=BG)  # 翼などが文字に重なっても読めるように
        kb = os.path.getsize(os.path.join(GLB, n + ".glb")) / 1024
        d.text((x + 14, y + 12), label, fill=(241, 245, 249), font=f1)
        d.text((x + 14, y + 40), f"{mid}   {sec} 秒ループ   {kb:.0f} KB", fill=(148, 163, 184), font=f2)
    x, y = 2 * cw, ch
    d.text((x + 14, y + 12), "アニメーションの見かた", fill=(241, 245, 249), font=f1)
    notes = ["各モデルのアニメーション途中の 1 コマ", "閉じた姿勢は 0 秒（一覧 1・2 枚目と同じ）",
             "24 fps、glb に焼き込み（ボーンなし）", "deck.gl: _animations: {'*': {playing: true}}",
             "視点は前方右上の 3/4 図"]
    for j, t in enumerate(notes):
        d.text((x + 14, y + 56 + j * 28), t, fill=(148, 163, 184), font=f2)
    for k in range(1, cols):
        d.line((k * cw, 0, k * cw, H), fill=(30, 41, 59))
    d.line((0, ch, W, ch), fill=(30, 41, 59))
    sheet.save(out)


def main(tiles, out, order=ORDER):
    st = json.load(open(os.path.join(tiles, "stats.json"), encoding="utf-8"))
    rows = -(-(len(order) + 1) // COLS)  # 凡例用に 1 マス余らせる
    cw, ch = W // COLS, H // rows
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    f1, f2 = font(21), font(16)
    for i, n in enumerate(order):
        x, y = (i % COLS) * cw, (i // COLS) * ch
        im = Image.open(os.path.join(tiles, n + ".png")).convert("RGB").resize((cw, ch), Image.LANCZOS)
        sheet.paste(im, (x, y))
        s = st[n]
        kb = os.path.getsize(os.path.join(GLB, n + ".glb")) / 1024
        dm = s["dims_m"]
        d.text((x + 14, y + 12), LABEL[n], fill=(241, 245, 249), font=f1)
        d.text((x + 14, y + 40), f"幅{dm[0]:.0f} x 長{dm[1]:.0f} x 高{dm[2]:.0f} m   {s['tris']:,} 三角形   {kb:.0f} KB",
               fill=(148, 163, 184), font=f2)
    x, y = 3 * cw, (rows - 1) * ch
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
    for k in range(1, rows):
        d.line((0, k * ch, W, k * ch), fill=(30, 41, 59))
    sheet.save(out)


if __name__ == "__main__":
    if "--anim" in sys.argv:
        main_anim(sys.argv[1], sys.argv[2])
        sys.exit(0)
    main(sys.argv[1], sys.argv[2], ORDER2 if "--set" in sys.argv and sys.argv[sys.argv.index("--set") + 1] == "2" else ORDER)
