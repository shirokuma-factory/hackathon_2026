"""地図用の乗り物 v2（10t / 4t / 軽バン / コンテナ船 / RoRo / 貨物列車 / 貨物機 + 空車・実車の荷台つき 4t / 10t）。

使い方（画面なし）:
    blender -b -P assets3d/source/v2/build_vehicles_v2.py -- [出力先 既定 outputs/assets_v2_2026-11-07/glb]
各モデルを <out>/<name>.glb（通常）と <out>/lo/<name>.glb（低ポリ版）に書き出す。
向き: 前方 = Blender の -Y（glTF の +Z）。原点は接地面（船は船底）の中心。
"""
import bpy
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
from common import Mdl, CONT  # noqa: E402


def cab(m, y0, y1, wid, zb, ztop, slope=0.45, mat="accent"):
    """キャブオーバー型の運転席。y0 が前面（負側）。"""
    pts = [(y0, zb), (y0, ztop - slope * 1.2), (y0 + slope, ztop), (y1, ztop), (y1, zb)]
    m.prism_yz(mat, pts, -wid, wid, 0.1)
    m.box("glass", -wid + 0.18, wid - 0.18, y0 - 0.03, y0 + 0.08, zb + (ztop - zb) * 0.42, ztop - slope * 1.15 - 0.05, 0.02)
    m.mbox("glass", wid - 0.01, wid + 0.03, y0 + 0.35, y1 - 0.3, zb + (ztop - zb) * 0.42, ztop - 0.25, r=0.02)
    m.mbox("mid", wid, wid + 0.18, y0 + 0.05, y0 + 0.3, ztop - 1.05, ztop - 0.6, r=0.02)  # ミラー
    m.box("dark", -wid, wid, y0 - 0.12, y0 + 0.1, zb - 0.05, zb + 0.35, 0.05)           # バンパー


def truck10(lod=False, body="closed"):
    """10t トラック。body: closed（ウイング箱）/ empty（空車の平ボディ）/ loaded（積荷あり）"""
    nm = {"closed": "truck_10t", "empty": "truck_10t_empty", "loaded": "truck_10t_loaded"}[body]
    m = Mdl(nm, lod)
    m.box("dark", -0.95, 0.95, -5.3, 5.6, 0.55, 1.0, 0.05)
    cab(m, -5.95, -3.85, 1.2, 0.95, 3.15)
    bx0, bx1, bz = -3.65, 5.75, 1.15
    if body == "closed":
        m.box("body", -1.25, 1.25, bx0, bx1, bz, 3.6, 0.1)
        m.box("dark", -1.15, 1.15, bx0 + 0.2, bx1 - 0.1, 0.8, bz, 0.05)
        for y in (-2.0, 0.6, 3.2):
            m.box("mid", -1.1, 1.1, y, y + 0.18, 3.6, 3.68, 0.02)
        m.box("accent", -1.255, 1.255, bx0, bx0 + 0.05, 1.5, 1.9, 0.0)
        m.mbox("accent", 1.24, 1.27, bx0 + 0.3, bx1 - 0.3, 1.65, 1.95, 0.0)
    else:
        m.box("wood", -1.22, 1.22, bx0, bx1, bz, bz + 0.14, 0.02)
        m.box("dark", -1.2, 1.2, bx0 + 0.2, bx1 - 0.1, 0.8, bz, 0.05)
        sb = 0.55
        m.mbox("mid", 1.15, 1.27, bx0, bx1, bz + 0.1, bz + sb, 0.02)       # 側板
        m.box("mid", -1.27, 1.27, bx0, bx0 + 0.12, bz + 0.1, bz + 1.0, 0.02)  # 前の板
        m.box("mid", -1.22, 1.22, bx1 - 0.1, bx1, bz + 0.1, bz + sb, 0.02)    # 後ろの板
        if body == "loaded":
            n = 0
            for ix, (xa, xb) in enumerate([(-1.1, -0.0), (0.04, 1.1)]):
                y = bx0 + 0.25
                k = 0
                while y < bx1 - 1.2:
                    h = 1.3 if (k + ix) % 3 else 1.75
                    ln = 1.45
                    m.box(CONT[(k * 2 + ix) % 5], xa + 0.03, xb - 0.03, y, y + ln - 0.05, bz + 0.14, bz + 0.14 + h, 0.06)
                    y += ln
                    k += 1
            m.box("dark", -1.2, 1.2, bx0 + 3.5, bx0 + 3.62, bz + 0.14, bz + 2.0, 0.0)
    for y in (3.0, 4.4):
        m.wheels_pair(1.1, y, 0.5, 0.3)
        m.wheels_pair(0.82, y, 0.5, 0.3) if not lod else None
    m.wheels_pair(1.1, -4.7, 0.5, 0.3)
    return m


def truck4(lod=False, body="closed"):
    nm = {"closed": "truck_4t", "empty": "truck_4t_empty", "loaded": "truck_4t_loaded"}[body]
    m = Mdl(nm, lod)
    m.box("dark", -0.85, 0.85, -3.9, 4.0, 0.45, 0.85, 0.05)
    cab(m, -4.2, -2.65, 1.05, 0.8, 2.6, slope=0.4)
    bx0, bx1, bz = -2.5, 4.15, 0.95
    if body == "closed":
        m.box("body", -1.1, 1.1, bx0, bx1, bz, 3.1, 0.1)
        m.box("dark", -1.0, 1.0, bx0 + 0.2, bx1 - 0.1, 0.7, bz, 0.05)
        for y in (-1.2, 0.8, 2.8):
            m.box("mid", -1.0, 1.0, y, y + 0.16, 3.1, 3.17, 0.02)
        m.mbox("accent", 1.09, 1.12, bx0 + 0.3, bx1 - 0.3, 1.45, 1.7, 0.0)
    else:
        m.box("wood", -1.07, 1.07, bx0, bx1, bz, bz + 0.12, 0.02)
        m.box("dark", -1.0, 1.0, bx0 + 0.2, bx1 - 0.1, 0.7, bz, 0.05)
        m.mbox("mid", 1.0, 1.12, bx0, bx1, bz + 0.08, bz + 0.5, 0.02)
        m.box("mid", -1.12, 1.12, bx0, bx0 + 0.1, bz + 0.08, bz + 0.9, 0.02)
        m.box("mid", -1.07, 1.07, bx1 - 0.08, bx1, bz + 0.08, bz + 0.5, 0.02)
        if body == "loaded":
            for ix, (xa, xb) in enumerate([(-0.98, 0.0), (0.04, 0.98)]):
                y, k = bx0 + 0.2, 0
                while y < bx1 - 1.0:
                    h = 1.1 if (k + ix) % 3 else 1.5
                    m.box(CONT[(k * 2 + ix + 1) % 5], xa + 0.03, xb - 0.03, y, y + 1.25, bz + 0.12, bz + 0.12 + h, 0.05)
                    y += 1.3
                    k += 1
    m.wheels_pair(1.0, -3.0, 0.42, 0.26)
    m.wheels_pair(1.0, 2.5, 0.42, 0.26)
    if not lod:
        m.wheels_pair(0.76, 2.5, 0.42, 0.26)
    return m


def kei(lod=False):
    m = Mdl("kei_van", lod)
    m.prism_yz("accent", [(-1.7, 0.3), (-1.7, 0.95), (-1.35, 1.45), (-0.9, 1.75), (1.7, 1.78), (1.7, 0.3)], -0.74, 0.74, 0.1)
    m.box("glass", -0.62, 0.62, -1.4, -1.0, 1.0, 1.55, 0.02)
    m.mbox("glass", 0.735, 0.76, -1.0, 0.2, 1.05, 1.58, 0.02)
    m.box("body", -0.7, 0.7, -0.9, 1.69, 1.77, 1.82, 0.03)  # 白い屋根板
    m.box("dark", -0.7, 0.7, -1.78, -1.66, 0.3, 0.6, 0.03)
    m.box("dark", -0.7, 0.7, 1.66, 1.78, 0.3, 0.6, 0.03)
    m.wheels_pair(0.68, -1.05, 0.29, 0.18)
    m.wheels_pair(0.68, 1.0, 0.29, 0.18)
    return m


def hull_plan(L, W, bow, stern_cut=0.0):
    h = L / 2
    return [(-W / 2, h - stern_cut), (W / 2, h - stern_cut), (W / 2, -h + bow * 1.6), (W * 0.33, -h + bow * 0.55), (0, -h), (-W * 0.33, -h + bow * 0.55), (-W / 2, -h + bow * 1.6)]


def container_ship(lod=False):
    m = Mdl("container_ship", lod)
    L, W = 115.0, 18.1
    m.prism_xy("hull", hull_plan(L, W, 18), 0, 8.0, 0.3)
    m.prism_xy("deck", hull_plan(L, W - 1.6, 18)[:], 8.0, 8.4, 0.0)
    m.box("acc_ship", -W / 2 - 0.02, W / 2 + 0.02, -L / 2 + 25, L / 2 - 1, 7.1, 7.9, 0.0) if False else None
    # 船体の側面の帯（差し色）
    m.mbox("acc_ship", W / 2 - 0.02, W / 2 + 0.06, -L / 2 + 36, L / 2 - 2, 5.4, 6.4, r=0.0)
    # 居住区（船尾 = +Y）
    m.box("body", -7, 7, 33, 45, 8.4, 20, 0.3)
    m.box("body", -7.4, 7.4, 33.5, 42, 20, 22.4, 0.3)
    m.box("glass", -6.6, 6.6, 33.3, 33.7, 20.4, 21.8, 0.0)
    m.cyl_z("acc_ship", 0, 41, 22.4, 27, 2.2) if not lod else m.box("acc_ship", -2, 2, 39, 43, 22.4, 27, 0)
    # コンテナ（5 列 × 数ベイ）
    k = 0
    for by in range(-48, 26, 10):
        for c in range(5):
            x = -6.3 + c * 2.52
            n = 2 + ((by // 10 + c) % 3)
            m.box(CONT[(k + c) % 5], x, x + 2.38, by, by + 8.9, 8.4, 8.4 + 2.55 * n, 0.12)
        k += 2
    return m


def roro_ship(lod=False):
    m = Mdl("roro_ship", lod)
    L, W = 170.0, 27.2
    m.prism_xy("hull", hull_plan(L, W, 22), 0, 9.0, 0.3)
    m.mbox("acc_ship", W / 2 - 0.02, W / 2 + 0.06, -L / 2 + 40, L / 2 - 3, 6.4, 8.2, r=0.0)
    m.prism_xy("body", [(-12.6, 74), (12.6, 74), (12.6, -34), (9, -66), (-9, -66), (-12.6, -34)], 9.0, 27.0, 0.4)
    m.box("deck", -11.5, 11.5, -30, 70, 27.0, 27.15, 0.0)
    m.box("mid", -12.7, 12.7, -60, 70, 17.9, 18.4, 0.0) if False else None
    # 側面の窓帯と上部構造
    m.mbox("glass", 12.58, 12.66, -50, 66, 23.5, 25.0, r=0.0)
    m.box("body", -9.5, 9.5, -62, -42, 27.0, 33.0, 0.3)
    m.box("glass", -9.2, 9.2, -62.2, -61.8, 29.0, 31.5, 0.0)
    m.box("body", -8, 8, 52, 68, 27.0, 31, 0.3)
    m.box("acc_ship", -2.2, 2.2, 58, 66, 31, 38, 0.3)
    # 後部の傾斜板（ランプ）
    m.prism_yz("dark", [(74, 6), (74, 14), (82, 3)], -9, 9, 0.0) if False else None
    m.box("dark", -9, 9, 73.5, 74.5, 3.0, 24, 0.1)
    # 甲板上の車両（小さな箱）
    for i in range(7):
        m.box(CONT[i % 5], -9 + (i % 2) * 9.4, -0.6 + (i % 2) * 9.4, -28 + i * 12, -19 + i * 12, 27.15, 29.2, 0.15)
    return m


def train(lod=False):
    m = Mdl("freight_train", lod)
    y = -41.0  # 先頭の y（負側）
    # 機関車
    ll = 17.5
    m.prism_yz("acc_rail", [(y, 0.9), (y, 3.0), (y + 1.5, 4.0), (y + ll - 1.5, 4.0), (y + ll, 3.0), (y + ll, 0.9)], -1.4, 1.4, 0.15)
    m.box("glass", -1.1, 1.1, y - 0.03, y + 0.1, 2.4, 3.3, 0.02)
    m.box("dark", -1.2, 1.2, y + 3, y + ll - 3, 4.0, 4.25, 0.05)  # パンタグラフ台
    m.box("mid", -0.9, 0.9, y + 6, y + 7.2, 4.25, 4.9, 0.03)
    m.mbox("dark", 1.0, 1.35, y + 0.5, y + ll - 0.5, 0.45, 0.95, r=0.03)
    for b in (y + 3.5, y + ll - 3.5):
        m.box("dark", -1.2, 1.2, b - 1.6, b + 1.6, 0.35, 0.9, 0.05)
        m.wheels_pair(1.0, b - 1.0, 0.5, 0.18, hub=False, seg=8)
        m.wheels_pair(1.0, b + 1.0, 0.5, 0.18, hub=False, seg=8)
    y += ll + 0.5
    # コンテナ貨車 4 両
    k = 0
    for w in range(4):
        wl = 16.0
        m.box("dark", -1.4, 1.4, y, y + wl, 1.0, 1.35, 0.04)
        for b in (y + 2.5, y + wl - 2.5):
            m.box("dark", -1.2, 1.2, b - 1.5, b + 1.5, 0.35, 1.0, 0.04)
            m.wheels_pair(1.0, b - 1.0, 0.43, 0.16, hub=False, seg=8)
            m.wheels_pair(1.0, b + 1.0, 0.43, 0.16, hub=False, seg=8)
        cl = (wl - 0.4) / 4
        for c in range(4):
            m.box(CONT[(k + c) % 5], -1.3, 1.3, y + 0.2 + c * cl, y + 0.2 + (c + 1) * cl - 0.12, 1.35, 3.6, 0.1)
        k += 1
        y += wl + 0.4
    return m


def cargo_plane(lod=False):
    m = Mdl("cargo_plane", lod)
    # 胴体: 機首は -Y
    secs = [(-32, 0.4, 0.4, 4.2), (-30, 2.0, 2.0, 4.4), (-26, 3.0, 3.0, 4.6), (-18, 3.35, 3.4, 4.8), (10, 3.35, 3.4, 4.8),
            (22, 2.8, 2.9, 5.1), (30, 1.4, 1.7, 5.8), (32.3, 0.5, 0.7, 6.2)]
    m.loft("body", secs, 2.3)
    m.loft("mid", [(-27, 1.8, 0.5, 2.2), (-20, 2.7, 0.9, 2.7), (20, 2.7, 0.9, 2.9), (27, 1.4, 0.5, 3.2)], 2.0)  # 腹
    m.box("glass", -1.6, 1.6, -29.0, -27.4, 5.6, 6.4, 0.2)
    # 主翼（後退翼）
    m.prism_xy("mid", [(-3, -9), (-31.5, 10), (-31.5, 14), (-3, 8.5), (3, 8.5), (31.5, 14), (31.5, 10), (3, -9)], 3.0, 3.9, 0.15)
    # 水平尾翼 / 垂直尾翼
    m.prism_xy("mid", [(-1, 25), (-12, 33), (-12, 35.5), (0, 31), (12, 35.5), (12, 33), (1, 25)], 6.4, 6.9, 0.1)
    m.prism_yz("acc_air", [(21, 6.5), (27, 16.5), (31.5, 16.5), (32.5, 6.5)], -0.35, 0.35, 0.1)
    # エンジン 4 基
    for x, y in ((-11.5, -1), (-21.5, 5), (11.5, -1), (21.5, 5)):
        m.cyl_z("dark", x, y, 1.2, 1.2, 0.01) if False else None
        m.loft("dark", [(y - 3.2, 0.6, 0.6, 1.7), (y - 2.4, 1.15, 1.15, 1.9), (y + 2.2, 1.15, 1.15, 1.9), (y + 3, 0.7, 0.7, 1.9)], 2.0, n=8) if False else None
        m.box("dark", x - 1.1, x + 1.1, y - 3.2, y + 2.8, 1.0, 3.0, 0.5)
    for s in (-1, 1):  # 脚のカバー
        m.box("dark", s * 3.9 - 0.9, s * 3.9 + 0.9, -4, 6, 0.9, 2.2, 0.3)
    return m


MODELS = [truck10, truck4, kei, container_ship, roro_ship, train, cargo_plane]
PAIRS = [(truck10, "empty"), (truck10, "loaded"), (truck4, "empty"), (truck4, "loaded")]


def build(fn, lod, **kw):
    m = fn(lod, **kw)
    ob = C.finish(m, ao=True, subdiv=None if lod else {"truck_10t": 1.6}.get(m.name, 1.6))
    return ob


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
    out = argv[0] if argv else os.path.join(root, "outputs", "assets_v2_2026-11-07", "glb")
    os.makedirs(os.path.join(out, "lo"), exist_ok=True)
    todo = [(f, {}) for f in MODELS] + [(f, {"body": b}) for f, b in PAIRS]
    only = set(argv[1:])
    res = {}
    for fn, kw in todo:
        for lod in (False, True):
            m = fn(lod, **kw)
            nm = m.name
            if only and nm not in only:
                m.bm.free()
                continue
            # 大きい面を細かく割って AO を面の中でも変化させる（長辺の 1/ 数十）
            sub = None
            if not lod:
                pts = [v.co for v in m.bm.verts]
                Lm = max(max(p[k] for p in pts) - min(p[k] for p in pts) for k in range(3))
                sub = max(0.9, Lm / 14)
            ob = C.finish(m, ao=True, subdiv=sub)
            p = os.path.join(out, ("lo" if lod else ""), nm + ".glb")
            size = C.export_glb(ob, p)
            res[(nm, lod)] = (len(ob.data.polygons), size)
            print("MODEL", nm, "lod" if lod else "hi", "polys", len(ob.data.polygons), "bytes", size, flush=True)
            bpy.data.objects.remove(ob)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(os.path.dirname(os.path.abspath(__file__)), "vehicles_v2_last.blend")) if False else None


if __name__ == "__main__":
    main()
