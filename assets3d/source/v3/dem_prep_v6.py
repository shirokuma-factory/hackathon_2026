"""v6: ズーム 10 の標高タイル（寄る場面の範囲）を、ジオラマの座標へ再投影した高精細な格子にする。出力: <out>/cache/dem_hi.npz
各領域: h（メートル）、w（0..1。タイルが無い所は 0、境から 1.2 単位でなだらかに 1 へ）、x0, y0, res。
使い方: python dem_prep_v6.py <out dir>   地形: 国土地理院 標高タイルを加工して作成"""
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
TD = os.path.join(ROOT, "data", "raw", "gsi_dem", "z10")
Z = 10
RES = 0.05
GROUPS = {"kanto": (905, 912, 399, 406), "kansai": (894, 901, 404, 409), "kyushu": (880, 887, 409, 419),
          "tohoku": (908, 915, 385, 396), "tokaido": (901, 905, 404, 407)}


def tile_lonlat(x, y, n=2 ** Z):
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


out = {}
for name, (x0, x1, y0, y1) in GROUPS.items():
    mos = np.zeros(((y1 - y0 + 1) * 256, (x1 - x0 + 1) * 256), np.float32)
    val = np.zeros_like(mos)
    have = 0
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            p = os.path.join(TD, "%d_%d.png" % (tx, ty))
            if not os.path.exists(p):
                continue
            im = cv2.imread(p, cv2.IMREAD_COLOR)[..., ::-1].astype(np.int64)
            v = im[..., 0] * 65536 + im[..., 1] * 256 + im[..., 2]
            h = np.where(v < 2 ** 23, v * 0.01, (v - 2 ** 24) * 0.01)
            h = np.where(v == 2 ** 23, 0.0, h)
            sl = (slice((ty - y0) * 256, (ty - y0 + 1) * 256), slice((tx - x0) * 256, (tx - x0 + 1) * 256))
            mos[sl] = np.clip(h, 0, None)
            val[sl] = 1.0
            have += 1
    lon0, lat0 = tile_lonlat(x0, y0)
    lon1, lat1 = tile_lonlat(x1 + 1, y1 + 1)
    gx0, gx1 = G.proj(lon0, 0)[0], G.proj(lon1, 0)[0]
    gy0, gy1 = G.proj(0, lat1)[1], G.proj(0, lat0)[1]
    nx = int((gx1 - gx0) / RES) + 1
    ny = int((gy1 - gy0) / RES) + 1
    X, Y = np.meshgrid(gx0 + np.arange(nx) * RES, gy0 + np.arange(ny) * RES)
    lon = G.LON0 + X / (G.S * G.KX)
    lat = G.LAT0 + Y / G.S
    W = 256 * 2 ** Z
    px = (lon + 180) / 360 * W - x0 * 256
    py = (0.5 - np.log(np.tan(math.pi / 4 + np.radians(lat) / 2)) / (2 * math.pi)) * W - y0 * 256
    hh = cv2.remap(mos, px.astype(np.float32), py.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    vv = cv2.remap(val, px.astype(np.float32), py.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    hh = cv2.GaussianBlur(hh, (0, 0), 0.8)
    valid = (vv > 0.999).astype(np.uint8)
    dist = cv2.distanceTransform(valid, cv2.DIST_L2, 3) * RES
    w = np.clip(dist / 1.2, 0, 1).astype(np.float32)
    out[name + "_h"] = hh.astype(np.float32)
    out[name + "_w"] = w
    out[name + "_o"] = np.array([gx0, gy0, RES])
    print(name, "tiles", have, "grid", hh.shape, "x %.1f..%.1f y %.1f..%.1f" % (gx0, gx1, gy0, gy1), "max %.0f m" % hh.max())
np.savez_compressed(os.path.join(OUT, "cache", "dem_hi.npz"), **out)
