"""物流の地図用 3D モデル 18 種を bpy だけで作り、glb に書き出す台本。

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
        s.parts = []               # アニメーション用に分けた子の部品（Mdl）
        s.pivot = Vector((0, 0, 0))  # 部品の回転・移動の基準点（モデル座標）

    def part(s, name, pivot):
        """動く部品を別オブジェクトにするための入れ物。座標はモデル座標のまま書き、
        finish で pivot を原点に付け替えて、ルートの子にする。"""
        p = Mdl(s.name + "_" + name)
        p.pivot = Vector(pivot)
        s.parts.append(p)
        return p

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
        ob = s._obj(Vector((0, 0, 0)))
        for p in s.parts:
            po = p._obj(p.pivot)
            po.parent = ob
            po.location = p.pivot
        return ob

    def _obj(s, pivot):
        me = bpy.data.meshes.new(s.name)
        me.from_pydata([tuple(Vector(v) - pivot) for v in s.V], [], s.F)
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
def wing_body(m, hx, y0, y1, zf, zt, stripe):
    """ウイングボディの荷台。床と中身は本体に、左右の翼（側面 + 屋根の半分）は別部品にする。
    翼の回転軸は屋根の中心線（x=0, z=zt, y 方向）。閉じた状態は従来の白い箱と同じ見た目。
    stripe = (y0, y1, z0, z1) は翼の外側に付ける橙帯。"""
    t = 0.1
    zw = zf + 0.3
    m.bx("white", -hx, hx, y0, y1, zf, zw, 0.05)                      # 床
    m.bx("light", -hx + t, hx - t, y0, y1, zw, zt - t, 0.0)           # 荷室の中身
    m.bx("white", -hx, hx, y0 - 0.06, y0, zf, zt, 0.03)               # 前面の板（翼が回る範囲の外）
    for sx, nm in ((1, "wing_r"), (-1, "wing_l")):
        w = m.part(nm, (0, (y0 + y1) / 2, zt))
        pts = [(0, zt), (sx * hx, zt), (sx * hx, zw), (sx * (hx - t), zw), (sx * (hx - t), zt - t), (0, zt - t)]
        w.prism("white", pts, 'xz', y0, y1, 0.05)
        xa, xb = sorted((sx * (hx - 0.01), sx * (hx + 0.02)))
        w.bx("truck", xa, xb, stripe[0], stripe[1], stripe[2], stripe[3], 0.0)


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
    wing_body(m, 1.25, -3.5, 5.9, 1.2, 3.85, (-3.3, 5.7, 1.85, 2.45))
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
    # 動く部品: トロリー、吊り具（コンテナ付き）、巻き上げワイヤ（上端 37.2 m に固定して縦に伸縮）
    tr = m.part("trolley", (0, -31, 37.2))
    sp = m.part("spreader", (0, -31, 21.5))
    cb = m.part("cables", (0, -31, 37.2))
    tr.bx("white", -4.6, 4.6, -34, -27.5, 37.2, 39.5, 0.2)
    sp.bx("charcoal", -6.1, 6.1, -32.2, -29.8, 21.0, 21.5, 0.05)
    sp.bx("ship_dk", -6.1, 6.1, -32.1, -29.9, 18.4, 21.0, 0.08)
    for sx in (-1, 1):
        for y in (-32.0, -30.0):
            cb.bx("charcoal", sx * 5.5 - 0.06, sx * 5.5 + 0.06, y - 0.06, y + 0.06, 21.5, 37.2, 0.0)
    m.bx("white", -5, 5, 8, 24, 37.2, 44, 0.5)
    m.bx("glass", -5.05, 5.05, 8.6, 12, 40, 42.5, 0.0)
    for sx in (-1, 1):
        m.beam("light", (sx * 3.5, 6, 37.2), (sx * 1.0, 11, 62), 0.7)
        m.beam("light", (sx * 3.5, 16, 37.2), (sx * 1.0, 11, 62), 0.7)
        m.beam("charcoal", (sx * 1.0, 11, 62), (sx * 2.6, -62, 37.2), 0.35)
        m.beam("charcoal", (sx * 1.0, 11, 62), (sx * 2.6, 32, 37.2), 0.35)
    m.bx("light", -1.4, 1.4, 9.0, 13.0, 61.5, 63.0, 0.1)
    return m


#
# ---- ここから 8 番以降（車両・コンテナ・船・旅客機・施設）。1〜7 番の関数は変更しない。----
#
def tilt_glass(m, y0, z0, y1, z1, xh, t=0.03):
    """傾いた窓ガラス（幅 ±xh）。(y0,z0)-(y1,z1) の面から外向きに t だけ出す。"""
    d = Vector((0, y1 - y0, z1 - z0)).normalized()
    n = Vector((0, -d.z, d.y))  # 前面の外向き（-Y 側）
    P = [(-xh, y0, z0), (xh, y0, z0), (xh, y1, z1), (-xh, y1, z1)]
    A = [(x, y - n.y * 0.03, z - n.z * 0.03) for x, y, z in P]
    B = [(x, y + n.y * t, z + n.z * t) for x, y, z in P]
    m.ext("glass", A, B)


def box_container(m, x0, x1, y0, y1, z0, z1, body, rib=None, rib_pitch=0.55, doors=False):
    """40ft 級の箱。rib を渡すと長側面（±X）に縦リブ、doors で +Y 端に扉の線を付ける。"""
    m.bx(body, x0, x1, y0, y1, z0, z1, 0.03)
    if rib:
        k = y0 + 0.4
        while k + 0.12 < y1 - 0.3:
            m.bx(rib, x1, x1 + 0.04, k, k + 0.12, z0 + 0.15, z1 - 0.15, 0.0)
            m.bx(rib, x0 - 0.04, x0, k, k + 0.12, z0 + 0.15, z1 - 0.15, 0.0)
            k += rib_pitch
    if doors:
        xc = (x0 + x1) / 2
        for dx in (-0.45, -0.15, 0.15, 0.45):
            m.bx("grey", xc + dx - 0.03, xc + dx + 0.03, y1, y1 + 0.05, z0 + 0.2, z1 - 0.2, 0.0)
        m.bx("charcoal", xc - 0.01, xc + 0.01, y1, y1 + 0.06, z0 + 0.1, z1 - 0.1, 0.0)


# ---------------------------------------------------------------- 8 軽バン
def build_kei_van():
    m = Mdl("kei_van")
    W = 0.74
    m.prism("white", [(-1.70, 0.32), (-1.72, 0.88), (-1.45, 1.02), (-0.78, 1.80), (1.66, 1.88),
                      (1.70, 1.80), (1.70, 0.32)], 'yz', -W, W, 0.04)
    m.bxm("truck", 0.73, 0.76, -1.5, 1.5, 0.55, 0.80, 0.0)           # 側面の橙帯
    m.bx("truck", -0.74, 0.74, -1.74, -1.66, 0.55, 0.80, 0.0)         # 前面の橙帯
    tilt_glass(m, -1.43, 1.06, -0.80, 1.77, 0.62)
    m.bxm("glass", 0.73, 0.76, -0.78, 0.18, 1.12, 1.66, 0.0)
    m.bxm("charcoal", 0.735, 0.755, 0.2, 0.23, 0.45, 1.75, 0.0)       # ドアの継ぎ目
    m.bx("glass", -0.55, 0.55, 1.70, 1.74, 1.15, 1.62, 0.0)
    m.bx("charcoal", -0.72, 0.72, -1.80, -1.62, 0.30, 0.52, 0.04)
    m.bx("charcoal", -0.35, 0.35, -1.75, -1.69, 0.62, 0.80, 0.0)
    m.bxm("lamp", 0.45, 0.68, -1.76, -1.69, 0.66, 0.82, 0.0)
    m.bx("charcoal", -0.72, 0.72, 1.66, 1.78, 0.30, 0.52, 0.04)
    for sx in (-1, 1):
        for y in (-1.05, 1.05):
            m.wheel(sx * 0.64, y, 0.28, 0.28, 0.2, 12)
    return m


# ---------------------------------------------------------------- 9 2t 箱車
def build_box2t():
    m = Mdl("box_2t")
    m.bx("charcoal", -0.6, 0.6, -2.3, 2.3, 0.5, 0.78, 0.04)
    # キャブオーバー
    m.prism("white", [(-2.35, 0.55), (-2.35, 1.4), (-2.25, 2.15), (-1.25, 2.2), (-1.25, 0.55)],
            'yz', -0.83, 0.83, 0.08)
    m.bx("truck", -0.845, 0.845, -2.37, -1.24, 1.12, 1.34, 0.03)
    tilt_glass(m, -2.34, 1.5, -2.26, 2.08, 0.7)
    m.bxm("glass", 0.82, 0.85, -2.1, -1.45, 1.5, 2.05, 0.0)
    m.bxm("lamp", 0.5, 0.75, -2.38, -2.33, 0.82, 1.0, 0.0)
    m.bx("charcoal", -0.4, 0.4, -2.38, -2.33, 0.82, 1.0, 0.0)
    m.bx("charcoal", -0.8, 0.8, -2.4, -2.28, 0.5, 0.78, 0.04)
    # 荷台（箱）
    m.bx("white", -0.85, 0.85, -1.15, 2.3, 0.8, 3.0, 0.08)
    m.bxm("truck", 0.84, 0.87, -0.95, 2.1, 1.35, 1.85, 0.0)
    m.bx("light", -0.8, 0.8, 2.3, 2.35, 0.95, 2.9, 0.02)
    m.bx("charcoal", -0.8, 0.8, 2.2, 2.35, 0.5, 0.78, 0.04)
    m.bxm("grey", 0.6, 0.86, -0.6, 0.5, 0.6, 0.85, 0.0)
    for sx in (-1, 1):
        m.wheel(sx * 0.72, -1.6, 0.37, 0.37, 0.24, 12)
        m.wheel(sx * 0.72, 1.2, 0.37, 0.37, 0.24, 12)
    return m


# ---------------------------------------------------------------- 10 4t ウイング車
def build_truck4t():
    m = Mdl("truck_4t")
    m.bx("charcoal", -0.9, 0.9, -4.1, 4.25, 0.85, 1.1, 0.05)
    # キャブ
    m.prism("white", [(-4.25, 0.9), (-4.25, 1.9), (-4.12, 2.7), (-2.5, 2.7), (-2.4, 0.9)],
            'yz', -1.05, 1.05, 0.1)
    m.bx("truck", -1.065, 1.065, -4.27, -2.38, 1.2, 1.45, 0.03)
    m.bx("truck", -0.95, 0.95, -3.7, -2.5, 2.7, 3.0, 0.15)          # ルーフデフレクタ
    tilt_glass(m, -4.24, 1.75, -4.13, 2.58, 0.88)
    m.bxm("glass", 1.04, 1.08, -3.95, -2.85, 1.8, 2.55, 0.0)
    m.bx("charcoal", -0.6, 0.6, -4.28, -4.22, 1.5, 1.7, 0.0)
    m.bxm("lamp", 0.7, 1.0, -4.28, -4.2, 1.5, 1.7, 0.02)
    m.bx("charcoal", -1.05, 1.05, -4.4, -4.2, 0.7, 1.0, 0.05)
    # 荷台（ウイングボディ）
    wing_body(m, 1.1, -2.2, 4.25, 1.1, 3.3, (-2.0, 4.05, 1.85, 2.45))
    m.bx("light", -1.05, 1.05, 4.25, 4.31, 1.3, 3.2, 0.03)
    m.bx("charcoal", -1.0, 1.0, 4.1, 4.3, 0.7, 0.95, 0.04)
    m.bxm("grey", 0.9, 1.12, -1.8, -0.5, 0.65, 1.0, 0.05)
    for sx in (-1, 1):
        m.wheel(sx * 0.98, -3.2, 0.43, 0.43, 0.26, 14)
        for xo in (0.76, 1.0):
            m.wheel(sx * xo, 2.4, 0.43, 0.43, 0.22, 14)
    return m


# ---------------------------------------------------------------- 11 トレーラー（トラクター + 40ft 海上コンテナ）
def build_trailer():
    m = Mdl("trailer")
    # トラクター
    m.bx("charcoal", -0.95, 0.95, -8.1, -3.0, 0.85, 1.15, 0.05)
    m.bx("truck", -1.2, 1.2, -8.25, -5.9, 1.0, 1.95, 0.12)
    m.bx("white", -1.18, 1.18, -8.2, -5.9, 1.95, 3.2, 0.12)
    m.bx("truck", -1.1, 1.1, -7.2, -5.85, 3.2, 3.9, 0.2)            # ルーフデフレクタ
    tilt_glass(m, -8.2, 2.1, -8.2, 3.0, 1.0)
    m.bxm("glass", 1.17, 1.21, -7.9, -6.4, 2.1, 3.0, 0.0)
    m.bxm("lamp", 0.7, 1.05, -8.3, -8.2, 1.25, 1.55, 0.02)
    m.bx("charcoal", -0.6, 0.6, -8.3, -8.2, 1.2, 1.6, 0.0)
    m.bx("charcoal", -1.15, 1.15, -8.4, -8.15, 0.75, 1.05, 0.05)
    for sx in (-1, 1):
        m.cyl("light", (sx * 1.3, -5.7, 2.5), 0.1, 3.2, 'z', 8)  # 排気管
    m.bx("charcoal", -1.0, 1.0, -5.3, -3.5, 1.15, 1.42, 0.04)      # 第五輪
    # 車輪（トラクター）
    for sx in (-1, 1):
        m.wheel(sx * 1.05, -7.0, 0.5, 0.5, 0.3, 14)
        for y in (-4.85, -3.65):
            for xo in (0.8, 1.07):
                m.wheel(sx * xo, y, 0.5, 0.5, 0.24, 14)
    # トレーラーのシャーシ
    m.bxm("charcoal", 0.45, 0.65, -4.6, 8.0, 1.05, 1.4, 0.03)
    for y in (-3.0, 0.0, 3.0, 6.0):
        m.bx("charcoal", -0.65, 0.65, y, y + 0.25, 1.05, 1.3, 0.0)
    m.bx("charcoal", -1.1, 1.1, 8.0, 8.25, 0.85, 1.3, 0.04)
    for sx in (-1, 1):
        for y in (4.8, 6.0, 7.2):
            for xo in (0.8, 1.07):
                m.wheel(sx * xo, y, 0.5, 0.5, 0.24, 14)
        m.bx("charcoal", sx * 1.2 - 0.06, sx * 1.2 + 0.06, 4.4, 7.6, 0.95, 1.1, 0.0)
    # 40ft コンテナ（中立色。リブ付き、後端に扉）
    box_container(m, -1.22, 1.22, -4.4, 7.792, 1.4, 4.0, "light", "white", 0.6, True)
    return m


# ---------------------------------------------------------------- 12 12ft コンテナ（鉄道）
def build_container12():
    m = Mdl("container_12ft")
    m.bx("charcoal", -1.2, 1.2, -1.8, 1.8, 0.0, 0.14, 0.02)
    m.bx("rail", -1.2, 1.2, -1.82, 1.82, 0.14, 2.45, 0.03)
    m.bx("rail_dk", -1.2, 1.2, -1.82, 1.82, 2.45, 2.5, 0.02)       # 屋根
    # 長側面の波板（縦リブ）
    k = -1.54
    while k < 1.5:
        for x0, x1 in ((1.2, 1.225), (-1.225, -1.2)):
            m.bx("rail_dk", x0, x1, k, k + 0.12, 0.3, 2.35, 0.0)
        k += 0.3
    # 前端（-Y）の平板、後端（+Y）の観音扉
    m.bx("rail_dk", -1.12, 1.12, -1.8575, -1.82, 0.25, 2.4, 0.0)
    for x0, x1 in ((-1.12, -0.01), (0.01, 1.12)):
        m.bx("rail_dk", x0, x1, 1.82, 1.845, 0.25, 2.4, 0.0)
    for xc in (-0.85, -0.35, 0.35, 0.85):
        m.bx("light", xc - 0.03, xc + 0.03, 1.84, 1.8575, 0.35, 2.3, 0.0)
    for xc in (-0.1, 0.1):
        m.bx("grey", xc - 0.04, xc + 0.04, 1.84, 1.8575, 0.9, 1.5, 0.0)
    # 隅金具
    for sx in (-1, 1):
        for sy in (-1, 1):
            for z0 in (0.0, 2.32):
                x0, x1 = sorted((sx * 1.04, sx * 1.225))
                y0, y1 = sorted((sy * 1.67, sy * 1.8575))
                m.bx("charcoal", x0, x1, y0, y1, z0, z0 + 0.18, 0.02)
    return m


# ---------------------------------------------------------------- 13 内航コンテナ船
def build_container_ship():
    m = Mdl("container_ship")
    L, HB, DEP = 115.0, 9.0, 9.5
    S = [0, .03, .1, .3, .6, .8, .88, .94, .98, 1.0]
    FB = [(0, .82), (.03, .92), (.1, 1), (.8, 1), (.88, .92), (.94, .72), (.98, .42), (1.0, .08)]
    rings = []
    for s in S:
        hw = HB * interp(s, FB)
        y = L / 2 - L * s  # 船尾 = +Y、船首 = -Y
        zb = 2.2 * smooth(.82, 1, s) ** 1.3
        zt = DEP + 1.6 * smooth(.9, 1, s)
        bw = hw * (0.9 - 0.3 * smooth(.8, 1, s))
        zs = zb + 3.0
        rings.append([(-bw, y, zb), (bw, y, zb), (hw, y, zs), (hw, y, zt), (-hw, y, zt), (-hw, y, zs)])
    m.loft(["ship_dk", "ship_dk", "ship_dk", "deck", "ship_dk", "ship_dk"], rings, "ship_dk", "ship_dk")
    m.bxm("ship", 8.97, 9.06, -34, 46, 5.8, 7.4, 0.0)          # 舷側の帯
    m.bxm("light", 8.75, 9.0, -34, 46, DEP, DEP + 0.9, 0.0)    # ブルワーク
    # 甲板のコンテナ（ベイごとに段数を変える）
    # 地図では橙=トラック、緑=鉄道の意味なので、船のコンテナには使わない
    cols = ["ship", "grey", "white", "ship", "light", "ship_dk", "grey", "light", "white", "ship_dk"]
    base_h = [3, 4, 4, 4, 3, 3, 2]
    for k in range(7):
        y1 = 38 - 12.5 * k
        y0 = y1 - 12.1
        hwm = min(HB * interp((L / 2 - y) / L, FB) for y in (y0, y1))
        rows = max(1, int((2 * hwm * 0.9) // 2.5))
        for r in range(rows):
            xc = (r - (rows - 1) / 2) * 2.5
            n = max(1, min(5, base_h[k] + (k * 3 + r * 5) % 3 - 1))
            for lv in range(n):
                z0 = DEP + 0.1 + lv * 2.6
                m.bx(cols[(k * 5 + r * 3 + lv * 7) % len(cols)], xc - 1.2, xc + 1.2, y0, y1, z0, z0 + 2.5, 0.0)
    # 船橋（船尾）
    for y0, y1, hw, z0, z1 in ((41, 55, 7.6, DEP, 13.5), (42, 54, 7.4, 13.5, 17.0), (43, 53, 7.2, 17.0, 20.5)):
        m.bx("white", -hw, hw, y0, y1, z0, z1, 0.3)
        zc = (z0 + z1) / 2 + 0.2
        m.bx("glass", -hw + 1.0, hw - 1.0, y0 - 0.08, y0 + 0.15, zc, zc + 0.8, 0.0)
        m.bxm("glass", hw - 0.1, hw + 0.08, y0 + 2, y1 - 2, zc, zc + 0.8, 0.0)
    m.bx("white", -8.8, 8.8, 43.5, 49, 20.5, 23.5, 0.2)
    m.bx("glass", -8.6, 8.6, 43.4, 43.65, 21.3, 22.9, 0.0)
    m.bxm("glass", 8.7, 8.9, 44.5, 48.5, 21.3, 22.9, 0.0)
    m.bx("light", -8.9, 8.9, 43.3, 49.1, 23.5, 23.8, 0.05)
    m.bx("ship", -2.6, 2.6, 49.5, 53.5, 20.5, 27.0, 0.3)           # 煙突
    m.bx("white", -2.65, 2.65, 49.45, 53.55, 24.0, 25.0, 0.0)
    m.bx("charcoal", -2.4, 2.4, 49.7, 53.3, 27.0, 27.4, 0.05)
    m.cyl("light", (0, 46, 26.0), 0.25, 5.0, 'z', 8)
    m.bx("light", -1.2, 1.2, 45.6, 46.4, 28.2, 28.5, 0.02)
    m.cyl("light", (0, -52, 13.5), 0.2, 6.0, 'z', 8)                # 船首のマスト
    m.bx("light", -2.0, 2.0, -53.5, -50, 10.7, 12.2, 0.1)           # ウインドラス
    return m


# ---------------------------------------------------------------- 14 貨物専用 RORO 船
def build_roro_ship():
    m = Mdl("roro_ship")
    HB, ZT = 13.5, 22.0
    S = [0, .06, .3, .6, .8, .88, .94, .98, 1.0]
    FB = [(0, 1), (.8, 1), (.88, .9), (.94, .7), (.98, .4), (1.0, .1)]
    rings = []
    for s in S:
        hw = HB * interp(s, FB)
        y = 75 - 160 * s  # 船尾の面 = +Y 75 m、船首 = -Y 85 m
        zb = 4.0 * smooth(.85, 1, s) ** 1.3
        zt = ZT + 1.8 * smooth(.85, 1, s)
        bw = hw * 0.93
        zs = zb + 5.0
        rings.append([(-bw, y, zb), (bw, y, zb), (hw, y, zs), (hw, y, zt), (-hw, y, zt), (-hw, y, zs)])
    m.loft(["ship_dk", "ship_dk", "ship", "deck", "ship", "ship_dk"], rings, "ship_dk", "ship_dk")
    m.bxm("white", 13.45, 13.6, -60, 70, 13.5, 14.6, 0.0)           # 舷側の白帯
    m.bxm("charcoal", 13.45, 13.62, 34, 60, 6.0, 17.0, 0.0)         # 舷側ランプドア
    m.bxm("light", 13.0, 13.45, -68, 74, ZT - 0.2, ZT + 0.9, 0.0)
    # 船尾ランプ
    m.bx("charcoal", -10.5, 10.5, 75.0, 75.2, 5.0, 19.0, 0.0)
    m.prism("light", [(75, 7.0), (85, 3.0), (85, 3.6), (75, 7.6)], 'yz', -9.5, 9.5, 0.0)
    for sx in (-1, 1):
        m.beam("charcoal", (sx * 9.2, 75.2, 19.0), (sx * 9.2, 84.5, 3.6), 0.3)
    # 甲板上の通風筒と荷役機器
    for i in range(10):
        y = 60 - i * 13
        m.bxm("light", 4.0, 7.0, y, y + 4.0, ZT, ZT + 2.4, 0.1)
    m.bx("light", -2.0, 2.0, 30, 60, ZT, ZT + 0.5, 0.05)
    # 船首の小さな船橋
    m.bx("white", -8.5, 8.5, -76, -62, 23.2, 27.4, 0.3)
    m.bx("white", -9.4, 9.4, -74, -64, 27.4, 30.6, 0.2)
    m.bx("glass", -9.2, 9.2, -74.1, -73.85, 28.2, 30.0, 0.0)
    m.bxm("glass", 9.3, 9.46, -73, -65, 28.2, 30.0, 0.0)
    m.bx("glass", -7.5, 7.5, -76.1, -75.85, 24.5, 26.2, 0.0)
    m.bx("light", -9.5, 9.5, -74.2, -63.8, 30.6, 30.9, 0.05)
    m.bx("ship", -2.0, 2.0, -70, -66, 30.9, 33.5, 0.2)
    m.cyl("light", (0, -68, 34.5), 0.25, 6.0, 'z', 8)
    m.bx("light", -1.5, 1.5, -68.4, -67.6, 36.5, 36.8, 0.02)
    # 船尾の煙突（細い）
    m.bx("ship", -1.8, 1.8, 40, 44, ZT, ZT + 7.0, 0.2)
    m.bx("charcoal", -1.6, 1.6, 40.2, 43.8, ZT + 7.0, ZT + 7.4, 0.05)
    return m


# ---------------------------------------------------------------- 15 旅客機（床下貨物を運ぶ）
def build_passenger_jet():
    m = Mdl("passenger_jet")
    st = [(-28.5, .4, 3.9), (-27.5, 1.4, 4.0), (-25.5, 2.2, 4.2), (-22, 2.7, 4.4), (-18, 2.8, 4.4),
          (15, 2.8, 4.4), (20, 2.6, 4.7), (24, 2.0, 5.4), (27, 1.2, 6.0), (28.5, .5, 6.3)]
    n = 16
    rings = []
    for y, r, zc in st:
        rings.append([(r * math.sin(2 * math.pi * k / n), y, zc - r * math.cos(2 * math.pi * k / n)) for k in range(n)])
    segm = []
    for k in range(n):
        zrel = -math.cos((k + .5) * 2 * math.pi / n)
        segm.append("plane" if zrel < -0.4 else "white")
    m.loft(segm, rings, "white", "white")
    # 操縦席の窓と客室窓の列（窓あり。貨物機との違い）
    m.bx("glass", -1.3, 1.3, -26.8, -25.7, 4.75, 5.35, 0.0)
    ys = -21.0
    while ys < 16.5:
        r = interp(ys, [(a, b) for a, b, _ in st])
        zc = interp(ys, [(a, c) for a, _, c in st])
        for sx in (-1, 1):
            x0, x1 = sorted((sx * r * 0.93, sx * (r * 0.975)))
            m.bx("glass", x0, x1, ys, ys + 0.45, zc + 0.35, zc + 0.75, 0.0)
        ys += 0.9
    # 乗降ドアの線
    for yd in (-23.5, -8.0, 14.0):
        r = interp(yd, [(a, b) for a, b, _ in st])
        zc = interp(yd, [(a, c) for a, _, c in st])
        for sx in (-1, 1):
            x0, x1 = sorted((sx * r * 0.94, sx * (r * 0.985)))
            m.bx("light", x0, x1, yd, yd + 1.1, zc - 1.0, zc + 0.9, 0.0)
    # 垂直尾翼・水平尾翼
    m.prism("plane", [(13.5, 6.8), (21, 16.5), (25.8, 16.5), (27.2, 6.4)], 'yz', -0.25, 0.25, 0.08)
    for sx in (1, -1):
        m.slab("white", [(sx * 1.2, 20.0, 6.0), (sx * 10.5, 25.0, 6.0), (sx * 10.5, 26.8, 6.0), (sx * 1.2, 27.0, 6.0)], 0.3)
        # 主翼（低翼）とウイングレット
        m.slab("white", [(sx * 2.5, -4.0, 3.4), (sx * 28.5, 8.5, 4.3), (sx * 28.5, 12.0, 4.3), (sx * 2.5, 8.0, 3.4)], 0.6)
        wl0, wl1 = sorted((sx * 28.2, sx * 28.6))
        m.bx("plane", wl0, wl1, 8.6, 12.0, 4.3, 6.4, 0.04)
        # エンジン（翼の下）
        x = sx * 9.5
        m.bx("light", x - 0.25, x + 0.25, -3.5, 0.5, 2.7, 3.4, 0.04)
        m.cyl("plane", (x, -5.2, 1.9), 1.3, 0.6, 'y', 16)
        m.cyl("white", (x, -2.6, 1.9), 1.22, 5.0, 'y', 16)
        m.cyl("grey", (x, 0.9, 1.9), 1.22, 2.0, 'y', 16, r2=0.8)
        m.cyl("charcoal", (x, 1.9, 1.9), 0.5, 0.8, 'y', 10, r2=0.15)
        m.cyl("charcoal", (x, -5.55, 1.9), 1.05, 0.1, 'y', 12)
        # 主脚
        for xo in (3.6, 4.3):
            m.wheel(sx * xo, 3.4, 0.55, 0.55, 0.38, 12)
        m.bx("charcoal", sx * 3.95 - 0.15, sx * 3.95 + 0.15, 3.2, 3.6, 0.5, 2.9, 0.03)
        m.wheel(sx * 0.35, -21, 0.4, 0.4, 0.22, 10)
    m.cyl("charcoal", (0, -21, 1.15), 0.1, 1.3, 'z', 8)
    return m


# ---------------------------------------------------------------- 16 冷凍冷蔵倉庫
def build_cold_storage():
    m = Mdl("cold_storage")
    m.bx("asphalt", -62, 62, -48, -33, 0, 0.25, 0.05)
    m.bx("concrete", -60.3, 60.3, -33, 30.3, 0, 1.2, 0.05)         # 基礎とドックの床
    m.bx("white", -60, 60, -30, 30, 1.2, 25, 0.4)                  # 窓なしの断熱パネル壁
    m.bx("light", -60.15, 60.15, -30.15, 30.15, 12.0, 12.6, 0.0)   # パネル継ぎ目の帯
    m.bx("grey", -60.3, 60.3, -30.3, 30.3, 24.2, 25.4, 0.1)        # 屋根の縁
    # 屋上の凝縮ユニット
    for ix in range(5):
        x = -44 + 22 * ix
        for y in (-12, 12):
            m.bx("light", x - 6, x + 6, y - 3, y + 3, 25.4, 28.0, 0.08)
            for fx in (-3, 3):
                m.cyl("charcoal", (x + fx, y, 28.08), 2.0, 0.16, 'z', 12)
    m.bx("grey", -50, 50, -0.5, 0.5, 25.4, 26.4, 0.0)
    m.bx("white", -8, 8, -2, 14, 25.4, 28.5, 0.15)                  # 機械室
    # 搬入口（-Y 側の長辺）
    for i in range(10):
        c = -49.5 + 11 * i
        m.bx("charcoal", c - 2.2, c + 2.2, -30.4, -29.9, 1.2, 5.2, 0.03)
        m.bx("grey", c - 2.6, c - 2.3, -33.0, -30.0, 1.2, 5.6, 0.0)
        m.bx("grey", c + 2.3, c + 2.6, -33.0, -30.0, 1.2, 5.6, 0.0)
        m.bx("grey", c - 2.6, c + 2.6, -33.0, -30.0, 5.4, 5.7, 0.0)
    # トラックの高さの庇（2 ドア分ずつ 3 か所）
    for i in (0, 4, 8):
        cx = -49.5 + 11 * i + 5.5
        m.bx("truck", cx - 10, cx + 10, -42, -30, 5.0, 5.3, 0.04)
        for sx in (-1, 1):
            m.bx("charcoal", cx + sx * 10 - 0.2, cx + sx * 10 + 0.2, -42.2, -41.8, 0.25, 5.0, 0.0)
    return m


# ---------------------------------------------------------------- 17 空港貨物ターミナル
def build_air_terminal():
    m = Mdl("air_cargo_terminal")
    m.bx("asphalt", -100, 100, -40, -5, 0, 0.25, 0.05)             # 空側のエプロン
    m.bx("asphalt", -100, 100, 30, 40, 0, 0.25, 0.05)              # 陸側の車寄せ
    m.bx("white", -100, 100, -5, 30, 0.25, 9.0, 0.3)
    m.prism("light", [(-5.3, 9.0), (30.3, 9.0), (27.0, 12.2), (12.5, 14.8), (-2.0, 12.2)],
            'yz', -100.3, 100.3, 0.0)
    m.bx("plane", -100.2, 100.2, -5.3, 30.3, 7.2, 8.2, 0.0)        # 薄紫の帯
    # 空側: 大扉と貨物の搬入口
    m.bx("grey", -35, 35, -5.25, -4.9, 0.25, 8.0, 0.02)
    for sx in (-1, 1):
        for c in (45, 60, 75, 90):
            cx = sx * c
            m.bx("charcoal", cx - 5, cx + 5, -5.3, -4.9, 0.25, 5.2, 0.03)
            m.bx("plane", cx - 6, cx + 6, -11, -5, 6.0, 6.3, 0.0)
    # 陸側: トラック搬入口
    for i in range(10):
        c = -81 + 18 * i
        m.bx("charcoal", c - 2.2, c + 2.2, 29.8, 30.3, 0.25, 4.6, 0.03)
        m.bx("truck", c - 3, c + 3, 30, 36, 5.0, 5.3, 0.0)
    # エプロンの目印線
    m.bx("lamp", -100, 100, -32, -31.8, 0.25, 0.28, 0.0)
    m.bx("lamp", -0.15, 0.15, -40, -32, 0.25, 0.28, 0.0)
    # ULD ドーリー（台車 + コンテナ）の列
    uld = ["light", "white", "grey", "plane", "light", "white"]
    for row, (y, x0, n) in enumerate(((-16.0, -88, 25), (-23.5, -62, 18))):
        r = m.part("uld_row%d" % (row + 1), (0, y, 0))  # 列ごとに x 方向へ往復する
        for i in range(n):
            x = x0 + i * 7.0
            r.bx("charcoal", x - 1.6, x + 1.6, y - 1.25, y + 1.25, 0.25, 0.55, 0.0)
            if (i + row) % 5 != 4:
                r.bx(uld[(i * 2 + row) % len(uld)], x - 1.5, x + 1.5, y - 1.1, y + 1.1, 0.55, 2.15, 0.0)
        # 牽引車
        r.bx("truck", x0 - 5.0, x0 - 2.6, y - 0.9, y + 0.9, 0.25, 1.5, 0.04)
    return m


# ---------------------------------------------------------------- 18 コンテナヤード
def build_container_yard():
    m = Mdl("container_yard")
    m.bx("asphalt", -75, 75, -30, 30, 0, 0.3, 0.05)
    # 橙・緑は輸送手段の色なので使わない（灰・白・水色系のみ）
    cols = ["ship", "grey", "white", "light", "ship_dk", "light", "grey", "grey", "white", "ship"]
    for blk, y0 in enumerate((-25.0, 5.0)):
        for r in range(5):
            for c in range(7):
                h = 1 + (c * 3 + r * 5 + blk * 2) % 4
                if (c + r * 2 + blk) % 7 == 0:
                    continue
                x0 = -44.1 + c * 12.6
                for lv in range(h):
                    z0 = 0.3 + lv * 2.6
                    m.bx(cols[(c * 3 + r * 7 + lv * 5 + blk) % len(cols)], x0, x0 + 12.2,
                         y0 + r * 2.7, y0 + r * 2.7 + 2.45, z0, z0 + 2.5, 0.0)
    # 走行レーンの線
    m.bx("lamp", -72, 72, -6.9, -6.8, 0.3, 0.32, 0.0)
    m.bx("lamp", -72, 72, 1.8, 1.9, 0.3, 0.32, 0.0)
    # タイヤ式門型クレーン（RTG）。ブロック A の 5 列をまたぐ。
    # 動く部品: 門型本体（x 方向に走行）、トロリー（本体上で y 方向に横行）、吊り具とコンテナ（両方に追従）
    gx = -12.0
    ylo, yhi = -27.4, -4.4
    gt = m.part("gantry", (gx, -15, 0))
    tr = m.part("trolley", (gx, -15, 18.4))
    sp = m.part("spreader", (gx, -15, 14.9))
    for xf in (gx - 4.5, gx + 4.5):
        for yl in (ylo, yhi):
            gt.bx("light", xf - 0.5, xf + 0.5, yl - 0.5, yl + 0.5, 0.9, 17.0, 0.08)
            gt.bx("charcoal", xf - 0.9, xf + 0.9, yl - 0.7, yl + 0.7, 0.3, 0.9, 0.04)
            for dx in (-0.6, 0.6):
                gt.cyl("rubber", (xf + dx, yl, 0.85), 0.55, 0.5, 'y', 10)
        gt.bx("ship", xf - 0.7, xf + 0.7, ylo - 0.8, yhi + 0.8, 17.0, 18.4, 0.1)
    gt.bx("ship", gx - 4.5, gx + 4.5, ylo - 0.4, ylo + 0.4, 16.2, 17.0, 0.05)
    gt.bx("ship", gx - 4.5, gx + 4.5, yhi - 0.4, yhi + 0.4, 16.2, 17.0, 0.05)
    tr.bx("white", gx - 3.0, gx + 3.0, -17.4, -12.6, 18.4, 19.8, 0.1)    # トロリー
    gt.bx("white", gx + 4.9, gx + 7.1, yhi - 2.2, yhi + 0.8, 12.0, 14.6, 0.1)  # 運転室
    gt.bx("glass", gx + 4.85, gx + 4.95, yhi - 1.9, yhi + 0.5, 12.6, 14.2, 0.0)
    gt.bx("light", gx - 4.0, gx - 1.0, yhi - 2.0, yhi, 17.0, 18.6, 0.1)  # 機械室
    # 吊り具と吊っているコンテナ（上空を通る高さ）
    sp.bx("grey", gx - 6.1, gx + 6.1, -16.2, -13.8, 14.4, 14.9, 0.03)
    sp.bx("ship", gx - 6.1, gx + 6.1, -16.2, -13.8, 11.8, 14.4, 0.03)
    for sx in (-1, 1):
        for sy in (-1, 1):
            sp.bx("charcoal", gx + sx * 5.5 - 0.04, gx + sx * 5.5 + 0.04, -15 + sy * 1.0 - 0.04,
                  -15 + sy * 1.0 + 0.04, 14.9, 18.4, 0.0)
    # 照明塔
    for x, y in ((-70, -26), (70, -26), (-70, 26), (70, 26)):
        m.bx("light", x - 0.25, x + 0.25, y - 0.25, y + 0.25, 0.3, 22, 0.02)
        m.bx("lamp", x - 1.2, x + 1.2, y - 0.5, y + 0.5, 22, 22.5, 0.05)
    return m

MODELS = [("truck", build_truck), ("freight_train", build_train), ("ferry", build_ferry),
          ("cargo_plane", build_plane), ("warehouse", build_warehouse),
          ("terminal", build_terminal), ("port_crane", build_crane),
          ("kei_van", build_kei_van), ("box_2t", build_box2t), ("truck_4t", build_truck4t),
          ("trailer", build_trailer), ("container_12ft", build_container12),
          ("container_ship", build_container_ship), ("roro_ship", build_roro_ship),
          ("passenger_jet", build_passenger_jet), ("cold_storage", build_cold_storage),
          ("air_cargo_terminal", build_air_terminal), ("container_yard", build_container_yard)]
NEW_MODELS = [n for n, _ in MODELS[7:]]  # 今回足した 11 種（コンタクトシートの 2 枚目）


# ---------------------------------------------------------------- アニメーション
# 動く部品は子オブジェクト（ノード）にして、オブジェクトのキーで動かす（ボーンなし）。
# 24 fps、ループ。glTF にはループ指定がないので、最後のキーを最初と同じにしてある。
# deck.gl の ScenegraphLayer では _animations: {'*': {playing: true}} で再生できる。
FPS = 24
ANIM_RANGE = {}  # モデル名: (開始フレーム, 終了フレーム)


def fr(t):
    return 1 + round(t * FPS)


def keys(ob, prop, idx, base, series):
    """series = [(秒, 変化量), ...]。base + 変化量 を idx 番の成分のキーにする。"""
    for t, v in series:
        getattr(ob, prop)[idx] = base + v
        ob.keyframe_insert(prop, index=idx, frame=fr(t))


def bake(objs, name, dur):
    """各オブジェクトのアクションを同じ名前の NLA トラックに移す（書き出しで 1 本のアニメーションにまとまる）。"""
    for o in objs:
        ad = o.animation_data
        a = ad.action
        a.name = name
        tr = ad.nla_tracks.new()
        tr.name = name
        tr.strips.new(a.name, fr(0), a)
        ad.action = None
    return (fr(0), fr(dur))


def part(n, nm):
    return bpy.data.objects[n + "_" + nm]


def anim_crane(n):
    """トロリーが海側へ出て、吊り具が降りて戻り、元の位置へ帰る。12 秒。"""
    tr, sp, cb = part(n, "trolley"), part(n, "spreader"), part(n, "cables")
    dy = [(0, 0), (1, 0), (4, -22), (9.5, -22), (11.5, 0), (12, 0)]
    dz = [(0, 0), (4, 0), (6.5, -9), (7, -9), (9.5, 0), (12, 0)]
    for o in (tr, sp, cb):
        keys(o, "location", 1, -31, dy)
    keys(sp, "location", 2, 21.5, dz)
    keys(cb, "location", 2, 37.2, [(t, 0) for t, _ in dz])
    keys(cb, "scale", 2, 1.0, [(t, -v / 15.7) for t, v in dz])  # ワイヤ長 15.7 m の (1 + 降下量/15.7) 倍
    return [tr, sp, cb], "crane_cycle", 12


def anim_yard(n):
    """RTG が列に沿ってゆっくり走り、トロリーが横行し、戻る。16 秒。"""
    gt, tr, sp = part(n, "gantry"), part(n, "trolley"), part(n, "spreader")
    dx = [(0, 0), (1, 0), (7, 18), (9, 18), (15, 0), (16, 0)]
    dy = [(0, 0), (3, 5), (6, -5), (9, 5), (12, -5), (16, 0)]
    for o in (gt, tr, sp):
        keys(o, "location", 0, -12.0, dx)
    for o in (tr, sp):
        keys(o, "location", 1, -15.0, dy)
    return [gt, tr, sp], "rtg_cycle", 16


WING_OPEN_DEG = 80.0


def anim_wings(n):
    """左右の翼が屋根の中心線を軸に上へ開いて閉じる。8 秒（閉じて止まる・開いて止まる）。"""
    wl, wr = part(n, "wing_l"), part(n, "wing_r")
    th = math.radians(WING_OPEN_DEG)
    keys(wr, "rotation_euler", 1, 0.0, [(0, 0), (1.5, 0), (3.0, -th), (5.0, -th), (6.5, 0), (8, 0)])
    keys(wl, "rotation_euler", 1, 0.0, [(0, 0), (1.5, 0), (3.0, th), (5.0, th), (6.5, 0), (8, 0)])
    return [wl, wr], "wing_open_close", 8


def anim_uld(n):
    """ULD ドーリーの 2 列が逆向きに少し往復する。10 秒。"""
    r1, r2 = part(n, "uld_row1"), part(n, "uld_row2")
    keys(r1, "location", 0, 0.0, [(0, 0), (1, 0), (4.5, 10), (5.5, 10), (9, 0), (10, 0)])
    keys(r2, "location", 0, 0.0, [(0, 0), (1, 0), (4.5, -10), (5.5, -10), (9, 0), (10, 0)])
    return [r1, r2], "uld_shuttle", 10


# 書き出しのサンプル間隔（フレーム）。コンテナヤードは形状が大きく、200 KB に収めるため粗くする
ANIM_STEP = {"container_yard": 4}
ANIMS = {"port_crane": anim_crane, "container_yard": anim_yard, "truck": anim_wings,
         "truck_4t": anim_wings, "air_cargo_terminal": anim_uld}
# 中間の姿勢を撮る時刻（秒）
ANIM_MID = {"port_crane": 6.75, "container_yard": 8.0, "truck": 4.0, "truck_4t": 4.0, "air_cargo_terminal": 5.0}


# ---------------------------------------------------------------- 全体
def reset():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras, bpy.data.actions):
        for d in list(coll):
            coll.remove(d)


def build_all():
    reset()
    sc = bpy.context.scene
    sc.render.fps = FPS
    for i, (n, f) in enumerate(MODELS):
        ob = f().finish()
        ob.location = (0, i * 260.0, 0)  # 見やすいように並べる。書き出し時は原点に戻す
    for n, fn in ANIMS.items():
        objs, name, dur = fn(n)
        ANIM_RANGE[n] = bake(objs, name, dur)
    sc.frame_set(1)
    return stats()


def family(ob):
    return [ob] + list(ob.children_recursive)


def world_corners(ob):
    """子の部品も含めた外形の角（ワールド座標）。"""
    bpy.context.view_layer.update()
    return [o.matrix_world @ Vector(c) for o in family(ob) for c in o.bound_box]


def stats():
    out = {}
    for n, _ in MODELS:
        ob = bpy.data.objects[n]
        cs = world_corners(ob)
        dims = [max(c[k] for c in cs) - min(c[k] for c in cs) for k in range(3)]
        tris = faces = verts = 0
        for o in family(ob):
            o.data.calc_loop_triangles()
            tris += len(o.data.loop_triangles)
            faces += len(o.data.polygons)
            verts += len(o.data.vertices)
        out[n] = {"dims_m": [round(v, 1) for v in dims], "faces": faces,
                  "tris": tris, "verts": verts, "materials": len(ob.data.materials)}
    return out


def export_all(outdir=GLB_DIR):
    os.makedirs(outdir, exist_ok=True)
    sizes = {}
    sc = bpy.context.scene
    for n, _ in MODELS:
        ob = bpy.data.objects[n]
        loc = ob.location.copy()
        ob.location = (0, 0, 0)
        for o in [x for x in bpy.context.view_layer.objects if x is not None]:
            o.select_set(False)
        for o in family(ob):
            o.select_set(True)
        bpy.context.view_layer.objects.active = ob
        if n in ANIM_RANGE:
            sc.frame_start, sc.frame_end = ANIM_RANGE[n]
        sc.frame_set(1)
        p = os.path.join(outdir, n + ".glb")
        bpy.ops.export_scene.gltf(filepath=p, export_format='GLB', use_selection=True,
                                  export_draco_mesh_compression_enable=False,
                                  export_apply=True, export_yup=True,
                                  export_cameras=False, export_lights=False,
                                  export_animations=(n in ANIM_RANGE),
                                  export_animation_mode='NLA_TRACKS',
                                  export_force_sampling=True,
                                  export_frame_step=ANIM_STEP.get(n, 1))
        ob.location = loc
        sizes[n] = os.path.getsize(p)
    sc.frame_set(1)
    return sizes


def render_tiles(outdir, w=960, h=1080, names=None, frames=None):
    """各モデルを 3/4 視点（前方右上）から直交カメラで撮る。names を渡すとその分だけ撮る。
    frames = {名前: フレーム} を渡すと、そのフレームの姿勢で撮る（省略時は 1 = 閉じた姿勢）。"""
    names = names or [n for n, _ in MODELS]
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
    for n in names:
        sc.frame_set((frames or {}).get(n, 1))
        for k, _f in MODELS:
            for o in family(bpy.data.objects[k]):
                o.hide_render = (k != n)
        ob = bpy.data.objects[n]
        loc = ob.location.copy()
        cs = world_corners(ob)
        ctr = sum(cs, Vector()) / len(cs)
        ext = max(max(c[k] for c in cs) - min(c[k] for c in cs) for k in range(3))
        gr.location = (loc.x, loc.y, -0.03)
        gr.scale = (ext * 6, ext * 6, 1)
        xs = [c.dot(right) for c in cs]
        ys = [c.dot(up) for c in cs]
        cr, cu = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        cam.location = right * cr + up * cu + back * (ctr.dot(back) + ext * 2)
        if w >= h:  # 横長の画面では ortho_scale が幅の寸法になる
            cam_d.ortho_scale = max(max(xs) - min(xs), (max(ys) - min(ys)) * w / h) * 1.12
        else:
            cam_d.ortho_scale = max((max(xs) - min(xs)) * h / w, max(ys) - min(ys)) * 1.12  # 縦長の画面では高さの寸法
        sc.render.filepath = os.path.join(outdir, n + ".png")
        bpy.ops.render.render(write_still=True)
    with open(os.path.join(outdir, "stats.json"), "w", encoding="utf-8", newline="\n") as fp:
        json.dump(stats(), fp, ensure_ascii=False)
    for k, _f in MODELS:
        for o in family(bpy.data.objects[k]):
            o.hide_render = False
    sc.frame_set(1)
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
        tdir = argv[argv.index("--tiles") + 1]
        render_tiles(tdir, names=[n for n, _ in MODELS if n not in NEW_MODELS])  # 1 枚目（縦長タイル）
        render_tiles(tdir, 960, 720, names=NEW_MODELS)  # 2 枚目（4:3 タイル）
        # アニメーションの中間の姿勢（contact_sheet.py --anim 用）
        render_tiles(os.path.join(tdir, "anim"), 640, 540, names=list(ANIMS),
                     frames={n: fr(t) for n, t in ANIM_MID.items()})


if bpy.app.background:
    main()
