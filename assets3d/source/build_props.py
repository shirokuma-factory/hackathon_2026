"""荷役・積み込み用の小物 3D モデル（基本キット）を bpy だけで作り、glb に書き出す台本。

使い方（画面なし）:
    blender --background --python assets3d/source/build_props.py -- [--tiles <dir>]

  - assets3d/source/props.blend に保存し、web/models/props/<name>.glb と index.json を書き出す。
  - --tiles を付けると、コンタクトシート用の 3/4 視点の画像を <dir> に出す。
    並べて文字を入れる処理は assets3d/source/contact_sheet_props.py（Pillow）。

向きの決め（build_models.py と同じ）:
  - Blender の座標: +Z が上、+X が右、前方 = -Y。書き出しは Y-up（glTF の前方は +Z）。
  - 原点は地面（床）の中心。単位はメートル（実寸）。
  - index.json の dims_m は [L, W, H]。L は前後（Y）方向、W は左右（X）方向、H は高さ。
  - コンテナ類の扉は後ろ（+Y）側、前面（-Y）は閉じた端面。
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
GLB_DIR = os.path.join(ROOT, "web", "models", "props")
BLEND = os.path.join(HERE, "props.blend")
SHARP_DEG = 35.0

# ---------------------------------------------------------------- 材質
# build_models.py と同じ色に、段ボールとコンテナ用の無彩色を足す（赤は使わない）。
PALETTE = {
    "truck": ("#f59e0b", 0.0, 0.5),    # トラック 橙
    "rail": ("#22c55e", 0.0, 0.5),     # 鉄道 緑
    "rail_dk": ("#15803d", 0.0, 0.55),
    "plane": ("#c4b5fd", 0.0, 0.45),   # 飛行機 薄紫
    "white": ("#f1f5f9", 0.0, 0.5),
    "light": ("#cbd5e1", 0.0, 0.55),
    "grey": ("#94a3b8", 0.1, 0.55),
    "concrete": ("#8896ab", 0.0, 0.85),
    "asphalt": ("#2b3547", 0.0, 0.95),
    "charcoal": ("#273244", 0.2, 0.6),
    "rubber": ("#0f172a", 0.0, 0.9),
    "hub": ("#9ca3af", 0.6, 0.35),
    "lamp": ("#fde68a", 0.0, 0.3),
    "kraft": ("#c8a97e", 0.0, 0.85),     # 段ボール
    "kraft_dk": ("#b8996d", 0.0, 0.85),
    "tape": ("#d9c9a4", 0.0, 0.8),       # 梱包テープ
    "steel": ("#64748b", 0.1, 0.6),      # コンテナ・パレットの無彩色
    "steel_dk": ("#475569", 0.1, 0.65),
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


# ---------------------------------------------------------------- 形の部品（build_models.py の Mdl を写したもの）
CUBE_F = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


class Mdl:
    """面を貯めて、最後に 1 つのメッシュ（材質ごとのスロット付き）にする。"""

    def __init__(s, name):
        s.name = name
        s.V, s.F, s.M, s.S, s.mats = [], [], [], [], []
        s.soft = False  # True の間に足した面は、細い棒やリブ用に滑らか法線にして頂点を減らす

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
            s.S.append(s.soft)
        bm.free()

    def bx(s, m, x0, x1, y0, y1, z0, z1, bevel=0.0, matrix=None, minb=0.01):
        d = min(x1 - x0, y1 - y0, z1 - z0)
        b = min(bevel, d * 0.4)
        if b < minb:  # 小さい面取りは省く（面取りありの箱は頂点数が 4 倍になり、glb が膨らむ）
            b = 0.0
        v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        s.add(v, CUBE_F, m, b, matrix)

    def bxm(s, m, x0, x1, *a, **k):
        """x0..x1（正）と、その左右反転の 2 つを置く。"""
        s.bx(m, x0, x1, *a, **k)
        s.bx(m, -x1, -x0, *a, **k)

    def cyl(s, m, c, r, length, axis='z', seg=16, r2=None):
        r2 = max(r if r2 is None else r2, 0.003)
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

    def beam(s, m, p0, p1, t=0.3, bevel=0.0):
        p0, p1 = Vector(p0), Vector(p1)
        d = p1 - p0
        up = 'X' if abs(d.normalized().z) > 0.99 else 'Z'
        q = d.to_track_quat('Y', up)
        M = Matrix.Translation((p0 + p1) / 2) @ q.to_matrix().to_4x4()
        h = d.length / 2
        s.bx(m, -t / 2, t / 2, -h, h, -t / 2, t / 2, bevel, M)

    def wheel(s, x, y, z, r, w, seg=12, hub=True):
        s.cyl("rubber", (x, y, z), r, w, 'x', seg)
        if hub:
            s.cyl("hub", (x, y, z), r * 0.55, w + 0.02, 'x', 8)

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
        soft = s.S
        for e in bm.edges:
            e.smooth = len(e.link_faces) == 2 and (e.calc_face_angle(0.0) < lim
                                                   or all(soft[f.index] for f in e.link_faces))
        for f in bm.faces:
            f.smooth = True
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(s.name, me)
        c = bpy.data.collections.get("props")
        if c is None:
            c = bpy.data.collections.new("props")
            bpy.context.scene.collection.children.link(c)
        c.objects.link(ob)
        return ob


# ---------------------------------------------------------------- 共通の部品
def add_pallet(m, cx=0.0, cy=0.0, z0=0.0):
    """JIS T11 パレット 1100 x 1100 x 144 mm。前後（±Y）から差せる二方差し。"""
    h, b = 0.144, 0.006
    # 上面デッキ: x 方向に長い板 5 枚
    for i in range(5):
        yc = -0.455 + i * 0.2275
        m.bx("steel", cx - 0.55, cx + 0.55, cy + yc - 0.095, cy + yc + 0.095, z0 + h - 0.022, z0 + h, b, minb=0.005)
    # 桁（3 x 3 の脚）
    for xi in (-0.49, 0.0, 0.49):
        for yi in (-0.49, 0.0, 0.49):
            m.bx("steel_dk", cx + xi - 0.06, cx + xi + 0.06, cy + yi - 0.06, cy + yi + 0.06,
                 z0 + 0.022, z0 + h - 0.022)
    # 下面の根がらみ: y 方向に長い板 3 本（x 列の間をフォークが通る）
    for xi in (-0.49, 0.0, 0.49):
        m.bx("steel", cx + xi - 0.06, cx + xi + 0.06, cy - 0.55, cy + 0.55, z0, z0 + 0.022)


def add_box(m, ex, ey, h, cx, cy, z0, mat_name="kraft", tape=True, tape_x=False, bevel=0.005):
    """段ボール箱。ex は左右、ey は前後。テープは上面の中央を走らせ、端の面に少し垂らす。
    テープの厚み分だけ本体を小さくして、全体の寸法を指定どおりにする。"""
    if not tape:
        m.bx(mat_name, cx - ex / 2, cx + ex / 2, cy - ey / 2, cy + ey / 2, z0, z0 + h, bevel, minb=0.005)
        return
    t = 0.0015
    if tape_x:
        m.bx(mat_name, cx - ex / 2 + t, cx + ex / 2 - t, cy - ey / 2, cy + ey / 2, z0, z0 + h - t, bevel, minb=0.005)
        ex = ex - 2 * t
    else:
        m.bx(mat_name, cx - ex / 2, cx + ex / 2, cy - ey / 2 + t, cy + ey / 2 - t, z0, z0 + h - t, bevel, minb=0.005)
        ey = ey - 2 * t
    zt = z0 + h - t
    drop = min(0.06, h * 0.4)
    if not tape_x:  # テープは Y 方向に走る
        tw = min(0.05, ex * 0.24)
        m.bx("tape", cx - tw / 2, cx + tw / 2, cy - ey / 2 - t, cy + ey / 2 + t, zt, zt + t)
        for sy in (-1, 1):
            a, b = cy + sy * ey / 2, cy + sy * (ey / 2 + t)
            m.bx("tape", cx - tw / 2, cx + tw / 2, min(a, b), max(a, b), zt - drop, zt)
    else:
        tw = min(0.05, ey * 0.24)
        m.bx("tape", cx - ex / 2 - t, cx + ex / 2 + t, cy - tw / 2, cy + tw / 2, zt, zt + t)
        for sx in (-1, 1):
            a, b = cx + sx * ex / 2, cx + sx * (ex / 2 + t)
            m.bx("tape", min(a, b), max(a, b), cy - tw / 2, cy + tw / 2, zt - drop, zt)
    m.soft = False


def add_casting(m, x, y, z0, z1, w=0.178, d=0.162):
    """ISO コンテナの角金具。"""
    m.bx("charcoal", x - w / 2, x + w / 2, y - d / 2, y + d / 2, z0, z1)


# ---------------------------------------------------------------- 1 パレット
def build_pallet():
    m = Mdl("pallet_t11")
    add_pallet(m)
    return m


# ---------------------------------------------------------------- 2 カゴ台車
def build_roll_cage():
    m = Mdl("roll_cage")
    w, l, top = 0.6, 0.8, 1.7
    hx, hy = w / 2, l / 2
    t = 0.013
    # 床の台と車輪（前後左右 4 つ）
    m.bx("steel", -hx, hx, -hy, hy, 0.14, 0.18, 0.006)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * (hx - 0.06), sy * (hy - 0.07)
            m.bx("grey", x - 0.04, x + 0.04, y - 0.045, y + 0.045, 0.125, 0.14, 0.004)
            m.cyl("rubber", (x, y, 0.065), 0.065, 0.032, 'x', 10)
            m.cyl("hub", (x, y, 0.065), 0.03, 0.04, 'x', 8)
    # 柱 4 本
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * (hx - t), sy * (hy - t)
            m.bx("grey", x - t, x + t, y - t, y + t, 0.18, top, 0.003)
    # 上の枠（背面と左右。前面は開ける）
    m.bx("grey", -hx, hx, hy - 2 * t, hy, top - 2 * t, top)
    for sx in (-1, 1):
        x = sx * (hx - t)
        m.bx("grey", x - t, x + t, -hy, hy, top - 2 * t, top)
    # 前面の下端の横桟
    m.bx("grey", -hx, hx, -hy, -hy + 2 * t, 0.18, 0.2)
    # メッシュ状の格子: 横棒と縦棒（細いので滑らか法線にする）
    rod = 0.004
    m.soft = True
    zs = [0.4 + i * 0.3 for i in range(5)]
    for sx in (-1, 1):
        x = sx * (hx - t)
        for z in zs:
            m.bx("light", x - rod, x + rod, -hy + t, hy - t, z - rod, z + rod)
        for k in range(1, 6):
            y = -hy + k * l / 6
            m.bx("light", x - rod, x + rod, y - rod, y + rod, 0.2, top - 2 * t)
    y = hy - t
    for z in zs:
        m.bx("light", -hx + t, hx - t, y - rod, y + rod, z - rod, z + rod)
    for k in range(1, 5):
        x = -hx + k * w / 5
        m.bx("light", x - rod, x + rod, y - rod, y + rod, 0.2, top - 2 * t)
    m.soft = False
    return m


# ---------------------------------------------------------------- 3 段ボール箱
# (名前, L 前後, W 左右, H)。三辺の和が 60..160 cm。
BOXES = [("box_60", 0.25, 0.20, 0.15), ("box_80", 0.35, 0.25, 0.20), ("box_100", 0.40, 0.35, 0.25),
         ("box_120", 0.50, 0.40, 0.30), ("box_140", 0.55, 0.45, 0.40), ("box_160", 0.60, 0.55, 0.45)]


def make_box_builder(name, L, W, H):
    def f():
        m = Mdl(name)
        add_box(m, W, L, H, 0, 0, 0)
        return m
    return f


# ---------------------------------------------------------------- 4 フォークリフト
def build_forklift():
    m = Mdl("forklift")
    # フォーク（前 = -Y）
    for sx in (-1, 1):
        x = sx * 0.28
        m.bx("charcoal", x - 0.06, x + 0.06, -1.8, -0.7, 0.03, 0.07, 0.008)
        m.bx("charcoal", x - 0.06, x + 0.06, -0.78, -0.7, 0.03, 0.62, 0.008)
    # キャリッジとマスト
    m.bx("charcoal", -0.5, 0.5, -0.76, -0.7, 0.1, 0.8, 0.01)
    for sx in (-1, 1):
        m.bx("charcoal", sx * 0.32 - 0.05, sx * 0.32 + 0.05, -0.72, -0.56, 0.0, 2.0, 0.012)
    for z in (0.5, 1.25, 1.95):
        m.bx("charcoal", -0.32, 0.32, -0.7, -0.6, z - 0.04, z + 0.04, 0.008)
    # 車体（側面形を左右に押し出す）
    prof = [(-0.6, 0.22), (1.78, 0.22), (1.78, 1.05), (1.45, 1.1), (0.45, 0.95), (0.45, 0.55), (-0.5, 0.55), (-0.6, 0.45)]
    m.prism("truck", prof, 'yz', -0.52, 0.52, 0.03)
    m.bx("charcoal", -0.53, 0.53, 1.5, 1.8, 0.25, 1.05, 0.04)   # カウンターウエイト
    m.bx("charcoal", -0.4, 0.4, -0.55, 0.45, 0.55, 0.58, 0.01)  # 運転席の床
    # 運転席
    m.bx("charcoal", -0.22, 0.22, 0.12, 0.5, 0.58, 0.66, 0.02)
    m.bx("charcoal", -0.22, 0.22, 0.46, 0.54, 0.66, 1.15, 0.02)
    m.beam("charcoal", (0, -0.35, 0.55), (0, -0.28, 1.08), 0.06)
    m.cyl("charcoal", (0, -0.27, 1.1), 0.17, 0.025, 'z', 14)
    # ヘッドガード
    for sx in (-1, 1):
        for y in (-0.55, 0.55):
            m.bx("truck", sx * 0.5 - 0.03, sx * 0.5 + 0.03, y - 0.03, y + 0.03, 0.55, 2.06, 0.008)
    m.bx("charcoal", -0.56, 0.56, -0.65, 0.62, 2.06, 2.1, 0.01)
    # 前後の車輪と前輪の泥除け
    for sx in (-1, 1):
        m.wheel(sx * 0.49, -0.28, 0.34, 0.34, 0.2, 14)
        m.wheel(sx * 0.44, 1.3, 0.27, 0.27, 0.16, 12)
        m.bx("truck", sx * 0.5 - 0.1, sx * 0.5 + 0.1, -0.68, 0.0, 0.7, 0.76, 0.01)
    return m


# ---------------------------------------------------------------- 5 航空 ULD LD3
def build_uld():
    m = Mdl("uld_ld3")
    hx, H = 0.78, 1.63
    y0, y1 = -0.747, 0.765   # 前面の扉の厚み 18 mm を足して L = 1.53 m
    # 側面形（左下を斜めに欠いた LD3 の輪郭）
    prof = [(-0.45, 0.0), (0.772, 0.0), (0.772, H), (-hx, H), (-hx, 0.5)]
    m.prism("plane", prof, 'xz', y0, y1, 0.02)
    m.bx("charcoal", -0.45, hx, y0, y1, 0.0, 0.1, 0.01)                       # 床
    m.bx("grey", -hx, hx, y0, y1, H - 0.05, H, 0.012)                          # 上縁
    m.bx("light", -0.72, 0.72, y0 - 0.012, y0, 0.18, 1.5)              # 前面のカーテン扉
    for x in (-0.45, 0.0, 0.45):
        m.bx("grey", x - 0.015, x + 0.015, y0 - 0.018, y0 - 0.01, 0.14, 1.56)
    for z in (0.5, 1.0):
        m.bx("grey", 0.772, hx, y0 + 0.05, y1 - 0.05, z - 0.015, z + 0.015)
    return m


# ---------------------------------------------------------------- 6 海上コンテナ 20ft
def corrugate_x(m, mat_name, xs, y0, y1, z0, z1, depth, pitch):
    """側面の波形。x=xs（壁面）から外向きに depth 張り出す台形の歯を y0..y1 に並べる。"""
    sgn = 1 if xs > 0 else -1
    n = int(round((y1 - y0) / pitch))
    p = (y1 - y0) / n
    m.soft = True
    for i in range(n):
        ya = y0 + i * p
        pts = [(xs, ya), (xs + sgn * depth, ya + p * 0.2), (xs + sgn * depth, ya + p * 0.8), (xs, ya + p)]
        m.prism(mat_name, pts, 'xy', z0, z1)
    m.soft = False


def corrugate_y(m, mat_name, ys, x0, x1, z0, z1, depth, pitch):
    """前面（-Y）の波形。"""
    n = int(round((x1 - x0) / pitch))
    p = (x1 - x0) / n
    m.soft = True
    for i in range(n):
        xa = x0 + i * p
        pts = [(xa, ys), (xa + p * 0.2, ys - depth), (xa + p * 0.8, ys - depth), (xa + p, ys)]
        m.prism(mat_name, pts, 'xy', z0, z1)
    m.soft = False


def iso_frame(m, hx, hy, H):
    """床枠・屋根枠・角金具の共通部分。"""
    m.bx("charcoal", -hx, hx, -hy, hy, 0.0, 0.14, 0.01)
    m.bx("steel_dk", -hx, hx, -hy, hy, H - 0.09, H, 0.012)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x, y = sx * (hx - 0.089), sy * (hy - 0.081)
            add_casting(m, x, y, 0.0, 0.118)
            add_casting(m, x, y, H - 0.118, H)


def build_container20():
    m = Mdl("container_20ft")
    hx, hy, H = 1.219, 3.029, 2.591
    m.bx("steel", -1.185, 1.185, -2.995, 2.995, 0.14, H - 0.09)           # 壁の芯
    corrugate_x(m, "steel", 1.185, -2.85, 2.85, 0.16, H - 0.1, 0.034, 0.16)
    corrugate_x(m, "steel", -1.185, -2.85, 2.85, 0.16, H - 0.1, 0.034, 0.16)
    corrugate_y(m, "steel", -2.995, -1.05, 1.05, 0.16, H - 0.1, 0.034, 0.16)
    iso_frame(m, hx, hy, H)
    # 後ろの扉（+Y）: 左右の戸、施錠棒、取っ手
    for sx in (-1, 1):
        m.bx("steel_dk", min(0, sx * 1.19), max(0, sx * 1.19), 2.99, 3.01, 0.14, H - 0.09, 0.006)
        for x in (0.2, 0.55, 0.9, 1.15):
            m.cyl("hub", (sx * x, 3.012, 1.33), 0.013, 2.22, 'z', 6)
        m.bx("steel_dk", sx * 0.6 - 0.04, sx * 0.6 + 0.04, 3.01, 3.029, 1.0, 1.1)
    return m


# ---------------------------------------------------------------- 7 JR 31ft 鉄道コンテナ
def build_container31():
    m = Mdl("container_31ft")
    hx, hy, H = 1.245, 4.705, 2.62
    m.bx("rail", -1.225, 1.225, -4.68, 4.68, 0.14, H - 0.09, 0.02)
    # 側面の縦リブ（等間隔・低い張り出し）
    m.soft = True
    for sx in (-1, 1):
        for i in range(16):
            y = -4.2 + i * 0.56
            m.bx("rail_dk", min(sx * 1.2, sx * 1.245), max(sx * 1.2, sx * 1.245), y - 0.04, y + 0.04, 0.2, H - 0.14)
    for i in range(5):
        x = -0.9 + i * 0.45
        m.bx("rail_dk", x - 0.035, x + 0.035, -4.705, -4.68, 0.2, H - 0.14)
    m.soft = False
    iso_frame(m, hx, hy, H)
    for sx in (-1, 1):
        m.bx("rail_dk", min(0, sx * 1.2), max(0, sx * 1.2), 4.68, 4.705, 0.14, H - 0.09, 0.006)
        for x in (0.2, 0.55, 0.9, 1.1):
            m.cyl("hub", (sx * x, 4.69, 1.35), 0.013, 2.25, 'z', 6)
    return m


# ---------------------------------------------------------------- 8 12ft 冷凍コンテナ
def build_reefer12():
    m = Mdl("reefer_12ft")
    hx, hy, H = 1.225, 1.86, 2.6
    m.bx("white", -1.215, 1.215, -1.6, 1.85, 0.14, H - 0.09, 0.03)
    # 側面の緑の帯（アクセント）
    for sx in (-1, 1):
        m.bx("rail", min(sx * 1.2, sx * 1.225), max(sx * 1.2, sx * 1.225), -1.55, 1.8, 1.3, 1.55, 0.004)
    # 前面（-Y）の冷凍ユニット
    m.bx("light", -1.1, 1.1, -1.84, -1.6, 0.2, 2.35, 0.03)
    m.cyl("charcoal", (0, -1.845, 1.55), 0.42, 0.03, 'y', 14)   # ファンの口
    for z in (0.5, 0.62, 0.74, 0.86):
        m.bx("charcoal", -0.85, 0.85, -1.86, -1.84, z, z + 0.06)  # 吸気の格子
    m.bx("grey", -0.95, 0.95, -1.86, -1.84, 2.05, 2.2)
    m.bx("rail", -1.1, 1.1, -1.86, -1.84, 0.25, 0.32)     # ユニット下の緑の帯
    iso_frame(m, hx, hy, H)
    # 後ろの扉（+Y）
    m.bx("white", -1.2, 1.2, 1.85, 1.86, 0.14, H - 0.09)
    m.bx("steel_dk", -0.008, 0.008, 1.85, 1.86, 0.14, H - 0.09)
    for x in (-0.9, -0.5, 0.5, 0.9):
        m.cyl("hub", (x, 1.847, 1.35), 0.013, 2.2, 'z', 6)
    return m


# ---------------------------------------------------------------- 9 コキ（コンテナ車）
def build_koki():
    m = Mdl("koki_wagon")
    # 台枠: 床板（コンテナを載せる面は z=1.0）と中梁
    m.bx("steel_dk", -1.3, 1.3, -9.8, 9.8, 0.88, 1.0, 0.02)
    m.bx("asphalt", -0.3, 0.3, -9.7, 9.7, 0.6, 0.88)
    for sx in (-1, 1):
        m.bx("rail", min(sx * 1.3, sx * 1.32), max(sx * 1.3, sx * 1.32), -9.8, 9.8, 0.9, 0.99)  # 緑の帯
    # 緩衝器と連結器（両端）
    for sy in (-1, 1):
        m.bx("asphalt", -1.2, 1.2, min(sy * 9.8, sy * 10.0), max(sy * 9.8, sy * 10.0), 0.78, 1.0)
        for sx in (-1, 1):
            m.cyl("hub", (sx * 0.89, sy * 10.1, 0.9), 0.09, 0.2, 'y', 10)
        m.bx("grey", -0.1, 0.1, min(sy * 10.0, sy * 10.2), max(sy * 10.0, sy * 10.2), 0.78, 0.9)
    # ブレーキシリンダー
    m.cyl("grey", (0, 2.0, 0.62), 0.15, 0.7, 'x', 10)
    # 台車 2 つ
    for yc in (-7.2, 7.2):
        m.bx("charcoal", -0.82, 0.82, yc - 0.18, yc + 0.18, 0.64, 0.88)   # 枕梁
        for sx in (-1, 1):
            m.bx("asphalt", min(sx * 0.68, sx * 0.84), max(sx * 0.68, sx * 0.84), yc - 1.4, yc + 1.4, 0.5, 0.64)
            for dy in (-1.05, 1.05):
                m.bx("asphalt", min(sx * 0.64, sx * 0.86), max(sx * 0.64, sx * 0.86), yc + dy - 0.1, yc + dy + 0.1, 0.38, 0.5)
        for dy in (-1.05, 1.05):
            m.cyl("hub", (0, yc + dy, 0.43), 0.06, 1.28, 'x', 6)
            for sx in (-1, 1):
                m.cyl("rubber", (sx * 0.58, yc + dy, 0.43), 0.43, 0.14, 'x', 12)
    # コンテナ止め（緑）
    for y in (-9.5, -4.8, 0.0, 4.8, 9.5):
        for sx in (-1, 1):
            m.bx("rail", sx * 1.16 - 0.045, sx * 1.16 + 0.045, y - 0.045, y + 0.045, 1.0, 1.14)
    return m


# ---------------------------------------------------------------- 10 ドック（搬入口）
def build_dock():
    m = Mdl("dock_leveler")
    m.bx("asphalt", -2.0, 2.0, -0.75, -0.05, 0.0, 0.04, 0.01)             # 前の舗装
    m.bxm("concrete", 1.3, 2.0, -0.05, 0.2, 0.04, 4.5, 0.03)               # 壁の左右
    m.bx("concrete", -1.3, 1.3, -0.05, 0.2, 4.0, 4.5, 0.03)                # まぐさ
    m.bx("concrete", -1.3, 1.3, -0.05, 0.2, 0.04, 1.2, 0.02)               # 開口の下
    m.bx("concrete", -2.0, 2.0, 0.2, 0.75, 0.04, 1.2, 0.02)                # 庫内の床
    m.bx("grey", -2.0, 2.0, 0.2, 0.75, 1.2, 1.22, 0.004)                   # 床の表面
    # ドックシェルター（黒い緩衝材）
    m.bxm("charcoal", 1.3, 1.55, -0.35, -0.05, 1.2, 3.9, 0.04)
    m.bx("charcoal", -1.55, 1.55, -0.35, -0.05, 3.65, 3.95, 0.04)
    # ドアストッパー（ゴムのバンパー）
    m.bxm("rubber", 1.1, 1.3, -0.2, -0.05, 0.95, 1.2, 0.015)
    # ドアパネル（半分下りた状態）とレール
    for z0, z1 in ((1.22, 1.9), (1.9, 2.6)):
        m.bx("white", -1.28, 1.28, 0.02, 0.08, z0, z1 - 0.01, 0.008)
    m.bxm("steel", 1.26, 1.3, -0.03, 0.1, 1.2, 4.0)
    # ドックレベラーの板（庫内の床の高さから前へ傾いて下がる）
    m.prism("steel_dk", [(-0.05, 1.2), (-0.75, 1.1), (-0.75, 1.06), (-0.05, 1.15)], 'yz', -1.05, 1.05, 0.004)
    m.bx("grey", -1.05, 1.05, -0.4, -0.05, 1.17, 1.19, 0.004)
    for x in (-1.0, 1.0):
        m.bx("steel_dk", x - 0.05, x + 0.05, -0.75, -0.05, 0.98, 1.06, 0.004)
    # 状況表示灯（緑）と照明
    m.bx("rail", -1.78, -1.6, -0.1, -0.05, 2.4, 2.55, 0.01)
    m.bx("lamp", -0.12, 0.12, -0.1, -0.05, 4.2, 4.35, 0.01)
    return m


# ---------------------------------------------------------------- 11 カラーコーン
def build_cone():
    m = Mdl("road_cone")
    m.bx("charcoal", -0.18, 0.18, -0.18, 0.18, 0.0, 0.03, 0.01)

    def r(z):
        return 0.145 - 0.115 * (z - 0.03) / 0.67
    for z0, z1, mn, k in ((0.03, 0.25, "truck", 1.0), (0.25, 0.4, "white", 1.03), (0.4, 0.7, "truck", 1.0)):
        m.cyl(mn, (0, 0, (z0 + z1) / 2), r(z0) * k, z1 - z0, 'z', 14, r2=r(z1) * k)
    return m


# ---------------------------------------------------------------- 12 パレット積み
def build_pallet_stack():
    m = Mdl("pallet_stack")
    add_pallet(m)
    z = 0.144
    bh = (1.6 - z) / 5
    for layer in range(5):
        kc = "kraft" if layer % 2 == 0 else "kraft_dk"
        if layer % 2 == 0:   # 横 2 x 縦 3
            ex, ey = 0.52, 0.35
            cells = [(-0.26 + 0.52 * i, -0.35 + 0.35 * j) for i in range(2) for j in range(3)]
        else:                # 横 3 x 縦 2
            ex, ey = 0.35, 0.52
            cells = [(-0.35 + 0.35 * i, -0.26 + 0.52 * j) for i in range(3) for j in range(2)]
        for cx, cy in cells:
            add_box(m, ex - 0.008, ey - 0.008, bh - 0.004, cx, cy, z + 0.002, kc,
                    tape=(layer == 4), tape_x=(layer % 2 == 0), bevel=0.0)
        z += bh
    return m


MODELS = [
    ("pallet_t11", build_pallet, "T11 パレット"),
    ("roll_cage", build_roll_cage, "カゴ台車"),
] + [(n, make_box_builder(n, L, W, H), f"段ボール {n.split('_')[1]}サイズ") for n, L, W, H in BOXES] + [
    ("forklift", build_forklift, "フォークリフト 2.5t"),
    ("uld_ld3", build_uld, "航空コンテナ LD3"),
    ("container_20ft", build_container20, "20ft 海上コンテナ"),
    ("container_31ft", build_container31, "31ft 鉄道コンテナ"),
    ("reefer_12ft", build_reefer12, "12ft 冷凍コンテナ"),
    ("koki_wagon", build_koki, "コキ コンテナ貨車"),
    ("dock_leveler", build_dock, "ドックレベラー（搬入口）"),
    ("road_cone", build_cone, "カラーコーン"),
    ("pallet_stack", build_pallet_stack, "パレット積み（段ボール 30 個）"),
]
NAMES = [n for n, _f, _l in MODELS]
LABEL_JA = {n: l for n, _f, l in MODELS}

# コンテナ・台車の内寸 [L, W, H]（実寸の目安）と、内床の高さ
INNER = {
    "roll_cage": ([0.76, 0.56, 1.52], 0.18),
    "uld_ld3": ([1.43, 1.40, 1.45], 0.10),
    "container_20ft": ([5.90, 2.35, 2.39], 0.14),
    "container_31ft": ([9.25, 2.35, 2.40], 0.14),
    "reefer_12ft": ([3.20, 2.30, 2.20], 0.14),
}
EXTRA = {"koki_wagon": {"deck_top_m": 1.0}, "pallet_t11": {"deck_top_m": 0.144}}


# ---------------------------------------------------------------- 全体
def reset():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
        for d in list(coll):
            coll.remove(d)


def build_all():
    reset()
    for i, (n, f, _l) in enumerate(MODELS):
        ob = f().finish()
        ob.location = (0, i * 40.0, 0)  # 見やすいように並べる。書き出し時は原点に戻す
    return stats()


def stats():
    out = {}
    for n in NAMES:
        ob = bpy.data.objects[n]
        me = ob.data
        me.calc_loop_triangles()
        d = ob.dimensions
        out[n] = {"dims_m": [round(d.y, 3), round(d.x, 3), round(d.z, 3)], "faces": len(me.polygons),
                  "tris": len(me.loop_triangles), "verts": len(me.vertices), "materials": len(me.materials)}
    return out


def export_all(outdir=GLB_DIR):
    os.makedirs(outdir, exist_ok=True)
    sizes = {}
    for n in NAMES:
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


def write_index(st, sizes):
    items = []
    for n in NAMES:
        it = {"name": n, "label_ja": LABEL_JA[n], "file": n + ".glb", "dims_m": st[n]["dims_m"],
              "glb_bytes": sizes[n], "tris": st[n]["tris"]}
        if n in INNER:
            it["inner_m"], it["inner_floor_z_m"] = INNER[n]
        it.update(EXTRA.get(n, {}))
        items.append(it)
    doc = {"note": "dims_m / inner_m は [L(前後=Y), W(左右=X), H]。単位 m。原点は床の中心、前 = glTF +Z。",
           "props": items}
    with open(os.path.join(GLB_DIR, "index.json"), "w", encoding="utf-8", newline="\n") as fp:
        json.dump(doc, fp, ensure_ascii=False, indent=2)
        fp.write("\n")


def render_tiles(outdir, w=768, h=408):
    """各モデルを 3/4 視点（前方右上）から直交カメラで撮る（build_models.py の render_tiles と同じ作り）。"""
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
    cam_d.type, cam_d.clip_start, cam_d.clip_end = 'ORTHO', 0.01, 3000
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
    for n in NAMES:
        for k in NAMES:
            bpy.data.objects[k].hide_render = (k != n)
        ob = bpy.data.objects[n]
        loc = ob.location.copy()
        cs = [loc + Vector(c) for c in ob.bound_box]
        ctr = sum(cs, Vector()) / 8
        ext = max(ob.dimensions)
        gr.location = (loc.x, loc.y, -0.002 * ext)
        gr.scale = (ext * 6, ext * 6, 1)
        xs = [c.dot(right) for c in cs]
        ys = [c.dot(up) for c in cs]
        cr, cu = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        cam.location = right * cr + up * cu + back * (ctr.dot(back) + ext * 2)
        cam_d.ortho_scale = max(max(xs) - min(xs), (max(ys) - min(ys)) * w / h) * 1.12
        sc.render.filepath = os.path.join(outdir, n + ".png")
        bpy.ops.render.render(write_still=True)
    for k in NAMES:
        bpy.data.objects[k].hide_render = False
    for o in (sun, cam, gr):
        bpy.data.objects.remove(o)
    bpy.data.lights.remove(sun_d)
    bpy.data.cameras.remove(cam_d)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    st = build_all()
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    sizes = export_all()
    write_index(st, sizes)
    for n in NAMES:
        print(n, st[n]["dims_m"], st[n]["tris"], "tris", sizes[n], "bytes")
    if "--tiles" in argv:
        render_tiles(argv[argv.index("--tiles") + 1])


if bpy.app.background:
    main()
