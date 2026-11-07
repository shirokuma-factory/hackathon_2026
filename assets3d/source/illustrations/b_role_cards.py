"""B 案: ホーム画面の役割カード 2 枚（荷主=荷物+寸法線 / 運送会社=積載の実線矢印+帰りの破線矢印）。
blender -b -P b_role_cards.py
3 つの様式: line=Freestyle 線画 / clay=やわらかい粘土風 / iso=単純な等角図（ベタ塗り）
出力: outputs/illustrations_2026-11-07/B_role_cards/raw/<role>_<style>.png（640x400、2x 用。1x は縮小）
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

OUTD = os.path.join(OUT, "B_role_cards", "raw")
os.makedirs(OUTD, exist_ok=True)
RES = (640, 400)
LOG = {}

PAL = {
    "line": dict(body="#fbfbfa", cargo="#ffffff", accent=TEAL, dark=INK, tape="#e3e6e2"),
    "clay": dict(body="#eceee9", cargo="#d8c9ae", accent="#3f7f72", dark="#8a948e", tape="#cfc3aa"),
    "iso": dict(body="#e4e7e3", cargo="#c9b48c", accent=TEAL, dark="#8d958f", tape="#b39d74"),
}
VEH = {"glass": "#8d958f", "charcoal": "#6a726d", "rubber": "#4a524e", "hub": "#b8beb9",
       "lamp": "#f2f2ee", "light": "#f2f2ee", "grey": "#a9afaa"}


def setup(style):
    sc = new_scene(RES, samples=64 if style != "line" else 40)
    if style == "clay":
        world("#ffffff", 0.9)
        sun("key", (-0.5, -0.6, 1.0), 1.6, 40)  # 大きい角度の太陽で影をやわらかくする
        sun("fill", (0.8, 0.1, 0.4), 0.4, 40)
        sc.cycles.max_bounces = 6
    elif style == "iso":
        world("#ffffff", 0.35)
        sun("key", (-0.6, -0.5, 0.9), 2.4, 3)
        sun("fill", (0.9, -0.1, 0.3), 0.7, 10)
    else:
        world("#ffffff", 0.55)
        sun("key", (-0.4, -0.5, 1.0), 1.6, 25)
    return sc


def two_pass(sc, style, main, accent, tag):
    """線画は輪郭線を主役にだけ付けたいので、主役と強調（矢印・寸法線）を別に撮って sheet_b で重ねる。"""
    if style != "line":
        render(os.path.join(OUTD, f"{tag}_{style}.png"))
        return
    for o in accent:
        o.hide_render = True
    freestyle(sc, 2.0)
    sc.view_layers[0].freestyle_settings.linesets[0].select_crease = False
    render(os.path.join(OUTD, f"{tag}_line_main.png"))
    for o in accent:
        o.hide_render = False
    for o in main:
        o.hide_render = True
    sc.render.use_freestyle = False
    render(os.path.join(OUTD, f"{tag}_line_acc.png"))


def dim_line(name, a, b, tick_dir, color, thick=0.008, tick=0.035):
    a, b, t = Vector(a), Vector(b), Vector(tick_dir).normalized() * tick
    return [bar(name, a, b, thick, color), bar(name + "_t0", a - t, a + t, thick, color),
            bar(name + "_t1", b - t, b + t, thick, color)]


def shipper(style):
    sc = setup(style)
    P = PAL[style]
    objs = import_glb("box_120")
    X, Y, Z = 0.2, 0.25, 0.3
    if style != "line":
        recolor(objs, {"kraft": P["cargo"], "tape": P["tape"]})
    else:
        recolor(objs, {"kraft": "#fbfbfa", "tape": "#dfe3df"})
    c = P["accent"]
    off = 0.09
    dims = dim_line("w", (-X, -Y - off, 0), (X, -Y - off, 0), (0, 1, 0), c)
    dims += dim_line("d", (X + off, -Y, 0), (X + off, Y, 0), (1, 0, 0), c)
    dims += dim_line("h", (-X - off, -Y - off, 0), (-X - off, -Y - off, Z), (1, 1, 0), c)
    if style == "clay":
        shadow_floor(8)
    pts = points(objs + dims)
    camera_iso(pts, az=40 if style != "iso" else 45, el=30 if style != "iso" else 35.264, margin=1.30, res=RES)
    two_pass(sc, style, objs, dims, "shipper")


def carrier(style):
    sc = setup(style)
    P = PAL[style]
    objs = import_glb("truck_4t")
    for o in objs:
        if o.parent is None:
            o.rotation_mode = "XYZ"
            o.rotation_euler[2] = math.radians(90)  # 前（-Y）を +X へ向ける
    if style == "line":
        recolor(objs, dict(VEH, glass="#e6e9e5", hub="#d5d9d5"), default="#fbfbfa")
    else:
        recolor(objs, VEH, default=P["body"])
    acc = P["accent"]
    extra = []
    # 左のトラックが積んで出る向きに実線矢印（右向き）、奥の車線に空車の帰りの破線矢印（左向き）
    lane1, lane2 = -3.8, 3.8
    th, z = 0.38, 0.04
    extra.append(bar("solid", (1.8, lane1, z), (6.4, lane1, z), th, acc))
    extra.append(cone("solid_head", (6.4, lane1, z), (8.8, lane1, z), 1.0, acc))
    x = 8.8
    while x > -6.0:
        extra.append(bar("dash", (x, lane2, z), (x - 1.3, lane2, z), th, acc))
        x -= 2.2
    extra.append(cone("dash_head", (-6.2, lane2, z), (-8.6, lane2, z), 1.0, acc))
    for o in objs:
        if o.parent is None:
            o.location.y = lane1
            o.location.x = -3.6
    bpy.context.view_layer.update()
    if style == "clay":
        shadow_floor(80)
    pts = points(objs + extra)
    camera_iso(pts, az=-25 if style != "iso" else -45, el=52 if style != "iso" else 45, margin=1.1, res=RES)
    two_pass(sc, style, objs, extra, "carrier")


if __name__ == "__main__":
    for st in ("line", "clay", "iso"):
        t0 = time.time()
        shipper(st)
        LOG[f"shipper_{st}"] = round(time.time() - t0, 1)
        t0 = time.time()
        carrier(st)
        LOG[f"carrier_{st}"] = round(time.time() - t0, 1)
    print("LOG", json.dumps(LOG))
