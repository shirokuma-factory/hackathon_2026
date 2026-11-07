"""国土地理院の標高タイル（ズーム 8、data/raw/gsi_dem/z8）をつなぎ、ジオラマの座標へ再投影した標高の格子（メートル）を作る。
使い方: python dem_prep.py <out dir>   出力: <out dir>/cache/dem.npy（float32、行が y、列が x、範囲は geo の DEM_* と同じ）
地形: 国土地理院 標高タイルを加工して作成"""
import math
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import geo_v4_center as G  # noqa

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.abspath(sys.argv[1])
TD = os.path.join(ROOT, "data", "raw", "gsi_dem", "z8")
X0, X1, Y0, Y1 = 219, 231, 91, 105
Z = 8
mosaic = np.zeros(((Y1 - Y0 + 1) * 256, (X1 - X0 + 1) * 256), np.float32)
have = 0
for tx in range(X0, X1 + 1):
    for ty in range(Y0, Y1 + 1):
        p = os.path.join(TD, "%d_%d.png" % (tx, ty))
        if not os.path.exists(p):
            continue
        im = cv2.imread(p, cv2.IMREAD_COLOR)[..., ::-1].astype(np.int64)
        x = im[..., 0] * 65536 + im[..., 1] * 256 + im[..., 2]
        h = np.where(x < 2 ** 23, x * 0.01, (x - 2 ** 24) * 0.01)
        h = np.where(x == 2 ** 23, 0.0, h)
        mosaic[(ty - Y0) * 256:(ty - Y0 + 1) * 256, (tx - X0) * 256:(tx - X0 + 1) * 256] = np.clip(h, 0, None)
        have += 1
print("tiles", have, "mosaic", mosaic.shape, "max %.0f m" % mosaic.max())
nx = int(round((G.DEM_X1 - G.DEM_X0) / G.DEM_RES)) + 1
ny = int(round((G.DEM_Y1 - G.DEM_Y0) / G.DEM_RES)) + 1
gx = G.DEM_X0 + np.arange(nx) * G.DEM_RES
gy = G.DEM_Y0 + np.arange(ny) * G.DEM_RES
X, Y = np.meshgrid(gx, gy)
lon = G.LON0 + X / (G.S * G.KX)
lat = G.LAT0 + Y / G.S
W = 256 * 2 ** Z
px = (lon + 180) / 360 * W - X0 * 256
py = (0.5 - np.log(np.tan(math.pi / 4 + np.radians(lat) / 2)) / (2 * math.pi)) * W - Y0 * 256
dem = cv2.remap(mosaic, px.astype(np.float32), py.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
dem = cv2.GaussianBlur(dem, (0, 0), 1.0)
np.save(os.path.join(OUT, "cache", "dem.npy"), dem.astype(np.float32))
print("dem", dem.shape, "max %.0f" % dem.max())
