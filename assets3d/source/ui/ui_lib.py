"""UI 用 3D アイコン共通部品（Blender 5.2 headless、手続き生成のみ）。
光は左上の夕方の低い太陽、彩度を抑えた実物の材質、背景透明。
"""
import math
import os

import bpy
import numpy as np
from contextlib import contextmanager
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.join(ROOT, "outputs", "ui_icons_2026-11-07")

PAR = [None]
_MATS = {}
PI = math.pi


def hexcol(h, a=1.0):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return (c[0], c[1], c[2], a)


def mat(h, rough=0.5, metal=0.0, em=None, ems=1.0, coat=0.0):
    key = (h, rough, metal, em, ems, coat)
    if key in _MATS:
        return _MATS[key]
    m = bpy.data.materials.new("m" + str(len(_MATS)))
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = hexcol(h)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    if coat:
        p.inputs["Coat Weight"].default_value = coat
        p.inputs["Coat Roughness"].default_value = 0.1
    if em:
        p.inputs["Emission Color"].default_value = hexcol(em)
        p.inputs["Emission Strength"].default_value = ems
    _MATS[key] = m
    return m


def _fin(o, m, bevel, seg=3, smooth=True):
    if m is not None:
        o.data.materials.append(m)
    if bevel:
        b = o.modifiers.new("bev", "BEVEL")
        b.width = bevel
        b.segments = seg
        b.harden_normals = True
    if smooth and hasattr(o.data, "polygons"):
        for p in o.data.polygons:
            p.use_smooth = True
    if PAR[0] is not None:
        o.parent = PAR[0]
    return o


def box(size, loc, m, bevel=0.015, rot=(0, 0, 0), seg=3):
    bpy.ops.mesh.primitive_cube_add(size=1)
    o = bpy.context.object
    o.scale = size
    bpy.ops.object.transform_apply(scale=True)
    o.location = loc
    o.rotation_euler = rot
    return _fin(o, m, bevel, seg)


def cyl(r, depth, loc, m, rot=(0, 0, 0), bevel=0.0, verts=64):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=depth, vertices=verts)
    o = bpy.context.object
    o.location = loc
    o.rotation_euler = rot
    return _fin(o, m, bevel)


def sphere(r, loc, m, seg=48):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, segments=seg, ring_count=seg // 2)
    o = bpy.context.object
    o.location = loc
    return _fin(o, m, 0)


def cone(r1, r2, depth, loc, m, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_cone_add(radius1=r1, radius2=r2, depth=depth, vertices=48)
    o = bpy.context.object
    o.location = loc
    o.rotation_euler = rot
    return _fin(o, m, 0)


def torus(R, r, loc, m, rot=(0, 0, 0)):
    bpy.ops.mesh.primitive_torus_add(major_radius=R, minor_radius=r, major_segments=64, minor_segments=16)
    o = bpy.context.object
    o.location = loc
    o.rotation_euler = rot
    return _fin(o, m, 0)


def text(s, size, loc, m, rot=(PI / 2, 0, 0), ext=0.004, align="CENTER"):
    bpy.ops.object.text_add()
    o = bpy.context.object
    o.data.body = s
    o.data.size = size
    o.data.extrude = ext
    o.data.align_x = align
    o.data.align_y = "CENTER"
    o.location = loc
    o.rotation_euler = rot
    return _fin(o, m, 0, smooth=False)


def sector(r, a0, a1, y, m, thick=0.012):
    """時計の面（XZ 平面、-y 向き）の扇形。角度は12時から時計回りの度。"""
    n = max(4, int(abs(a1 - a0) / 4))
    vs = [(0, y, 0)]
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        vs.append((r * math.sin(a), y, r * math.cos(a)))
    fs = [(0, i, i + 1) for i in range(1, n + 1)]
    me = bpy.data.meshes.new("sec")
    me.from_pydata(vs, [], fs)
    me.update()
    o = bpy.data.objects.new("sec", me)
    bpy.context.collection.objects.link(o)
    s = o.modifiers.new("sol", "SOLIDIFY")
    s.thickness = thick
    return _fin(o, m, 0)


@contextmanager
def group(loc=(0, 0, 0), rot=(0, 0, 0)):
    e = bpy.data.objects.new("grp", None)
    bpy.context.collection.objects.link(e)
    e.location = loc
    e.rotation_euler = rot
    if PAR[0] is not None:
        e.parent = PAR[0]
    prev = PAR[0]
    PAR[0] = e
    try:
        yield e
    finally:
        PAR[0] = prev


def seg(a, b, w, z, m, h=0.01):
    """2 点間の細い帯（XY 平面）。"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    return box((L, w, h), ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, z), m, bevel=0,
               rot=(0, 0, math.atan2(dy, dx)))


def look(o, target):
    d = Vector(target) - o.location
    o.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def setup_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _MATS.clear()
    PAR[0] = None
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
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 6
    sc.render.film_transparent = True
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA"
    sc.view_settings.view_transform = "AgX"
    try:
        sc.view_settings.look = "AgX - Base Contrast"
    except Exception:
        pass
    w = bpy.data.worlds.new("W")
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sp = nt.nodes.new("ShaderNodeSeparateXYZ")
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs[1].default_value = -1.0
    mr.inputs[2].default_value = 1.0
    cr = nt.nodes.new("ShaderNodeValToRGB")
    e = cr.color_ramp.elements
    e[0].position = 0.0
    e[0].color = hexcol("#3a3631")
    e[1].position = 1.0
    e[1].color = hexcol("#9db6d4")
    m2 = e.new(0.5)
    m2.color = hexcol("#d6d3cc")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs[1].default_value = 0.8
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(tc.outputs["Generated"], sp.inputs[0])
    nt.links.new(sp.outputs["Z"], mr.inputs[0])
    nt.links.new(mr.outputs[0], cr.inputs[0])
    nt.links.new(cr.outputs[0], bg.inputs[0])
    nt.links.new(bg.outputs[0], out.inputs[0])
    sc.world = w
    sun = bpy.data.lights.new("sun", "SUN")
    sun.energy = 4.2
    sun.angle = math.radians(5)
    sun.color = (1.0, 0.88, 0.72)
    so = bpy.data.objects.new("sun", sun)
    bpy.context.collection.objects.link(so)
    d = -Vector((-3.0, -2.4, 6.5))
    so.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    fl = bpy.data.lights.new("fill", "AREA")
    fl.energy = 260
    fl.size = 6
    fl.color = (0.75, 0.86, 1.0)
    fo = bpy.data.objects.new("fill", fl)
    bpy.context.collection.objects.link(fo)
    fo.location = (6, -5, 3.5)
    look(fo, (0, 0, 0.8))
    bpy.ops.mesh.primitive_plane_add(size=60)
    g = bpy.context.object
    g.is_shadow_catcher = True
    cam = bpy.data.cameras.new("cam")
    cam.type = "ORTHO"
    cam.ortho_scale = 14
    co = bpy.data.objects.new("cam", cam)
    bpy.context.collection.objects.link(co)
    az, el = math.radians(38.7), math.radians(28)
    dirv = Vector((math.cos(el) * math.sin(az), -math.cos(el) * math.cos(az), math.sin(el)))
    co.location = dirv * 30
    look(co, (0, 0, 0))
    sc.camera = co
    return sc, cam


def render_to(sc, path, res, samples):
    sc.render.resolution_x = sc.render.resolution_y = res
    sc.cycles.samples = samples
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)


def bbox_of(path, thr=0.3):
    img = bpy.data.images.load(path)
    w, h = img.size
    a = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    al = a.reshape(h, w, 4)[:, :, 3]
    ys, xs = np.where(al > thr)
    # Blender の画像は下から数える
    return xs.min() / w, xs.max() / w, ys.min() / h, ys.max() / h


def autofit(sc, cam, scratch, fill=0.82):
    """低解像度で一度撮り、被写体が中央・一定の占有率になるように正射影の幅とシフトを決める。"""
    render_to(sc, scratch, 384, 12)
    x0, x1, y0, y1 = bbox_of(scratch)
    cx, cy = (x0 + x1) / 2 - 0.5, (y0 + y1) / 2 - 0.5
    mx = max(x1 - x0, y1 - y0)
    old = cam.ortho_scale
    new = old * mx / fill
    cam.shift_x = cx * old / new
    cam.shift_y = cy * old / new
    cam.ortho_scale = new
