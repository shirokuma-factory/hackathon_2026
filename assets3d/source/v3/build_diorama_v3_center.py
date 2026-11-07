"""日本のジオラマ v3（Blender 5.x、Cycles + OptiX）。ランディングページの背景用。
使い方（画面なし）:
  blender -b -P build_diorama_v3.py -- <out dir> --variant golden|morning|dusk [--still W H] [--loop N [--first I --last J]] [--glb] [--blend] [--samples N]
前提: <out>/cache に prep_land_v3.py の出力（land.json, sdf.npy, sdf.png）がある。
  --still : <out>/raw/still_<variant>.png（16bit、後処理前）
  --loop N: N コマの循環動画の素材を <out>/raw/loop_<variant>/f0000.png ... に描く（あるコマは飛ばす）
  --glb   : <out>/glb/japan_diorama_v3.glb（陸 + 都市 + 木 + 道路 + 経路、トラックなし）
座標: 1 度 = 10 単位（経度は cos(36.5°) を掛ける）。
"""
import bpy
import bmesh
import json
import math
import os
import sys
import time
import numpy as np
from mathutils import Vector, Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
from geo_v4_center import *  # noqa

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(argv[0]) if argv else os.path.join(ROOT, "outputs", "hero_v4_center_2026-11-07")
CACHE = os.path.join(OUT, "cache")


def opt(name, default=None, n=1, cast=str):
    if name in argv:
        i = argv.index(name)
        v = [cast(x) for x in argv[i + 1:i + 1 + n]]
        return v if n > 1 else v[0]
    return default


VARIANT = opt("--variant", "real")
LAYOUT = opt("--layout", "center")
SEED = np.random.RandomState(20261107)

# ---------------------------------------------------------------- 照明の 3 種
VARIANTS = {
    # 太陽の高さ・方位[度]、太陽の色と強さ、空の強さ、街灯の強さ、経路の発光、露出、霞の色と量
    "golden": dict(elev=15.0, az=198.0, sun_col="#ffb472", sun=4.4, size=2.6, sky=0.14, air=1.0, dust=1.4,
                   lights=1.6, route=1.3, exposure=0.3, haze="#f6d2a8", haze_amt=0.16),
    "real": dict(elev=17.0, az=198.0, sun_col="#ffc794", sun=3.9, size=2.2, sky=0.16, air=1.0, dust=1.1,
                 lights=0.9, route=1.9, exposure=0.15, haze="#d8cbb8", haze_amt=0.14),
    "morning": dict(elev=34.0, az=168.0, sun_col="#fff0d8", sun=2.7, size=1.6, sky=0.17, air=1.0, dust=0.35,
                    lights=0.1, route=1.3, exposure=0.0, haze="#dbe8f6", haze_amt=0.12),
    "dusk": dict(elev=4.0, az=205.0, sun_col="#ff8a6a", sun=1.5, size=3.2, sky=0.25, air=1.0, dust=2.2,
                 lights=16.0, route=2.2, exposure=0.7, haze="#b79fc9", haze_amt=0.2),
}
V = dict(VARIANTS[VARIANT])
for _k in ("sun", "sky", "exposure", "lights", "route", "elev", "az", "size", "dust"):
    if "--" + _k in argv:
        V[_k] = float(argv[argv.index("--" + _k) + 1])


def hexcol(h, a=1.0):
    h = h.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return (r, g, b, a)


def lin(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def hexlin(h):
    return tuple(lin(hexcol(h)[:3])) + (1.0,)


def hex_np(h):
    return np.array(hexcol(h)[:3])


def mix(a, b, t):
    t = np.asarray(t)[..., None] if np.ndim(t) else t
    return a * (1 - t) + b * t


# ---------------------------------------------------------------- 材質
def new_mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    return m, m.node_tree.nodes, m.node_tree.links, m.node_tree.nodes["Principled BSDF"]


def vc_mat(name, rough=0.85, spec=0.35):
    m, n, l, b = new_mat(name)
    a = n.new("ShaderNodeAttribute")
    a.attribute_name = "Col"
    a.attribute_type = 'GEOMETRY'
    l.new(a.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = rough
    b.inputs["Specular IOR Level"].default_value = spec
    return m


def emit_mat(name, col, strength, rough=0.5):
    m, n, l, b = new_mat(name)
    b.inputs["Base Color"].default_value = hexlin("#050505")
    b.inputs["Roughness"].default_value = rough
    b.inputs["Specular IOR Level"].default_value = 0.0
    b.inputs["Emission Color"].default_value = hexlin(col)
    b.inputs["Emission Strength"].default_value = strength
    return m


def halo_mat(name, col, strength, alpha):
    """経路の周りの淡い発光（幅方向に中心が濃い）。UV の u が幅方向 0..1"""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    n, l = m.node_tree.nodes, m.node_tree.links
    n.clear()
    uv = n.new("ShaderNodeTexCoord")
    sep = n.new("ShaderNodeSeparateXYZ")
    l.new(uv.outputs["UV"], sep.inputs["Vector"])
    a = n.new("ShaderNodeMath"); a.operation = 'SUBTRACT'; a.inputs[1].default_value = 0.5
    l.new(sep.outputs["X"], a.inputs[0])
    b = n.new("ShaderNodeMath"); b.operation = 'MULTIPLY'; b.inputs[1].default_value = 2.0
    l.new(a.outputs[0], b.inputs[0])
    c = n.new("ShaderNodeMath"); c.operation = 'ABSOLUTE'
    l.new(b.outputs[0], c.inputs[0])
    d = n.new("ShaderNodeMath"); d.operation = 'SUBTRACT'; d.inputs[0].default_value = 1.0
    l.new(c.outputs[0], d.inputs[1])
    e = n.new("ShaderNodeMath"); e.operation = 'POWER'; e.inputs[1].default_value = 2.0
    l.new(d.outputs[0], e.inputs[0])
    f = n.new("ShaderNodeMath"); f.operation = 'MULTIPLY'; f.inputs[1].default_value = alpha
    l.new(e.outputs[0], f.inputs[0])
    em = n.new("ShaderNodeEmission"); em.inputs["Color"].default_value = hexlin(col); em.inputs["Strength"].default_value = strength
    tr = n.new("ShaderNodeBsdfTransparent")
    mx = n.new("ShaderNodeMixShader")
    l.new(f.outputs[0], mx.inputs["Fac"])
    l.new(tr.outputs[0], mx.inputs[1])
    l.new(em.outputs[0], mx.inputs[2])
    o = n.new("ShaderNodeOutputMaterial")
    l.new(mx.outputs[0], o.inputs["Surface"])
    m.use_backface_culling = False
    return m


# ---------------------------------------------------------------- メッシュ部品
def make_obj(name, V_, F_, cols=None, mat=None, smooth=False, uv=None, coll=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in V_], [], [tuple(int(i) for i in f) for f in F_])
    if cols is not None:
        ca = me.color_attributes.new("Col", 'FLOAT_COLOR', 'CORNER')
        ca.data.foreach_set("color", np.asarray(cols, dtype=np.float32).reshape(-1))
        me.color_attributes.active_color = ca
    if uv is not None:
        ul = me.uv_layers.new(name="UV")
        ul.data.foreach_set("uv", np.asarray(uv, dtype=np.float32).reshape(-1))
    if mat is not None:
        me.materials.append(mat)
    me.update()
    (me.shade_smooth if smooth else me.shade_flat)()
    ob = bpy.data.objects.new(name, me)
    (coll or bpy.context.scene.collection).objects.link(ob)
    return ob


def col4(arr):
    a = np.asarray(arr, dtype=np.float64)
    out = np.ones(a.shape[:-1] + (4,))
    out[..., :3] = lin(a)
    return out


def boxes(cx, cy, z0, w, d, h, rot, colors, roof_boost=1.12, side=0.9, sides_only_top=True):
    """向きの付いた箱を N 個まとめて作る（底面なし）。colors: (N,3) の sRGB。戻り値は頂点・面・角ごとの色"""
    N = len(cx)
    loc = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]], dtype=np.float64) * 0.5
    c, s = np.cos(rot), np.sin(rot)
    V_ = np.zeros((N, 8, 3))
    for k in range(4):
        lx, ly = loc[k, 0] * w, loc[k, 1] * d
        x = cx + c * lx - s * ly
        y = cy + s * lx + c * ly
        V_[:, k, 0], V_[:, k, 1], V_[:, k, 2] = x, y, z0
        V_[:, k + 4, 0], V_[:, k + 4, 1], V_[:, k + 4, 2] = x, y, z0 + h
    base = (np.arange(N) * 8)[:, None]
    quads = np.array([[4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]])
    F_ = (quads[None] + base[:, None]).reshape(-1, 4)
    cc = np.asarray(colors, dtype=np.float64)
    fcol = np.zeros((N, 5, 3))
    fcol[:, 0] = np.clip(cc * roof_boost, 0, 1)
    shade = np.array([0.0, 0.88, 0.8, 0.9, 0.82])
    for k in range(1, 5):
        fcol[:, k] = cc * shade[k] * side / 0.9
    cornercols = np.repeat(fcol.reshape(-1, 3), 4, axis=0)
    return V_.reshape(-1, 3), F_, col4(cornercols)


def join(parts):
    Vs, Fs, Cs, off = [], [], [], 0
    for v, f, c in parts:
        Vs.append(v)
        Fs.append(f + off)
        Cs.append(c)
        off += len(v)
    return np.vstack(Vs), np.vstack(Fs), np.vstack(Cs)


def ribbon_arrays(P, width, thick=0.0, z_off=0.0, with_uv=False, s0=0.0):
    """経路 P(N,3) に沿った帯。上面のみ（thick>0 なら側面と下面もつける）。u が幅方向"""
    N = len(P)
    T = np.gradient(P[:, :2], axis=0)
    T /= np.maximum(np.hypot(T[:, 0], T[:, 1]), 1e-9)[:, None]
    side = np.stack([-T[:, 1], T[:, 0]], 1)
    L = P.copy(); R = P.copy()
    L[:, :2] += side * width / 2
    R[:, :2] -= side * width / 2
    L[:, 2] += z_off; R[:, 2] += z_off
    Vs = np.vstack([L, R])
    F_ = [(i, N + i, N + i + 1, i + 1) for i in range(N - 1)]
    uv = None
    if with_uv:
        uv = []
        for (a, b, c, d) in F_:
            uv += [(0, 0), (1, 0), (1, 1), (0, 1)]
    if thick > 0:
        Vb = Vs.copy(); Vb[:, 2] -= thick
        Vs = np.vstack([Vs, Vb])
        M = 2 * N
        for i in range(N - 1):
            F_.append((i, i + 1, M + i + 1, M + i))              # 左の側面
            F_.append((N + i + 1, N + i, M + N + i, M + N + i + 1))  # 右の側面
        F_.append((0, M, M + N, N))
        F_.append((N - 1, 2 * N - 1, M + 2 * N - 1, M + N - 1))
    return Vs, np.array(F_), uv


# ---------------------------------------------------------------- 陸
def land_mat():
    """頂点色 + 森の部分に細かい樹冠の濃淡と凹凸（ワールド座標のノイズ）"""
    m, n, l, b = new_mat("land")
    at = n.new("ShaderNodeAttribute"); at.attribute_name = "Col"; at.attribute_type = 'GEOMETRY'
    geo = n.new("ShaderNodeNewGeometry")
    n1 = n.new("ShaderNodeTexNoise"); n1.inputs["Scale"].default_value = 11.0; n1.inputs["Detail"].default_value = 6.0
    n2 = n.new("ShaderNodeTexNoise"); n2.inputs["Scale"].default_value = 46.0; n2.inputs["Detail"].default_value = 5.0
    l.new(geo.outputs["Position"], n1.inputs["Vector"]); l.new(geo.outputs["Position"], n2.inputs["Vector"])
    mm = n.new("ShaderNodeMath"); mm.operation = 'MULTIPLY'
    l.new(n1.outputs["Fac"], mm.inputs[0]); l.new(n2.outputs["Fac"], mm.inputs[1])
    ramp = n.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.14; ramp.color_ramp.elements[0].color = (0.50, 0.52, 0.48, 1)
    ramp.color_ramp.elements[1].position = 0.40; ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1)
    l.new(mm.outputs[0], ramp.inputs["Fac"])
    mx = n.new("ShaderNodeMix"); mx.data_type = 'RGBA'; mx.blend_type = 'MULTIPLY'
    l.new(at.outputs["Alpha"], mx.inputs[0])
    l.new(at.outputs["Color"], mx.inputs[6]); l.new(ramp.outputs["Color"], mx.inputs[7])
    l.new(mx.outputs[2], b.inputs["Base Color"])
    bp = n.new("ShaderNodeBump"); bp.inputs["Distance"].default_value = 0.05
    l.new(n2.outputs["Fac"], bp.inputs["Height"]); l.new(at.outputs["Alpha"], bp.inputs["Strength"])
    l.new(bp.outputs["Normal"], b.inputs["Normal"])
    b.inputs["Roughness"].default_value = 0.92
    b.inputs["Specular IOR Level"].default_value = 0.2
    return m


FIELD = np.load(os.path.join(CACHE, "sdf.npy")).astype(np.float32)
import geo_v4_center as geo_v3
geo_v3.FIELD_REF[0] = FIELD
geo_v3.DEM_REF[0] = np.load(os.path.join(CACHE, "dem.npy"))
LAND = json.load(open(os.path.join(CACHE, "land.json")))


def pref_color(idx):
    tints = ["#c8d29a", "#cfd39c", "#c0d09b", "#d3d4a0", "#c4cf9f", "#cbd29a"]
    if idx in KANTO:
        base = hex_np("#dccf97")
    elif idx in KANSAI:
        base = hex_np("#b0d48b")
    else:
        base = hex_np(tints[(idx * 3 + idx // 5) % len(tints)])
    r = np.random.RandomState(idx * 31 + 7)
    return np.clip(base * (1 + (r.rand(3) - 0.5) * 0.06), 0, 1)


def build_land():
    t0 = time.time()
    verts, faces, ring_pref = {}, [], []
    plist = []
    for p in LAND["polys"]:
        idxs = []
        for x, y in p["ring"]:
            k = (round(x, 4), round(y, 4))
            if k not in verts:
                verts[k] = len(verts)
            if not idxs or idxs[-1] != verts[k]:
                idxs.append(verts[k])
        if idxs[0] == idxs[-1]:
            idxs.pop()
        if len(idxs) >= 3:
            faces.append(idxs)
            ring_pref.append(p["pref"])
    bpts = np.array(list(verts.keys()))
    # 内部の点（海岸から離して、ゆるくずらした格子）
    gx, gy = np.meshgrid(np.arange(-90, 80, 0.3), np.arange(-60, 98, 0.3))
    gx = gx.ravel() + (SEED.rand(gx.size) - 0.5) * 0.17
    gy = gy.ravel() + (SEED.rand(gy.size) - 0.5) * 0.17
    sd = sample_field(FIELD, gx, gy)
    keep = sd > 0.2
    ip = np.stack([gx[keep], gy[keep]], 1)
    allp = np.vstack([bpts, ip])
    t1 = time.time()
    res = geometry_cdt(allp, faces)
    outV, outF, orig_faces = res
    print("CDT verts", len(outV), "tris", len(outF), "%.1fs" % (time.time() - t1))
    outV = np.array([(v.x, v.y) for v in outV])
    tris = np.array(outF)
    pref = np.array([ring_pref[of[0]] if len(of) else 0 for of in orig_faces])
    # 高さ
    h, sdf = height(outV[:, 0], outV[:, 1], FIELD)
    rel = relief(outV[:, 0], outV[:, 1], sdf)
    eps = 0.25
    hx = relief(outV[:, 0] + eps, outV[:, 1], sdf) - relief(outV[:, 0] - eps, outV[:, 1], sdf)
    hy = relief(outV[:, 0], outV[:, 1] + eps, sdf) - relief(outV[:, 0], outV[:, 1] - eps, sdf)
    slope = np.hypot(hx, hy) / (2 * eps)
    e = np.clip(rel / HMAX, 0, 1.2)
    cw = city_weight(outV[:, 0], outV[:, 1])
    fn = fbm(outV[:, 0] * 0.9, outV[:, 1] * 0.9, 3, 5)
    # 三角形の向きをそろえる（上向き）
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    cross = (outV[b, 0] - outV[a, 0]) * (outV[c, 1] - outV[a, 1]) - (outV[b, 1] - outV[a, 1]) * (outV[c, 0] - outV[a, 0])
    flip = cross < 0
    tris[flip] = tris[flip][:, [0, 2, 1]]
    nV = len(outV)
    top = np.column_stack([outV, h])
    # 側面（海岸）
    ed = {}
    for t in tris:
        for i in range(3):
            ed[(int(t[i]), int(t[(i + 1) % 3]))] = 1
    bnd = [(a_, b_) for (a_, b_) in ed if (b_, a_) not in ed]
    bot = np.column_stack([outV, np.full(nV, 0.0)])
    Vall = np.vstack([top, bot])
    walls = np.array([(a_, a_ + nV, b_ + nV, b_) for a_, b_ in bnd])
    Fall = tris.tolist() + (walls.tolist() if len(walls) else [])
    # 頂点ごとの色（衛星写真に近い、彩度を抑えた自然な配色）
    tv = tris
    def vs(arr):
        return arr[tv]
    X, Y = outV[tv][..., 0], outV[tv][..., 1]
    ee = vs(e) + 0.22 * (fbm(X * 0.9, Y * 0.9, 3, 5) - 0.5)
    sl = vs(slope); sd_ = vs(sdf); cw_ = np.clip(vs(cw), 0, 1)
    n1 = fbm(X * 0.30, Y * 0.30, 4, 51)
    n2 = fbm(X * 1.7, Y * 1.7, 3, 61)
    n3 = fbm(X * 0.08 + 3, Y * 0.08 - 5, 3, 71)
    patch = smoothstep(0.42, 0.58, n1)[..., None]
    farm = hex_np("#a3a672") * (1 - patch) + hex_np("#8e9a63") * patch
    farm = farm * (1 - 0.35 * smoothstep(0.45, 0.65, n3)[..., None]) + hex_np("#bdb68a") * 0.35 * smoothstep(0.45, 0.65, n3)[..., None]
    fa = smoothstep(0.35, 0.65, n2)[..., None]
    forest = (hex_np("#3f5b34") * (1 - fa) + hex_np("#52703c") * fa)
    forest = forest * (1 - 0.3 * smoothstep(0.5, 0.7, n3)[..., None]) + hex_np("#36502f") * 0.3 * smoothstep(0.5, 0.7, n3)[..., None]
    fw = smoothstep(0.035, 0.13, ee + 0.07 * (n1 - 0.5) + 0.3 * smoothstep(0.08, 0.4, sl))
    fw = np.clip(fw * (1 - smoothstep(0.78, 1.0, ee) * 0.6), 0, 1)
    col = farm * (1 - fw[..., None]) + forest * fw[..., None]
    hi = (smoothstep(0.62, 0.98, ee) * 0.65)[..., None]
    col = col * (1 - hi) + hex_np("#767760") * hi
    rock = (smoothstep(0.30, 0.85, sl) * 0.55 * smoothstep(0.25, 0.6, ee))[..., None]
    col = col * (1 - rock) + hex_np("#7f786a") * rock
    cwm = (cw_ * 0.82)[..., None]
    col = col * (1 - cwm) + hex_np("#8f8d88") * cwm
    sand = (smoothstep(0.30, 0.0, sd_) * 0.7)[..., None]
    col = col * (1 - sand) + hex_np("#b9ae8c") * sand
    col = np.clip(col * (0.93 + 0.14 * n2)[..., None], 0, 1)
    tcols = col4(col.reshape(-1, 3)).reshape(-1, 4)
    forest_w = (fw * (1 - cw_)).reshape(-1)
    tcols[:, 3] = forest_w
    wcols = []
    for _ in bnd:
        wcols += [hexlin("#8a806c"), hexlin("#5a5043"), hexlin("#5a5043"), hexlin("#8a806c")]
    allcols = np.vstack([tcols, np.array(wcols).reshape(-1, 4)]) if wcols else tcols
    mat = land_mat()
    ob = make_obj("land", Vall, Fall, allcols, mat, smooth=True)
    # 角を丸める
    bm_ = ob.modifiers.new("bevel", 'BEVEL')
    bm_.width = 0.07; bm_.segments = 2; bm_.limit_method = 'ANGLE'; bm_.angle_limit = math.radians(35); bm_.profile = 0.62
    dg = bpy.context.evaluated_depsgraph_get()
    me2 = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    ob.modifiers.remove(bm_)
    old = ob.data
    ob.data = me2
    bpy.data.meshes.remove(old)
    me2.color_attributes.active_color = me2.color_attributes["Col"]
    with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(42))
    print("LAND tris", len(ob.data.polygons), "%.1fs" % (time.time() - t0))
    # 県境の線
    bparts = []
    ccol = np.tile(hexlin("#7c8668"), (1, 1))
    for ch in LAND["borders"]:
        P = np.array(ch)
        if len(P) < 2:
            continue
        hh, _ = height(P[:, 0], P[:, 1], FIELD)
        Q = np.column_stack([P, hh + 0.045])
        v, f, _uv = ribbon_arrays(Q, 0.07)
        cols_ = np.tile(hexlin("#8a9473"), (len(f) * 4, 1))
        bparts.append((v, f, cols_))
    if False and bparts:
        v, f, c = join(bparts)
        make_obj("borders", v, f, c, vc_mat("border", 0.9, 0.1))
    return ob


def geometry_cdt(allp, faces):
    from mathutils import geometry as g
    pts = [Vector((float(x), float(y))) for x, y in allp]
    res = g.delaunay_2d_cdt(pts, [], faces, 1, 1e-5)
    return res[0], res[2], res[5]


# ---------------------------------------------------------------- 経路
def build_routes():
    hw, u = highway_xy(520)
    i0 = int(np.argmin(np.hypot(hw[:, 0] - proj(135.62, 34.80)[0], hw[:, 1] - proj(135.62, 34.80)[1])))
    i1 = len(hw) - 1
    hw, u = hw[i0:i1 + 1], u[i0:i1 + 1] - u[i0]
    t = u / u[-1]
    T = np.gradient(hw, axis=0)
    T /= np.hypot(T[:, 0], T[:, 1])[:, None]
    nrm = np.stack([-T[:, 1], T[:, 0]], 1)         # 進行方向の左（東へ向かうとき北）
    lanes = {}
    for name, sgn in (("loaded", -1.0), ("empty", 1.0)):
        off = sgn * (1.15 + 1.9 * np.sin(np.pi * t))
        p = geo_v3.push_inland(hw + nrm * off[:, None], 1.0)    # 海にはみ出す点は内陸へ寄せる
        k = 10
        pad = np.pad(p, ((k, k), (0, 0)), mode='edge')
        ker = np.ones(2 * k + 1) / (2 * k + 1)
        lanes[name] = np.stack([np.convolve(pad[:, 0], ker, 'valid'), np.convolve(pad[:, 1], ker, 'valid')], 1)
    # 道路と経路の高さ
    hh, _ = height(hw[:, 0], hw[:, 1], FIELD)
    road = np.column_stack([hw, hh + 0.03])
    zs = {}
    for name, p in lanes.items():
        zz = []
        for dx in (-0.6, 0, 0.6):
            zz.append(height(p[:, 0] + nrm[:, 0] * dx, p[:, 1] + nrm[:, 1] * dx, FIELD)[0])
        z = np.max(zz, axis=0) + 0.17
        k = 20
        z = np.maximum(np.convolve(np.pad(z, k, mode='edge'), np.ones(2 * k + 1) / (2 * k + 1), mode='valid'), np.max(zz, axis=0) + 0.10)
        zs[name] = z
    arc = {n: np.concatenate([[0], np.cumsum(np.hypot(*(np.diff(p, axis=0)).T))]) for n, p in lanes.items()}
    LN = {n: np.column_stack([lanes[n], zs[n]]) for n in lanes}
    # 道路
    v, f, _ = ribbon_arrays(road, 0.46)
    make_obj("highway", v, f, np.tile(hexlin("#6a707b"), (len(f) * 4, 1)), vc_mat("asphalt", 0.7, 0.3))
    v, f, _ = ribbon_arrays(np.column_stack([hw, hh + 0.036]), 0.07)
    make_obj("highway_line", v, f, np.tile(hexlin("#e8e2cf"), (len(f) * 4, 1)), vc_mat("asphalt_line", 0.7, 0.3))
    # 青い実線（実車、関西 → 関東）
    Pb = LN["loaded"]
    n_ = len(Pb)
    head_len = 1.3
    cut = int(np.searchsorted(arc["loaded"], arc["loaded"][-1] - head_len))
    v, f, _ = ribbon_arrays(Pb[:cut + 1], 0.30, thick=0.08)
    make_obj("route_loaded", v, f, None, emit_mat("route_loaded", "#5cc8ff", V["route"]))
    v, f, uv = ribbon_arrays(Pb, 1.5, z_off=-0.10, with_uv=True)
    make_obj("route_loaded_halo", v, f, None, halo_mat("halo_loaded", "#5cc8ff", V["route"] * 0.7, 0.34), uv=uv)
    # 矢じり
    tip = Pb[-1]; pb = Pb[cut]
    tg = (tip - pb)[:2]; tg /= np.hypot(*tg)
    sd2 = np.array([-tg[1], tg[0]])
    a = np.array([pb[0], pb[1], pb[2]]) + np.array([*(sd2 * 0.6), 0])
    b = np.array([pb[0], pb[1], pb[2]]) - np.array([*(sd2 * 0.6), 0])
    tipv = np.array([tip[0] + tg[0] * 0.5, tip[1] + tg[1] * 0.5, tip[2]])
    hv = np.array([a, b, tipv])
    hv2 = hv.copy(); hv2[:, 2] -= 0.13
    vv = np.vstack([hv, hv2])
    ff = [(0, 1, 2), (3, 5, 4), (0, 2, 5, 3), (2, 1, 4, 5), (1, 0, 3, 4)]
    make_obj("route_loaded_head", vv, ff, None, emit_mat("route_loaded", "#5cc8ff", V["route"]))
    # 橙の点線の下の淡い光（空車、関東 → 関西）
    Pe = LN["empty"]
    v, f, uv = ribbon_arrays(Pe, 1.3, z_off=-0.10, with_uv=True)
    make_obj("route_empty_halo", v, f, None, halo_mat("halo_empty", "#f49646", V["route"] * 0.7, 0.30), uv=uv)
    # 節点（円盤）
    nodes = []
    for nm, P in (("node_kansai", hw[0]), ("node_kanto", hw[-1])):
        hz = height(np.array([P[0]]), np.array([P[1]]), FIELD)[0][0]
        segs = 48
        th = np.linspace(0, 2 * np.pi, segs, endpoint=False)
        r1, r0 = 1.6, 1.38
        zt = hz + 0.1
        ring_o = np.column_stack([P[0] + r1 * np.cos(th), P[1] + r1 * np.sin(th), np.full(segs, zt)])
        ring_i = np.column_stack([P[0] + r0 * np.cos(th), P[1] + r0 * np.sin(th), np.full(segs, zt)])
        ring_b = np.column_stack([P[0] + r1 * np.cos(th), P[1] + r1 * np.sin(th), np.full(segs, hz - 0.05)])
        vv = np.vstack([ring_o, ring_i, ring_b])
        ff = []
        for i in range(segs):
            j = (i + 1) % segs
            ff.append((i, j, segs + j, segs + i))            # 上面の縁
            ff.append((j, i, 2 * segs + i, 2 * segs + j))    # 外側面
        make_obj(nm, vv, ff, None, emit_mat(nm, "#f3f6fa", 0.7 if VARIANT != "dusk" else 1.6))
        nodes.append(nm)
    return dict(hw=hw, t=t, LN=LN, arc=arc, Lb=arc["loaded"][-1], Le=arc["empty"][-1])


def build_dash_objs(R):
    """点線の部品を作る。向きは +Y が進行方向。位置は後でコマごとに置く"""
    pts = [(-0.14, -0.38), (0.14, -0.38), (0.14, 0.16), (0.0, 0.40), (-0.14, 0.16)]
    z0, z1 = 0.0, 0.08
    Vs = [(x, y, z0) for x, y in pts] + [(x, y, z1) for x, y in pts]
    n = len(pts)
    F_ = [tuple(range(n, 2 * n)), tuple(range(n - 1, -1, -1))]
    for i in range(n):
        j = (i + 1) % n
        F_.append((i, j, n + j, n + i))
    me = bpy.data.meshes.new("dash")
    me.from_pydata(Vs, [], F_)
    me.update()
    me.shade_flat()
    me.materials.append(emit_mat("route_empty", "#f49646", V["route"] * 1.6))
    P = 1.7
    N = int(math.ceil(R["Le"] / P)) + 1
    objs = []
    for k in range(N):
        ob = bpy.data.objects.new("dash%02d" % k, me)
        bpy.context.scene.collection.objects.link(ob)
        objs.append(ob)
    return objs, P


def lane_at(LN, arc, name, s):
    P = LN[name]
    s = float(np.clip(s, 0, arc[name][-1]))
    x = np.interp(s, arc[name], P[:, 0]); y = np.interp(s, arc[name], P[:, 1]); z = np.interp(s, arc[name], P[:, 2])
    s2 = min(s + 0.3, arc[name][-1]); s1 = max(s - 0.3, 0)
    d = np.array([np.interp(s2, arc[name], P[:, 0]) - np.interp(s1, arc[name], P[:, 0]),
                  np.interp(s2, arc[name], P[:, 1]) - np.interp(s1, arc[name], P[:, 1]),
                  np.interp(s2, arc[name], P[:, 2]) - np.interp(s1, arc[name], P[:, 2])])
    return np.array([x, y, z]), d / np.linalg.norm(d)


def set_pose(ob, pos, fwd, scale, local_fwd_neg_y=False):
    fwd = np.array(fwd, dtype=float)
    fwd[2] = np.clip(fwd[2], -0.2, 0.2)
    f = Vector(fwd).normalized()
    Y = -f if local_fwd_neg_y else f
    Z = Vector((0, 0, 1))
    X = Y.cross(Z)
    if X.length < 1e-6:
        X = Vector((1, 0, 0))
    X.normalize()
    Zo = X.cross(Y).normalized()
    M = Matrix(((X.x, Y.x, Zo.x), (X.y, Y.y, Zo.y), (X.z, Y.z, Zo.z)))
    ob.rotation_mode = 'QUATERNION'
    ob.rotation_quaternion = M.to_quaternion()
    ob.location = Vector(pos)
    ob.scale = (scale, scale, scale)


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


# ---------------------------------------------------------------- 都市・木
def build_cities(strength_lights):
    """細かい格子の小さな建物（灰色）。格子 6〜7 マスごとに道路の隙間。明かりは小さく暖色"""
    rs = np.random.RandomState(11)
    parts, lparts = [], []
    wall_pal = np.array([hex_np(h) for h in ("#aaa9a5", "#b9b7b1", "#999ca0", "#c3bfb5", "#8f9296", "#a7a39a", "#b3b6b9")])
    for ci, (nm, lo, la, r, tall) in enumerate(CITIES):
        cx0, cy0 = proj(lo, la)
        ang = 0.25 + 0.5 * rs.rand()
        sp = 0.19
        R = r * 1.3
        gi = np.arange(-int(R / sp), int(R / sp) + 1)
        ix, iy = np.meshgrid(gi, gi)
        ix, iy = ix.ravel(), iy.ravel()
        street = ((ix % 6) == 0) | ((iy % 7) == 0)
        gx, gy = ix * sp, iy * sp
        rr = np.hypot(gx, gy)
        prob = np.exp(-(rr / (r * 0.95)) ** 2) * 1.15
        sel = (rs.rand(len(gx)) < prob) & (~street)
        gx, gy, rr = gx[sel], gy[sel], rr[sel]
        c, s_ = math.cos(ang), math.sin(ang)
        wx = cx0 + c * gx - s_ * gy
        wy = cy0 + s_ * gx + c * gy
        sd = sample_field(FIELD, wx, wy)
        ok = sd > 0.2
        wx, wy, rr = wx[ok], wy[ok], rr[ok]
        n = len(wx)
        if n == 0:
            continue
        hz, _ = height(wx, wy, FIELD)
        core = np.clip(1 - rr / (r * 1.0), 0, 1)
        ht = 0.035 + 0.05 * rs.rand(n) + tall * (0.38 * core ** 2.0) * rs.rand(n) ** 1.5
        w_ = 0.11 + 0.07 * rs.rand(n)
        d_ = 0.11 + 0.07 * rs.rand(n)
        cols = wall_pal[rs.randint(0, len(wall_pal), n)] * (0.66 + 0.10 * rs.rand(n))[:, None]
        v, f, cc = boxes(wx, wy, hz - 0.08, w_, d_, ht + 0.08, np.full(n, ang), np.clip(cols, 0, 1), roof_boost=1.03)
        parts.append((v, f, cc))
        nl = max(1, int(n * 0.7))
        sel2 = rs.choice(n, nl, replace=True)
        lx = wx[sel2] + (rs.rand(nl) - 0.5) * 0.1
        ly = wy[sel2] + (rs.rand(nl) - 0.5) * 0.1
        lz = hz[sel2] + ht[sel2] * (0.3 + 0.7 * rs.rand(nl)) + 0.01
        lv, lf, lc = boxes(lx, ly, lz, np.full(nl, 0.04), np.full(nl, 0.04), np.full(nl, 0.03), np.full(nl, ang), np.tile(hex_np("#ffcf8a"), (nl, 1)))
        lparts.append((lv, lf, lc))
    v, f, c = join(parts)
    ob = make_obj("city", v, f, c, vc_mat("city", 0.75, 0.3))
    lv, lf, lc = join(lparts)
    lo = make_obj("city_lights", lv, lf, None, emit_mat("city_light", "#ffc77a", strength_lights))
    print("CITY tris", len(ob.data.polygons) * 2, "lights", len(lo.data.polygons))
    return ob, lo


def tree_templates():
    def cone(r, z0, z1, seg=6, rot=0.0):
        th = np.linspace(0, 2 * np.pi, seg, endpoint=False) + rot
        Vb = [(r * math.cos(a), r * math.sin(a), z0) for a in th] + [(0, 0, z1)]
        Fb = [(i, (i + 1) % seg, seg) for i in range(seg)]
        return Vb, Fb
    tpl = {}
    # 針葉樹: 2 段の円錐
    v1, f1 = cone(0.20, 0.06, 0.34, 6)
    v2, f2 = cone(0.145, 0.24, 0.52, 6, 0.3)
    tpl["pine"] = (np.array(v1 + v2, float), np.array(f1 + [(a + 7, b + 7, c + 7) for a, b, c in f2]),
                   np.array([0] * 6 + [1] * 6))
    # 広葉樹: 六角のまるい塊
    seg = 6
    th = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    ring_mid = [(0.2 * math.cos(a), 0.2 * math.sin(a), 0.26) for a in th]
    ring_low = [(0.14 * math.cos(a + 0.5), 0.14 * math.sin(a + 0.5), 0.10) for a in th]
    Vr = ring_low + ring_mid + [(0, 0, 0.44), (0, 0, 0.08)]
    Fr = [(i, (i + 1) % seg, seg + (i + 1) % seg, seg + i) for i in range(seg)]
    Fr = [q for q in Fr]
    tri = []
    for q in Fr:
        tri += [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
    tri += [(seg + i, seg + (i + 1) % seg, 2 * seg) for i in range(seg)]
    tri += [((i + 1) % seg, i, 2 * seg + 1) for i in range(seg)]
    tpl["round"] = (np.array(Vr, float), np.array(tri), np.array([0] * (2 * seg) + [1] * seg + [0] * seg))
    return tpl


def build_trees(max_trees=170000, tight=False):
    """小さな針葉樹・広葉樹を大量に置いて、遠目には樹冠に見えるようにする"""
    rs = np.random.RandomState(5)
    step = float(opt('--treestep', 0.17, cast=float))
    gx, gy = np.meshgrid(np.arange(-90, 80, step), np.arange(-60, 98, step))
    gx = gx.ravel() + (rs.rand(gx.size) - 0.5) * step
    gy = gy.ravel() + (rs.rand(gy.size) - 0.5) * step
    sd = sample_field(FIELD, gx, gy)
    m = sd > 0.5
    gx, gy, sd = gx[m], gy[m], sd[m]
    rel = relief(gx, gy, sd)
    e = rel / HMAX
    n1 = fbm(gx * 0.30, gy * 0.30, 4, 51)
    fw = smoothstep(0.035, 0.13, e + 0.07 * (n1 - 0.5)) * (1 - smoothstep(0.85, 1.1, e) * 0.7)
    dens = fw * (0.7 + 0.8 * fbm(gx * 0.5, gy * 0.5, 3, 21)) + 0.02
    dens *= 1 - smoothstep(0.1, 0.5, city_weight(gx, gy))
    cd = corridor_dist(gx, gy)
    dens *= smoothstep(3.4, 4.7, cd)
    dens *= 1 + 0.7 * np.exp(-((cd - 6.5) / 3.0) ** 2)
    sel = rs.rand(len(gx)) < np.clip(dens, 0, 1) * 0.9
    gx, gy = gx[sel], gy[sel]
    if len(gx) > max_trees:
        k = rs.choice(len(gx), max_trees, replace=False)
        gx, gy = gx[k], gy[k]
    n = len(gx)
    hz, _ = height(gx, gy, FIELD)
    e2 = (relief(gx, gy, sample_field(FIELD, gx, gy)) / HMAX)
    kind = rs.rand(n) < np.clip(0.30 + 0.8 * e2, 0, 0.95)       # True: 針葉樹（背が高く濃い）
    sz = (0.75 + 0.7 * rs.rand(n)) * 0.45
    rot = rs.rand(n) * 6.28
    th = np.array([0, 2.094, 4.189])
    ang = rot[:, None] + th[None, :]
    rad = np.where(kind, 0.085, 0.10)[:, None]
    hh = np.where(kind, 0.34, 0.24)[:, None]
    base = np.stack([gx[:, None] + np.cos(ang) * rad * sz[:, None], gy[:, None] + np.sin(ang) * rad * sz[:, None],
                     (hz - 0.04)[:, None] * np.ones((1, 3))], -1)
    apex = np.stack([gx, gy, hz - 0.04 + hh[:, 0] * sz], -1)[:, None, :]
    VV = np.concatenate([base, apex], 1)                       # n x 4 x 3
    tri = np.array([[0, 1, 3], [1, 2, 3], [2, 0, 3]])
    FF = tri[None] + (np.arange(n) * 4)[:, None, None]
    pal_c = [hex_np(h) for h in ("#3a5632", "#41613a", "#35502e", "#4a6a3b", "#2f4a2d")]
    pal_b = [hex_np(h) for h in ("#506f3c", "#5b7a42", "#47683a", "#6a8148")]
    gi = rs.randint(0, 5, n)
    colc = np.array([pal_c[g] for g in gi])
    colb = np.array([pal_b[g % 4] for g in gi])
    base_col = np.where(kind[:, None], colc, colb) * (0.9 + 0.2 * rs.rand(n))[:, None]
    shade = np.array([1.0, 0.82, 0.9])
    fc = np.clip(base_col[:, None, :] * shade[None, :, None], 0, 1)          # n x 3 faces
    cc = np.repeat(fc, 3, axis=1).reshape(-1, 3)
    ob = make_obj("trees", VV.reshape(-1, 3), FF.reshape(-1, 3), col4(cc), vc_mat("tree", 0.85, 0.2))
    print("TREES", n, "tris", n * 3)
    return ob


# ---------------------------------------------------------------- 海・空・雲
def build_sea():
    bpy.ops.mesh.primitive_plane_add(size=1)
    sea = bpy.context.active_object
    sea.name = "sea"
    sea.scale = (3000, 3000, 1)
    sea.location = (0, 0, Z_SEA)
    m = bpy.data.materials.new("sea")
    m.use_nodes = True
    n, l = m.node_tree.nodes, m.node_tree.links
    bsdf = n["Principled BSDF"]
    geo = n.new("ShaderNodeNewGeometry")
    mp = n.new("ShaderNodeMapping")
    mp.inputs["Location"].default_value = (-FX0 / (FX1 - FX0), -FY0 / (FY1 - FY0), 0)
    mp.inputs["Scale"].default_value = (1 / (FX1 - FX0), 1 / (FY1 - FY0), 1)
    l.new(geo.outputs["Position"], mp.inputs["Vector"])
    im = n.new("ShaderNodeTexImage")
    img = bpy.data.images.load(os.path.join(CACHE, "sdf.png"))
    img.colorspace_settings.name = 'Non-Color'
    im.image = img
    im.interpolation = 'Linear'
    im.extension = 'EXTEND'
    l.new(mp.outputs["Vector"], im.inputs["Vector"])
    sep = n.new("ShaderNodeSeparateColor")
    l.new(im.outputs["Color"], sep.inputs["Color"])
    sdf = n.new("ShaderNodeMath"); sdf.operation = 'MULTIPLY_ADD'
    sdf.inputs[1].default_value = 48.0; sdf.inputs[2].default_value = -16.0
    l.new(sep.outputs["Red"], sdf.inputs[0])
    dist = n.new("ShaderNodeMath"); dist.operation = 'MULTIPLY'; dist.inputs[1].default_value = -1.0; dist.use_clamp = False
    l.new(sdf.outputs[0], dist.inputs[0])
    dpos = n.new("ShaderNodeMath"); dpos.operation = 'MAXIMUM'; dpos.inputs[1].default_value = 0.0
    l.new(dist.outputs[0], dpos.inputs[0])
    fac = n.new("ShaderNodeMath"); fac.operation = 'DIVIDE'; fac.inputs[1].default_value = 22.0; fac.use_clamp = True
    l.new(dpos.outputs[0], fac.inputs[0])
    ramp = n.new("ShaderNodeValToRGB")
    cr = ramp.color_ramp
    cr.interpolation = 'LINEAR'
    stops = [(0.0, "#6fb0a8"), (0.03, "#3f8d95"), (0.10, "#23708e"), (0.26, "#175580"), (0.55, "#0f3c66"), (1.0, "#0a2a4c")]
    cr.elements[0].position, cr.elements[0].color = stops[0][0], hexlin(stops[0][1])
    cr.elements[1].position, cr.elements[1].color = stops[-1][0], hexlin(stops[-1][1])
    for pos, c in stops[1:-1]:
        e = cr.elements.new(pos)
        e.color = hexlin(c)
    l.new(fac.outputs[0], ramp.inputs["Fac"])
    # 泡の線
    noi = n.new("ShaderNodeTexNoise")
    noi.inputs["Scale"].default_value = 2.2
    noi.inputs["Detail"].default_value = 4.0
    l.new(geo.outputs["Position"], noi.inputs["Vector"])
    f1 = n.new("ShaderNodeMapRange")
    f1.inputs["From Min"].default_value = 0.30; f1.inputs["From Max"].default_value = 0.04
    f1.inputs["To Min"].default_value = 0.0; f1.inputs["To Max"].default_value = 1.0
    f1.clamp = True
    l.new(dpos.outputs[0], f1.inputs["Value"])
    nm = n.new("ShaderNodeMath"); nm.operation = 'MULTIPLY_ADD'
    nm.inputs[1].default_value = 0.8; nm.inputs[2].default_value = 0.45
    l.new(noi.outputs["Fac"], nm.inputs[0])
    foam = n.new("ShaderNodeMath"); foam.operation = 'MULTIPLY'
    l.new(f1.outputs["Result"], foam.inputs[0]); l.new(nm.outputs[0], foam.inputs[1])
    foam2 = n.new("ShaderNodeMath"); foam2.operation = 'MULTIPLY'; foam2.inputs[1].default_value = 0.5; foam2.use_clamp = True
    l.new(foam.outputs[0], foam2.inputs[0])
    # 岸から広がる細い波線
    rp = n.new("ShaderNodeMath"); rp.operation = 'MULTIPLY_ADD'; rp.inputs[1].default_value = 13.0
    l.new(dpos.outputs[0], rp.inputs[0])
    nn = n.new("ShaderNodeMath"); nn.operation = 'MULTIPLY'; nn.inputs[1].default_value = 4.5
    l.new(noi.outputs["Fac"], nn.inputs[0])
    rs_ = n.new("ShaderNodeMath"); rs_.operation = 'SUBTRACT'
    l.new(rp.outputs[0], rs_.inputs[0]); l.new(nn.outputs[0], rs_.inputs[1])
    rsin = n.new("ShaderNodeMath"); rsin.operation = 'SINE'
    l.new(rs_.outputs[0], rsin.inputs[0])
    rh = n.new("ShaderNodeMath"); rh.operation = 'MULTIPLY_ADD'; rh.inputs[1].default_value = 0.5; rh.inputs[2].default_value = 0.5
    l.new(rsin.outputs[0], rh.inputs[0])
    rpow = n.new("ShaderNodeMath"); rpow.operation = 'POWER'; rpow.inputs[1].default_value = 10.0
    l.new(rh.outputs[0], rpow.inputs[0])
    rf = n.new("ShaderNodeMapRange")
    rf.inputs["From Min"].default_value = 1.3; rf.inputs["From Max"].default_value = 0.3
    rf.inputs["To Min"].default_value = 0.0; rf.inputs["To Max"].default_value = 0.025; rf.clamp = True
    l.new(dpos.outputs[0], rf.inputs["Value"])
    rl = n.new("ShaderNodeMath"); rl.operation = 'MULTIPLY'
    l.new(rpow.outputs[0], rl.inputs[0]); l.new(rf.outputs["Result"], rl.inputs[1])
    allf = n.new("ShaderNodeMath"); allf.operation = 'ADD'; allf.use_clamp = True
    l.new(foam2.outputs[0], allf.inputs[0]); l.new(rl.outputs[0], allf.inputs[1])
    mixc = n.new("ShaderNodeMix"); mixc.data_type = 'RGBA'
    mixc.inputs[7].default_value = hexlin("#f3fbfa")
    l.new(allf.outputs[0], mixc.inputs[0])
    l.new(ramp.outputs["Color"], mixc.inputs[6])
    l.new(mixc.outputs[2], bsdf.inputs["Base Color"])
    # さざ波の凹凸
    n2 = n.new("ShaderNodeTexNoise")
    n2.inputs["Scale"].default_value = 7.0; n2.inputs["Detail"].default_value = 3.0
    l.new(geo.outputs["Position"], n2.inputs["Vector"])
    bump = n.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.14; bump.inputs["Distance"].default_value = 0.2
    l.new(n2.outputs["Fac"], bump.inputs["Height"])
    l.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = 0.14
    bsdf.inputs["Specular IOR Level"].default_value = 0.7
    sea.data.materials.append(m)
    return sea


def build_world():
    sc = bpy.context.scene
    wd = bpy.data.worlds.new("w")
    wd.use_nodes = True
    n, l = wd.node_tree.nodes, wd.node_tree.links
    bg = n["Background"]
    sky = n.new("ShaderNodeTexSky")
    sky.sky_type = 'MULTIPLE_SCATTERING'
    sky.sun_disc = False
    sky.sun_elevation = math.radians(V["elev"])
    sky.sun_rotation = math.radians(90 - V["az"])
    sky.air_density = V["air"]; sky.aerosol_density = V["dust"]; sky.ozone_density = 1.0
    l.new(sky.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = V["sky"]
    sc.world = wd
    # 太陽
    sun = bpy.data.lights.new("sun", 'SUN')
    sun.energy = V["sun"]
    sun.angle = math.radians(V["size"])
    sun.color = hexcol(V["sun_col"])[:3]
    so = bpy.data.objects.new("sun", sun)
    el, az = math.radians(V["elev"]), math.radians(V["az"])
    toward = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
    so.rotation_euler = (-toward).to_track_quat('-Z', 'Y').to_euler()
    sc.collection.objects.link(so)


CLOUD_D = 7.0          # 循環 1 回で雲が流れる距離（地面の単位）。ゆっくり
LAYOUTS = {
    # res: 出力の解像度、az/pitch: カメラの向き、width: 画面の横幅が地面の何単位か、ts: 経路の中心を置く画面位置（左から, 上から）
    "center": dict(res=(2560, 1440), az=22.0, pitch=41.0, width=112.0, ts=(0.5, 0.25),
                   clouds=[(0.12, 0.52, 2.6, 1.8), (0.90, 0.60, 2.6, 1.9), (0.20, 0.86, 2.6, 1.8)]),
    "portrait": dict(res=(1080, 1920), az=20.0, pitch=46.0, width=60.0, ts=(0.5, 0.20),
                     clouds=[(0.18, 0.60, 2.6, 1.0), (0.84, 0.74, 2.6, 1.1)]),
}
LAY = LAYOUTS[LAYOUT]


def cloud_mat():
    m = bpy.data.materials.new("cloud")
    m.use_nodes = True
    n, l = m.node_tree.nodes, m.node_tree.links
    b = n["Principled BSDF"]
    b.inputs["Base Color"].default_value = hexlin("#fff4e6")
    b.inputs["Roughness"].default_value = 1.0
    b.inputs["Emission Color"].default_value = hexlin("#ffeedd")
    b.inputs["Emission Strength"].default_value = 0.75
    geo = n.new("ShaderNodeNewGeometry")
    dt = n.new("ShaderNodeVectorMath"); dt.operation = 'DOT_PRODUCT'
    l.new(geo.outputs["Normal"], dt.inputs[0]); l.new(geo.outputs["Incoming"], dt.inputs[1])
    ab = n.new("ShaderNodeMath"); ab.operation = 'ABSOLUTE'
    l.new(dt.outputs["Value"], ab.inputs[0])
    mr = n.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = 0.12; mr.inputs["From Max"].default_value = 0.85
    mr.clamp = True
    l.new(ab.outputs[0], mr.inputs["Value"])
    oi = n.new("ShaderNodeObjectInfo")
    m1 = n.new("ShaderNodeMath"); m1.operation = 'MULTIPLY'
    l.new(mr.outputs["Result"], m1.inputs[0]); l.new(oi.outputs["Alpha"], m1.inputs[1])
    tc = n.new("ShaderNodeTexCoord")
    mp = n.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (0.5, 1.4, 2.0)
    l.new(tc.outputs["Object"], mp.inputs["Vector"])
    nz = n.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 1.1; nz.inputs["Detail"].default_value = 5.0
    l.new(mp.outputs["Vector"], nz.inputs["Vector"])
    nr = n.new("ShaderNodeMapRange"); nr.inputs["From Min"].default_value = 0.42; nr.inputs["From Max"].default_value = 0.60; nr.inputs["To Min"].default_value = 0.04; nr.clamp = True
    l.new(nz.outputs["Fac"], nr.inputs["Value"])
    m3 = n.new("ShaderNodeMath"); m3.operation = 'MULTIPLY'
    l.new(m1.outputs[0], m3.inputs[0]); l.new(nr.outputs["Result"], m3.inputs[1])
    m2 = n.new("ShaderNodeMath"); m2.operation = 'MULTIPLY'; m2.inputs[1].default_value = 0.15
    l.new(m3.outputs[0], m2.inputs[0])
    l.new(m2.outputs[0], b.inputs["Alpha"])
    return m


def build_clouds():
    """薄い層状の雲。平たい楕円体を重ねて、縁ほど透ける。循環のため、位相をずらした 2 つを交互に出す"""
    rs = np.random.RandomState(3)
    mat = cloud_mat()
    out = []
    for ci, (nx, ny, z, size) in enumerate(LAY["clouds"]):
        bm = bmesh.new()
        for _ in range(11):
            r = 1.0
            m = bmesh.ops.create_icosphere(bm, subdivisions=3, radius=r)
            bmesh.ops.scale(bm, verts=m["verts"], vec=(2.4 + 2.4 * rs.rand(), 0.7 + 0.8 * rs.rand(), 0.35 + 0.2 * rs.rand()))
            bmesh.ops.translate(bm, verts=m["verts"], vec=((rs.rand() - 0.5) * 12, (rs.rand() - 0.5) * 3.0, (rs.rand() - 0.5) * 0.5))
        me = bpy.data.meshes.new("cloud")
        bm.to_mesh(me); bm.free()
        me.materials.append(mat)
        me.shade_smooth()
        for off in (0.0, 0.5):
            ob = bpy.data.objects.new("cloud", me)
            bpy.context.scene.collection.objects.link(ob)
            ob.scale = (size,) * 3
            ob.rotation_euler = (0, 0, math.radians(LAY["az"]))
            ob["off"] = off
            ob["anchor_s"] = (nx, ny, z)
            out.append(ob)
    return out


CAMD = {}


def build_camera():
    sc = bpy.context.scene
    cam = bpy.data.cameras.new("cam")
    cam.sensor_fit = 'HORIZONTAL'
    cam.sensor_width = 36
    cam.clip_start, cam.clip_end = 50, 3000
    co = bpy.data.objects.new("cam", cam)
    sc.collection.objects.link(co)
    sc.camera = co
    CAMD.update(co=co, cam=cam)


def aim(tgt_xy, az_deg, pitch, width, dist=600.0):
    co, cam = CAMD["co"], CAMD["cam"]
    W, H = LAY["res"]
    az, el = math.radians(az_deg), math.radians(pitch)
    tgt = Vector((tgt_xy[0], tgt_xy[1], 0.8))
    d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    co.location = tgt + d * dist
    co.rotation_euler = (tgt - co.location).to_track_quat('-Z', 'Y').to_euler()
    cam.lens = 36.0 * dist / width
    nx, ny = LAY["ts"]
    cam.shift_x = 0.5 - nx
    cam.shift_y = (ny - 0.5) * H / W


def screen_to_ground(nx, ny, z):
    sc = bpy.context.scene
    bpy.context.view_layer.update()
    co, cam = CAMD["co"], CAMD["cam"]
    fr = cam.view_frame(scene=sc)
    xs = [v.x for v in fr]; ys = [v.y for v in fr]; zf = fr[0].z
    p = Vector((min(xs) + nx * (max(xs) - min(xs)), max(ys) - ny * (max(ys) - min(ys)), zf))
    wp = co.matrix_world @ p
    o = co.matrix_world.translation
    dvec = (wp - o).normalized()
    t = (z - o.z) / dvec.z
    return o + dvec * t


def setup_render(w, h, samples):
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
    cy = sc.cycles
    cy.device = 'GPU'
    prefs = bpy.context.preferences.addons['cycles'].preferences
    prefs.compute_device_type = 'OPTIX'
    prefs.get_devices()
    for d in prefs.devices:
        d.use = (d.type == 'OPTIX')
    cy.samples = samples
    cy.use_adaptive_sampling = True
    cy.adaptive_threshold = float(opt('--thr', 0.006, cast=float))
    cy.use_denoising = True
    cy.denoiser = opt('--denoiser', 'OPTIX')
    cy.max_bounces = int(opt('--bounces', 5, cast=int))
    cy.diffuse_bounces = min(3, cy.max_bounces)
    cy.glossy_bounces = min(3, cy.max_bounces)
    cy.transparent_max_bounces = int(opt('--tbounces', 12, cast=int))
    cy.use_animated_seed = False
    cy.sample_clamp_indirect = 10.0
    sc.view_settings.view_transform = 'Standard'
    sc.view_settings.look = 'None'
    sc.view_settings.exposure = V["exposure"]
    sc.display_settings.display_device = 'sRGB'
    sc.render.image_settings.file_format = 'PNG'
    sc.render.image_settings.color_depth = '16'
    sc.render.image_settings.color_mode = 'RGB'


def load_truck(name, tag):
    p = os.path.join(ROOT, "web", "models", "v2", name + ".glb")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=p)
    new = [o for o in bpy.data.objects if o not in before]
    for o in new:
        for m in getattr(o.data, "materials", []) if hasattr(o, "data") and o.data is not None else []:
            if m and m.name.startswith("v2_accent") and m.use_nodes:
                bc = m.node_tree.nodes["Principled BSDF"].inputs["Base Color"]
                tgt = bc
                if bc.links and bc.links[0].from_node.bl_idname == 'ShaderNodeMix':
                    for i in (7, 6):
                        if not bc.links[0].from_node.inputs[i].links:
                            tgt = bc.links[0].from_node.inputs[i]
                            break
                tgt.default_value = hexlin("#aebbd0")
    root = bpy.data.objects.new("truck_" + tag, None)
    bpy.context.scene.collection.objects.link(root)
    for o in new:
        if o.parent is None:
            o.parent = root
    return root


TRUCK_SCALE = 0.6


def main():
    t00 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    build_world()
    sea = build_sea()
    land = build_land()
    R = build_routes()
    cities, lights = build_cities(V["lights"])
    trees = build_trees(max_trees=int(opt("--trees", 170000, cast=int)))
    dashes, DP = build_dash_objs(R)
    build_camera()
    do_glb = "--glb" in argv
    PLATE = "--plate" in argv
    clouds = [] if (do_glb or PLATE) else build_clouds()
    trucks = []
    if PLATE:
        for o in list(bpy.data.objects):
            if o.name.startswith(("highway", "route_", "node_", "dash")):
                bpy.data.objects.remove(o)
        dashes = []
    if not do_glb and not PLATE:
        for tag, nm in (("L1", "truck_10t_loaded"), ("L2", "truck_10t_loaded"), ("L3", "truck_10t_loaded"), ("E1", "truck_10t_empty")):
            trucks.append((tag, load_truck(nm, tag)))
    print("SCENE built %.1fs" % (time.time() - t00))

    LN, arc = R["LN"], R["arc"]
    Lb, Le = R["Lb"], R["Le"]

    def pose(u, still=False):
        # トラック: 実車は青の線の上を関西 → 関東、空車は橙の線の上を関東 → 関西
        ph = {"L1": 0.08, "L2": 0.41, "L3": 0.74, "E1": 0.2}
        if still:
            ph = {"L1": 0.17, "L2": 0.50, "L3": 0.84, "E1": 0.68}
            u = 0.0
        for tag, root in trucks:
            f = (u + ph[tag]) % 1.0
            if tag.startswith("L"):
                s = f * Lb
                pos, fw = lane_at(LN, arc, "loaded", s)
                sc_ = TRUCK_SCALE * ease(min(f / 0.07, (1 - f) / 0.07))
            else:
                s = (1 - f) * Le
                pos, fw = lane_at(LN, arc, "empty", s)
                fw = -fw
                sc_ = TRUCK_SCALE * ease(min(f / 0.07, (1 - f) / 0.07))
            set_pose(root, pos, fw, max(sc_, 1e-4), local_fwd_neg_y=True)
        # 点線: 関東（lane の終端）側から関西側へ流れる
        for k, ob in enumerate(dashes):
            sg = (k + u) * DP
            if sg > Le:
                ob.scale = (1e-4,) * 3
                continue
            s = Le - sg
            pos, fw = lane_at(LN, arc, "empty", s)
            fw = -fw
            fz = ease(min(sg / 1.6, (Le - sg) / 1.6))
            set_pose(ob, pos + np.array([0, 0, 0.0]), fw, max(fz, 1e-4))

    AZR = math.radians(LAY["az"])
    Rv = np.array([math.cos(AZR), math.sin(AZR)])
    hwp = R["hw"]
    cam_t = ((hwp[0][0] + hwp[-1][0]) / 2, (hwp[0][1] + hwp[-1][1]) / 2)

    def cam_at(u):
        a = 2 * math.pi * u
        aim((cam_t[0] + 0.6 * math.sin(a), cam_t[1] + 0.4 * math.cos(a)), LAY["az"] + 1.0 * math.sin(a),
            LAY["pitch"] + 0.4 * math.cos(a), LAY["width"] + 1.0 * math.sin(a))

    cam_at(0.0)
    anchors = {}
    for ob in clouds:
        nx, ny, z = ob["anchor_s"]
        g = screen_to_ground(nx, ny, z)
        anchors[ob.name] = (g.x, g.y, g.z)
        ob["ax"], ob["ay"], ob["az_"] = g.x, g.y, g.z

    def clouds_at(u):
        for ob in clouds:
            ph = (u + ob["off"]) % 1.0
            al = math.sin(math.pi * ph) ** 2
            ob.color = (1, 1, 1, al)
            s_ = (ph - 0.5) * CLOUD_D
            ob.location = (ob["ax"] + Rv[0] * s_, ob["ay"] + Rv[1] * s_, ob["az_"])

    if "--blend" in argv:
        pose(0.0, still=True)
        clouds_at(0.0)
        os.makedirs(os.path.join(OUT, "blend"), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "blend", "diorama_v3_%s.blend" % VARIANT))
    samples = opt("--samples", 192, cast=int)
    if "--still" in argv:
        w, h = opt("--still", LAY["res"], 2, int)
        setup_render(w, h, samples)
        pose(0.0, still=True)
        clouds_at(0.0)
        cam_at(0.0)
        os.makedirs(os.path.join(OUT, "raw"), exist_ok=True)
        sc.render.filepath = os.path.join(OUT, "raw", "still_%s_%s%s.png" % (LAYOUT, VARIANT, opt("--tag", "")))
        t = time.time()
        bpy.ops.render.render(write_still=True)
        print("STILL %s %.1fs" % (VARIANT, time.time() - t))
    if "--loop" in argv:
        N = opt("--loop", 192, cast=int)
        first = opt("--first", 0, cast=int)
        last = opt("--last", N - 1, cast=int)
        setup_render(opt("--w", 1920 if LAYOUT == "center" else 1080, cast=int), opt("--h", 1080 if LAYOUT == "center" else 1920, cast=int), samples)
        d = os.path.join(OUT, "raw", "loop_%s_%s" % (LAYOUT, VARIANT))
        os.makedirs(d, exist_ok=True)
        t = time.time()
        done = 0
        for f in range(first, last + 1):
            fp = os.path.join(d, "f%04d.png" % f)
            if os.path.exists(fp):
                continue
            u = f / N
            pose(u)
            clouds_at(u)
            cam_at(u)
            sc.render.filepath = fp
            bpy.ops.render.render(write_still=True)
            done += 1
            print("FRAME", f, "%.1fs" % (time.time() - t), flush=True)
        print("LOOP rendered", done, "frames %.1fs" % (time.time() - t))
    if "--tour" in argv:
        import tour_v5 as TV
        import tour_cam as TC
        tw, th = opt("--w", 1920, cast=int), opt("--h", 1080, cast=int)
        setup_render(tw, th, samples)
        f0, f1 = opt("--tour", (0, TV.NFR - 1), 2, int)
        stp = opt("--step", 1, cast=int)
        d = os.path.join(OUT, "raw", opt("--outdir", "tour_plate"))
        cj = opt("--camjson", None, 7, float) if "--camjson" in argv else None
        os.makedirs(d, exist_ok=True)
        t = time.time()
        for f in range(f0, f1 + 1, stp):
            fp = os.path.join(d, "p%04d.png" % f)
            if os.path.exists(fp):
                continue
            TC.apply(CAMD["co"], CAMD["cam"], dict(zip(("x", "y", "az", "pitch", "width", "nx", "ny"), cj)) if cj else TV.cam_params(f / TV.FPS), tw, th)
            sc.render.filepath = fp
            bpy.ops.render.render(write_still=True)
            print("FRAME", f, "%.1fs" % (time.time() - t), flush=True)
    if do_glb:
        export_glb()
    print("TOTAL %.1fs" % (time.time() - t00))


def export_glb():
    glb_dir = os.path.join(OUT, "glb")
    os.makedirs(glb_dir, exist_ok=True)
    ratio = opt("--ratio", 0.35, cast=float)
    # 木を減らした版を別に作る
    for o in list(bpy.data.objects):
        if o.name in ("trees", "sea", "city_lights", "borders") or o.name.startswith("dash") or o.name == "cam" or "halo" in o.name:
            bpy.data.objects.remove(o)
    build_trees(max_trees=int(opt("--glbtrees", 1800, cast=int)))
    dashes, DP = build_dash_objs(R_GLOBAL[0])
    # 点線を静止配置
    R = R_GLOBAL[0]
    for k, ob in enumerate(dashes):
        sg = (k + 0.0) * DP + 0.6
        if sg > R["Le"] - 1.0:
            bpy.data.objects.remove(ob)
            continue
        pos, fw = lane_at(R["LN"], R["arc"], "empty", R["Le"] - sg)
        set_pose(ob, pos, -fw, 1.0)
    keep = [o for o in bpy.data.objects if o.type == 'MESH']
    land = bpy.data.objects["land"]
    # 陸を間引く
    mod = land.modifiers.new("dec", 'DECIMATE')
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    dg = bpy.context.evaluated_depsgraph_get()
    me2 = bpy.data.meshes.new_from_object(land.evaluated_get(dg))
    land.modifiers.remove(mod)
    old = land.data
    land.data = me2
    bpy.data.meshes.remove(old)
    tris = 0
    for o in bpy.data.objects:
        if o.type == 'MESH':
            o.data.calc_loop_triangles()
            tris += len(o.data.loop_triangles)
    print("GLB triangles", tris, "land", len(land.data.loop_triangles))
    for o in bpy.data.objects:
        o.select_set(o.type == 'MESH')
    path = os.path.join(glb_dir, "japan_diorama_v3.glb")
    kw = dict(filepath=path, export_format='GLB', use_selection=True, export_yup=True, export_apply=True,
              export_materials='EXPORT', export_cameras=False, export_lights=False)
    props = bpy.ops.export_scene.gltf.get_rna_type().properties
    if "export_vertex_color" in props:
        kw["export_vertex_color"] = 'ACTIVE'
    bpy.ops.export_scene.gltf(**kw)
    print("GLB size", os.path.getsize(path))


R_GLOBAL = [None]
_orig_build_routes = build_routes


def build_routes():  # noqa  glb 書き出しで経路の配列が必要なので保持する
    r = _orig_build_routes()
    R_GLOBAL[0] = r
    return r


if __name__ == "__main__":
    main()
