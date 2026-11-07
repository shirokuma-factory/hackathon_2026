"""Backfull 説明用イラスト共通の関数（Blender 5.2 headless 用）。
render_media.py と同じ流儀（Cycles + GPU、透過 PNG、中立の照明）で、真上・斜め・線画の絵を撮る。
"""
import math
import os
import sys
import time

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MODELS = os.path.join(ROOT, "web", "models")
PROPS = os.path.join(MODELS, "props")
OUT = os.path.join(ROOT, "outputs", "illustrations_2026-11-07")

# 色は意味があるところだけに使う（画面案の深緑に合わせる）
TEAL = "#1f6b5e"
INK = "#26332f"
GREY = "#c9ceca"
GREY_DK = "#8d958f"
PAPER = "#f4f5f3"


def hexcol(h, a=1.0):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return (c[0], c[1], c[2], a)


def glb(name):
    p = os.path.join(MODELS, name + ".glb")
    return p if os.path.exists(p) else os.path.join(PROPS, name + ".glb")


def new_scene(res, samples=48, transparent=True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
            prefs.refresh_devices()
        except Exception:
            continue
        devs = [d for d in prefs.devices if d.type == kind and "NVIDIA" in d.name]
        if devs:
            for d in prefs.devices:
                d.use = d in devs
            break
    sc.render.engine = "CYCLES"
    sc.cycles.device = "GPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 4
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = transparent
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    return sc


def import_glb(name, frame=None):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=glb(name))
    new = [o for o in bpy.data.objects if o not in before]
    if frame is not None:
        bpy.context.scene.frame_set(frame)
    return new


def world(color="#ffffff", strength=0.6):
    w = bpy.data.worlds.new("W")
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs[0].default_value = hexcol(color)
    bg.inputs[1].default_value = strength
    bpy.context.scene.world = w


def sun(name, src, strength, angle=12):
    d = bpy.data.lights.new(name, "SUN")
    d.energy = strength
    d.angle = math.radians(angle)
    o = bpy.data.objects.new(name, d)
    bpy.context.scene.collection.objects.link(o)
    o.rotation_euler = (-Vector(src).normalized()).to_track_quat("-Z", "Y").to_euler()


def soft_lights():
    """真上から柔らかい主光と弱い補助光。"""
    world("#ffffff", 0.7)
    sun("key", (-0.25, -0.35, 1.0), 2.6, 25)
    sun("fill", (0.6, 0.2, 0.5), 0.6, 30)


def points(objs):
    pts = []
    dg = bpy.context.evaluated_depsgraph_get()
    for o in objs:
        if o.type != "MESH":
            continue
        eo = o.evaluated_get(dg)
        mw = eo.matrix_world
        pts += [mw @ Vector(c) for c in eo.bound_box]
    return pts


def camera_top(pts, tilt_deg=0.0, margin=1.12, rot_z=0.0, persp=False):
    """真上（tilt 0）か、少し倒したカメラ。正射影で外接の中心を画面中央に置く。"""
    sc = bpy.context.scene
    cd = bpy.data.cameras.new("_cam")
    cd.type = "PERSP" if persp else "ORTHO"
    cd.clip_start, cd.clip_end = 0.1, 2000
    cam = bpy.data.objects.new("_cam", cd)
    sc.collection.objects.link(cam)
    sc.camera = cam
    # 既定は車の前（-Y）が画面の上。傾けるときは後ろ側から見下ろす
    cam.rotation_euler = (math.radians(tilt_deg), 0, math.radians(180 + rot_z))
    bpy.context.view_layer.update()
    back = cam.matrix_world.to_3x3() @ Vector((0, 0, 1))
    ctr = Vector(((max(p.x for p in pts) + min(p.x for p in pts)) / 2,
                  (max(p.y for p in pts) + min(p.y for p in pts)) / 2,
                  (max(p.z for p in pts) + min(p.z for p in pts)) / 2))
    cam.location = ctr + back * 200
    bpy.context.view_layer.update()
    inv = cam.matrix_world.inverted()
    xs = [(inv @ p).x for p in pts]
    ys = [(inv @ p).y for p in pts]
    cd.ortho_scale = max(max(xs) - min(xs), max(ys) - min(ys)) * margin
    cx = (max(xs) + min(xs)) / 2
    cy = (max(ys) + min(ys)) / 2
    cam.location = cam.location + cam.matrix_world.to_3x3() @ Vector((cx, cy, 0))
    return cam


def render(path, fmt="PNG"):
    sc = bpy.context.scene
    sc.render.image_settings.file_format = fmt
    sc.render.image_settings.color_mode = "RGBA" if sc.render.film_transparent else "RGB"
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)


def flat_mat(name, color, rough=0.9):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = hexcol(color)
    b.inputs["Roughness"].default_value = rough
    return m


def recolor(objs, mapping, default=None):
    """材質名 -> 色 の対応で張り替える。mapping に無い材質は default（None なら元のまま）。"""
    cache = {}
    for o in objs:
        if o.type != "MESH":
            continue
        for slot in o.material_slots:
            nm = slot.material.name.split(".")[0] if slot.material else ""
            col = mapping.get(nm, default)
            if col is None:
                continue
            if col not in cache:
                cache[col] = flat_mat("m" + col, col)
            slot.material = cache[col]


def add_box(name, size, loc, color, rot_z=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    o.rotation_euler[2] = math.radians(rot_z)
    o.data.materials.append(flat_mat(name, color))
    return o


def freestyle(sc, thickness=1.6, color=INK, crease=140):
    """輪郭線（Freestyle）。"""
    sc.render.use_freestyle = True
    sc.render.line_thickness_mode = "ABSOLUTE"
    sc.render.line_thickness = thickness
    vl = sc.view_layers[0]
    vl.use_freestyle = True
    if len(vl.freestyle_settings.linesets) == 0:
        vl.freestyle_settings.linesets.new("L")
    s = vl.freestyle_settings.linesets[0]
    s.select_silhouette = True
    s.select_border = True
    s.select_crease = True
    if s.linestyle is None:
        s.linestyle = bpy.data.linestyles.new("ls")
    vl.freestyle_settings.crease_angle = math.radians(crease)
    s.linestyle.color = hexcol(color)[:3]
    s.linestyle.thickness = thickness


def camera_iso(pts, az=45.0, el=35.264, margin=1.15, res=(640, 400), shift=(0.0, 0.0)):
    """正射影の等角（既定）カメラ。az=0 で -Y 側から見る。外接を画面中央に収める。"""
    sc = bpy.context.scene
    cd = bpy.data.cameras.new("_cam")
    cd.type = "ORTHO"
    cd.clip_start, cd.clip_end = 0.1, 4000
    cam = bpy.data.objects.new("_cam", cd)
    sc.collection.objects.link(cam)
    sc.camera = cam
    cam.rotation_euler = (math.radians(90 - el), 0, math.radians(az))
    bpy.context.view_layer.update()
    back = cam.matrix_world.to_3x3() @ Vector((0, 0, 1))
    ctr = Vector(((max(p.x for p in pts) + min(p.x for p in pts)) / 2,
                  (max(p.y for p in pts) + min(p.y for p in pts)) / 2,
                  (max(p.z for p in pts) + min(p.z for p in pts)) / 2))
    cam.location = ctr + back * 300
    bpy.context.view_layer.update()
    inv = cam.matrix_world.inverted()
    xs = [(inv @ p).x for p in pts]
    ys = [(inv @ p).y for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    cd.ortho_scale = max(w, h * res[0] / res[1]) * margin
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    cam.location = cam.location + cam.matrix_world.to_3x3() @ Vector((cx + shift[0], cy + shift[1], 0))
    return cam


def bar(name, p0, p1, thick, color, mat=None):
    """点 p0 から p1 までの角棒。"""
    p0, p1 = Vector(p0), Vector(p1)
    d = p1 - p0
    bpy.ops.mesh.primitive_cube_add(size=1, location=(p0 + p1) / 2)
    o = bpy.context.active_object
    o.name = name
    o.scale = (thick, thick, d.length)
    o.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    o.data.materials.append(mat or flat_mat(name, color))
    return o


def cone(name, base, tip, radius, color):
    base, tip = Vector(base), Vector(tip)
    d = tip - base
    bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=radius, radius2=0, depth=d.length, location=(base + tip) / 2)
    o = bpy.context.active_object
    o.name = name
    o.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    o.data.materials.append(flat_mat(name, color))
    return o


def shadow_floor(size=60.0, z=0.0):
    """影だけを受ける床（透過背景のまま影が落ちる）。"""
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, z))
    f = bpy.context.active_object
    f.name = "_shadow"
    f.is_shadow_catcher = True
    return f
