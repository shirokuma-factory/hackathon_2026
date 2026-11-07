"""v7 のプレビュー: 指定コマを合成し、コンタクトシートを作る（欄の位置 x600-1320, y430-940 を 1 コマに描く）。
使い方: python sheet_v7.py <out dir> <フレーム番号,...> [W H]   プレビューの板は raw/pv、重ねは raw/pv_ov"""
import os
import subprocess
import sys
import time
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(sys.argv[1])
FRS = [int(x) for x in sys.argv[2].split(",")]
W = int(sys.argv[3]) if len(sys.argv) > 3 else 960
H = int(sys.argv[4]) if len(sys.argv) > 4 else 540
os.makedirs(os.path.join(OUT, "raw", "pv_comp"), exist_ok=True)
t0 = time.time()
tiles = []
for f in FRS:
    o = os.path.join(OUT, "raw", "pv_comp", "c%04d.png" % f)
    subprocess.run([sys.executable, "-I", os.path.join(HERE, "comp_v7.py"), OUT, "one", os.path.join(OUT, "raw", "pv", "p%04d.png" % f),
                    os.path.join(OUT, "raw", "pv_ov", "o%04d.exr" % f), str(f / 24.0), o, str(W), str(H)], check=True)
    im = cv2.imread(o)
    if f == FRS[len(FRS) // 2]:
        s = W / 1920.0
        cv2.rectangle(im, (int(600 * s), int(430 * s)), (int(1320 * s), int(940 * s)), (0, 255, 255), 2)
    cv2.putText(im, "t=%.1fs" % (f / 24.0), (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    tiles.append(im)
print("comp %.1fs / %d" % (time.time() - t0, len(FRS)))
cols = 3
rows = (len(tiles) + cols - 1) // cols
sheet = np.zeros((rows * H, cols * W, 3), np.uint8)
for i, im in enumerate(tiles):
    sheet[(i // cols) * H:(i // cols + 1) * H, (i % cols) * W:(i % cols + 1) * W] = im
sheet = cv2.resize(sheet, (cols * W * 2 // 3, rows * H * 2 // 3), interpolation=cv2.INTER_AREA)
cv2.imwrite(os.path.join(OUT, "preview_sheet.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
