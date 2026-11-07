"""C 案: 操作の流れの小さい挿絵 3 枚と、荷待ちの記録の 4 つ組アイコン。
blender -b -P c_steps.py
様式: line=Freestyle 線画（主役だけに輪郭線）/ iso=ベタ塗りの等角図
出力: outputs/illustrations_2026-11-07/C_steps/raw/<name>_<style>.png（320x320、2x 用）
構成: 主役（main、輪郭線あり）/ 細部（detail、輪郭線なし、グレー）/ 強調（accent、輪郭線なし、深緑）
線画は 2 回撮る。2 回目は主役をホールドアウト（透明で隠す）にして、細部と強調だけを撮り、重ねる。
"""
import os
import sys
import time
import json
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bpy
from mathutils import Vector, Matrix
from bf_lib import *

OUTD = os.path.join(OUT, "C_steps", "raw")
os.makedirs(OUTD, exist_ok=True)
RES = (320, 320)
LOG = {}
PAL = {
    "line": dict(paper="#fbfbfa", box="#fbfbfa", dark="#e1e4e0", detail="#9aa19b", accent=TEAL),
    "iso": dict(paper="#f1f2ef", box="#c9b48c", dark="#9aa19b", detail="#aeb5af", accent=TEAL),
}


class Scene:
    def __init__(self, style):
        self.style = style
        self.sc = new_scene(RES, samples=40)
        if style == "iso":
            world("#ffffff", 0.35)
            sun("key", (-0.6, -0.5, 0.9), 2.4, 3)
            sun("fill", (0.9, -0.1, 0.3), 0.7, 10)
        else:
            world("#ffffff", 0.55)
            sun("key", (-0.4, -0.5, 1.0), 1.6, 25)
        self.main, self.detail, self.accent = [], [], []
        self.p = PAL[style]

    def mat(self, name, color):
        return flat_mat(name, color, 0.9)

    def box(self, grp, tone, size, loc, rot=(0, 0, 0)):
        bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
        o = bpy.context.active_object
        o.scale = size
        o.rotation_euler = [math.radians(a) for a in rot]
        o.data.materials.append(self.mat(grp + tone, self.p[tone]))
        getattr(self, grp).append(o)
        return o

    def disc(self, grp, tone, r, depth, loc, rot=(90, 0, 0)):
        bpy.ops.mesh.primitive_cylinder_add(vertices=40, radius=r, depth=depth, location=loc)
        o = bpy.context.active_object
        o.rotation_euler = [math.radians(a) for a in rot]
        o.data.materials.append(self.mat(grp + tone, self.p[tone]))
        getattr(self, grp).append(o)
        return o

    def arrow(self, grp, tone, a, b, th=0.07, head=0.22):
        a, b = Vector(a), Vector(b)
        d = (b - a).normalized()
        base = b - d * head * 1.4
        o1 = bar("sh", a, base, th, self.p[tone])
        o2 = cone("hd", base, b, head, self.p[tone])
        for o in (o1, o2):
            getattr(self, grp).append(o)

    def text(self, grp, tone, s, size, loc, rot=(90, 0, 0)):
        bpy.ops.object.text_add(location=loc)
        t = bpy.context.active_object
        t.data.body = s
        t.data.size = size
        t.data.align_x = "CENTER"
        t.data.align_y = "CENTER"
        t.data.extrude = 0.05
        t.rotation_euler = [math.radians(a) for a in rot]
        t.data.materials.append(self.mat("txt", self.p[tone]))
        bpy.ops.object.convert(target="MESH")
        o = bpy.context.active_object
        getattr(self, grp).append(o)

    def doc(self, loc=(0, 0, 0), w=0.9, h=1.2, rows=4, header=True, rot=(90, 0, 0), y=0.0):
        """立てた書面。細部の行を紙の前面に貼る（紙の厚み 0.04 の手前）。"""
        self.box("main", "paper", (w, 0.04, h), loc, (0, 0, 0))
        fy = loc[1] - 0.025
        if header:
            self.box("accent", "accent", (w * 0.8, 0.012, 0.09), (loc[0], fy, loc[2] + h / 2 - 0.2))
        for i in range(rows):
            ww = w * (0.8 if i % 3 != 2 else 0.5)
            self.box("detail", "detail", (ww, 0.012, 0.05),
                     (loc[0] - (w * 0.8 - ww) / 2, fy, loc[2] + h / 2 - 0.42 - i * 0.2))


def finish(S, name, az, el, margin=1.25):
    sc = S.sc
    allo = S.main + S.detail + S.accent
    pts = points(allo)
    camera_iso(pts, az=az, el=el, margin=margin, res=RES)
    if S.style == "iso":
        render(os.path.join(OUTD, f"{name}_iso.png"))
        return
    freestyle(sc, 2.2)
    sc.view_layers[0].freestyle_settings.linesets[0].select_crease = False
    for o in S.detail + S.accent:
        o.hide_render = True
    render(os.path.join(OUTD, f"{name}_line_main.png"))
    for o in S.detail + S.accent:
        o.hide_render = False
    for o in S.main:
        o.is_holdout = True
    sc.render.use_freestyle = False
    render(os.path.join(OUTD, f"{name}_line_acc.png"))


# ------------------------------------------------------------------ 3 つの挿絵
def step_data(S):
    """CSV の表が箱に入っていく。表は平らな座標で作ってから、まとめて傾ける。"""
    S.box("main", "box", (1.2, 1.2, 0.05), (0, 0, 0.025))
    for sx, sy, w, d in ((0, -0.6, 1.2, 0.05), (0, 0.6, 1.2, 0.05), (-0.6, 0, 0.05, 1.2), (0.6, 0, 0.05, 1.2)):
        S.box("main", "box", (w, d, 0.55), (sx, sy, 0.3))
    n0 = (len(S.main), len(S.detail), len(S.accent))
    S.box("main", "paper", (0.8, 0.95, 0.04), (0, 0, 0))
    S.box("accent", "accent", (0.68, 0.14, 0.012), (0, 0.3, 0.026))   # 見出しの行
    for i in range(4):
        S.box("detail", "detail", (0.68, 0.035, 0.012), (0, 0.1 - i * 0.2, 0.026))
    for x in (-0.12, 0.12):
        S.box("detail", "detail", (0.015, 0.8, 0.012), (x, -0.05, 0.026))
    R = Matrix.Rotation(math.radians(24), 3, "X")
    c = Vector((0, 0.05, 0.78))
    for o in S.main[n0[0]:] + S.detail[n0[1]:] + S.accent[n0[2]:]:
        o.location = c + R @ Vector(o.location)
        o.rotation_euler = (R @ o.rotation_euler.to_matrix()).to_euler()
    S.arrow("accent", "accent", (0, 0.05, 2.0), (0, 0.05, 1.5), th=0.07, head=0.2)


def step_bars(S):
    """曜日の棒と、空の帰りの車。"""
    hs = [0.55, 0.65, 0.6, 0.7, 1.5, 0.85, 0.45]
    w, g = 0.22, 0.1
    x0 = -(7 * w + 6 * g) / 2 + w / 2
    S.box("main", "dark", (7 * w + 6 * g + 0.3, 1.3, 0.05), (0, -0.15, 0.025))
    for i, h in enumerate(hs):
        S.box("accent" if i == 4 else "main", "accent" if i == 4 else "paper", (w, 0.3, h), (x0 + i * (w + g), 0.3, 0.05 + h / 2))
    # 空の車（2t 箱車）: 手前に左向きの帰り
    objs = import_glb("box_2t")
    for o in objs:
        if o.parent is None:
            o.rotation_euler[2] = math.radians(-90)
            o.scale = (0.2, 0.2, 0.2)
            o.location = (0.55, -0.55, 0.05)
    recolor(objs, {"glass": "#8d958f", "charcoal": "#6a726d", "rubber": "#4a524e", "hub": "#b8beb9",
                   "lamp": "#f2f2ee", "light": "#f2f2ee", "grey": "#a9afaa"}, default=S.p["paper"])
    S.main += objs
    S.arrow("detail", "detail", (0.0, -0.55, 0.06), (-0.9, -0.55, 0.06), th=0.05, head=0.17)


def step_consult(S):
    """書面と疑問符。"""
    S.doc(loc=(-0.15, 0, 0.8), w=1.0, h=1.3, rows=4)
    S.text("accent", "accent", "?", 1.15, (0.55, -0.12, 0.35), (90, 0, 0))


# ------------------------------------------------------------------ 荷待ちの記録の 4 つ組
def chain_press(S):
    """ドライバーが押す: 台の上の大きな丸いボタンと、押したときの輪。"""
    S.disc("main", "dark", 0.75, 0.2, (0, 0, 0.1), (0, 0, 0))
    S.disc("main", "paper", 0.5, 0.12, (0, 0, 0.26), (0, 0, 0))
    S.disc("accent", "accent", 0.4, 0.14, (0, 0, 0.38), (0, 0, 0))
    bpy.ops.mesh.primitive_torus_add(major_radius=0.98, minor_radius=0.03, location=(0, 0, 0.03))
    t = bpy.context.active_object
    t.data.materials.append(S.mat("ring", S.p["detail"]))
    S.detail.append(t)


def chain_paste(S):
    """事務が貼る: 板に表の紙と、貼る欄。"""
    S.box("main", "dark", (0.95, 0.05, 1.3), (0, 0, 0.7))
    S.box("main", "dark", (0.35, 0.08, 0.14), (0, -0.02, 1.38))
    S.box("main", "paper", (0.78, 0.03, 1.05), (0, -0.04, 0.68))
    for i in range(4):
        S.box("detail", "detail", (0.66, 0.012, 0.025), (0, -0.062, 1.05 - i * 0.2))
    S.box("accent", "accent", (0.66, 0.012, 0.16), (0, -0.062, 0.27))


def chain_doc(S):
    """書面: ヘッダーと本文の行、印。"""
    S.doc(loc=(0, 0, 0.8), w=1.0, h=1.3, rows=4)
    S.disc("accent", "accent", 0.17, 0.03, (0.28, -0.045, 0.25))


def chain_check(S):
    """荷主が照合: 書面と大きなチェック。"""
    S.doc(loc=(-0.1, 0, 0.8), w=0.95, h=1.3, rows=4, header=False)
    c = (0.3, -0.1, 0.35)
    S.accent.append(bar("c1", (c[0] - 0.25, c[1], c[2] + 0.02), (c[0] - 0.05, c[1], c[2] - 0.22), 0.11, S.p["accent"]))
    S.accent.append(bar("c2", (c[0] - 0.05, c[1], c[2] - 0.22), (c[0] + 0.4, c[1], c[2] + 0.42), 0.11, S.p["accent"]))


JOBS = [("step_data", step_data, 38, 30), ("step_bars", step_bars, 30, 28), ("step_consult", step_consult, 24, 18),
        ("chain_1_press", chain_press, 24, 30), ("chain_2_paste", chain_paste, 24, 16),
        ("chain_3_doc", chain_doc, 24, 16), ("chain_4_check", chain_check, 24, 16)]

if __name__ == "__main__":
    for style in ("line", "iso"):
        for name, fn, az, el in JOBS:
            t0 = time.time()
            S = Scene(style)
            fn(S)
            finish(S, name, az, el)
            LOG[f"{name}_{style}"] = round(time.time() - t0, 1)
    print("LOG", json.dumps(LOG))
