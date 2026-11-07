"""web/data/pref.geojson から、隣り合う県の境界を共有したまま簡略化した輪郭と、海岸からの距離場を作る（通常の Python）。
使い方: python prep_land_v3.py <cache dir>
出力: land.json（県ごとの輪郭 rings、境界線 borders）、sdf.npy（float16、陸で正の距離）、sdf.png（16bit、Blender の海の材質用）"""
import json
import math
import os
import sys
import time
import numpy as np
import cv2
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo_v3 import *  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.abspath(sys.argv[1])
TOL = float(os.environ.get("DIO_TOL", "0.06"))      # 0.006 度 ≒ 0.06 単位
MIN_AREA = float(os.environ.get("DIO_MINAREA", "0.0008"))   # 度²
MAXSEG = 0.45


def rdp(pts, tol):
    n = len(pts)
    keep = [False] * n
    keep[0] = keep[-1] = True
    st = [(0, n - 1)]
    while st:
        a, b = st.pop()
        if b <= a + 1:
            continue
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-12
        dm, im = -1, a
        for i in range(a + 1, b):
            d = abs(dy * pts[i][0] - dx * pts[i][1] + bx * ay - by * ax) / L
            if d > dm:
                dm, im = d, i
        if dm > tol:
            keep[im] = True
            st.append((a, im))
            st.append((im, b))
    return [p for p, k in zip(pts, keep) if k]


def densify(pts):
    out = [pts[0]]
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, int(math.ceil(L / MAXSEG)))
        for j in range(1, k + 1):
            out.append((a[0] + (b[0] - a[0]) * j / k, a[1] + (b[1] - a[1]) * j / k))
    return out


def area(ring):
    n = len(ring)
    return abs(sum(ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1] for i in range(n))) / 2


def chain_simplify(run):
    """run: 点列（端点を含む）。向きを正規化して簡略化と分割をし、元の向きで返す"""
    rev = tuple(run[0]) > tuple(run[-1])
    r = run[::-1] if rev else run
    r = densify(rdp(r, TOL))
    return r[::-1] if rev else r


def main():
    t0 = time.time()
    gj = json.load(open(os.path.join(ROOT, "web", "data", "pref.geojson"), encoding="utf-8"))
    polys = []  # (pref idx, ring list of (lon,lat))
    for idx, f in enumerate(gj["features"]):
        if idx == 46:
            continue
        ps = f["geometry"]["coordinates"]
        if f["geometry"]["type"] == "Polygon":
            ps = [ps]
        for p in ps:
            ring = [tuple(q) for q in p[0]]
            if ring[0] == ring[-1]:
                ring = ring[:-1]
            if min(q[1] for q in ring) < 30.5 or area(ring) < MIN_AREA or len(ring) < 3:
                continue
            polys.append((idx, ring))
    edge_owner = {}
    for pid, (idx, ring) in enumerate(polys):
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            edge_owner.setdefault((a, b) if a < b else (b, a), set()).add(pid)
    out_polys, borders = [], []
    for pid, (idx, ring) in enumerate(polys):
        n = len(ring)
        flags = []
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            flags.append(frozenset(edge_owner[(a, b) if a < b else (b, a)] - {pid}))
        starts = [i for i in range(n) if flags[i] != flags[i - 1]]
        if not starts:  # 輪郭全体が同じ扱い（島など）。最も遠い 2 点で分ける
            k = max(range(n), key=lambda i: (ring[i][0] - ring[0][0]) ** 2 + (ring[i][1] - ring[0][1]) ** 2)
            runs = [(ring[:k + 1], frozenset()), (ring[k:] + [ring[0]], frozenset())]
            runs = [(([proj(*q) for q in r]), fl) for r, fl in runs]
        else:
            runs = []
            s0 = starts[0]
            order = starts[starts.index(s0):] + [s0 + n]
            for a, b in zip(order[:-1], order[1:]):
                seg = [ring[i % n] for i in range(a, b + 1)]
                runs.append(([proj(*q) for q in seg], flags[a % n]))
        new = []
        for r, fl in runs:
            sr = chain_simplify(r)
            new.extend(sr[:-1])
            if fl and pid < min(fl):
                borders.append(sr)
        if len(new) >= 3:
            out_polys.append({"pref": idx, "ring": [[round(x, 5), round(y, 5)] for x, y in new]})
    json.dump({"polys": out_polys, "borders": [[[round(x, 5), round(y, 5)] for x, y in b] for b in borders]},
              open(os.path.join(OUT, "land.json"), "w"))
    print("rings", len(out_polys), "verts", sum(len(p["ring"]) for p in out_polys), "borders", len(borders), "%.1fs" % (time.time() - t0))
    # 距離場
    mask = np.zeros((FH, FW), np.uint8)
    sx, sy = (FW - 1) / (FX1 - FX0), (FH - 1) / (FY1 - FY0)
    for p in out_polys:
        pts = np.array([[(x - FX0) * sx * 16, (y - FY0) * sy * 16] for x, y in p["ring"]], np.int32)
        cv2.fillPoly(mask, [pts], 255, lineType=cv2.LINE_8, shift=4)
    land = mask > 0
    res = (FX1 - FX0) / (FW - 1)
    din = ndimage.distance_transform_edt(land)
    dout = ndimage.distance_transform_edt(~land)
    sdf = np.where(land, (din - 0.5) * res, -(dout - 0.5) * res).astype(np.float32)
    np.save(os.path.join(OUT, "sdf.npy"), sdf.astype(np.float16))
    enc = np.clip((np.clip(sdf, -16, 32) + 16) / 48, 0, 1)
    cv2.imwrite(os.path.join(OUT, "sdf.png"), np.flipud((enc * 65535).astype(np.uint16)))
    print("sdf done", sdf.shape, "%.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
