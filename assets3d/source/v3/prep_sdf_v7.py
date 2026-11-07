"""v7: 伊豆諸島の部分の海岸の距離場を海にした sdf_v7.png を作る（海の白い泡が島の位置に残らないように）。使い方: python prep_sdf_v7.py <out dir>"""
import os, sys
import cv2, numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import geo_v4_center as G
C = os.path.join(os.path.abspath(sys.argv[1]), "cache")
im = cv2.imread(os.path.join(C, "sdf.png"), cv2.IMREAD_UNCHANGED)
x0, y0 = G.proj(139.0, 32.0); x1, y1 = G.proj(140.3, 34.85)
H, W = im.shape[:2]
c0, c1 = int((x0 - G.FX0) / (G.FX1 - G.FX0) * (W - 1)), int((x1 - G.FX0) / (G.FX1 - G.FX0) * (W - 1))
rA = int((1 - (y1 - G.FY0) / (G.FY1 - G.FY0)) * (H - 1)); rB = int((1 - (y0 - G.FY0) / (G.FY1 - G.FY0)) * (H - 1))
sea = int((-2.0 + 16.0) / 48.0 * 65535)
reg = im[rA:rB, c0:c1].astype(np.float32)
yy = np.minimum(np.arange(rB - rA)[:, None], np.arange(rB - rA)[::-1][:, None]) / 90.0
xx = np.minimum(np.arange(c1 - c0)[None, :], np.arange(c1 - c0)[::-1][None, :]) / 90.0
w = np.clip(np.minimum(yy, xx), 0, 1) ** 1.0
w = w * w * (3 - 2 * w)
im[rA:rB, c0:c1] = (np.minimum(reg, sea) * w + reg * (1 - w)).astype(np.uint16)
cv2.imwrite(os.path.join(C, "sdf_v7.png"), im)
print("sdf_v7", im.shape, (rA, rB, c0, c1))
