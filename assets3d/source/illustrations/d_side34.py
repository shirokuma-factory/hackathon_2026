"""D 案（2 回目）: 斜め上から見た横向きの車。運転席が見え、車の長さが車格に比例する。
blender -b -P d_side34.py
真上では 10t・2t・軽が細い長方形に見えるという弱点への対策。車は右向き（前が右）。
出力: outputs/illustrations_2026-11-07/D_side34/raw/<モデル>_<状態>_<様式>.png（768x384、全車種で同じ縮尺）
様式: orig=元の色 / line=白地+輪郭線
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
from bf_lib import *
from a_topdown import open_top, load_cargo

OUTD = os.path.join(OUT, "D_side34", "raw")
os.makedirs(OUTD, exist_ok=True)
RES = (768, 384)
SCALE = 13.8  # 画面の幅が世界の何 m 相当か。最長の 10t 車（12.1）が入る固定値。車格をまたいで同じにする
EL = 48.0     # 水平から測った仰角。小さいほど横から、大きいほど真上に近い
LOG = {}


def lights():
    world("#ffffff", 0.5)
    sun("key", (0.5, -0.45, 1.0), 2.0, 25)   # 画面の手前上から
    sun("fill", (-0.6, 0.3, 0.4), 0.7, 30)


def shot(name, tag, style, loaded=None, bedspan=None):
    t0 = time.time()
    sc = new_scene(RES, samples=48)
    objs = import_glb(name)
    if loaded is not None:
        objs += open_top(objs)
    if loaded:
        objs += load_cargo("pallets", bedspan[0], bedspan[1], 2.2)
    lights()
    pts = points([o for o in objs if not o.hide_render])
    cam = camera_iso(pts, az=-90, el=EL, margin=1.0, res=RES)
    cam.data.ortho_scale = SCALE
    if style == "line":
        recolor([o for o in objs if not o.name.startswith(("cargo", "_slab"))], {}, default="#fbfbfa")
        freestyle(sc, 2.2)
    else:
        shadow_floor(60.0, -0.01)
    render(os.path.join(OUTD, f"{name}_{tag}_{style}.png"))
    LOG[f"{name}_{tag}_{style}"] = round(time.time() - t0, 1)


if __name__ == "__main__":
    for style in ("orig", "line"):
        shot("kei_van", "closed", style)
        shot("box_2t", "closed", style)
        shot("truck_4t", "closed", style)
        shot("truck", "closed", style)
        shot("truck_4t", "empty", style, loaded=False)
        shot("truck_4t", "loaded", style, loaded=True, bedspan=(-2.0, 4.0))
    print("LOG", json.dumps(LOG))
