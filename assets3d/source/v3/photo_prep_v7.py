"""v7: 国土地理院の空中写真（全国 z9、寄る範囲 z11）を、ジオラマの座標へ再投影したテクスチャにする。
出力: <out>/cache/photo_global.png（RGBA、範囲は geo の DEM_* と同じ）、photo_<領域>.png（RGBA、DEM の高精細領域と同じ範囲）、photo_meta.json
タイルが無い所は alpha 0（海や無データ）。領域の境は 0.8 単位でなだらかに alpha 1 へ。黄金色の光に合わせた軽い補正つき。
使い方: python photo_prep_v7.py <out dir>   空中写真: 国土地理院"""
import json
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
PD = os.path.join(ROOT, "data", "raw", "gsi_photo")
GROUPS = {"kanto": (905, 912, 399, 406), "kansai": (894, 901, 404, 409), "kyushu": (880, 887, 409, 419),
          "tohoku": (908, 915, 385, 396), "tokaido": (901, 905, 404, 407)}     # z10 のタイル範囲（DEM と同じ）
RES_G = 0.0279
RES_R = 0.008
DELTA = {}      # 描画で測った九州と四国の差（Lab）を打ち消す補正


def mosaic(zdir, x0, x1, y0, y1):
    h, w = (y1 - y0 + 1) * 256, (x1 - x0 + 1) * 256
    mos = np.zeros((h, w, 3), np.uint8)
    val = np.zeros((h, w), np.float32)
    n = 0
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            p = os.path.join(zdir, "%d_%d.jpg" % (tx, ty))
            if not os.path.exists(p):
                continue
            im = cv2.imread(p, cv2.IMREAD_COLOR)
            if im is None or im.shape[:2] != (256, 256):
                continue
            mos[(ty - y0) * 256:(ty - y0 + 1) * 256, (tx - x0) * 256:(tx - x0 + 1) * 256] = im
            val[(ty - y0) * 256:(ty - y0 + 1) * 256, (tx - x0) * 256:(tx - x0 + 1) * 256] = 1.0
            n += 1
    return mos, val, n


def resample(mos, val, z, x0t, y0t, gx0, gy0, nx, ny, res):
    X, Y = np.meshgrid(gx0 + np.arange(nx) * res, gy0 + np.arange(ny) * res)
    lon = G.LON0 + X / (G.S * G.KX)
    lat = G.LAT0 + Y / G.S
    W = 256 * 2 ** z
    px = ((lon + 180) / 360 * W - x0t * 256).astype(np.float32)
    py = ((0.5 - np.log(np.tan(math.pi / 4 + np.radians(lat) / 2)) / (2 * math.pi)) * W - y0t * 256).astype(np.float32)
    col = cv2.remap(mos, px, py, cv2.INTER_AREA if res > 0.02 else cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    vv = cv2.remap(val, px, py, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return col, vv


def grade(bgr):
    """黄金色の光に合わせた軽い補正: 写真に写り込んだ陰影を少しならし、彩度を少し落とし、明るさを揃える"""
    x = bgr.astype(np.float32) / 255.0
    lum = x @ np.array([0.114, 0.587, 0.299], np.float32)
    k = max(3, int(min(x.shape[:2]) * 0.02) | 1)
    lb = cv2.GaussianBlur(lum, (0, 0), k)
    flat = np.clip((lb + 0.02) / (lum + 0.02), 0.6, 1.6) ** 0.28
    x = x * flat[..., None]
    gray = (x @ np.array([0.114, 0.587, 0.299], np.float32))[..., None]
    x = gray + (x - gray) * 0.88
    x = np.clip(x * np.array([0.98, 0.99, 1.0], np.float32) * 1.05, 0, 1)
    return (x * 255 + 0.5).astype(np.uint8)


def land_fill(bgr):
    """海の部分（青い）を、近くの陸の色をぼかして埋める。ジオラマの海岸線は写真と少し違うので、陸の地面に海の色が出ないようにする"""
    b, g, r = [bgr[..., i].astype(np.float32) for i in range(3)]
    lum = 0.114 * b + 0.587 * g + 0.299 * r
    mu = cv2.blur(lum, (7, 7)); sd = np.sqrt(np.maximum(cv2.blur(lum * lum, (7, 7)) - mu * mu, 0))
    smooth_ = sd < (2.2 + 0.0 * lum)                      # 海はなめらか。暗い森は細かい模様があるので海にしない
    water = ((((b > g * 1.10) & (b > r * 1.6) & (lum > 40)) | ((b >= g) & (b > r * 1.25) & (lum <= 40))) & smooth_).astype(np.uint8)
    water = cv2.morphologyEx(water, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    water = cv2.dilate(water, np.ones((3, 3), np.uint8))
    if not water.any():
        return bgr, water.astype(bool)
    L = (1 - water).astype(np.float32)
    x = bgr.astype(np.float32)
    out = x.copy()
    need = water.astype(bool)
    for sig, sc in ((3.0, 1), (12.0, 4), (48.0, 16)):
        sh = (x.shape[1] // sc, x.shape[0] // sc)
        xs = cv2.resize(x * L[..., None], sh, interpolation=cv2.INTER_AREA)
        ls = cv2.resize(L, sh, interpolation=cv2.INTER_AREA)
        bx = cv2.GaussianBlur(xs, (0, 0), sig / sc * 1.0 if sc > 1 else sig)
        bl = cv2.GaussianBlur(ls, (0, 0), sig / sc * 1.0 if sc > 1 else sig)
        col = bx / np.maximum(bl, 1e-4)[..., None]
        col = cv2.resize(col, (x.shape[1], x.shape[0]), interpolation=cv2.INTER_LINEAR)
        w = cv2.resize(bl, (x.shape[1], x.shape[0]), interpolation=cv2.INTER_LINEAR)
        ok = need & (w > 0.02)
        out[ok] = col[ok]
        need &= ~ok
    if need.any():
        out[need] = x[L > 0].mean(0)
    return np.clip(out, 0, 255).astype(np.uint8), water.astype(bool)


def match_low(reg, gx0, gy0, nx, ny, glob):
    """寄る範囲の写真（z11）の色の大きなむらを、全国の写真（z9）に合わせる。細かい模様はそのまま残す（継ぎ目が見えないように）"""
    gcol, galpha = glob
    i0 = (gx0 - G.DEM_X0) / RES_G
    j0 = (gy0 - G.DEM_Y0) / RES_G
    X, Y = np.meshgrid(np.arange(nx) * RES_R / RES_G + i0, np.arange(ny) * RES_R / RES_G + j0)
    gc = cv2.remap(gcol, X.astype(np.float32), Y.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    ga = cv2.remap(galpha, X.astype(np.float32), Y.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    sig = 90
    w = ga[..., None].astype(np.float32)
    bg = cv2.GaussianBlur(gc.astype(np.float32) * w, (0, 0), sig) / np.maximum(cv2.GaussianBlur(w, (0, 0), sig)[..., None] if False else cv2.GaussianBlur(ga.astype(np.float32), (0, 0), sig)[..., None], 1e-3)
    br = cv2.GaussianBlur(reg.astype(np.float32), (0, 0), sig)
    sig2 = 400
    br = cv2.GaussianBlur(reg.astype(np.float32), (0, 0), sig2)
    bg = cv2.GaussianBlur(gc.astype(np.float32) * w, (0, 0), sig2) / np.maximum(cv2.GaussianBlur(ga.astype(np.float32), (0, 0), sig2)[..., None], 1e-3)
    k = np.clip((bg + 6) / (br + 6), 0.65, 1.5)
    x = reg.astype(np.float32) * k
    # 陸の平均と分散を全国の写真に合わせる（領域が目立たないように）
    lb, lg, lr = x[..., 0], x[..., 1], x[..., 2]
    land = (lb <= lg * 1.05) & (ga > 0.5)
    if land.sum() > 1000:
        mr, sr = x[land].mean(0), x[land].std(0)
        mg, sg = gc[land].astype(np.float32).mean(0), gc[land].astype(np.float32).std(0)
        sc = np.clip(sg / np.maximum(sr, 1e-3), 0.85, 1.2)
        x = (x - mr) * sc + mg
    return np.clip(x, 0, 255).astype(np.uint8)


def sea_alpha(wm, alpha):
    """海・暗い海の写真は使わず、地面の頂点色（陸の色）に切り替える（島が黒くならないように）"""
    k = cv2.GaussianBlur(wm.astype(np.float32), (0, 0), 3.0)
    return alpha * np.clip(1.0 - k * 1.6, 0, 1)


def lab_stats(bgr, mask):
    lab = cv2.cvtColor(bgr.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)
    v = lab[mask]
    return v.mean(0), v.std(0)


def lab_transfer(bgr, mask, ref):
    """Lab で、陸の平均と標準偏差（L・a・b）を基準（中国・四国の陸）に合わせる"""
    lab = cv2.cvtColor(bgr.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)
    m, sd = lab[mask].mean(0), lab[mask].std(0)
    rm, rs = ref
    k = np.clip(rs / np.maximum(sd, 1e-3), 0.8, 1.25)
    lab = (lab - m) * k + rm
    out = cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_LAB2BGR)
    return np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8)


def box_mask(shape, gx0, gy0, res, lon0, lon1, lat0, lat1):
    ys, xs = np.mgrid[0:shape[0], 0:shape[1]]
    X = gx0 + xs * res
    Y = gy0 + ys * res
    lon = G.LON0 + X / (G.S * G.KX)
    lat = G.LAT0 + Y / G.S
    return (lon > lon0) & (lon < lon1) & (lat > lat0) & (lat < lat1)


def save_rgba(path, bgr, alpha):
    # Blender の画像は先頭の行が上（v=1）。この座標は y が北向きに増えるので、北を上にして保存する
    bgr = bgr[::-1]; alpha = alpha[::-1]
    a = (np.clip(alpha, 0, 1) * 255 + 0.5).astype(np.uint8)
    cv2.imwrite(path, np.dstack([bgr, a]), [cv2.IMWRITE_PNG_COMPRESSION, 3])


def main():
    meta = {}
    mp_ = os.path.join(OUT, "cache", "photo_meta.json")
    if os.environ.get("REGIONS") and os.path.exists(mp_):
        meta = json.load(open(mp_))
    GLOB = None
    REF = None
    # 全国（z9）
    zd = os.path.join(PD, "z9")
    names = [f for f in os.listdir(zd) if f.endswith(".jpg")] if os.path.isdir(zd) else []
    if names:
        xs = [int(f.split("_")[0]) for f in names]
        ys = [int(f.split("_")[1].split(".")[0]) for f in names]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        mos, val, n = mosaic(zd, x0, x1, y0, y1)
        nx = int((G.DEM_X1 - G.DEM_X0) / RES_G) + 1
        ny = int((G.DEM_Y1 - G.DEM_Y0) / RES_G) + 1
        col, vv = resample(mos, val, 9, x0, y0, G.DEM_X0, G.DEM_Y0, nx, ny, RES_G)
        vb = cv2.GaussianBlur(vv, (0, 0), 1.0)
        gf, wm = land_fill(col)
        gg = grade(gf)
        ga0 = np.where(vb > 0.98, 1.0, 0.0).astype(np.float32)
        GLOB = (gg, ga0)
        bm = box_mask(gg.shape, G.DEM_X0, G.DEM_Y0, RES_G, 132.0, 134.4, 33.0, 35.2) & (ga0 > 0.5) & ~wm
        REF = lab_stats(gg, bm)
        print("基準（中国・四国の陸）Lab 平均", REF[0], "標準偏差", REF[1])
        save_rgba(os.path.join(OUT, "cache", "photo_global.png"), gg, sea_alpha(wm, ga0))
        meta["global"] = dict(x0=G.DEM_X0, y0=G.DEM_Y0, x1=G.DEM_X0 + (nx - 1) * RES_G, y1=G.DEM_Y0 + (ny - 1) * RES_G, res=RES_G, tiles=n)
        print("global tiles", n, "grid", nx, ny)
    # 領域（z11）
    zd = os.path.join(PD, "z11")
    dh = np.load(os.path.join(OUT, "cache", "dem_hi.npz"))
    only = os.environ.get("REGIONS")
    for name, (a0, a1, b0, b1) in GROUPS.items():
        if not os.path.isdir(zd):
            break
        if only and name not in only.split(","):
            continue
        x0, x1, y0, y1 = a0 * 2, a1 * 2 + 1, b0 * 2, b1 * 2 + 1
        mos, val, n = mosaic(zd, x0, x1, y0, y1)
        if n == 0:
            continue
        o, h = dh[name + "_o"], dh[name + "_h"]
        gx0, gy0 = float(o[0]), float(o[1])
        nx = int((h.shape[1] - 1) * float(o[2]) / RES_R) + 1
        ny = int((h.shape[0] - 1) * float(o[2]) / RES_R) + 1
        col, vv = resample(mos, val, 11, x0, y0, gx0, gy0, nx, ny, RES_R)
        valid = np.pad((vv > 0.999).astype(np.uint8), 1)
        dist = cv2.distanceTransform(valid, cv2.DIST_L2, 3)[1:-1, 1:-1] * RES_R
        alpha = np.clip(dist / 0.8, 0, 1)
        gf, wm = land_fill(col)
        rg = grade(gf)
        if GLOB is not None:
            rg = match_low(rg, gx0, gy0, nx, ny, GLOB)
            lm = (alpha > 0.5) & ~wm
            print(name, "Lab 前", lab_stats(rg, lm)[0])
            if name != "tokaido" or True:
                rg = lab_transfer(rg, lm, REF)
            dl = DELTA.get(name)
            if dl is not None:
                lab_ = cv2.cvtColor(rg.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB) + np.array(dl, np.float32)
                rg = np.clip(cv2.cvtColor(lab_, cv2.COLOR_LAB2BGR) * 255 + 0.5, 0, 255).astype(np.uint8)
            print(name, "Lab 後", lab_stats(rg, lm)[0])
        save_rgba(os.path.join(OUT, "cache", "photo_%s.png" % name), rg, sea_alpha(wm, alpha))
        meta[name] = dict(x0=gx0, y0=gy0, x1=gx0 + (nx - 1) * RES_R, y1=gy0 + (ny - 1) * RES_R, res=RES_R, tiles=n)
        print(name, "tiles", n, "grid", nx, ny)
    json.dump(meta, open(os.path.join(OUT, "cache", "photo_meta.json"), "w"), indent=1)


main()
