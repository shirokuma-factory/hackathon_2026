"""v7 ツアーの「重ね合わせ層」: 主役の細い線だけ（鹿屋→東京の載せた車（青の実線）と東京→鹿屋の空車（橙の点線）、副の関西↔関東）。
トラックや建物などの、この高さでは大きすぎるものは描かない。流れの光は合成側（particles_v7）。
使い方: blender -b -P build_tour_v7.py -- <out dir> [--first I --last J --step K] [--samples N] / 単発 --t 秒 [--tag x]
出力: <out>/raw/tour_ov/o0000.exr（RGBA、加算合成。線は alpha 0 のまま色だけを持つ）"""
import bpy
import math
import os
import sys
import time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_network_v4 as nv  # noqa
import build_diorama_v7 as bd  # noqa
import tour_v7 as TV  # noqa
import tour_cam7 as TC  # noqa
import flows_v6 as FL  # noqa

argv, OUT = bd.argv, bd.OUT
val, setv, mth, add_emit, new_empty_mat = nv.val, nv.setv, nv.mth, nv.add_emit, nv.new_empty_mat
DUR = TV.DUR
PERIOD = 1.9
VD = 60 * PERIOD / DUR
HERO = {"kagoshima": (20.0, 28.6), "kansai": (13.2, 21.0)}


def hexlin(h):
    return bd.hexlin(h)


def smooth(a, b, x):
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def win_gain(t, w):
    a, b = w
    return smooth(a, a + 0.9, t) * (1 - smooth(b - 0.9, b, t))


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


def strip(lane, width):
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


def zoomgain(width):
    return float(np.clip((width / 70.0) ** 0.55, 0.6, 1.9))


def main():
    t00 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    bd.V["sun"] = 3.0
    bd.V["sky"] = 0.10
    bd.build_world()
    bd.build_camera()
    co, cam = bd.CAMD["co"], bd.CAMD["cam"]
    FL.FLOWS[:] = [f for f in FL.FLOWS if f["key"] in HERO]
    for f in FL.FLOWS:
        f["win"] = HERO[f["key"]]
        f["off"] = 0.10
    flows = FL.build_flows()
    if "--export" in argv:
        d = {}
        for f in flows:
            k = f["key"]
            for nm, ln in (("L", f["loaded"]), ("E", f["empty"])):
                if ln is not None:
                    d[k + "_" + nm] = np.stack([ln["s"], ln["x"], ln["y"], ln["z"]], 1)
        np.savez(os.path.join(OUT, "cache", "lanes_v7.npz"), **d)
        print("EXPORTED", list(d.keys()), flush=True)
        return
    for f in flows:
        k = f["key"]
        for v, wm in (("t", 1.0), ("k", 3.2)):
            if f["loaded"] is not None:
                m = blue_mat("bl_%s_%s" % (k, v), "%s_L%s" % (k, v), 1.3 * f["boost"])
                V, F, A = strip(f["loaded"], 0.07 * wm)
                mesh_obj("L_%s_%s" % (k, v), V, F, m, A)
            m = orange_mat("or_%s_%s" % (k, v), "%s_E%s" % (k, v), 2.0 * f["boost"])
            V, F, A = strip(f["empty"], 0.075 * wm)
            mesh_obj("E_%s_%s" % (k, v), V, F, m, A)
    w = bd.opt("--w", 1920, cast=int)
    h = bd.opt("--h", 1080, cast=int)
    bd.setup_render(w, h, bd.opt("--samples", 64, cast=int))
    cy = sc.cycles
    cy.use_denoising = False
    cy.adaptive_threshold = 0.02
    cy.max_bounces = 2; cy.diffuse_bounces = 1; cy.glossy_bounces = 1; cy.transparent_max_bounces = 6
    sc.view_settings.exposure = 0.0
    sc.render.film_transparent = True
    im = sc.render.image_settings
    im.file_format = 'OPEN_EXR'; im.color_mode = 'RGBA'; im.color_depth = '16'; im.exr_codec = 'ZIPS'
    print("SCENE built %.1fs" % (time.time() - t00), [(f["key"], round(f["L"], 1)) for f in flows])

    def set_state(t):
        c = TV.cam_params(t)
        TC.apply(co, cam, c, w, h)
        setv("t", t)
        setv("zg", zoomgain(c["width"]))
        wk = smooth(95.0, 165.0, c["width"])
        for f in flows:
            k = f["key"]
            g = win_gain(t, f["win"])
            ease = smooth(0, 1, min(max((t - f["win"][0]) / 2.6, 0), 1))
            fr = (f["L"] + 2.0) * ease
            for v, wv in (("t", 1.0 - wk), ("k", wk)):
                setv("%s_L%s_g" % (k, v), g * wv); setv("%s_E%s_g" % (k, v), g * wv)
                setv("%s_L%s_front" % (k, v), fr); setv("%s_E%s_front" % (k, v), fr)

    base = os.path.join(OUT, "raw", bd.opt("--outdir", "tour_ov"))
    os.makedirs(base, exist_ok=True)
    if "--t" in argv:
        t = bd.opt("--t", 24.0, cast=float)
        set_state(t)
        sc.render.filepath = os.path.join(base, "single_%s.exr" % bd.opt("--tag", "x"))
        bpy.ops.render.render(write_still=True)
        print("DONE single", t)
        return
    first = bd.opt("--first", 0, cast=int)
    last = bd.opt("--last", TV.NFR - 1, cast=int)
    step = bd.opt("--step", 1, cast=int)
    t0 = time.time()
    frl = [int(x) for x in bd.opt("--frames", "").split(",") if x] if "--frames" in argv else range(first, last + 1, step)
    for f in frl:
        fp = os.path.join(base, "o%04d.exr" % f)
        if os.path.exists(fp):
            continue
        set_state(f / TV.FPS)
        sc.render.filepath = fp
        bpy.ops.render.render(write_still=True)
        print("FRAME", f, "%.1fs" % (time.time() - t0), flush=True)
    print("TOTAL %.1fs" % (time.time() - t00))


if __name__ == "__main__":
    main()
