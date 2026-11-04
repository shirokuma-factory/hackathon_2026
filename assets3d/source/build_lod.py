"""低ポリ版（LOD）の glb を作る。

使い方（画面なし）:
    blender --background --python assets3d/source/build_lod.py -- [--hi-trial <dir>]

  - build_models.py の部品関数を LOD = True（面取りなし・円柱 8 角形以下・小さい部品を省く）で作り直す。
  - さらに Decimate（collapse）で目標の三角形数まで減らす。
  - 全材質を 1 つの材質（パレット画像 128x4 の NEAREST 参照）にまとめ、描画呼び出しを 1 つにする。
  - ノード名・アニメーション・原点・向き・スケールは build_models.py と同じ。
  - web/models/lo/<name>.glb と web/models/lod.json を出す。vehicles.blend は触らない。
  - --hi-trial を付けると、高詳細版にも同じ材質の統合をかけたものを <dir> に出す（採否の試算用）。
"""
import bpy
import bmesh
import importlib.util
import json
import math
import os
import struct
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location("bm", os.path.join(HERE, "build_models.py"))
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)
from glb_report import report  # noqa: E402

ROOT = bm.ROOT
HI_DIR = os.path.join(ROOT, "web", "models")
LO_DIR = os.path.join(HI_DIR, "lo")

# モデルごとの設定。min = 外形の対角線が最長辺のこの割合より小さい部品を省く
# tgt = 目標の三角形数（None なら高詳細版の 25 %）
SMALL_ROAD = {"truck", "truck_4t", "kei_van", "box_2t", "trailer", "container_12ft"}
PARAMS = {n: {"seg": 8, "tgt": 300 if n in SMALL_ROAD else None} for n, _ in bm.MODELS}
# 小さい部品を省く閾値の上限、目標の上書き（部品が消えて形が変わるモデル）
PARAMS["freight_train"]["maxmin"] = 0.05
PARAMS["trailer"].update({"maxmin": 0.1, "tgt": 450})
PARAMS.update(json.load(open(os.path.join(HERE, "lod_params.json"), encoding="utf-8"))
              if os.path.exists(os.path.join(HERE, "lod_params.json")) else {})

CELL, PW, PH = 4, 128, 4  # パレット 1 色 = 4 px 四方。画像は 128 x 4（2 のべき乗）


def png(path, w, h, px):
    """px[y][x] = (r, g, b, a) の 8 bit PNG を書く。"""
    raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in px)

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(data)


def make_palette_material(tmp):
    """全モデル共通の材質 'pal'。色画像と、粗さ(G)・金属(B)の画像を NEAREST で引く。"""
    names = list(bm.PALETTE)
    base = [[(0, 0, 0, 255)] * PW for _ in range(PH)]
    mr = [[(0, 255, 0, 255)] * PW for _ in range(PH)]
    for i, n in enumerate(names):
        h, metal, rough = bm.PALETTE[n]
        h = h.lstrip("#")
        rgb = tuple(int(h[k:k + 2], 16) for k in (0, 2, 4))
        for y in range(PH):
            for x in range(i * CELL, (i + 1) * CELL):
                base[y][x] = rgb + (255,)
                mr[y][x] = (0, round(rough * 255), round(metal * 255), 255)
    p1, p2 = os.path.join(tmp, "palette_color.png"), os.path.join(tmp, "palette_mr.png")
    png(p1, PW, PH, base)
    png(p2, PW, PH, mr)
    m = bpy.data.materials.new("pal")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    t1 = nt.nodes.new("ShaderNodeTexImage")
    t1.image = bpy.data.images.load(p1)
    t1.image.pack()
    t1.interpolation = 'Closest'
    t2 = nt.nodes.new("ShaderNodeTexImage")
    t2.image = bpy.data.images.load(p2)
    t2.image.colorspace_settings.name = 'Non-Color'
    t2.image.pack()
    t2.interpolation = 'Closest'
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(t1.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(t2.outputs["Color"], sep.inputs["Color"])
    nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
    nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
    return m, {n: i for i, n in enumerate(names)}


def to_palette(ob, pal, index):
    """ob の面ごとの材質を、パレット上の UV に置き換えて材質を 1 つにする。"""
    me = ob.data
    names = [m.name for m in me.materials]
    uv = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        i = index[names[poly.material_index]]
        u, v = (i * CELL + CELL / 2) / PW, 0.5
        for li in poly.loop_indices:
            uv.data[li].uv = (u, v)
    me.materials.clear()
    me.materials.append(pal)
    for poly in me.polygons:
        poly.material_index = 0


def mesh_objs(ob):
    return [o for o in bm.family(ob) if o.type == 'MESH']


def ntris(o):
    o.data.calc_loop_triangles()
    return len(o.data.loop_triangles)


def decimate(ob, target):
    objs = mesh_objs(ob)
    tot = sum(ntris(o) for o in objs)
    if tot <= target:
        return
    small = [o for o in objs if ntris(o) <= 40]
    big = [o for o in objs if ntris(o) > 40]
    keep = sum(ntris(o) for o in small)
    bigtot = sum(ntris(o) for o in big)
    ratio = max(0.02, min(1.0, (target - keep) / bigtot)) if bigtot else 1.0
    dg = bpy.context.evaluated_depsgraph_get()
    for o in big:
        md = o.modifiers.new("lod", 'DECIMATE')
        md.decimate_type = 'COLLAPSE'
        md.ratio = ratio
        md.use_collapse_triangulate = True
        dg.update()
        newme = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
        o.modifiers.remove(md)
        old = o.data
        newme.validate()  # collapse で重なった面・つぶれた面を取り除く
        # collapse で崩れた法線を直す: 面の角度が大きい辺だけ角にする（_obj と同じ規則）
        b = bmesh.new()
        b.from_mesh(newme)
        lim = math.radians(bm.SHARP_DEG)
        for e in b.edges:
            e.smooth = len(e.link_faces) == 2 and e.calc_face_angle(0.0) < lim
        for fc in b.faces:
            fc.smooth = True
        b.to_mesh(newme)
        b.free()
        o.data = newme
        bpy.data.meshes.remove(old)


def hi_dims():
    out = {}
    for n, _ in bm.MODELS:
        import glb_report as g
        js, _b = g.read_glb(os.path.join(HI_DIR, n + ".glb"))
        lo = [1e9] * 3
        hi = [-1e9] * 3
        for m in js["meshes"]:
            for pr in m["primitives"]:
                a = js["accessors"][pr["attributes"]["POSITION"]]
                for k in range(3):
                    lo[k], hi[k] = min(lo[k], a["min"][k]), max(hi[k], a["max"][k])
        out[n] = max(h - l for l, h in zip(lo, hi))
    return out


def drop(ob):
    for o in bm.family(ob):
        me = o.data
        bpy.data.objects.remove(o)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)


# 小さい部品を省く閾値（最長辺に対する対角線の割合）の候補。collapse の縮小率が緩くなるまで上げる
MIN_LADDER = [0.0, 0.02, 0.03, 0.04, 0.05, 0.065, 0.08, 0.1, 0.125, 0.15, 0.2]
SLACK = 1.6  # 部品を省いたあとの三角形数が目標のこの倍以内になったら、残りは collapse で減らす


def build_lod_model(n, f, dim, tgt):
    p = PARAMS[n]
    bm.LOD_SEG = p["seg"]
    ladder = [x for x in MIN_LADDER if x <= p.get("maxmin", 1.0)]
    for frac in ladder:
        bm.LOD_MIN = frac * dim
        ob = f().finish()
        t = sum(ntris(o) for o in mesh_objs(ob))
        print('  ladder', n, frac, t)
        if t <= tgt * SLACK or frac == ladder[-1]:
            print("LOD", n, "min", frac, "tris before collapse", t, "target", tgt)
            return ob
        drop(ob)


def build(lod, merge, outdir, tmp):
    bm.reset()
    sc = bpy.context.scene
    sc.render.fps = bm.FPS
    bm.LOD = lod
    dims = hi_dims()
    hi = {n: report(os.path.join(HI_DIR, n + ".glb")) for n, _ in bm.MODELS}
    tg = {}
    for i, (n, f) in enumerate(bm.MODELS):
        tg[n] = int(hi[n]["tris"] * 0.25)
        if PARAMS[n]["tgt"]:
            tg[n] = min(tg[n], PARAMS[n]["tgt"])
        if lod:
            ob = build_lod_model(n, f, dims[n], tg[n])
        else:
            bm.LOD_MIN = 0.0
            ob = f().finish()
        ob.location = (0, i * 260.0, 0)
    for n, fn in bm.ANIMS.items():
        objs, name, dur = fn(n)
        bm.ANIM_RANGE[n] = bm.bake(objs, name, dur)
    sc.frame_set(1)
    if lod:
        for n, _ in bm.MODELS:
            decimate(bpy.data.objects[n], tg[n])
    if merge:
        pal, index = make_palette_material(tmp)
        for n, _ in bm.MODELS:
            for o in mesh_objs(bpy.data.objects[n]):
                to_palette(o, pal, index)
    return bm.export_all(outdir)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    tmp = tempfile.mkdtemp()
    if "--hi-trial" in argv:
        d = argv[argv.index("--hi-trial") + 1]
        os.makedirs(d, exist_ok=True)
        build(False, True, d, tmp)
        return
    build(True, True, LO_DIR, tmp)
    out = {}
    for n, _ in bm.MODELS:
        h, l = report(os.path.join(HI_DIR, n + ".glb")), report(os.path.join(LO_DIR, n + ".glb"))
        out[n] = {"hi": {"tris": h["tris"], "bytes": h["bytes"]}, "lo": {"tris": l["tris"], "bytes": l["bytes"]}}
    with open(os.path.join(HI_DIR, "lod.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("done")


if bpy.app.background and __name__ == "__main__":
    main()
