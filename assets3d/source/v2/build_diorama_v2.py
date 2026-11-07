"""日本のジオラマ v2（Blender）。web/data/pref.geojson から都道府県を押し出し、関東・関西の間に実車（アプリの青の実線）と空車（アプリの橙の点線）の矢印とトラックを置く。

使い方（画面なし）:
    blender -b -P assets3d/source/v2/build_diorama_v2.py -- <out> [--still] [--glb] [--anim N] [--fmt mp4|webm]
  --still : <out>/hero/hero_1600x900.png と hero_800x450.png を描く
  --glb   : <out>/glb/japan_diorama.glb（陸 + 矢印 + トラックの静止配置）を出す
  --anim N: N コマ（24fps）の循環動画を <out>/hero/hero_loop.<fmt> に出す
座標: 1 度 = 10 単位（経度は cos(36.5°) を掛ける）。陸の高さは 1.0。
"""
import bpy
import bmesh
import json
import math
import os
import sys
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(argv[0]) if argv else os.path.join(ROOT, "outputs", "assets_v2_2026-11-07")
for d in ("hero", "glb"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)
GLB_DIR = os.path.join(OUT, "glb")

S = 10.0
LON0, LAT0 = 137.5, 36.5
KX = math.cos(math.radians(36.5))
TOL = float(os.environ.get("DIO_TOL", "0.03"))      # 輪郭の簡略化（度）
H_LAND = 1.6
Z_SEA = 0.28
KANTO = {7, 8, 9, 10, 11, 12, 13}       # 茨城〜神奈川（JIS コード - 1）
KANSAI = {23, 24, 25, 26, 27, 28, 29}   # 三重〜和歌山
PAL = {
    "land_a": "#c3ccd8", "land_b": "#b9c3d1", "land_c": "#cdd5df",
    "kanto": "#dccbb4", "kansai": "#cdd3b9",
    "sea": "#1d3050", "shelf": "#3a6290",
    "loaded": "#5cc8ff", "empty": "#f49646", "node": "#f3f6fa",
}


def hexcol(h):
    h = h.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return (f(r), f(g), f(b), 1.0)


def mk(name, col, rough=0.8, metal=0.0, emit=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = hexcol(col)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if emit:
        b.inputs["Emission Color"].default_value = hexcol(col)
        b.inputs["Emission Strength"].default_value = emit
    m.diffuse_color = hexcol(col)
    return m


def proj(lon, lat):
    return ((lon - LON0) * KX * S, (lat - LAT0) * S)


def rdp(pts, tol):
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1e-12
    dm, im = 0, 0
    for i in range(1, len(pts) - 1):
        d = abs(dy * pts[i][0] - dx * pts[i][1] + b[0] * a[1] - b[1] * a[0]) / L
        if d > dm:
            dm, im = d, i
    if dm > tol:
        return rdp(pts[:im + 1], tol)[:-1] + rdp(pts[im:], tol)
    return [a, b]


def area(ring):
    return abs(sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1] for i in range(len(ring)))) / 2


def build_land():
    gj = json.load(open(os.path.join(ROOT, "web", "data", "pref.geojson"), encoding="utf-8"))
    mats = {k: mk("land_" + k, PAL[k], 0.85) for k in ("land_a", "land_b", "land_c", "kanto", "kansai")}
    names = ["land_a", "land_b", "land_c"]
    objs = []
    tris = 0
    for idx, f in enumerate(gj["features"]):
        key = "kanto" if idx in KANTO else "kansai" if idx in KANSAI else names[(idx * 7 + idx // 3) % 3]
        bm = bmesh.new()
        n_poly = 0
        polys = f["geometry"]["coordinates"]
        if f["geometry"]["type"] == "Polygon":
            polys = [polys]
        for poly in polys:
            ring = poly[0][:-1] if poly[0][0] == poly[0][-1] else poly[0]
            ring = [(x, y) for x, y in ring]
            if min(p[1] for p in ring) < 30.5 and idx != 46:  # 小笠原などの遠い島を除く
                continue
            if idx == 46:  # 沖縄は描かない
                continue
            if area(ring) < 0.0035:  # 小さな島は除く（0.0035 度² ≈ 35 km²）
                continue
            k = max(range(len(ring)), key=lambda i: (ring[i][0] - ring[0][0]) ** 2 + (ring[i][1] - ring[0][1]) ** 2)
            r2 = rdp(ring[:k + 1], TOL)[:-1] + rdp(ring[k:] + [ring[0]], TOL)[:-1]
            if len(r2) < 3:
                continue
            pts = [proj(x, y) for x, y in r2]
            vs = [bm.verts.new((x, y, 0)) for x, y in pts]
            try:
                face = bm.faces.new(vs)
            except ValueError:
                continue
            if face.normal.z < 0:
                face.normal_flip()
            ext = bmesh.ops.extrude_face_region(bm, geom=[face])
            ev = [e for e in ext["geom"] if isinstance(e, bmesh.types.BMVert)]
            bmesh.ops.translate(bm, verts=ev, vec=(0, 0, H_LAND))
            top = [e for e in ext["geom"] if isinstance(e, bmesh.types.BMFace) and e.normal.z > 0.5]
            if top:
                edges = [e for e in top[0].edges]
                try:
                    bmesh.ops.bevel(bm, geom=edges, offset=0.14, segments=2, affect='EDGES', clamp_overlap=True)
                except Exception:
                    pass
            n_poly += 1
        if not n_poly:
            bm.free()
            continue
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bmesh.ops.triangulate(bm, faces=bm.faces, quad_method='BEAUTY', ngon_method='EAR_CLIP')
        me = bpy.data.meshes.new("pref%02d" % (idx + 1))
        bm.to_mesh(me)
        tris += len(me.polygons)
        bm.free()
        for p in me.polygons:
            p.use_smooth = False
        me.materials.append(mats[key])
        ob = bpy.data.objects.new("pref%02d" % (idx + 1), me)
        bpy.context.scene.collection.objects.link(ob)
        objs.append(ob)
    print("LAND objs", len(objs), "tris", tris)
    return objs


# ---------------------------------------------------------------- 経路
TOKYO = Vector((*proj(139.70, 35.68), 0))
OSAKA = Vector((*proj(135.50, 34.69), 0))


def path_pt(kind, t):
    """kind: out（東京→大阪、北側にふくらむ）/ back（大阪→東京、南側にふくらむ）。戻り値: 位置, 接線"""
    a, b = (OSAKA, TOKYO) if kind == "out" else (TOKYO, OSAKA)  # 実車: 加古川側（関西）→ 江東側（関東）、空車はその逆
    d = (b - a)
    nrm = Vector((-d.y, d.x, 0)).normalized()
    sgn = 1 if kind == "out" else -1  # out は進行方向の左 = 北寄り? 下で確認
    off = -7.0  # どちらも進行方向の右側へふくらむ（行きと帰りで反対側になる）
    c = (a + b) / 2 + Vector((-d.y, d.x, 0)).normalized() * off
    p = (1 - t) ** 2 * a + 2 * (1 - t) * t * c + t * t * b
    tg = 2 * (1 - t) * (c - a) + 2 * t * (b - c)
    z = H_LAND + 0.55 + 1.6 * math.sin(math.pi * t)
    return Vector((p.x, p.y, z)), tg.normalized()


def ribbon(name, kind, t0, t1, width, mat, steps=40, head=False):
    bm = bmesh.new()
    n = max(2, int(steps * (t1 - t0)) + 1)
    L, R = [], []
    for i in range(n):
        t = t0 + (t1 - t0) * i / (n - 1)
        p, tg = path_pt(kind, t)
        side = Vector((-tg.y, tg.x, 0)).normalized()
        w = width / 2
        L.append(bm.verts.new(p + side * w + Vector((0, 0, 0.0))))
        R.append(bm.verts.new(p - side * w))
    fs = []
    for i in range(n - 1):
        fs.append(bm.faces.new((L[i], R[i], R[i + 1], L[i + 1])))
    # 厚み（下の面に薄く押し出す）
    ext = bmesh.ops.extrude_face_region(bm, geom=fs)
    vv = [e for e in ext["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=vv, vec=(0, 0, -0.18))
    if head:
        p, tg = path_pt(kind, t1)
        side = Vector((-tg.y, tg.x, 0)).normalized()
        tip = p + tg * width * 1.5
        v0, v1, v2 = bm.verts.new(p + side * width * 0.95), bm.verts.new(p - side * width * 0.95), bm.verts.new(tip)
        f = bm.faces.new((v0, v1, v2))
        e2 = bmesh.ops.extrude_face_region(bm, geom=[f])
        bmesh.ops.translate(bm, verts=[e for e in e2["geom"] if isinstance(e, bmesh.types.BMVert)], vec=(0, 0, -0.18))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def disc(name, p, r, mat):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=28, radius1=r, radius2=r, depth=0.35)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(p.x, p.y, H_LAND + 0.17))
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _base_socket(m):
    """材質の基本色の入力口。Mix ノード（頂点カラー × 色）につながっているときは、Mix の色の入力口（つながっていない方）"""
    bc = m.node_tree.nodes["Principled BSDF"].inputs["Base Color"]
    if bc.links and bc.links[0].from_node.bl_idname == 'ShaderNodeMix':
        for i in (7, 6):
            sk = bc.links[0].from_node.inputs[i]
            if not sk.links:
                return sk
    return bc


def set_base(m, col):
    _base_socket(m).default_value = col


def get_base(m):
    return _base_socket(m).default_value


def load_truck(name, kind):
    p = os.path.join(OUT, "glb", "lo" if "--lodtruck" in argv else "", name + ".glb")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=p)
    new = [o for o in bpy.data.objects if o not in before]
    for o in new:  # 運転席の橙は空車の橙と紛らわしいので、薄い青灰にする
        for m in getattr(o.data, "materials", []):
            if m and m.name.startswith("v2_accent") and m.use_nodes:
                set_base(m, hexcol("#aebbd0"))
                m.diffuse_color = hexcol("#aebbd0")
    root = bpy.data.objects.new("truck_" + kind, None)
    bpy.context.scene.collection.objects.link(root)
    for o in new:
        if o.parent is None:
            o.parent = root
    return root, new


TRUCK_SCALE = 1.0  # 12 m の車体 → 約 9 単位（誇張した大きさ）


def place_truck(root, kind, t, fade=True):
    p, tg = path_pt(kind, t)
    root.location = p + Vector((0, 0, 0.0))
    root.rotation_euler = (0, 0, math.atan2(tg.x, -tg.y))
    s = TRUCK_SCALE
    if fade:
        f = min(1.0, t / 0.08, (1 - t) / 0.08)
        s *= max(0.001, f * f * (3 - 2 * f))
    root.scale = (s, s, s)


def build_scene():
    sc = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    land = build_land()
    loaded_m = mk("arrow_loaded", PAL["loaded"], 0.5, 0, 0.25)
    empty_m = mk("arrow_empty", PAL["empty"], 0.5, 0, 0.2)
    node_m = mk("node", PAL["node"], 0.6)
    global mat
    arrows = []
    mat = loaded_m
    arrows.append(ribbon("arrow_loaded", "out", 0.06, 0.88, 2.3, loaded_m, head=True))
    mat = empty_m
    # 点線: 長さ 0.045、間 0.03（経路の割合）
    t = 0.12
    k = 0
    while t < 0.9:
        arrows.append(ribbon("dash%02d" % k, "back", t, min(t + 0.045, 0.9), 1.9, empty_m))
        t += 0.075
        k += 1
    arrows.append(ribbon("dash_head", "back", 0.915, 0.94, 1.9, empty_m, head=True))
    nodes = [disc("node_kanto", TOKYO, 1.7, node_m), disc("node_kansai", OSAKA, 1.7, node_m)]
    return land, arrows, nodes


def setup_render(w, h, still=True):
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_EEVEE'
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = 'Standard'
    try:
        sc.eevee.taa_render_samples = 48 if still else 16
    except Exception:
        pass
    for prop in ("use_gtao", "use_raytracing"):
        try:
            setattr(sc.eevee, prop, True)
        except Exception:
            pass
    wd = bpy.data.worlds.new("w")
    wd.use_nodes = True
    bg = wd.node_tree.nodes["Background"]
    bg.inputs[0].default_value = hexcol("#8d9db5")
    bg.inputs[1].default_value = 0.6
    sc.world = wd


CAM = {}


def setup_scene_env(aspect=16 / 9):
    sc = bpy.context.scene
    # 海
    bpy.ops.mesh.primitive_plane_add(size=1)
    sea = bpy.context.active_object
    sea.name = "sea"
    sea.scale = (900, 900, 1)
    sea.location = (0, -10, Z_SEA)
    sm = mk("sea", PAL["sea"], 0.55)
    nt = sm.node_tree
    tc = nt.nodes.new("ShaderNodeTexCoord")
    gr = nt.nodes.new("ShaderNodeTexGradient")
    gr.gradient_type = 'SPHERICAL'
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / 150, 1 / 150, 1)
    cr = nt.nodes.new("ShaderNodeValToRGB")
    cr.color_ramp.elements[0].color = hexcol(PAL["shelf"])
    cr.color_ramp.elements[1].color = hexcol("#1a2c48")
    cr.color_ramp.elements[1].position = 0.75
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], gr.inputs["Vector"])
    nt.links.new(gr.outputs["Fac"], cr.inputs["Fac"])
    nt.links.new(cr.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    sea.data.materials.append(sm)
    # 光
    sun = bpy.data.lights.new("sun", 'SUN')
    sun.energy = 3.6
    sun.angle = math.radians(10)
    so = bpy.data.objects.new("sun", sun)
    so.rotation_euler = (math.radians(52), math.radians(8), math.radians(-30))
    sc.collection.objects.link(so)
    fill = bpy.data.lights.new("fill", 'SUN')
    fill.energy = 0.9
    fo = bpy.data.objects.new("fill", fill)
    fo.rotation_euler = (math.radians(60), 0, math.radians(150))
    sc.collection.objects.link(fo)
    cam = bpy.data.cameras.new("cam")
    cam.type = 'ORTHO'
    cam.ortho_scale = 190
    cam.clip_start, cam.clip_end = 1, 2000
    co = bpy.data.objects.new("cam", cam)
    sc.collection.objects.link(co)
    tgt = bpy.data.objects.new("tgt", None)
    sc.collection.objects.link(tgt)
    c = co.constraints.new('TRACK_TO')
    c.target = tgt
    c.track_axis, c.up_axis = 'TRACK_NEGATIVE_Z', 'UP_Y'
    sc.camera = co
    CAM.update(co=co, cam=cam, tgt=tgt)


def aim(az_deg=38.0, pitch=48.0, center=(2, 3), scale=118.0, dist=600):
    """az_deg: カメラの方位（0 = 南から北を見る、正は東側へ回る）"""
    co, cam, tgt = CAM["co"], CAM["cam"], CAM["tgt"]
    az = math.radians(az_deg)
    el = math.radians(pitch)
    tgt.location = (center[0], center[1], 0.8)
    d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    co.location = tgt.location + d * dist
    cam.ortho_scale = scale


def merge_materials():
    """glb 用: 陸・トラックの色を頂点カラー（元の AO の灰色 × 材質色）に焼き、材質を共有の 2 つ（艶消し・ガラス）にまとめる。
    矢印 2 つと節点の材質はそのまま。合計 5 材質"""
    matte = mk("dio_matte", "#ffffff", 0.8)
    glass = mk("dio_glass", "#ffffff", 0.2)
    for ob in bpy.data.objects:
        if ob.type != 'MESH' or ob.name == "sea" or ob.name.startswith(("arrow", "dash", "node")):
            continue
        me = ob.data
        old = None
        for a in me.color_attributes:
            old = a
            break
        grey = {}
        if old is not None:
            for li, lo in enumerate(me.loops):
                c = old.data[li].color if old.domain == 'CORNER' else old.data[lo.vertex_index].color
                grey[li] = (c[0], c[1], c[2])
        cols = []
        for m in me.materials:
            b = get_base(m) if m and m.use_nodes else (1, 1, 1, 1)
            cols.append((b[0], b[1], b[2]))
        names = [m.name if m else "" for m in me.materials]
        new = me.color_attributes.new("Col", 'FLOAT_COLOR', 'CORNER')
        mi = []
        for pl in me.polygons:
            c = cols[pl.material_index] if cols else (1, 1, 1)
            for li in pl.loop_indices:
                g = grey.get(li, (1, 1, 1))
                new.data[li].color = (c[0] * g[0], c[1] * g[1], c[2] * g[2], 1.0)
            mi.append(1 if "glass" in names[pl.material_index] else 0)
        if old is not None:
            me.color_attributes.remove(old)
        me.color_attributes.active_color = new
        me.color_attributes.render_color_index = me.color_attributes.find("Col")
        me.materials.clear()
        me.materials.append(matte)
        me.materials.append(glass)
        for pl, i in zip(me.polygons, mi):
            pl.material_index = i


def main():
    build_scene()
    setup_scene_env()
    trucks = {}
    for nm, kind, key in (("truck_10t_loaded", "out", "L1"), ("truck_10t_loaded", "out", "L2"), ("truck_10t_empty", "back", "E1")):
        trucks[key] = (load_truck(nm, key), kind)
    # 静止画の配置（コマ 0 の配置と同じ）
    place = {"L1": 0.30, "L2": 0.72, "E1": 0.52}

    def layout(u, fade=True):
        ph = {"L1": 0.0, "L2": 0.5, "E1": 0.25}
        for k, ((root, objs), kind) in trucks.items():
            place_truck(root, kind, (u + ph[k]) % 1.0, fade)

    def layout_still():
        for k, ((root, objs), kind) in trucks.items():
            place_truck(root, kind, place[k], fade=False)

    if "--glb" in argv or "--still" in argv or "--anim" in argv:
        pass
    if "--still" in argv:
        layout_still()
        aim()
        for w, h in ((1920, 1080), (1600, 900), (800, 450)):
            setup_render(w, h)
            sc = bpy.context.scene
            sc.render.filepath = os.path.join(OUT, "hero", "hero_%dx%d.png" % (w, h))
            bpy.ops.render.render(write_still=True)
            print("STILL", sc.render.filepath)
    if "--anim" in argv:
        n = int(argv[argv.index("--anim") + 1])
        sc = bpy.context.scene
        setup_render(960, 540, still=False)
        sc.render.fps = 24
        sc.render.filepath = os.path.join(OUT, "hero", "hero_loop_")
        frames = []
        for f in range(n):
            u = f / n
            layout(u)
            aim(az_deg=38 + 2.2 * math.sin(2 * math.pi * u), pitch=48 + 1.2 * math.cos(2 * math.pi * u),
                center=(2 + 3.5 * math.sin(2 * math.pi * u), 3 + 2.0 * math.cos(2 * math.pi * u)), scale=118 + 3 * math.sin(2 * math.pi * u))
            sc.frame_set(f + 1)
            sc.render.image_settings.file_format = 'PNG'
            sc.render.filepath = os.path.join(OUT, "hero", "frames", "f%04d.png" % f)
            bpy.ops.render.render(write_still=True)
        print("ANIM frames", n)
    if "--glb" in argv:
        layout_still()
        merge_materials()
        bpy.context.view_layer.update()
        for o in bpy.data.objects:
            o.select_set(False)
        keep = [o for o in bpy.data.objects if o.type == 'MESH' and o.name != "sea"]
        for o in keep:
            o.select_set(True)
        for k, ((root, objs), kind) in trucks.items():
            root.select_set(True)
        kw = dict(filepath=os.path.join(GLB_DIR, "japan_diorama.glb"), export_format='GLB', use_selection=True,
                  export_yup=True, export_apply=True, export_materials='EXPORT', export_cameras=False, export_lights=False)
        props = bpy.ops.export_scene.gltf.get_rna_type().properties
        if "export_vertex_color" in props:
            kw["export_vertex_color"] = 'ACTIVE'
        bpy.ops.export_scene.gltf(**kw)
        print("GLB", os.path.getsize(kw["filepath"]))
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, "hero", "diorama.blend"))


if __name__ == "__main__":
    main()
