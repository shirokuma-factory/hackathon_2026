"""v5 ツアーの「重ね合わせ層」。陸と海は描かない（背景は build_diorama_v3_center.py --plate --tour で別に描く）。
使い方（画面なし）:
  blender -b -P build_tour_v5.py -- <out dir> [--first I --last J --step K] [--samples N] [--w W --h H]
  単発: --t 秒（ツアーのカメラ）または --camjson x,y,az,pitch,width,nx,ny（縦長など）と --t 秒
出力: <out>/raw/tour_ov/o0000.exr（RGBA、加算合成。線は alpha 0）。トラックの動きは毎コマにキーを打ち、モーションブラーを効かせる。
"""
import bpy
import math
import os
import sys
import time
import numpy as np
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_network_v4 as nv  # noqa
import build_diorama_v3_center as bd  # noqa
import tour_v5 as TV  # noqa
import tour_cam as TC  # noqa
import flows_v5 as FL  # noqa
import geo_v4_center as G  # noqa

argv, OUT = bd.argv, bd.OUT
smooth, val, setv, mth, add_emit, new_empty_mat = nv.smooth, nv.val, nv.setv, nv.mth, nv.add_emit, nv.new_empty_mat
FPS, NFR, DUR = TV.FPS, TV.NFR, TV.DUR
PERIOD = 1.9
VD = 60 * PERIOD / DUR          # 点線の流れる速さ。36 秒で 60 周期（つなぎ目が合う）
TRUCK_V = 3.4


def hexlin(h):
    return bd.hexlin(h)


def zoomgain(width):
    return float(np.clip((width / 70.0) ** 0.55, 0.6, 1.9))


def truck_scale(width):
    return float(np.clip(0.27 * (width / 70.0) ** 0.7, 0.14, 0.46))


def win_gain(t, w):
    a, b = w
    return smooth(a, a + 0.9, t) * (1 - smooth(b - 0.9, b, t))


def net_gain(t):
    base = 1.0 - 0.5 * smooth(5.5, 7.5, t) + 0.5 * smooth(31.5, 33.5, t)
    return base * (1.0 - smooth(34.2, 36.0, t))


# ---------------------------------------------------------------- 材質
def blue_mat(name, key, base):
    m = new_empty_mat(name)
    nt = m.node_tree
    at = nt.nodes.new("ShaderNodeAttribute"); at.attribute_name = "NA"; at.attribute_type = 'GEOMETRY'
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(at.outputs["Color"], sep.inputs["Color"])
    S = sep.outputs["Red"]
    gain, zg, fr = val(m, key + "_g"), val(m, "zg", 1.0), val(m, key + "_front")
    d = mth(nt, 'SUBTRACT', fr, S)
    mask = mth(nt, 'MULTIPLY', d, 4.0, clamp=True)
    age = mth(nt, 'MAXIMUM', d, 0.0)
    tip = mth(nt, 'MULTIPLY_ADD', mth(nt, 'EXPONENT', mth(nt, 'MULTIPLY', age, -0.6)), 0.8, 1.0)
    st = mth(nt, 'MULTIPLY', mth(nt, 'MULTIPLY', mask, tip), mth(nt, 'MULTIPLY', gain, zg))
    st = mth(nt, 'MULTIPLY', st, base)
    add_emit(m, hexlin("#5cc8ff"), st)
    return m


def orange_mat(name, key, base):
    m = new_empty_mat(name)
    nt = m.node_tree
    at = nt.nodes.new("ShaderNodeAttribute"); at.attribute_name = "NA"; at.attribute_type = 'GEOMETRY'
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(at.outputs["Color"], sep.inputs["Color"])
    S = sep.outputs["Red"]
    tn, gain, zg, fr = val(m, "t"), val(m, key + "_g"), val(m, "zg", 1.0), val(m, key + "_front")
    d = mth(nt, 'SUBTRACT', fr, S)
    mask = mth(nt, 'MULTIPLY', d, 4.0, clamp=True)
    ph = mth(nt, 'DIVIDE', mth(nt, 'SUBTRACT', S, mth(nt, 'MULTIPLY', tn, VD)), PERIOD)
    fr_ = mth(nt, 'FRACT', ph)
    mr1 = nt.nodes.new("ShaderNodeMapRange"); mr1.interpolation_type = 'SMOOTHSTEP'
    mr1.inputs["From Min"].default_value = 0.0; mr1.inputs["From Max"].default_value = 0.10
    nt.links.new(fr_, mr1.inputs["Value"])
    mr2 = nt.nodes.new("ShaderNodeMapRange"); mr2.interpolation_type = 'SMOOTHSTEP'
    mr2.inputs["From Min"].default_value = 0.52; mr2.inputs["From Max"].default_value = 0.62
    mr2.inputs["To Min"].default_value = 1.0; mr2.inputs["To Max"].default_value = 0.0
    nt.links.new(fr_, mr2.inputs["Value"])
    dash = mth(nt, 'MULTIPLY', mr1.outputs["Result"], mr2.outputs["Result"])
    st = mth(nt, 'MULTIPLY', mth(nt, 'MULTIPLY', mask, dash), mth(nt, 'MULTIPLY', gain, zg))
    st = mth(nt, 'MULTIPLY', st, base)
    add_emit(m, hexlin("#f49646"), st)
    return m


PD = 0.36
NPER = int(round(DUR / PD))      # 100。粒の出発の間隔の数（36 秒で割り切れる）


def day_profile():
    """JARTIC の 10 地点（上り・下りの合計）を時刻ごとに平均し、3 倍音までの周期関数にする。0〜1 に正規化して返す"""
    import json
    d = json.load(open(os.path.join(FL.DATA, "jartic.json"), encoding="utf-8"))
    acc = np.zeros(24); cnt = np.zeros(24)
    for p in d["points"]:
        for key in ("up", "down"):
            for i, v in enumerate(p[key]):
                if v is not None:
                    acc[i % 24] += v; cnt[i % 24] += 1
    prof = acc / np.maximum(cnt, 1)
    prof = prof / prof.max()
    h = np.arange(24)
    coef = [(np.mean(prof * np.cos(2 * np.pi * k * h / 24)), np.mean(prof * np.sin(2 * np.pi * k * h / 24))) for k in range(0, 4)]
    return prof, coef


def day_value(coef, t):
    hr = (t / DUR) * 24.0
    v = coef[0][0]
    for k in range(1, 4):
        v += 2 * (coef[k][0] * math.cos(2 * math.pi * k * hr / 24) + coef[k][1] * math.sin(2 * math.pi * k * hr / 24))
    return float(np.clip(v, 0.0, 1.0))


def flow_mat(name, vmname):
    """流れの材質。粒は theta（辺を進む所要時間）でそろえた位相で出発する。
    速い所は粒の間隔が広く、遅い所（都市の近く）は自然に密になる。尾は指数で消える。向きは theta の増える向きと減る向きの 2 組"""
    m = new_empty_mat(name)
    nt = m.node_tree
    at = nt.nodes.new("ShaderNodeAttribute"); at.attribute_name = "NA"; at.attribute_type = 'GEOMETRY'
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(at.outputs["Color"], sep.inputs["Color"])
    TH, WF, WB, TG = sep.outputs["Red"], sep.outputs["Green"], sep.outputs["Blue"], at.outputs["Alpha"]
    tn, gain, zg = val(m, "t"), val(m, "gain"), val(m, "zg", 1.0)
    vmv, dg = val(m, vmname, 1.0), val(m, "daygain", 1.0)
    mask = mth(nt, 'MULTIPLY', mth(nt, 'SUBTRACT', tn, TG), 6.0, clamp=True)

    def train(sign, W, seed):
        x = mth(nt, 'DIVIDE', mth(nt, 'ADD' if sign > 0 else 'SUBTRACT', tn, TH) if sign > 0 else mth(nt, 'SUBTRACT', tn, TH), PD)
        flo = mth(nt, 'FLOOR', x)
        fr = mth(nt, 'SUBTRACT', x, flo)
        idx = mth(nt, 'FLOORED_MODULO', flo, float(NPER))
        wn = nt.nodes.new("ShaderNodeTexWhiteNoise"); wn.noise_dimensions = '1D'
        nt.links.new(mth(nt, 'ADD', mth(nt, 'MULTIPLY', idx, 0.7311), seed), wn.inputs["W"])
        dens = mth(nt, 'MULTIPLY_ADD', mth(nt, 'POWER', W, 0.6), mth(nt, 'MULTIPLY', dg, 1.15), 0.05, clamp=True)
        pres = mth(nt, 'LESS_THAN', wn.outputs["Value"], dens)
        tail = mth(nt, 'EXPONENT', mth(nt, 'MULTIPLY', fr, -3.5))
        mr = nt.nodes.new("ShaderNodeMapRange"); mr.interpolation_type = 'SMOOTHSTEP'
        mr.inputs["From Min"].default_value = 0.0; mr.inputs["From Max"].default_value = 0.03
        nt.links.new(fr, mr.inputs["Value"])
        br = mth(nt, 'MULTIPLY_ADD', W, 0.75, 0.25)
        return mth(nt, 'MULTIPLY', mth(nt, 'MULTIPLY', pres, mth(nt, 'MULTIPLY', tail, mr.outputs["Result"])), br)

    # theta の増える向き（粒は theta の小さい側から来る）: 位相 (t - theta)、減る向き: (t + theta)
    i_f = train(-1, WF, 0.123)
    i_b = train(+1, WB, 0.571)
    tot = mth(nt, 'ADD', i_f, i_b)
    st = mth(nt, 'MULTIPLY', mth(nt, 'MULTIPLY', tot, mask), mth(nt, 'MULTIPLY', gain, zg))
    st = mth(nt, 'MULTIPLY', st, mth(nt, 'MULTIPLY', vmv, mth(nt, 'MULTIPLY_ADD', dg, 0.4, 0.6)))
    st = mth(nt, 'MULTIPLY', st, 1.15)
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = 'RGBA'
    mix.inputs[6].default_value = hexlin("#9fdcf5"); mix.inputs[7].default_value = hexlin("#e6f6ff")
    nt.links.new(tot, mix.inputs[0])
    add_emit(m, mix.outputs[2], st)
    return m


def obj_alpha_mat(name, col, k):
    m = new_empty_mat(name)
    nt = m.node_tree
    oi = nt.nodes.new("ShaderNodeObjectInfo")
    st = mth(nt, 'MULTIPLY', oi.outputs["Alpha"], k)
    add_emit(m, hexlin(col), st)
    return m


def obj_color_mat(name, k):
    m = new_empty_mat(name)
    nt = m.node_tree
    oi = nt.nodes.new("ShaderNodeObjectInfo")
    st = mth(nt, 'MULTIPLY', oi.outputs["Alpha"], k)
    add_emit(m, oi.outputs["Color"], st)
    return m


def solid_mat(name, col, rough=0.8):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = hexlin(col)
    b.inputs["Roughness"].default_value = rough
    return m


# ---------------------------------------------------------------- 形
def strip(lane, width, s0=0.0, s1=None, attr_s=True):
    """車線に沿った帯。属性 NA の R が車線の累積長 S"""
    n = len(lane["s"])
    h = np.stack([lane["hx"], lane["hy"]], 1)
    nrm = np.stack([-h[:, 1], h[:, 0]], 1)
    xy = np.stack([lane["x"], lane["y"]], 1)
    L = xy + nrm * width / 2
    R = xy - nrm * width / 2
    z = lane["z"]
    V = np.vstack([np.column_stack([L, z]), np.column_stack([R, z])])
    F = [(i, n + i, n + i + 1, i + 1) for i in range(n - 1)]
    S = lane["s"]
    A = []
    for (a, b, c, d) in F:
        A += [(S[a % n], 0, 0, 0), (S[b % n], 0, 0, 0), (S[c % n], 0, 0, 0), (S[d % n], 0, 0, 0)]
    return V, F, np.array(A, np.float32)


def mesh_obj(name, V, F, mat, attr=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in V], [], [tuple(int(i) for i in f) for f in F])
    if attr is not None:
        ca = me.color_attributes.new("NA", 'FLOAT_COLOR', 'CORNER')
        ca.data.foreach_set("color", attr.reshape(-1).astype(np.float32))
    me.materials.append(mat)
    me.update()
    me.shade_flat()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def ring_mesh(r0, r1, seg=28):
    th = np.linspace(0, 2 * np.pi, seg, endpoint=False)
    V = np.vstack([np.column_stack([r0 * np.cos(th), r0 * np.sin(th), np.zeros(seg)]), np.column_stack([r1 * np.cos(th), r1 * np.sin(th), np.zeros(seg)])])
    F = [(i, (i + 1) % seg, seg + (i + 1) % seg, seg + i) for i in range(seg)]
    return V, F


def box_mesh(w, d, h):
    V = np.array([(-w / 2, -d / 2, 0), (w / 2, -d / 2, 0), (w / 2, d / 2, 0), (-w / 2, d / 2, 0),
                  (-w / 2, -d / 2, h), (w / 2, -d / 2, h), (w / 2, d / 2, h), (-w / 2, d / 2, h)], float)
    F = [(4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return V, F


def main():
    t00 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    bd.V["sun"] = 3.0
    bd.V["sky"] = 0.10
    bd.build_world()
    bd.build_camera()
    co, cam = bd.CAMD["co"], bd.CAMD["cam"]
    flows = FL.build_flows()
    DAYPROF, DAYCOEF = day_profile()
    print("DAY", np.round(DAYPROF, 2))
    # ---- 基本の網（実データ）
    net_objs, hubs = nv.build_network(pulse=0.2, floor=0.6)
    nv.build_network("network_thick.npz", "vm_thick", hubs_too=False, pulse=0.2, floor=0.6)
    # 交通量の流れ（粒と尾）。細い版と太い版（全国の画面用）
    dnet = np.load(os.path.join(bd.CACHE, "network.npz"))
    flow_m_thin = flow_mat("flow_thin", "fvm_thin")
    flow_m_thick = flow_mat("flow_thick", "fvm_thick")
    mesh_obj("flow_thin", dnet["V3"], dnet["F3"], flow_m_thin, dnet["A3"])
    mesh_obj("flow_thick", dnet["V4"], dnet["F4"], flow_m_thick, dnet["A4"])
    hub_m = obj_color_mat("hub_c", 3.0)
    for ob in hubs:
        x, y = ob.location.x, ob.location.y
        ob["busy"] = 1.0 if (11.0 < x < 25.0 and -15.0 < y < -1.0) else 0.0
        ob.data = ob.data.copy()
        ob.data.materials.clear()
        ob.data.materials.append(hub_m)
    # ---- 流れの帯
    for f in flows:
        key = f["key"]
        f["objs"] = []
        if f["loaded"] is not None:
            m = blue_mat("bl_" + key, key + "_L", 1.0 * f["boost"])
            V, F, A = strip(f["loaded"], f["wl"])
            f["objs"].append(mesh_obj("L_" + key, V, F, m, A))
            if key == "kansai":                       # 東京側の矢じり（東京へ向かう）
                pos, fw = FL.at(f["loaded"], 0.9)
                tg = -fw[:2] / np.hypot(*fw[:2])
                sd = np.array([-tg[1], tg[0]])
                tip = pos[:2] + tg * 0.55
                a_, b_ = pos[:2] - tg * 0.25 + sd * 0.42, pos[:2] - tg * 0.25 - sd * 0.42
                zz = pos[2] + 0.02
                Vh = [(tip[0], tip[1], zz), (a_[0], a_[1], zz), (b_[0], b_[1], zz)]
                f["objs"].append(mesh_obj("H_" + key, Vh, [(0, 2, 1)], m, np.tile(np.array([0.9, 0, 0, 0], np.float32), (3, 1))))
        m = orange_mat("or_" + key, key + "_E", 2.4 * f["boost"])
        V, F, A = strip(f["empty"], f["we"])
        f["objs"].append(mesh_obj("E_" + key, V, F, m, A))
    # ---- 配送センターと待ち行列（関東）
    import json
    hubs_json = json.load(open(os.path.join(FL.DATA, "hubs.json"), encoding="utf-8"))["stations"]
    hx = G.proj(139.40, 35.75)
    qs = FL.queue_fixed(hx, (np.cos(np.radians(45.0)), np.sin(np.radians(45.0))), 10.5)
    qlane, gate, qdir = qs["lane"], qs["gate"], qs["dir"]
    print("QUEUE dir", qdir, "sd", qs["sd"])
    road_m = solid_mat("qroad", "#4f545c", 0.9)
    ql = dict(qlane)
    ql["z"] = qlane["z"] - 0.04
    Vq, Fq, _ = strip(ql, 0.85)
    q_road = mesh_obj("q_road", Vq, Fq, road_m)
    q_road.location = (0, 0, 0)
    ang = math.atan2(-qdir[1], -qdir[0])          # 車線の向き（待ち行列の先頭がセンターの門へ向かう向き）
    dc_m = solid_mat("dc", "#9ea4ab", 0.8)
    dc_m2 = solid_mat("dc2", "#6f757d", 0.9)
    gz = float(np.interp(qlane["L"], qlane["s"], qlane["z"])) + 0.0
    parts = []
    for (w_, d_, h_, ox, oy, mm) in ((1.7, 1.3, 0.12, 0.0, 1.1, dc_m), (1.9, 0.28, 0.06, 0.0, 0.15, dc_m2)):
        V, F = box_mesh(w_, d_, h_)
        ob = mesh_obj("dc", V, F, mm)
        ob.location = (gate[0] + math.cos(ang) * oy, gate[1] + math.sin(ang) * oy, gz - 0.05)
        ob.rotation_euler = (0, 0, ang - math.pi / 2)
        parts.append(ob)
    # 待ちの印（琥珀色の輪）
    wait_m = obj_alpha_mat("wait", "#ffb03a", 2.2)
    rv, rf = ring_mesh(0.38, 0.55)
    mr = bpy.data.meshes.new("waitring"); mr.from_pydata([tuple(v) for v in rv], [], rf); mr.update(); mr.materials.append(wait_m)
    waits = []
    for j in range(6):
        ob = bpy.data.objects.new("wait", mr)
        bpy.context.scene.collection.objects.link(ob)
        waits.append(ob)
    # 節点の輪（関西・東京）
    node_m = obj_alpha_mat("nodes", "#f3f6fa", 2.0)
    rv2, rf2 = ring_mesh(0.8, 1.05)
    mn = bpy.data.meshes.new("nodering"); mn.from_pydata([tuple(v) for v in rv2], [], rf2); mn.update(); mn.materials.append(node_m)
    kf = [f for f in flows if f["key"] == "kansai"][0]
    nodes = []
    for s_ in (0.0, kf["L"]):
        pos, _ = FL.at(kf["empty"], s_)
        ob = bpy.data.objects.new("node", mn)
        bpy.context.scene.collection.objects.link(ob)
        ob.location = (pos[0], pos[1], pos[2] + 0.03)
        nodes.append(ob)
    # ---- トラック
    trucks = []

    def add_truck(kind, flow, j, n):
        tag = "%s_%s_%d" % (flow["key"] if flow else "queue", kind, j)
        root = bd.load_truck("truck_10t_loaded" if kind != "E" else "truck_10t_empty", tag)
        root.rotation_mode = 'QUATERNION'
        trucks.append(dict(root=root, kind=kind, flow=flow, j=j, n=n, prevq=None))
        return root

    for f in flows:
        for j in range(f["trucks"]):
            add_truck("L", f, j, f["trucks"])
        for j in range(f["empties"]):
            add_truck("E", f, j, f["empties"])
    for j in range(6):
        add_truck("Q", None, j, 6)
    bpy.context.view_layer.update()
    # 車の長さ（モデルの最も長い辺）
    r0 = trucks[0]["root"]
    xs = []
    for ch in r0.children_recursive:
        if ch.type == 'MESH':
            for c in ch.bound_box:
                xs.append(ch.matrix_world @ Vector(c))
    L0 = max(max(v[i] for v in xs) - min(v[i] for v in xs) for i in range(3))
    print("TRUCK L0", L0, "scene %.1fs" % (time.time() - t00))

    # ---- 描画設定
    cmj = bd.opt("--camjson", None, 7, float) if "--camjson" in argv else None
    w = bd.opt("--w", 1920, cast=int)
    h = bd.opt("--h", 1080, cast=int)
    bd.setup_render(w, h, bd.opt("--samples", 96, cast=int))
    cy = sc.cycles
    cy.use_denoising = False
    cy.adaptive_threshold = 0.01
    cy.max_bounces = 2; cy.diffuse_bounces = 1; cy.glossy_bounces = 1; cy.transparent_max_bounces = 6
    cy.sample_clamp_indirect = 3.0
    sc.view_settings.exposure = 0.0
    sc.render.film_transparent = True
    sc.render.use_motion_blur = True
    sc.render.motion_blur_shutter = 0.5
    im = sc.render.image_settings
    im.file_format = 'OPEN_EXR'; im.color_mode = 'RGBA'; im.color_depth = '16'; im.exr_codec = 'ZIPS'
    sc.frame_start, sc.frame_end = 0, NFR
    sc.render.fps = FPS

    def cam_for(t):
        if cmj is not None:
            c = dict(zip(("x", "y", "az", "pitch", "width", "nx", "ny"), cmj))
            return c
        return TV.cam_params(t)

    def truck_pose(tr, t, c):
        f = tr["flow"]
        sc_t = truck_scale(c["width"])
        if tr["kind"] == "Q":
            N1 = tr["n"]
            qt = math.floor(t / 5.0) + smooth(3.6, 5.0, t % 5.0) if False else math.floor(t / 5.0) + smooth(0, 1, min(max(((t % 5.0) - 3.6) / 1.4, 0), 1))
            p = (tr["j"] - qt) % N1
            sc_t = sc_t * 0.55
            sp = 1.15 * L0 * sc_t
            s = qlane["L"] - 0.8 - p * sp
            lane = qlane
            fw = FL.at(lane, s)[1]
            pos = FL.at(lane, s)[0]
            vis = smooth(5.2, 6.4, t) * (1 - smooth(13.0, 14.2, t))
            sf = vis * smooth(0, 1, min(p / 0.5, 1)) * smooth(0, 1, min((N1 - p) / 0.9, 1))
            return pos, fw, sc_t * sf, p
        lane = f["loaded"] if tr["kind"] == "L" else f["empty"]
        L = f["L"]
        lo = 0.0 if f["tail"] is None else max(0.0, L - f["tail"])
        R = L - lo
        u = ((t * TRUCK_V + tr["j"] * R / tr["n"] + (0.37 * R if tr["kind"] == "E" else 0.0)) % R) / R
        s = lo + (1 - u) * R if tr["kind"] == "L" else lo + u * R
        pos, fw = FL.at(lane, s)
        if tr["kind"] == "L":
            fw = -fw
        vis = win_gain(t, f["win"])
        sf = vis * smooth(0, 1, min(u / 0.05, 1)) * smooth(0, 1, min((1 - u) / 0.05, 1))
        return pos, fw, sc_t * sf, u

    # ---- キーフレーム（毎コマ。モーションブラー用）
    t_k = time.time()
    for tr in trucks:
        root = tr["root"]
        for fr in range(NFR + 1):
            t = fr / FPS
            c = cam_for(t)
            pos, fw, scl, _ = truck_pose(tr, t, c)
            bd.set_pose(root, pos, fw, max(scl, 1e-4), local_fwd_neg_y=True)
            q = root.rotation_quaternion.copy()
            if tr["prevq"] is not None and q.dot(tr["prevq"]) < 0:
                q.negate()
            root.rotation_quaternion = q
            tr["prevq"] = q.copy()
            root.keyframe_insert("location", frame=fr)
            root.keyframe_insert("rotation_quaternion", frame=fr)
            root.keyframe_insert("scale", frame=fr)
    print("KEYS %.1fs" % (time.time() - t_k))

    def set_state(t, fr):
        c = cam_for(t)
        TC.apply(co, cam, c, w, h)
        ng = net_gain(t)
        setv("t", t); setv("gain", ng); setv("zg", zoomgain(c["width"]))
        wk = smooth(95.0, 165.0, c["width"])
        setv("vm", 1.0 - 0.75 * wk); setv("vm_thick", 0.9 * wk)
        setv("fvm_thin", 1.0 - 1.0 * wk); setv("fvm_thick", 1.0 * wk)
        setv("daygain", 0.55 + 0.45 * day_value(DAYCOEF, t))
        for f in flows:
            g = win_gain(t, f["win"])
            k = f["key"]
            ease = smooth(0, 1, min(max((t - f["win"][0]) / 2.4, 0), 1))
            fr_ = (f["L"] + 2.0) * ease
            setv(k + "_L_g", g); setv(k + "_E_g", g)
            setv(k + "_L_front", fr_); setv(k + "_E_front", fr_)
        # 道・配送センター
        vis = smooth(5.2, 6.4, t) * (1 - smooth(13.0, 14.2, t))
        for ob in [q_road] + parts:
            ob.scale = (1.0, 1.0, max(vis, 1e-3)) if ob is not q_road else (1, 1, 1)
            ob.hide_render = vis < 0.01
        # 待ちの印
        sc_t = truck_scale(c["width"])
        for tr, wo in zip([x for x in trucks if x["kind"] == "Q"], waits):
            pos, fw, scl, p = truck_pose(tr, t, c)
            wo.location = (pos[0], pos[1], pos[2] + 0.02)
            pulse = 0.55 + 0.45 * math.sin(2 * math.pi * (t / 1.6 + tr["j"] * 0.17))
            wo.color = (1, 1, 1, vis * pulse * (1.0 if p > 0.3 else 0.0))
            wo.scale = (max(sc_t * 3.2 * (0.8 + 0.2 * pulse), 1e-3),) * 3
        # ハブ
        hz = float(np.clip((c["width"] / 70.0) ** 0.6, 0.6, 1.9))
        busy_w = smooth(5.8, 7.0, t) * (1 - smooth(12.8, 14.0, t))
        for ob in hubs:
            dt = t - ob["T"]
            if dt < 0:
                a, sc2 = 0.0, 1e-3
            else:
                pul = math.exp(-dt * 1.4) * (0.5 + 0.5 * math.cos(dt * 6.0))
                a = (0.28 + 1.0 * pul) * ng
                sc2 = (0.9 + 0.9 * math.exp(-dt * 2.0)) * smooth(0, 0.4, dt)
                if ob["busy"] > 0 and busy_w > 0:
                    cong = 0.5 + 0.5 * math.sin(2 * math.pi * (t / 1.3 + ob.location.x * 0.13))
                    a = a + busy_w * (0.9 + 1.4 * cong)
                    sc2 = sc2 * (1 + 0.6 * busy_w * (0.6 + 0.4 * cong))
            kk = busy_w * ob["busy"]
            lin_w, lin_a = (0.80, 0.92, 1.0), (1.0, 0.62, 0.22)
            ob.color = (lin_w[0] * (1 - kk) + lin_a[0] * kk, lin_w[1] * (1 - kk) + lin_a[1] * kk, lin_w[2] * (1 - kk) + lin_a[2] * kk, a)
            ob.scale = (sc2 * hz,) * 3
        # 節点の輪（関西の場面）
        gk = win_gain(t, kf["win"])
        for ob in nodes:
            ob.color = (1, 1, 1, gk)
            ob.scale = (max(hz * (0.8 + 0.4 * gk), 1e-3),) * 3

    base = os.path.join(OUT, "raw", "tour_ov")
    os.makedirs(base, exist_ok=True)
    if "--t" in argv:
        t = bd.opt("--t", 5.8, cast=float)
        fr = int(round(t * FPS))
        sc.frame_set(fr)
        set_state(fr / FPS, fr)
        sc.render.filepath = os.path.join(base, "single_%s.exr" % bd.opt("--tag", "x"))
        bpy.ops.render.render(write_still=True)
        print("DONE single", t)
        return
    first = bd.opt("--first", 0, cast=int)
    last = bd.opt("--last", NFR - 1, cast=int)
    step = bd.opt("--step", 1, cast=int)
    t0 = time.time()
    for f in range(first, last + 1, step):
        fp = os.path.join(base, "o%04d.exr" % f)
        if os.path.exists(fp):
            continue
        sc.frame_set(f)
        set_state(f / FPS, f)
        sc.render.filepath = fp
        bpy.ops.render.render(write_still=True)
        print("FRAME", f, "%.1fs" % (time.time() - t0), flush=True)
    print("TOTAL %.1fs" % (time.time() - t00))


if __name__ == "__main__":
    main()
