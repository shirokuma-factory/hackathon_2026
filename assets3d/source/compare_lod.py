"""高詳細版と低ポリ版の glb を読み込み、同じカメラで並べて撮る。
使い方（画面なし）:
    blender --background --python assets3d/source/compare_lod.py -- <出力ディレクトリ> [名前 ...]
  出力: <dir>/<name>.png（左 = hi、右 = lo）。一覧への組み立ては compare_lod_sheet.py（Pillow）。
  glb ファイルそのものを読み込むので、書き出し後のノード・材質・テクスチャ・アニメーションの確認も兼ねる。"""
import bpy
import math
import os
import sys
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
HI = os.path.join(ROOT, "web", "models")
LO = os.path.join(HI, "lo")


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    for c in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.actions):
        for d in list(c):
            c.remove(d)


def load(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def corners(objs):
    bpy.context.view_layer.update()
    return [o.matrix_world @ Vector(c) for o in objs if o.type == 'MESH' for c in o.bound_box]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    out, names = argv[0], argv[1:]
    os.makedirs(out, exist_ok=True)
    if not names:
        names = sorted(f[:-4] for f in os.listdir(LO) if f.endswith(".glb"))
    sc = bpy.context.scene
    sc.render.engine = 'BLENDER_EEVEE'
    W, H = 1000, 500
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.image_settings.file_format = 'PNG'
    sc.view_settings.view_transform = 'Standard'
    back = Vector((0.75, -1.0, 0.62)).normalized()
    for n in names:
        clear()
        world = bpy.data.worlds.get("W") or bpy.data.worlds.new("W")
        world.use_nodes = True
        bg = world.node_tree.nodes["Background"]
        bg.inputs[0].default_value = (0.0033, 0.006, 0.0145, 1)
        bg.inputs[1].default_value = 1.1
        sc.world = world
        hi, lo = load(os.path.join(HI, n + ".glb")), load(os.path.join(LO, n + ".glb"))
        cs = corners(hi)
        ext = max(max(c[k] for c in cs) - min(c[k] for c in cs) for k in range(3))
        # 右（画面の横方向）に ext * 1.15 ずらして lo を置く
        cam_d = bpy.data.cameras.new("c")
        cam_d.type, cam_d.clip_start, cam_d.clip_end = 'ORTHO', 1, 5000
        cam = bpy.data.objects.new("c", cam_d)
        sc.collection.objects.link(cam)
        sc.camera = cam
        cam.rotation_euler = (-back).to_track_quat('-Z', 'Y').to_euler()
        R = cam.rotation_euler.to_matrix()
        right, up = R @ Vector((1, 0, 0)), R @ Vector((0, 1, 0))
        shift = right * ext * 1.15
        for o in lo:
            if o.parent is None:
                o.location = o.location + shift
        sun_d = bpy.data.lights.new("s", 'SUN')
        sun_d.energy, sun_d.angle = 4.0, math.radians(3)
        sun = bpy.data.objects.new("s", sun_d)
        sun.rotation_euler = (math.radians(50), 0, math.radians(22))
        sc.collection.objects.link(sun)
        # 地面
        gm = bpy.data.materials.new("g")
        gm.use_nodes = True
        gm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.008, 0.014, 0.04, 1)
        bpy.ops.mesh.primitive_plane_add(size=1)
        g = bpy.context.active_object
        g.data.materials.append(gm)
        allc = corners(hi) + corners(lo)
        ctr = sum(allc, Vector()) / len(allc)
        g.location = (ctr.x, ctr.y, -0.03)
        g.scale = (ext * 8, ext * 8, 1)
        xs = [c.dot(right) for c in allc]
        ys = [c.dot(up) for c in allc]
        cr, cu = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        cam.location = right * cr + up * cu + back * (ctr.dot(back) + ext * 2)
        cam_d.ortho_scale = max(max(xs) - min(xs), (max(ys) - min(ys)) * W / H) * 1.1
        sc.frame_set(1)
        sc.render.filepath = os.path.join(out, n + ".png")
        bpy.ops.render.render(write_still=True)
        print("rendered", n)


if bpy.app.background and __name__ == "__main__":
    main()
