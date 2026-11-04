"""物流の地図用 3D モデル 7 種を bpy だけで作り、glb に書き出す台本。

使い方（画面なし）:
    blender --background --python assets3d/source/build_models.py -- [--tiles <dir>]

  - assets3d/source/vehicles.blend に保存し、web/models/<name>.glb に書き出す。
  - --tiles を付けると、コンタクトシート用の 3/4 視点の画像を <dir> に出す。
    並べて文字を入れる処理は assets3d/source/contact_sheet.py（Pillow）。

向きの決め（deck.gl ScenegraphLayer 用）:
  - Blender の座標: +Z が上、+X が右、前方 = -Y（鼻先・先頭・船首は -Y 側）。
  - 書き出しは Y-up（Blender の -Y が glTF の +Z になる）。つまり glTF の前方は +Z。
  - 原点は地面（水面）の中心。乗り物は車輪の接地面、船は船底、施設は地面が z=0。
  - 単位はメートル（実寸）。
"""
import bpy
import bmesh
import json
import math
import os
import sys
from mathutils import Vector, Matrix

try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:  # MCP 経由の exec では __file__ がない
    HERE = os.path.dirname(bpy.data.filepath)
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
GLB_DIR = os.path.join(ROOT, "web", "models")
BLEND = os.path.join(HERE, "vehicles.blend")
SHARP_DEG = 35.0

# ---------------------------------------------------------------- 材質
# 名前: (色, metallic, roughness)。手段の色は SPEC §17.2 に合わせる（赤は使わない）。
PALETTE = {
    "truck": ("#f59e0b", 0.0, 0.5),    # トラック 橙
    "rail": ("#22c55e", 0.0, 0.5),     # 鉄道 緑
    "rail_dk": ("#15803d", 0.0, 0.55),
    "ship": ("#38bdf8", 0.0, 0.45),    # 船 水色
    "ship_dk": ("#0369a1", 0.0, 0.55),
    "plane": ("#c4b5fd", 0.0, 0.45),   # 飛行機 薄紫
    "white": ("#f1f5f9", 0.0, 0.5),
    "light": ("#cbd5e1", 0.0, 0.55),
    "grey": ("#94a3b8", 0.1, 0.55),
    "deck": ("#a8b5c8", 0.0, 0.7),
    "concrete": ("#8896ab", 0.0, 0.85),
    "asphalt": ("#2b3547", 0.0, 0.95),
    "charcoal": ("#273244", 0.2, 0.6),
    "rubber": ("#0f172a", 0.0, 0.9),
    "hub": ("#9ca3af", 0.6, 0.35),
    "glass": ("#0b1424", 0.7, 0.12),
    "lamp": ("#fde68a", 0.0, 0.3),
}


def hexcol(h):
    h = h.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def f(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return (f(r), f(g), f(b), 1.0)


def mat(name):
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m.use_nodes = True
    col, metal, rough = PALETTE[name]
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = hexcol(col)
    b.inputs["Metallic"].default_value = metal
    b.inputs["Roughness"].default_value = rough
    m.diffuse_color = hexcol(col)
    return m


# ---------------------------------------------------------------- 形の部品
CUBE_F = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def interp(x, tab):
    for (x0, y0), (x1, y1) in zip(tab, tab[1:]):
        if x <= x1:
            return y1 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return tab[-1][1]


def smooth(a, b, x):
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


class Mdl:
    """面を貯めて、最後に 1 つのメッシュ（材質ごとのスロット付き）にする。"""

    def __init__(s, name):
        s.name = name
        s.V, s.F, s.M, s.mats = [], [], [], []

    def _mi(s, n):
        if n not in s.mats:
            s.mats.append(n)
        return s.mats.index(n)

    def add(s, verts, faces, m, bevel=0.0, matrix=None):
        bm = bmesh.new()
        vs = [bm.verts.new(v) for v in verts]
        for f in faces:
            bm.faces.new([vs[i] for i in f])
        bm.faces.ensure_lookup_table()
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        ms = m if isinstance(m, (list, tuple)) else [m] * len(faces)
        for f, mm in zip(bm.faces, ms):
            f.material_index = s._mi(mm)
        if bevel > 0:
            bmesh.ops.bevel(bm, geom=list(bm.edges), offset=bevel, offset_type='OFFSET',
                            segments=1, affect='EDGES', clamp_overlap=True)
        if matrix is not None:
            bmesh.ops.transform(bm, matrix=matrix, verts=list(bm.verts))
        bm.verts.index_update()
        base = len(s.V)
        for v in bm.verts:
            s.V.append(tuple(v.co))
        for f in bm.faces:
            s.F.append([base + v.index for v in f.verts])
            s.M.append(f.material_index)
        bm.free()

    def bx(s, m, x0, x1, y0, y1, z0, z1, bevel=0.08, matrix=None):
        d = min(x1 - x0, y1 - y0, z1 - z0)
        b = min(bevel, d * 0.4)
        v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        s.add(v, CUBE_F, m, b, matrix)

    def bxm(s, m, x0, x1, *a, **k):
        """x0..x1（正）と、その左右反転の 2 つを置く。"""
        s.bx(m, x0, x1, *a, **k)
        s.bx(m, -x1, -x0, *a, **k)

    def cyl(s, m, c, r, length, axis='z', seg=16, r2=None):
        r2 = max(r if r2 is None else r2, 0.01)
        h = length / 2
        v = [(r * math.cos(2 * math.pi * k / seg), r * math.sin(2 * math.pi * k / seg), -h) for k in range(seg)]
        v += [(r2 * math.cos(2 * math.pi * k / seg), r2 * math.sin(2 * math.pi * k / seg), h) for k in range(seg)]
        f = [(k, (k + 1) % seg, seg + (k + 1) % seg, seg + k) for k in range(seg)]
        f += [tuple(range(seg)), tuple(seg + k for k in range(seg))]
        R = {'z': Matrix.Identity(4),
             'x': Matrix.Rotation(math.pi / 2, 4, 'Y'),
             'y': Matrix.Rotation(-math.pi / 2, 4, 'X')}[axis]
        s.add(v, f, m, 0.0, Matrix.Translation(c) @ R)

    def ext(s, m, A, B, bevel=0.0):
        n = len(A)
        f = [tuple(range(n)), tuple(n + i for i in range(n))]
        f += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
        s.add(list(A) + list(B), f, m, bevel)

    def prism(s, m, pts, plane, a0, a1, bevel=0.0):
        def P(p, a):
            return {'yz': (a, p[0], p[1]), 'xz': (p[0], a, p[1]), 'xy': (p[0], p[1], a)}[plane]
        s.ext(m, [P(p, a0) for p in pts], [P(p, a1) for p in pts], bevel)

    def slab(s, m, top, thick, bevel=0.0):
        s.ext(m, top, [(x, y, z - thick) for x, y, z in top], bevel)

    def loft(s, segm, rings, cap0, cap1):
        n = len(rings[0])
        v = [p for r in rings for p in r]
        f, ms = [], []
        for r in range(len(rings) - 1):
            for k in range(n):
                a, b = r * n + k, r * n + (k + 1) % n
                f.append((a, b, b + n, a + n))
                ms.append(segm[k])
        f.append(tuple(range(n)))
        ms.append(cap0)
        last = (len(rings) - 1) * n
        f.append(tuple(last + k for k in range(n)))
        ms.append(cap1)
        s.add(v, f, ms)

    def beam(s, m, p0, p1, t=0.3, bevel=0.0):
        p0, p1 = Vector(p0), Vector(p1)
        d = p1 - p0
        up = 'X' if abs(d.normalized().z) > 0.99 else 'Z'
        q = d.to_track_quat('Y', up)
        M = Matrix.Translation((p0 + p1) / 2) @ q.to_matrix().to_4x4()
        h = d.length / 2
        s.bx(m, -t / 2, t / 2, -h, h, -t / 2, t / 2, bevel, M)

    def wheel(s, x, y, z, r, w, seg=14, hub=True):
        s.cyl("rubber", (x, y, z), r, w, 'x', seg)
        if hub:
            s.cyl("hub", (x, y, z), r * 0.55, w + 0.06, 'x', seg)

    def finish(s):
        me = bpy.data.meshes.new(s.name)
        me.from_pydata(s.V, [], s.F)
        for n in s.mats:
            me.materials.append(mat(n))
        me.polygons.foreach_set("material_index", s.M)
        me.update()
        bm = bmesh.new()
        bm.from_mesh(me)
        lim = math.radians(SHARP_DEG)
        for e in bm.edges:
            e.smooth = len(e.link_faces) == 2 and e.calc_face_angle(0.0) < lim
        for f in bm.faces:
            f.smooth = True
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(s.name, me)
        c = bpy.data.collections.get("models")
        if c is None:
            c = bpy.data.collections.new("models")
            bpy.context.scene.collection.children.link(c)
        c.objects.link(ob)
        return ob


# ---------------------------------------------------------------- 1 トラック
def build_truck():
    m = Mdl("truck")
    m.bx("charcoal", -1.0, 1.0, -5.5, 5.9, 0.85, 1.15, 0.05)
    # 運転席
    m.bx("white", -1.2, 1.2, -5.95, -3.75, 1.0, 3.0, 0.14)
    m.bx("white", -1.18, 1.18, -5.75, -4.6, 3.0, 3.3, 0.12)
    m.bx("truck", -1.215, 1.215, -5.97, -3.77, 1.3, 1.58, 0.03)
    m.bx("glass", -1.0, 1.0, -6.0, -5.9, 1.95, 2.85, 0.05)
    m.bxm("glass", 1.19, 1.24, -5.3, -4.2, 1.95, 2.8, 0.03)
    m.bx("charcoal", -0.7, 0.7, -6.0, -5.93, 1.64, 1.92, 0.02)
    m.bxm("lamp", 0.75, 1.1, -6.0, -5.92, 1.62, 1.85, 0.02)
    m.bx("charcoal", -1.15, 1.15, -6.12, -5.92, 0.7, 1.05, 0.05)
    m.bxm("charcoal", 1.25, 1.5, -5.6, -5.45, 2.0, 2.55, 0.03)
    # 荷台（ウイングボディ）
    m.bx("white", -1.25, 1.25, -3.5, 5.9, 1.2, 3.85, 0.1)
    m.bxm("truck", 1.24, 1.27, -3.3, 5.7, 1.85, 2.45, 0.0)
    m.bx("light", -1.2, 1.2, 5.9, 5.97, 1.4, 3.75, 0.03)
    m.bx("charcoal", -1.15, 1.15, 5.7, 6.0, 0.75, 1.0, 0.04)
    m.bxm("grey", 1.0, 1.28, -2.8, -1.2, 0.7, 1.1, 0.05)
    # 車輪
    for sx in (-1, 1):
        m.wheel(sx * 1.1, -4.6, 0.5, 0.5, 0.3)
        for y in (2.9, 4.25):
            for xo in (0.88, 1.22):
                m.wheel(sx * xo, y, 0.5, 0.5, 0.3)
    return m


# ---------------------------------------------------------------- 2 貨物列車
def build_train():
    m = Mdl("freight_train")
    prof = [(-41, 1.1), (-41, 2.7), (-39.4, 3.75), (-24.2, 3.75), (-22.6, 2.7), (-22.6, 1.1)]
    m.prism("white", prof, 'yz', -1.4, 1.4, 0.12)
    m.bx("rail_dk", -1.405, 1.405, -41.02, -22.58, 1.0, 1.35, 0.03)
    m.bx("rail", -1.415, 1.415, -40.9, -22.7, 1.35, 1.8, 0.03)
    m.bx("glass", -1.05, 1.05, -41.06, -40.95, 2.0, 2.65, 0.03)
    m.bx("glass", -1.05, 1.05, -22.65, -22.54, 2.0, 2.65, 0.03)
    m.bxm("light", 1.40, 1.43, -39.0, -37.6, 1.9, 3.3, 0.02)
    m.bxm("light", 1.40, 1.43, -25.6, -24.2, 1.9, 3.3, 0.02)
    m.bx("grey", -1.0, 1.0, -37.8, -35.6, 3.75, 4.05, 0.1)
    m.bx("charcoal", -0.8, 0.8, -32.5, -30.5, 3.75, 4.1, 0.1)
    m.bx("grey", -1.0, 1.0, -26.8, -24.5, 3.75, 4.05, 0.1)
    for y in (-34.4, -28.6):  # パンタグラフ
        m.bx("light", -0.4, 0.4, y - 0.2, y + 0.2, 3.75, 3.9, 0.02)
        for sx in (-0.45, 0.45):
            m.beam("charcoal", (sx, y, 3.9), (sx, y + 1.3, 4.6), 0.09)
            m.beam("charcoal", (sx, y + 1.3, 4.6), (sx, y, 5.1), 0.09)
        m.bx("charcoal", -0.95, 0.95, y - 0.12, y + 0.12, 5.05, 5.15, 0.02)
    m.bx("charcoal", -1.3, 1.3, -40.5, -23.1, 0.9, 1.1, 0.04)
    for yc in (-37.2, -31.8, -26.4):
        m.bx("charcoal", -1.0, 1.0, yc - 2.0, yc + 2.0, 0.5, 0.95, 0.05)
        for dy in (-1.1, 1.1):
            for sx in (-1, 1):
                m.wheel(sx * 0.72, yc + dy, 0.55, 0.55, 0.16, 8, False)
    cols = ["rail", "white", "rail_dk", "light", "rail", "white", "rail_dk"]
    for i in range(3):
        y0 = -21.8 + i * 21.2
        y1 = y0 + 20.4
        m.bx("charcoal", -0.2, 0.2, y0 - 0.8, y0 + 0.2, 0.95, 1.1, 0.02)
        m.bx("charcoal", -1.25, 1.25, y0, y1, 0.95, 1.2, 0.04)
        for yc in (y0 + 3.2, y1 - 3.2):
            m.bx("charcoal", -0.95, 0.95, yc - 1.8, yc + 1.8, 0.5, 0.95, 0.05)
            for dy in (-0.9, 0.9):
                for sx in (-1, 1):
                    m.wheel(sx * 0.72, yc + dy, 0.45, 0.45, 0.16, 8, False)
        for j in range(5):
            cy = y0 + 0.4 + j * 3.98
            m.bx(cols[(i * 5 + j) % len(cols)], -1.22, 1.22, cy, cy + 3.66, 1.2, 3.8, 0.07)
    return m


# ---------------------------------------------------------------- 3 フェリー
def build_ferry():
    m = Mdl("ferry")
    HB = 13.5
    S = [0, .05, .15, .3, .5, .68, .78, .85, .9, .94, .97, .99, 1.0]
    FB = [(0, .9), (.05, .97), (.15, 1), (.68, 1), (.78, .95), (.85, .86), (.9, .74),
          (.94, .58), (.97, .38), (.99, .18), (1.0, .04)]
    rings = []
    for s in S:
        hw = HB * interp(s, FB)
        y = 95 - 190 * s
        zb = 7.0 * smooth(0.62, 1.0, s) ** 1.3
        zt = 11 + 2.4 * smooth(0.78, 1.0, s)
        bw = hw * (0.97 - 0.6 * smooth(0.6, 1.0, s))
        zs = zb + 4.0
        rings.append([(-bw, y, zb), (bw, y, zb), (hw, y, zs), (hw, y, zt), (-hw, y, zt), (-hw, y, zs)])
    m.loft(["ship_dk", "ship", "white", "deck", "white", "ship"], rings, "white", "white")
    m.bxm("charcoal", 13.46, 13.6, -34, 50, 5.8, 8.2, 0.0)
    m.bx("charcoal", -6, 6, 94.6, 95.2, 2.5, 9, 0.1)
    tiers = [(-55, 75, 12.5, 10.0, 13.3), (-50, 60, 11.5, 13.3, 16.6),
             (-44, 45, 10.5, 16.6, 19.9), (-42, -12, 10.0, 19.9, 23.0)]
    for y0, y1, hw, z0, z1 in tiers:
        m.bx("white", -hw, hw, y0, y1, z0, z1, 0.4)
        m.bxm("glass", hw - 0.2, hw + 0.08, y0 + 4, y1 - 4, z0 + 1.1, z0 + 2.3, 0.0)
        m.bx("glass", -hw + 2, hw - 2, y0 - 0.08, y0 + 0.2, z0 + 1.1, z0 + 2.3, 0.0)
    m.bx("white", -12, 12, -40, -22, 23, 26, 0.4)
    m.bx("glass", -11, 11, -40.1, -39.6, 23.7, 25.3, 0.0)
    m.bxm("glass", 11.8, 12.08, -39, -23, 23.7, 25.3, 0.0)
    m.bx("light", -10, 10, -38, -24, 26, 26.4, 0.1)
    m.bx("white", -4.5, 4.5, 14, 30, 19.9, 30.5, 1.0)
    m.bx("ship", -4.6, 4.6, 14, 30, 27.5, 33, 1.0)
    m.bx("charcoal", -3.5, 3.5, 16, 28, 33, 33.2, 0.05)
    m.cyl("light", (0, -31, 30.2), 0.35, 7.6, 'z', 8)
    m.bx("light", -3, 3, -31.2, -30.8, 31.5, 31.7, 0.0)
    return m


# ---------------------------------------------------------------- 4 貨物機
def build_plane():
    m = Mdl("cargo_plane")
    st = [(-32, .35, 3.7), (-31, 1.4, 3.95), (-29, 2.2, 4.1), (-26, 2.8, 4.2), (-22, 3.1, 4.2),
          (-12, 3.1, 4.2), (10, 3.1, 4.2), (20, 3.0, 4.3), (26, 2.4, 5.0), (30, 1.5, 5.8), (32.5, .6, 6.4)]
    n = 16
    rings = []
    for y, r, zc in st:
        rings.append([(r * math.sin(2 * math.pi * k / n), y, zc - r * math.cos(2 * math.pi * k / n)) for k in range(n)])
    segm = []
    for k in range(n):
        zrel = -math.cos((k + .5) * 2 * math.pi / n)
        segm.append("deck" if zrel < -0.8 else ("plane" if zrel < -0.4 else "white"))
    m.loft(segm, rings, "white", "white")
    m.bxm("glass", 2.1, 2.5, -29.0, -27.4, 4.5, 5.1, 0.0)
    m.bx("glass", -1.4, 1.4, -30.7, -29.7, 4.4, 5.0, 0.0)
    # 垂直尾翼・水平尾翼
    m.prism("plane", [(13, 7.0), (24, 19.4), (30.5, 19.4), (31.5, 6.5)], 'yz', -0.3, 0.3, 0.1)
    for sx in (1, -1):
        m.slab("white", [(sx * 1.2, 24.5, 6.1), (sx * 12.5, 30.5, 6.1), (sx * 12.5, 32.7, 6.1), (sx * 1.2, 32.4, 6.1)], 0.35)
        # 主翼
        m.slab("white", [(sx * 2.5, -5.0, 3.0), (sx * 31.5, 8.0, 5.0), (sx * 31.5, 12.5, 5.0), (sx * 2.5, 9.5, 3.0)], 0.8)
        m.bx("plane", min(sx * 31.3, sx * 31.9), max(sx * 31.3, sx * 31.9), 7.5, 12.6, 4.2, 5.2, 0.05)
        # エンジン
        x = sx * 12.5
        m.bx("light", x - 0.3, x + 0.3, -2.5, 1.5, 2.9, 3.7, 0.05)
        m.cyl("plane", (x, -5.1, 1.75), 1.82, 0.8, 'y', 18)
        m.cyl("white", (x, -2.2, 1.75), 1.7, 5.6, 'y', 18)
        m.cyl("grey", (x, 1.6, 1.75), 1.7, 2.2, 'y', 18, r2=1.1)
        m.cyl("charcoal", (x, 3.1, 1.75), 0.7, 1.0, 'y', 12, r2=0.2)
        m.cyl("charcoal", (x, -5.55, 1.75), 1.5, 0.1, 'y', 14)
        # 脚
        for xo in (4.65, 5.35):
            for y in (3.2, 5.0):
                m.wheel(sx * xo, y, 0.55, 0.55, 0.4, 12)
        m.bx("charcoal", sx * 5.0 - 0.2, sx * 5.0 + 0.2, 3.4, 5.0, 0.5, 2.6, 0.03)
        m.wheel(sx * 0.4, -22, 0.5, 0.5, 0.28, 12)
        m.wheel(sx * 0.5, 6.0, 0.5, 0.5, 0.28, 12)
        m.wheel(sx * 0.5, 7.6, 0.5, 0.5, 0.28, 12)
    m.cyl("charcoal", (0, -22, 0.85), 0.1, 1.3, 'z', 8)
    m.bx("charcoal", -0.25, 0.25, 5.8, 7.8, 0.5, 1.3, 0.03)
    return m


# ---------------------------------------------------------------- 5 倉庫
def build_warehouse():
    m = Mdl("warehouse")
    m.bx("asphalt", -104, 104, -64, -40, 0, 0.3, 0.05)
    m.bx("white", -100, 100, -40, 40, 0, 32, 0.5)
    m.bx("truck", -100.2, 100.2, -40.2, 40.2, 30.6, 31.8, 0.0)
    m.bx("charcoal", -99, 99, -39, 39, 31.8, 32.4, 0.1)
    for f in (1, 2, 3):
        z0 = f * 8 + 4.0
        m.bx("glass", -97, 97, -40.15, -39.8, z0, z0 + 1.4, 0.0)
        m.bx("glass", -97, 97, 39.8, 40.15, z0, z0 + 1.4, 0.0)
    for i in range(16):
        c = -82.5 + 11 * i
        m.bx("charcoal", c - 2.0, c + 2.0, -40.35, -39.9, 1.3, 4.6, 0.04)
        m.bx("truck", c - 2.6, c + 2.6, -43.0, -39.9, 5.0, 5.35, 0.04)
        if i < 15:
            m.bx("light", c + 5.4, c + 5.6, -62, -41, 0.3, 0.33, 0.0)
    # ランプウェイ（東側のつづら折り。1 本目は南から北へ 0→16 m、2 本目は北から南へ 16→32 m）
    m.prism("light", [(-40, 0), (40, 16), (40, 16.8), (-40, 0.8)], 'yz', 102, 113, 0.0)
    m.prism("light", [(40, 16), (-40, 32), (-40, 32.8), (40, 16.8)], 'yz', 113, 124, 0.0)
    for x0, x1 in ((102, 102.5), (112.5, 113.0)):
        m.prism("white", [(-40, 0.8), (40, 16.8), (40, 18.0), (-40, 2.0)], 'yz', x0, x1, 0.0)
    for x0, x1 in ((113.0, 113.5), (123.5, 124)):
        m.prism("white", [(40, 16.8), (-40, 32.8), (-40, 34.0), (40, 18.0)], 'yz', x0, x1, 0.0)
    m.bx("light", 102, 124, 40, 48, 15.2, 16.0, 0.05)
    m.bx("light", 100, 124, -48, -40, 31.2, 32.0, 0.05)
    for y in range(-36, 41, 18):
        m.bx("light", 107 - 0.6, 107 + 0.6, y - 0.6, y + 0.6, 0, 0.8 + (y + 40) / 5.0, 0.05)
        m.bx("light", 118 - 0.6, 118 + 0.6, y - 0.6, y + 0.6, 0, 16.8 + (40 - y) / 5.0, 0.05)
    for z, y in ((8, 0), (24, 0)):
        m.bx("charcoal", 99.9, 100.3, y - 6, y + 6, z + 0.8, z + 5.0, 0.04)
    for x in (-60, -30, 0, 30, 60):
        m.bx("light", x - 3, x + 3, -10, -4, 32.4, 34.0, 0.15)
    return m


# ---------------------------------------------------------------- 6 ターミナル
def build_terminal():
    m = Mdl("terminal")
    m.bx("asphalt", -68, 68, -48, -24, 0, 0.2, 0.05)
    m.prism("light", [(-15, 0), (15, 0), (15, 9), (0, 13), (-15, 9)], 'yz', -60, 60, 0.3)
    m.prism("rail_dk", [(-16.5, 8.6), (0.1, 13.2), (0.1, 13.8), (-16.5, 9.2)], 'yz', -61.5, 61.5, 0.0)
    m.prism("rail_dk", [(16.5, 8.6), (-0.1, 13.2), (-0.1, 13.8), (16.5, 9.2)], 'yz', -61.5, 61.5, 0.0)
    m.bx("rail", -61.5, 61.5, -0.5, 0.5, 13.6, 14.1, 0.05)
    for i in range(10):
        c = -49.5 + 11 * i
        m.bx("charcoal", c - 3.5, c + 3.5, -15.2, -14.8, 1.3, 5.5, 0.1)
        m.bx("charcoal", c - 3.5, c + 3.5, 14.8, 15.2, 1.1, 5.3, 0.1)
    m.bx("concrete", -61, 61, -24, -15, 0, 1.3, 0.1)
    m.bx("white", -62, 62, -24.5, -14.5, 7.0, 7.4, 0.05)
    m.bx("rail", -62, 62, -24.6, -24.2, 6.4, 7.5, 0.05)
    for x in range(-60, 61, 15):
        m.bx("light", x - 0.3, x + 0.3, -24, -23.4, 1.3, 7.0, 0.05)
    m.bx("concrete", -61, 61, 15, 18.6, 0, 1.1, 0.1)
    m.bx("asphalt", -66, 66, 18.6, 27, 0, 0.3, 0.05)
    for y in (20.4, 21.85, 23.8, 25.25):
        m.bx("hub", -66, 66, y, y + 0.14, 0.3, 0.45, 0.0)
    cols = ["rail", "white", "rail_dk", "light"]
    for ix in range(4):
        for iy in range(3):
            for iz in range(2):
                x0 = 68 + ix * 6.4
                y0 = -14 + iy * 2.7
                m.bx(cols[(ix + iy + iz) % 4], x0, x0 + 6.06, y0, y0 + 2.44, iz * 2.6 + 0.2, iz * 2.6 + 2.8, 0.06)
    return m


# ---------------------------------------------------------------- 7 ガントリークレーン
def build_crane():
    m = Mdl("port_crane")
    m.bx("asphalt", -16, 16, -22, 22, 0, 0.2, 0.05)
    for y in (-15, 15):
        m.bx("hub", -14, 14, y - 0.3, y + 0.3, 0.2, 0.4, 0.0)
        for sx in (-1, 1):
            m.bx("charcoal", sx * 9 - 1.8, sx * 9 + 1.8, y - 1.8, y + 1.8, 0.4, 1.6, 0.1)
            m.bx("light", sx * 9 - 1.0, sx * 9 + 1.0, y - 1.4, y + 1.4, 1.6, 34, 0.15)
    m.bx("ship", -10, 10, -16.4, -13.6, 31, 34, 0.15)
    m.bx("ship", -10, 10, 13.6, 16.4, 31, 34, 0.15)
    m.bxm("ship", 8.0, 10.0, -15, 15, 31, 34, 0.15)
    for sx in (-9, 9):
        m.beam("light", (sx, -14, 3), (sx, 14, 31), 0.6)
        m.beam("light", (sx, 14, 3), (sx, -14, 31), 0.6)
    # ブーム（二本桁）
    m.bxm("ship", 2.0, 4.4, -62, 32, 34, 37.2, 0.15)
    for y in range(-60, 31, 10):
        m.bx("white", -4.4, 4.4, y, y + 0.8, 34.3, 36.9, 0.05)
    m.bx("white", -4.6, 4.6, -34, -27.5, 37.2, 39.5, 0.2)
    m.bx("charcoal", -6.1, 6.1, -32.2, -29.8, 21.0, 21.5, 0.05)
    m.bx("ship_dk", -6.1, 6.1, -32.1, -29.9, 18.4, 21.0, 0.08)
    for sx in (-1, 1):
        for y in (-32.0, -30.0):
            m.bx("charcoal", sx * 5.5 - 0.06, sx * 5.5 + 0.06, y - 0.06, y + 0.06, 21.5, 37.2, 0.0)
    m.bx("white", -5, 5, 8, 24, 37.2, 44, 0.5)
    m.bx("glass", -5.05, 5.05, 8.6, 12, 40, 42.5, 0.0)
    for sx in (-1, 1):
        m.beam("light", (sx * 3.5, 6, 37.2), (sx * 1.0, 11, 62), 0.7)
        m.beam("light", (sx * 3.5, 16, 37.2), (sx * 1.0, 11, 62), 0.7)
        m.beam("charcoal", (sx * 1.0, 11, 62), (sx * 2.6, -62, 37.2), 0.35)
        m.beam("charcoal", (sx * 1.0, 11, 62), (sx * 2.6, 32, 37.2), 0.35)
    m.bx("light", -1.4, 1.4, 9.0, 13.0, 61.5, 63.0, 0.1)
    return m


MODELS = [("truck", build_truck), ("freight_train", build_train), ("ferry", build_ferry),
          ("cargo_plane", build_plane), ("warehouse", build_warehouse),
          ("terminal", build_terminal), ("port_crane", build_crane)]


# ---------------------------------------------------------------- 全体
def reset():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
        for d in list(coll):
            coll.remove(d)


def build_all():
    reset()
    for i, (n, f) in enumerate(MODELS):
        ob = f().finish()
        ob.location = (0, i * 260.0, 0)  # 見やすいように並べる。書き出し時は原点に戻す
    return stats()


def stats():
    out = {}
    dg = bpy.context.evaluated_depsgraph_get()
    for n, _ in MODELS:
        ob = bpy.data.objects[n]
        me = ob.data
        me.calc_loop_triangles()
        out[n] = {"dims_m": [round(v, 1) for v in ob.dimensions], "faces": len(me.polygons),
                  "tris": len(me.loop_triangles), "verts": len(me.vertices), "materials": len(me.materials)}
    return out


def export_all(outdir=GLB_DIR):
    os.makedirs(outdir, exist_ok=True)
    sizes = {}
    for n, _ in MODELS:
        ob = bpy.data.objects[n]
        loc = ob.location.copy()
        ob.location = (0, 0, 0)
        for o in [x for x in bpy.context.view_layer.objects if x is not None]:
            o.select_set(False)
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        p = os.path.join(outdir, n + ".glb")
        bpy.ops.export_scene.gltf(filepath=p, export_format='GLB', use_selection=True,
                                  export_draco_mesh_compression_enable=False,
                                  export_apply=True, export_yup=True,
                                  export_cameras=False, export_lights=False)
        ob.location = loc
        sizes[n] = os.path.getsize(p)
    return sizes


def render_tiles(outdir, w=960, h=1080):
    """各モデルを 3/4 視点（前方右上）から直交カメラで撮る。"""
    os.makedirs(outdir, exist_ok=True)
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_EEVEE'
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
    sc.render.image_settings.file_format = 'PNG'
    sc.view_settings.view_transform = 'Standard'
    world = bpy.data.worlds.get("W") or bpy.data.worlds.new("W")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = hexcol("#0b1220")
    bg.inputs[1].default_value = 1.1
    sc.world = world
    coll = sc.collection
    sun_d = bpy.data.lights.new("_sun", 'SUN')
    sun_d.energy, sun_d.angle = 4.0, math.radians(3)
    sun = bpy.data.objects.new("_sun", sun_d)
    sun.rotation_euler = (math.radians(50), 0, math.radians(22))
    cam_d = bpy.data.cameras.new("_cam")
    cam_d.type, cam_d.clip_start, cam_d.clip_end = 'ORTHO', 1, 3000
    cam = bpy.data.objects.new("_cam", cam_d)
    gmat = bpy.data.materials.new("_ground")
    gmat.use_nodes = True
    gb = gmat.node_tree.nodes["Principled BSDF"]
    gb.inputs["Base Color"].default_value = hexcol("#16203a")
    gb.inputs["Roughness"].default_value = 1.0
    bpy.ops.mesh.primitive_plane_add(size=1)
    gr = bpy.context.active_object
    gr.name = "_ground"
    gr.data.materials.append(gmat)
    for o in (sun, cam):
        coll.objects.link(o)
    sc.camera = cam
    back = Vector((0.75, -1.0, 0.62)).normalized()
    cam.rotation_euler = (-back).to_track_quat('-Z', 'Y').to_euler()
    R = cam.rotation_euler.to_matrix()
    right, up = R @ Vector((1, 0, 0)), R @ Vector((0, 1, 0))
    for n, _ in MODELS:
        for k, _f in MODELS:
            bpy.data.objects[k].hide_render = (k != n)
        ob = bpy.data.objects[n]
        loc = ob.location.copy()
        cs = [loc + Vector(c) for c in ob.bound_box]
        ctr = sum(cs, Vector()) / 8
        ext = max(ob.dimensions)
        gr.location = (loc.x, loc.y, -0.03)
        gr.scale = (ext * 6, ext * 6, 1)
        xs = [c.dot(right) for c in cs]
        ys = [c.dot(up) for c in cs]
        cr, cu = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        cam.location = right * cr + up * cu + back * (ctr.dot(back) + ext * 2)
        cam_d.ortho_scale = max((max(xs) - min(xs)) * h / w, max(ys) - min(ys)) * 1.12  # 縦長の画面では高さの寸法
        sc.render.filepath = os.path.join(outdir, n + ".png")
        bpy.ops.render.render(write_still=True)
    with open(os.path.join(outdir, "stats.json"), "w", encoding="utf-8", newline="\n") as fp:
        json.dump(stats(), fp, ensure_ascii=False)
    for k, _f in MODELS:
        bpy.data.objects[k].hide_render = False
    for o in (sun, cam, gr):
        bpy.data.objects.remove(o)
    for d in (sun_d,):
        bpy.data.lights.remove(d)
    bpy.data.cameras.remove(cam_d)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    st = build_all()
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    sizes = export_all()
    for n in st:
        print(n, st[n], sizes[n], "bytes")
    if "--tiles" in argv:
        render_tiles(argv[argv.index("--tiles") + 1])


if bpy.app.background:
    main()
