"""A 案: 既存モデルの真上（と少し斜め）の透過 PNG。
blender -b -P a_topdown.py
出力: outputs/illustrations_2026-11-07/A_topdown/raw/<name>_<variant>_<style>.png（512px。256・128 は sheets 側で縮小）
style: orig=元の色 / mono=低彩度の単色 / line=白地+輪郭線
"""
import os
import sys
import time
import json
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
from mathutils import Vector
from bf_lib import *

OUTD = os.path.join(OUT, "A_topdown", "raw")
os.makedirs(OUTD, exist_ok=True)
RES = 512
LOG = {}
MONO = {"glass": "#8d958f", "charcoal": "#6a726d", "lamp": "#f2f2ee", "light": "#f2f2ee",
        "rubber": "#4a524e", "hub": "#b8beb9", "grey": "#a9afaa", "asphalt": "#d5d9d5"}


def lights():
    world("#ffffff", 0.45)
    sun("key", (-0.25, -0.35, 1.0), 1.7, 25)
    sun("fill", (0.6, 0.2, 0.5), 0.5, 30)


def bed_z(x=0.0, y=1.0):
    dg = bpy.context.evaluated_depsgraph_get()
    hit, loc, *_ = bpy.context.scene.ray_cast(dg, Vector((x, y, 20)), Vector((0, 0, -1)))
    return loc.z if hit else 1.4


def open_top(objs, y0=-2.0, y1=4.1, zcut=1.5):
    """ウイングを隠して荷台を上から見えるようにする。"""
    for o in objs:
        if "wing" in o.name:
            o.hide_render = True
            o.hide_viewport = True
    main = [o for o in objs if o.type == "MESH" and "wing" not in o.name][0]
    cut = add_box("_cutter", (2.0, 6.4, 3.0), (0, 1.0, 3.0), "#ffffff")  # z 1.5 以上の屋根を切り取る
    cut.hide_render = True
    m = main.modifiers.new("cut", "BOOLEAN")
    m.operation = "DIFFERENCE"
    m.solver = "EXACT"
    m.object = cut
    bpy.context.view_layer.update()
    z0 = bed_z(0, 1.0)
    slab = add_box("_slab", (2.0, y1 - y0, 0.06), (0, (y0 + y1) / 2, z0 - 0.02), "#c3c9c4")
    return [slab]
    bpy.context.view_layer.update()


def load_cargo(kind, ylo, yhi, width):
    """荷台に積み荷（深緑の箱）を置く。kind: pallets / parcels"""
    z0 = bed_z(0, (ylo + yhi) / 2)
    made = []
    n = 0
    y = ylo + 0.15
    step = 1.25
    while y + 1.1 <= yhi:
        for xs in ([-0.5, 0.5] if width > 1.8 else [0.0]):
            made.append(add_box(f"cargo{n}", (0.92, 1.08, 1.3), (xs, y + 0.55, z0 + 0.65), TEAL))
            n += 1
        y += step
    return made


def make_unknown():
    """車格が分からないときの記号。cab と箱だけの無地の車に「?」を載せる。"""
    out = [add_box("u_cab", (2.0, 1.8, 1.8), (0, -2.6, 0.9), "#9aa19b"),
           add_box("u_box", (2.0, 4.6, 2.2), (0, 0.4, 1.1), "#e4e7e3")]
    bpy.ops.object.text_add(location=(0, 0.4, 2.25))
    t = bpy.context.active_object
    t.rotation_euler[2] = math.radians(180)  # 画面の上が車の前なので、読める向きに回す
    t.data.body = "?"
    t.data.size = 2.6
    t.data.align_x = "CENTER"
    t.data.align_y = "CENTER"
    t.data.extrude = 0.05
    t.data.materials.append(flat_mat("q", "#6a726d"))
    out.append(t)
    return out


def shot(name, tag, tilt, style, frame=None, rot=0.0, loaded=None, bedspan=None, width=2.2, extra=None, mono=None, margin=1.10, ylim=None, xlim=None):
    t0 = time.time()
    sc = new_scene((RES, RES), samples=48)
    objs = import_glb(name, frame) if name != "unknown" else make_unknown()
    if loaded is not None:
        objs += open_top(objs)
    if style == "mono":
        recolor(objs, dict(MONO, **(mono or {})), default="#d3d7d3")
    if loaded:
        objs += load_cargo("pallets", bedspan[0], bedspan[1], width)
    if extra:
        objs += extra(objs) or []
    lights()
    pts = points([o for o in objs if not o.hide_render])
    if ylim:  # 一部だけを拡大するときは、その範囲の箱を外接として使う
        pts = [Vector((x, y, z)) for x in xlim for y in ylim for z in (0, 8)]
    camera_top(pts, tilt, margin, rot_z=rot)
    if style == "line":
        recolor([o for o in objs if not o.name.startswith(("cargo", "_slab", "q"))], {}, default="#fbfbfa")
        freestyle(sc, 2.0)
    p = os.path.join(OUTD, f"{name}_{tag}_{style}.png")
    render(p)
    LOG[os.path.basename(p)] = round(time.time() - t0, 1)


if __name__ == "__main__":
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    jobs = []
    DOOR = {"truck": TEAL, "asphalt": "#d5d9d5", "charcoal": "#c4c9c5"}
    for style in ("orig", "mono", "line"):
        jobs.append(("warehouse", "docks_top", 0, style, dict(mono=DOOR, margin=1.04)))
        jobs.append(("warehouse", "docks_tilt", 40, style, dict(mono=DOOR, rot=180, margin=1.04)))
        jobs.append(("warehouse", "docks_zoom", 50, style, dict(mono=DOOR, rot=180, margin=1.0, ylim=(-66, -20), xlim=(-26, 14))))
        jobs.append(("unknown", "symbol_top", 0, style, {}))
        jobs.append(("unknown", "symbol_tilt", 38, style, {}))
    for style in ("orig", "mono", "line"):
        for tilt, tg in ((0, "top"), (38, "tilt")):
            jobs.append(("truck_4t", f"empty_{tg}", tilt, style, dict(loaded=False)))
            jobs.append(("truck_4t", f"loaded_{tg}", tilt, style, dict(loaded=True, bedspan=(-2.0, 4.0))))
            jobs.append(("truck_4t", f"closed_{tg}", tilt, style, {}))
            jobs.append(("truck", f"closed_{tg}", tilt, style, {}))
            for n in ("box_2t", "kei_van"):
                jobs.append((n, f"closed_{tg}", tilt, style, {}))
            jobs.append(("pallet_t11", f"plain_{tg}", tilt, style, {}))
    for name, tag, tilt, style, kw in jobs:
        if only and name not in only:
            continue
        shot(name, tag, tilt, style, **kw)
    print("LOG", json.dumps(LOG))
