"""物流網が成長して流れる動画の「重ね合わせ層」を描く（陸や海は描かない。背景は別に 1 枚だけ高品質で描く）。
使い方（画面なし）:
  blender -b -P build_network_v4.py -- <out dir> --layout center|portrait [--first I --last J | --t 秒] [--w W --h H] [--samples N]
前提: <out>/cache に prep_land_v3.py と net_data.py の出力がある。
出力: <out>/raw/net_<layout>/n0000.exr（RGBA、加算で合成する。線は alpha 0 のまま色だけを持つ）
時間: 12 秒 288 コマ。1〜6 秒 道路網と貨物鉄道が成長、4〜10 秒 関西→関東の主役の線とトラック、10〜12 秒 全体がうすれて 0 秒と同じ状態に戻る。
"""
import bpy
import json
import math
import os
import sys
import time
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_diorama_v3_center as bd  # noqa

argv = bd.argv
OUT = bd.OUT
LAY = bd.LAY
LAYOUT = bd.LAYOUT
V = bd.V
NFR, FPS = 288, 24
DUR = NFR / FPS
VP, SP = 10.0 / 3.0, 4.0        # 光の粒の速さ（単位/秒）と間隔。12 秒で ちょうど 10 周期 → つなぎ目が合う
REG = {}


def smooth(a, b, x):
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def val(mat, name, init=0.0):
    n = mat.node_tree.nodes.new("ShaderNodeValue")
    n.outputs[0].default_value = init
    REG.setdefault(name, []).append(n)
    return n.outputs[0]


def setv(name, v):
    for n in REG.get(name, []):
        n.outputs[0].default_value = v


def mth(nt, op, a=None, b=None, c=None, clamp=False):
    m = nt.nodes.new("ShaderNodeMath")
    m.operation = op
    m.use_clamp = clamp
    for i, x in enumerate((a, b, c)):
        if x is None:
            continue
        if isinstance(x, (int, float)):
            m.inputs[i].default_value = float(x)
        else:
            nt.links.new(x, m.inputs[i])
    return m.outputs[0]


def add_emit(mat, color_in, strength_out):
    """透明 + 発光の加算。alpha は 0 のまま、色だけが乗る"""
    nt = mat.node_tree
    em = nt.nodes.new("ShaderNodeEmission")
    if isinstance(color_in, tuple):
        em.inputs["Color"].default_value = color_in
    else:
        nt.links.new(color_in, em.inputs["Color"])
    nt.links.new(strength_out, em.inputs["Strength"])
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    ad = nt.nodes.new("ShaderNodeAddShader")
    nt.links.new(tr.outputs[0], ad.inputs[0])
    nt.links.new(em.outputs[0], ad.inputs[1])
    o = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(ad.outputs[0], o.inputs["Surface"])


def new_empty_mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.node_tree.nodes.clear()
    return m


def net_mat(vm="vm", pulse=1.1, floor=0.4):
    m = new_empty_mat("net")
    nt = m.node_tree
    at = nt.nodes.new("ShaderNodeAttribute"); at.attribute_name = "NA"; at.attribute_type = 'GEOMETRY'
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(at.outputs["Color"], sep.inputs["Color"])
    T, W, S, K = sep.outputs["Red"], sep.outputs["Green"], sep.outputs["Blue"], at.outputs["Alpha"]
    tn, gain = val(m, "t"), val(m, "gain")
    zg = val(m, "zg", 1.0)
    vmv = val(m, vm, 1.0)
    sub = mth(nt, 'SUBTRACT', tn, T)
    mask = mth(nt, 'MULTIPLY', sub, 9.0, clamp=True)
    age = mth(nt, 'MAXIMUM', sub, 0.0)
    ex = mth(nt, 'EXPONENT', mth(nt, 'MULTIPLY', age, -2.4))
    tip = mth(nt, 'MULTIPLY_ADD', ex, 1.2, 1.0)
    a = mth(nt, 'MULTIPLY', tn, VP)
    b = mth(nt, 'SUBTRACT', S, a)
    c = mth(nt, 'MULTIPLY', b, 2 * math.pi / SP)
    d = mth(nt, 'COSINE', c)
    e = mth(nt, 'MULTIPLY_ADD', d, 0.5, 0.5)
    f = mth(nt, 'POWER', e, 12.0)
    pm = mth(nt, 'MULTIPLY_ADD', f, pulse, floor)
    s0 = mth(nt, 'MULTIPLY_ADD', K, -0.28, 0.50)                 # 道路 0.95、鉄道 0.45
    wf = mth(nt, 'MULTIPLY_ADD', W, 1.2, 0.5)
    st = mth(nt, 'MULTIPLY', s0, wf)
    st = mth(nt, 'MULTIPLY', st, mask)
    st = mth(nt, 'MULTIPLY', st, tip)
    st = mth(nt, 'MULTIPLY', st, pm)
    st = mth(nt, 'MULTIPLY', st, gain)
    st = mth(nt, 'MULTIPLY', st, zg)
    st = mth(nt, 'MULTIPLY', st, vmv)
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = 'RGBA'
    mix.inputs[6].default_value = bd.hexlin("#62c4ee")
    mix.inputs[7].default_value = bd.hexlin("#a9dcf5")
    nt.links.new(K, mix.inputs[0])
    add_emit(m, mix.outputs[2], st)
    return m


def hero_mat(name, col, base, axis_sign=1.0, grow="loaded"):
    """主役の線。世界座標の X でしきい値を切って、関西側から（または関東側から）伸ばす"""
    m = new_empty_mat(name)
    nt = m.node_tree
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    sx = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Position"], sx.inputs["Vector"])
    X = sx.outputs["X"]
    fr, hg = val(m, "hero_front_" + grow), val(m, "hero_gain")
    d = mth(nt, 'SUBTRACT', fr, X) if grow == "loaded" else mth(nt, 'SUBTRACT', X, fr)
    mask = mth(nt, 'MULTIPLY', d, 3.0, clamp=True)
    age = mth(nt, 'MAXIMUM', d, 0.0)
    ex = mth(nt, 'EXPONENT', mth(nt, 'MULTIPLY', age, -0.7))
    tip = mth(nt, 'MULTIPLY_ADD', ex, 1.6, 1.0)
    st = mth(nt, 'MULTIPLY', mth(nt, 'MULTIPLY', mask, tip), hg)
    st = mth(nt, 'MULTIPLY', st, base)
    add_emit(m, bd.hexlin(col), st)
    return m


def plain_mat(name, col, key, base):
    m = new_empty_mat(name)
    g = val(m, key)
    st = mth(m.node_tree, 'MULTIPLY', g, base)
    add_emit(m, bd.hexlin(col), st)
    return m


def hub_mat():
    m = new_empty_mat("hub")
    nt = m.node_tree
    oi = nt.nodes.new("ShaderNodeObjectInfo")
    st = mth(nt, 'MULTIPLY', oi.outputs["Alpha"], 3.0)
    add_emit(m, bd.hexlin("#e8f6ff"), st)
    return m


def build_network(fname="network.npz", vm="vm", hubs_too=True, pulse=1.1, floor=0.4):
    d = np.load(os.path.join(bd.CACHE, fname))
    mat = net_mat(vm, pulse, floor)
    objs = []
    for k in ("1", "2"):
        Vv, Ff, A = d["V" + k], d["F" + k], d["A" + k]
        me = bpy.data.meshes.new("net" + k)
        me.from_pydata([tuple(v) for v in Vv], [], [tuple(int(i) for i in f) for f in Ff])
        ca = me.color_attributes.new("NA", 'FLOAT_COLOR', 'CORNER')
        # 角ごと: 面の角の並びは from_pydata の面の頂点順と同じ
        ca.data.foreach_set("color", A.reshape(-1).astype(np.float32))
        me.materials.append(mat)
        me.update()
        ob = bpy.data.objects.new("net" + k, me)
        bpy.context.scene.collection.objects.link(ob)
        objs.append(ob)
    if not hubs_too:
        return objs, []
    # ハブの輪
    hub = []
    hm = hub_mat()
    seg = 28
    th = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    r0, r1 = 0.30, 0.52
    Vh = np.vstack([np.column_stack([r0 * np.cos(th), r0 * np.sin(th), np.zeros(seg)]), np.column_stack([r1 * np.cos(th), r1 * np.sin(th), np.zeros(seg)])])
    Fh = [(i, (i + 1) % seg, seg + (i + 1) % seg, seg + i) for i in range(seg)]
    mh = bpy.data.meshes.new("hub")
    mh.from_pydata([tuple(v) for v in Vh], [], Fh)
    mh.update()
    mh.materials.append(hm)
    for (x, y), z, T in zip(d["hub_xy"], d["hub_z"], d["hub_T"]):
        ob = bpy.data.objects.new("hub", mh)
        bpy.context.scene.collection.objects.link(ob)
        ob.location = (x, y, z)
        ob["T"] = float(T)
        hub.append(ob)
    return objs, hub


def main():
    t00 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    bd.V["sun"] = 3.0
    bd.V["sky"] = 0.10
    bd.build_world()
    R = bd.build_routes()
    bd.build_camera()
    for o in list(bpy.data.objects):
        if o.name in ("highway", "highway_line", "route_loaded_halo", "route_empty_halo"):
            bpy.data.objects.remove(o)
    LN, arc = R["LN"], R["arc"]
    Lb, Le = R["Lb"], R["Le"]
    xs = LN["loaded"][:, 0]
    xmin, xmax = float(xs.min()), float(xs.max())
    # 主役の線の材質
    mb = hero_mat("hero_blue", "#3aaeff", 1.2, grow="loaded")
    for nm in ("route_loaded", "route_loaded_head"):
        o = bpy.data.objects[nm]
        o.data.materials.clear()
        o.data.materials.append(mb)
    nodes_mat = plain_mat("hero_nodes", "#f3f6fa", "hero_node", 1.0)
    for nm in ("node_kansai", "node_kanto"):
        o = bpy.data.objects[nm]
        o.data.materials.clear()
        o.data.materials.append(nodes_mat)
    net_objs, hubs = build_network()
    dashes, DP = bd.build_dash_objs(R)
    dmat = plain_mat("hero_orange", "#f0701a", "hero_dash", 1.3)
    for o in dashes[:1]:
        o.data.materials.clear()
        o.data.materials.append(dmat)
    trucks = []
    for tag, nm in (("L1", "truck_10t_loaded"), ("L2", "truck_10t_loaded"), ("L3", "truck_10t_loaded"), ("E1", "truck_10t_empty")):
        trucks.append((tag, bd.load_truck(nm, tag)))
    hw = R["hw"]
    cam_t = ((hw[0][0] + hw[-1][0]) / 2, (hw[0][1] + hw[-1][1]) / 2)
    bd.aim(cam_t, LAY["az"], LAY["pitch"], LAY["width"])
    print("SCENE built %.1fs" % (time.time() - t00), "xrange", xmin, xmax)

    w = bd.opt("--w", LAY["res"][0] if LAYOUT == "portrait" else 1920, cast=int)
    h = bd.opt("--h", LAY["res"][1] if LAYOUT == "portrait" else 1080, cast=int)
    bd.setup_render(w, h, bd.opt("--samples", 32, cast=int))
    cy = sc.cycles
    cy.use_denoising = False
    cy.adaptive_threshold = 0.02
    cy.max_bounces = 2; cy.diffuse_bounces = 1; cy.glossy_bounces = 1; cy.transparent_max_bounces = 6
    cy.sample_clamp_indirect = 3.0
    sc.view_settings.exposure = 0.0
    sc.render.film_transparent = True
    im = sc.render.image_settings
    im.file_format = 'OPEN_EXR'
    im.color_mode = 'RGBA'
    im.color_depth = '16'
    im.exr_codec = 'ZIPS'

    def state(t):
        ng = 1.0 - smooth(10.0, 12.0, t)
        hg = smooth(3.9, 4.8, t) * (1.0 - smooth(10.0, 12.0, t))
        e1 = smooth(0, 1, min(max((t - 4.0) / 1.7, 0.0), 1.0))
        e2 = smooth(0, 1, min(max((t - 4.9) / 1.7, 0.0), 1.0))
        fl = xmin - 0.5 + (xmax - xmin + 2.0) * e1
        fe = xmax + 0.5 - (xmax - xmin + 1.0) * e2
        th = smooth(4.6, 5.6, t) * (1.0 - smooth(10.0, 11.0, t))
        return ng, hg, fl, fe, th

    def pose(t):
        ng, hg, fl, fe, th = state(t)
        setv("t", t); setv("gain", ng)
        setv("hero_gain", hg); setv("hero_front_loaded", fl); setv("hero_front_empty", fe)
        setv("hero_node", hg)
        setv("hero_dash", hg)
        u = t / DUR
        ph = {"L1": 0.06, "L2": 0.39, "L3": 0.72, "E1": 0.30}
        for tag, root in trucks:
            f = (u + ph[tag]) % 1.0
            if tag.startswith("L"):
                pos, fw = bd.lane_at(LN, arc, "loaded", f * Lb)
            else:
                pos, fw = bd.lane_at(LN, arc, "empty", (1 - f) * Le)
                fw = -fw
            sc_ = bd.TRUCK_SCALE * 0.92 * bd.ease(min(f / 0.05, (1 - f) / 0.05)) * th
            bd.set_pose(root, pos, fw, max(sc_, 1e-4), local_fwd_neg_y=True)
        for k, ob in enumerate(dashes):
            sg = (k + 21.0 * u) * DP
            sg = sg % (len(dashes) * DP)
            if sg > Le:
                ob.scale = (1e-4,) * 3
                continue
            pos, fw = bd.lane_at(LN, arc, "empty", Le - sg)
            fw = -fw
            fz = bd.ease(min(sg / 1.6, (Le - sg) / 1.6))
            vis = 1.0 if pos[0] >= fe else 0.0
            bd.set_pose(ob, pos, fw, max(fz * vis, 1e-4))
        for ob in hubs:
            dt = t - ob["T"]
            if dt < 0:
                a, sc2 = 0.0, 1e-3
            else:
                pul = math.exp(-dt * 1.4) * (0.5 + 0.5 * math.cos(dt * 6.0))
                a = (0.28 + 1.0 * pul) * ng
                sc2 = (0.9 + 0.9 * math.exp(-dt * 2.0)) * smooth(0, 0.4, dt)
            ob.color = (1, 1, 1, a)
            ob.scale = (sc2,) * 3

    base = os.path.join(OUT, "raw", "net_" + LAYOUT)
    os.makedirs(base, exist_ok=True)
    if "--t" in argv:
        t = bd.opt("--t", 8.0, cast=float)
        pose(t)
        sc.render.filepath = os.path.join(base, "single_t%05.2f.exr" % t)
        bpy.ops.render.render(write_still=True)
        print("DONE single", t)
        return
    first = bd.opt("--first", 0, cast=int)
    last = bd.opt("--last", NFR - 1, cast=int)
    t0 = time.time()
    for f in range(first, last + 1):
        fp = os.path.join(base, "n%04d.exr" % f)
        if os.path.exists(fp):
            continue
        pose(f / FPS)
        sc.render.filepath = fp
        bpy.ops.render.render(write_still=True)
        print("FRAME", f, "%.1fs" % (time.time() - t0), flush=True)
    print("TOTAL %.1fs" % (time.time() - t00))


if __name__ == "__main__":
    main()
