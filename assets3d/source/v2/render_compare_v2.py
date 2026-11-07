"""現行の glb と v2 の glb を、地図の典型的なカメラ（傾き 45°、遠近、暗い地面）で並べて描くための静止画を出す。

使い方: blender -b -P assets3d/source/v2/render_compare_v2.py -- <glb v2 の場所> <現行 glb の場所> <出力先> [--lod]
出力: <出力先>/<名前>_<cur|v2>_<med|small>.png （med = 画面の幅の約 80%、small = 約 40 px の長さ）
"""
import bpy
import math
import os
import sys
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
V2, CUR, OUT = [os.path.abspath(a) for a in argv[:3]]
USE_LOD = "--lod" in argv
os.makedirs(OUT, exist_ok=True)

# (v2 の名前, 現行の名前)
PAIRS = [("truck_10t", "truck"), ("truck_4t", "truck_4t"), ("kei_van", "kei_van"), ("container_ship", "container_ship"),
         ("roro_ship", "roro_ship"), ("freight_train", "freight_train"), ("cargo_plane", "cargo_plane"),
         ("truck_10t_empty", "truck"), ("truck_10t_loaded", "truck"), ("truck_4t_empty", "truck_4t"), ("truck_4t_loaded", "truck_4t")]


def hexcol(h):
    h = h.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return (f(r), f(g), f(b), 1.0)


def setup():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_EEVEE'
    sc.render.film_transparent = False
    sc.view_settings.view_transform = 'Standard'
    w = bpy.data.worlds.new("w")
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs[0].default_value = hexcol("#9aa6b8")
    bg.inputs[1].default_value = 0.55
    sc.world = w
    sun = bpy.data.lights.new("sun", 'SUN')
    sun.energy = 3.2
    sun.angle = math.radians(8)
    so = bpy.data.objects.new("sun", sun)
    so.rotation_euler = (math.radians(48), 0, math.radians(-35))
    sc.collection.objects.link(so)
    cam = bpy.data.cameras.new("cam")
    cam.lens = 60
    cam.clip_end = 5000
    co = bpy.data.objects.new("cam", cam)
    sc.collection.objects.link(co)
    sc.camera = co
    gm = bpy.data.materials.new("g")
    gm.use_nodes = True
    gm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = hexcol("#1b2536")
    gm.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 1.0
    return sc, co, cam, gm


def import_glb(p):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=p)
    new = [o for o in bpy.data.objects if o not in before]
    root = bpy.data.objects.new("root", None)
    bpy.context.scene.collection.objects.link(root)
    for o in new:
        if o.parent is None:
            o.parent = root
    return root, new


def bbox(objs):
    pts = []
    for o in objs:
        if o.type == 'MESH':
            pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
    mn = Vector((min(p[i] for p in pts) for i in range(3)))
    mx = Vector((max(p[i] for p in pts) for i in range(3)))
    return mn, mx


sc, co, cam, gm = setup()
tile = {"med": (480, 360, 0.80), "small": (120, 90, 0.42)}
for v2n, curn in PAIRS:
    for tag, base in (("v2", os.path.join(V2, "lo") if USE_LOD else V2), ("cur", CUR)):
        name = v2n if tag == "v2" else curn
        p = os.path.join(base, name + ".glb")
        if not os.path.exists(p):
            continue
        if tag == "cur" and os.path.exists(os.path.join(OUT, "%s_cur_med.png" % v2n)) and v2n.endswith(("empty", "loaded")):
            pass
        root, objs = import_glb(p)
        root.rotation_euler = (0, 0, math.radians(-125))  # 進行方向は右上へ（地図でよくある向き）
        bpy.context.view_layer.update()
        mn, mx = bbox(objs)
        ctr = (mn + mx) / 2
        root.location = -Vector((ctr.x, ctr.y, 0)) + Vector((0, 0, 0))
        bpy.context.view_layer.update()
        mn, mx = bbox(objs)
        ext = max(mx.x - mn.x, mx.y - mn.y)
        gp = bpy.data.objects.new("gp", None)
        bpy.ops.mesh.primitive_plane_add(size=1)
        gp = bpy.context.active_object
        gp.scale = (ext * 20, ext * 20, 1)
        gp.location = (0, 0, -0.02)
        gp.data.materials.append(gm)
        for key, (w, h, fill) in tile.items():
            sc.render.resolution_x, sc.render.resolution_y = w, h
            sc.render.resolution_percentage = 100
            sc.render.use_simplify = False
            pitch = math.radians(45)
            # 画面上の長辺がタイルの幅 * fill になる距離（近似）
            R = ext * 0.62
            d = R / (fill * 0.5 * (36 / cam.lens) * 0.95)
            co.location = Vector((0, -math.cos(pitch) * d, math.sin(pitch) * d))
            co.rotation_euler = (math.radians(45), 0, 0)
            sc.render.filepath = os.path.join(OUT, "%s_%s_%s.png" % (v2n, tag, key))
            bpy.ops.render.render(write_still=True)
        bpy.data.objects.remove(gp)
        for o in objs + [root]:
            bpy.data.objects.remove(o)
print("RENDER_DONE")
