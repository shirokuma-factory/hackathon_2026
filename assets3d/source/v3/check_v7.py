"""4 コマの確認: 合成して 1920 の PNG を書き、2x2 の一覧と、色の測定（主役の線の中心の色、九州と四国の Lab）を出す。使い方: python check_v7.py <out dir> f1,f2,f3,f4"""
import os, subprocess, sys
import cv2, numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import tour_v7 as TV
OUT = os.path.abspath(sys.argv[1]); FR = [int(x) for x in sys.argv[2].split(",")]
ims = []
for f in FR:
    o = os.path.join(OUT, "raw", "pv_comp", "c%04d.png" % f)
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "comp_v7.py"), OUT, "one", os.path.join(OUT, "raw", "pv", "p%04d.png" % f), "x", str(f / 24.0), o, "1920", "1080"], check=True)
    ims.append(cv2.imread(o))
tiles = [cv2.resize(i, (960, 540), interpolation=cv2.INTER_AREA) for i in ims]
sc = 0.5
for t_ in tiles[:1]:
    pass
cv2.rectangle(tiles[0], (300, 215), (660, 470), (0, 255, 255), 2)
sheet = np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:4])])
cv2.imwrite(os.path.join(OUT, "check_hero.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])
