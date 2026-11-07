"""10 個の UI アイコンのモデル（原点が足元、ground は z=0）。"""
import math
import random

from ui_lib import (PI, box, cone, cyl, group, mat, sector, seg, sphere, text, torus)

INK = "#1b2536"
NAVY = "#1f3556"
BLUE = "#5cc8ff"
ORANGE = "#f49646"
KRAFT = ["#b98e5f", "#c29768", "#ad8458"]
WOOD = ["#b99a6b", "#c4a574", "#ad8f62"]


def truck(loaded, accent):
    paint = mat("#e6e9ec", 0.32, coat=0.4)
    body_m = mat("#d3d8dd", 0.38, 0.6)
    inner = mat("#e4e7ea", 0.6)
    dark = mat("#262a30", 0.6)
    glass = mat("#27323f", 0.08, 0.2)
    steel = mat("#a9b0b7", 0.28, 1.0)
    tire = mat("#1e2125", 0.85)
    acc = mat(accent, 0.4)
    floor = mat("#8f8a80", 0.7)
    # 台枠と下回り
    box((6.0, 1.1, 0.15), (0, 0, 0.62), dark, 0.02)
    box((4.9, 1.3, 0.2), (-0.55, 0, 0.84), dark, 0.02)
    # 車輪
    for x in (2.15, -1.95, -1.25):
        for y in (-0.72, 0.72):
            cyl(0.42, 0.3, (x, y, 0.42), tire, (PI / 2, 0, 0), 0.05)
            cyl(0.24, 0.32, (x, y * 1.01, 0.42), steel, (PI / 2, 0, 0), 0.01)
            cyl(0.1, 0.34, (x, y * 1.02, 0.42), dark, (PI / 2, 0, 0))
    # 燃料タンク
    cyl(0.23, 1.1, (0.55, -0.52, 0.66), steel, (0, PI / 2, 0))
    # キャビン
    box((1.15, 1.62, 1.5), (2.42, 0, 1.55), paint, 0.09, seg=5)
    box((0.6, 1.5, 0.12), (2.15, 0, 2.33), paint, 0.04)
    box((0.02, 1.4, 0.62), (3.0, 0, 1.98), glass, 0.01)
    for y in (-0.815, 0.815):
        box((0.6, 0.02, 0.58), (2.55, y, 1.98), glass, 0.01)
        box((0.06, 0.03, 0.04), (2.85, y * 1.01, 1.55), steel, 0.01)
        box((0.02, 0.025, 1.2), (2.25, y, 1.55), dark, 0, )
    box((0.03, 1.2, 0.3), (3.0, 0, 1.35), dark, 0.01)
    box((0.03, 1.4, 0.07), (3.0, 0, 1.12), acc, 0.01)
    box((0.03, 1.1, 0.04), (3.0, 0, 1.22), dark, 0.0)
    for y in (-0.62, 0.62):
        box((0.04, 0.22, 0.14), (3.0, y, 1.1), mat("#f1ead3", 0.2, em="#ffeec2", ems=0.6), 0.02)
    box((0.14, 1.7, 0.18), (3.05, 0, 0.92), mat("#9aa1a8", 0.4, 0.5), 0.03)
    box((1.0, 0.012, 0.1), (2.5, -0.816, 1.28), acc, 0.0)
    for y in (-0.93, 0.93):
        box((0.04, 0.08, 0.34), (2.88, y, 1.78), dark, 0.015)
    # 荷台
    L0, L1 = -3.0, 1.82
    cx, ln = (L0 + L1) / 2, L1 - L0
    box((ln, 1.7, 0.12), (cx, 0, 0.98), mat("#6d6a63", 0.7), 0.01)
    box((ln, 0.07, 1.66), (cx, 0.78, 1.87), body_m, 0.015)
    box((0.07, 1.64, 1.66), (L1, 0, 1.87), body_m, 0.015)
    box((0.07, 1.64, 1.66), (L0, 0, 1.87), body_m, 0.015)
    box((ln, 1.72, 0.1), (cx, 0, 2.72), body_m, 0.02)
    box((ln, 0.012, 1.55), (cx, 0.74, 1.87), inner, 0)
    box((ln, 0.06, 0.18), (cx, -0.83, 1.0), acc, 0.01)
    box((ln - 0.2, 0.01, 0.1), (cx, 0.742, 1.5), acc, 0.0)
    # 開いた跳ね上げの側板（ウイング）
    with group((cx, -0.8, 2.7), (math.radians(-128), 0, 0)):
        box((ln, 0.05, 1.64), (0, 0, -0.82), body_m, 0.015)
        box((ln - 0.1, 0.012, 0.16), (0, -0.03, -0.3), acc, 0.0)
    # 貨物
    if loaded:
        rnd = random.Random(7)
        pal = mat("#b99a6b", 0.8)
        for i, x in enumerate((-2.5, -1.35, -0.2, 0.95)):
            box((1.0, 1.35, 0.1), (x, 0.0, 1.09), pal, 0.01)
            for lv in range(2):
                m = mat(rnd.choice(KRAFT), 0.85)
                box((0.96, 1.3, 0.7), (x, 0, 1.19 + 0.35 + lv * 0.71 - 0.0), m, 0.02,
                    rot=(0, 0, rnd.uniform(-0.02, 0.02)))
                box((0.14, 1.32, 0.005), (x, 0, 1.19 + 0.7 + lv * 0.71 + 0.0), mat("#d8c7a3", 0.6), 0)
            if i % 2 == 0:
                box((0.4, 0.004, 0.26), (x - 0.1, -0.66, 1.58), mat("#f3f2ee", 0.7), 0)


def clock():
    chrome = mat("#aab1b8", 0.28, 1.0)
    dark = mat("#1e2a3c", 0.4)
    face = mat("#f1efe8", 0.6)
    org = mat(ORANGE, 0.45)
    with group((0, 0, 1.0), (math.radians(-12), 0, math.radians(25))):
        cyl(1.0, 0.26, (0, 0, 0), chrome, (PI / 2, 0, 0), 0.05)
        cyl(0.9, 0.02, (0, -0.135, 0), face, (PI / 2, 0, 0))
        cyl(0.93, 0.03, (0, -0.12, 0), mat("#2a3140", 0.5), (PI / 2, 0, 0))
        cyl(0.88, 0.02, (0, -0.145, 0), face, (PI / 2, 0, 0))
        sector(0.84, 0, 150, -0.158, org, 0.012)
        for i in range(12):
            a = math.radians(i * 30)
            big = i % 3 == 0
            r = 0.76 if not big else 0.74
            box((0.045 if big else 0.03, 0.012, 0.14 if big else 0.09),
                (r * math.sin(a), -0.166, r * math.cos(a)), dark, 0.0, (0, a, 0))
        for ang, L, w, h in ((150, 0.72, 0.045, "d"), (35, 0.5, 0.06, "d")):
            a = math.radians(ang)
            box((w, 0.012, L), (L / 2 * math.sin(a), -0.18, L / 2 * math.cos(a)), dark, 0.0, (0, a, 0))
        cyl(0.07, 0.03, (0, -0.19, 0), dark, (PI / 2, 0, 0))
        cyl(0.1, 0.18, (0, 0, 1.08), chrome, (0, 0, 0), 0.01)
        cyl(0.17, 0.12, (0, 0, 1.22), chrome, (0, 0, 0), 0.03)
        a = math.radians(45)
        cyl(0.07, 0.2, (1.08 * math.sin(a), 0, 1.08 * math.cos(a)), chrome, (0, a, 0), 0.01)
        cyl(0.11, 0.1, (1.2 * math.sin(a), 0, 1.2 * math.cos(a)), chrome, (0, a, 0), 0.02)


def dock():
    wall = mat("#bfc3c4", 0.85)
    wall2 = mat("#a9aeb1", 0.85)
    dark = mat("#2b2f35", 0.7)
    conc = mat("#9c9f9f", 0.9)
    navy = mat(NAVY, 0.5)
    inside = mat("#cfd2d1", 0.8)
    W, H = 4.2, 3.2
    o0, o1 = 0.95, 2.55  # 開口の床と上端
    ow = 1.0
    # 外周の壁（-y が正面、奥行き 2.6）
    box((W / 2 - ow, 0.25, H), (-(W / 2 + ow) / 2, 0, H / 2), wall, 0.02)
    box((W / 2 - ow, 0.25, H), ((W / 2 + ow) / 2, 0, H / 2), wall, 0.02)
    box((2 * ow, 0.25, H - o1), (0, 0, (H + o1) / 2), wall, 0.02)
    box((2 * ow, 0.25, o0), (0, 0, o0 / 2), wall, 0.02)
    box((0.12, 2.6, H), (-W / 2 + 0.06, 1.3 - 0.125, H / 2), wall2, 0.02)
    box((0.12, 2.6, H), (W / 2 - 0.06, 1.3 - 0.125, H / 2), wall2, 0.02)
    box((W, 0.12, H), (0, 2.5, H / 2), wall2, 0.02)
    box((W, 2.6, 0.12), (0, 1.3 - 0.125, H + 0.06), mat("#7d8488", 0.8), 0.03)
    box((W + 0.2, 2.8, 0.06), (0, 1.3 - 0.125, H + 0.14), mat("#6a7176", 0.8), 0.02)
    box((W - 0.2, 2.5, 0.1), (0, 1.3, o0 - 0.05), inside, 0.01)
    # 開口まわりの縁取り
    box((0.08, 0.1, o1 - o0), (-ow + 0.04, -0.15, (o0 + o1) / 2), dark, 0.01)
    box((0.08, 0.1, o1 - o0), (ow - 0.04, -0.15, (o0 + o1) / 2), dark, 0.01)
    # 半分開いたシャッター
    cyl(0.15, 2 * ow - 0.1, (0, -0.22, o1 + 0.12), mat("#8a9096", 0.4, 0.8), (0, PI / 2, 0))
    for i in range(4):
        box((2 * ow - 0.16, 0.03, 0.115), (0, -0.14, o1 - 0.06 - i * 0.125), mat("#b7bcc0", 0.35, 0.7), 0.01)
    # 搬入口の標識
    box((0.5, 0.05, 0.4), (0, -0.14, H - 0.35), navy, 0.02)
    text("3", 0.3, (0, -0.18, H - 0.35), mat("#f1f3f4", 0.5), ext=0.01)
    # 前の舗装と段差プレート
    box((W + 1.0, 2.6, 0.06), (0, -1.5, 0.03), conc, 0.01)
    box((1.8, 0.9, 0.05), (0, -0.6, o0 + 0.0), mat("#767c82", 0.4, 0.8), 0.01)
    for x in (-1.5, 1.5):
        box((0.35, 0.22, 0.42), (x, -0.25, 0.55), mat("#17191c", 0.9), 0.04)
    for x in (-1.55, 1.55):
        cyl(0.1, 0.8, (x, -1.1, 0.4), mat("#d1a93a", 0.5), (0, 0, 0))
        cyl(0.101, 0.1, (x, -1.1, 0.5), dark, (0, 0, 0))
    # 奥の荷
    for x, z in ((-0.45, 0), (0.4, 0)):
        box((0.7, 0.7, 0.08), (x, 1.6, o0 + 0.04), mat("#b99a6b", 0.8), 0.01)
        box((0.64, 0.64, 0.5), (x, 1.6, o0 + 0.33), mat(KRAFT[0], 0.85), 0.02)


def document():
    paper = mat("#f6f5f0", 0.8)
    line = mat("#9aa3b0", 0.8)
    red = mat("#b8432f", 0.45)
    wood = mat("#a9805a", 0.5)
    navy = mat(NAVY, 0.6)
    with group((0, 0, 0), (0, 0, math.radians(25))):
        with group((0.3, 0.35, 0.0), (math.radians(-9), 0, math.radians(-5))):
            box((1.5, 0.03, 2.0), (0, 0, 1.0), mat("#e9e8e2", 0.85), 0.012)
        with group((0, 0, 0), (math.radians(-9), 0, 0)):
            box((1.5, 0.035, 2.0), (0, 0, 1.0), paper, 0.012)
            box((0.9, 0.006, 0.14), (-0.15, -0.021, 1.78), navy, 0.0)
            for i in range(7):
                w = 1.1 if i % 3 != 2 else 0.7
                box((w, 0.006, 0.045), (-0.55 + w / 2 - 0.0, -0.021, 1.5 - i * 0.14), line, 0.0)
            box((0.5, 0.006, 0.045), (-0.35, -0.021, 0.48), line, 0.0)
            torus(0.21, 0.025, (0.38, -0.025, 0.6), red, (PI / 2, 0, 0))
            for k, w in enumerate((0.26, 0.2, 0.26)):
                box((w, 0.008, 0.035), (0.38, -0.028, 0.67 - k * 0.07), red, 0.0)
        # 印鑑
        cyl(0.13, 0.5, (1.05, -0.55, 0.28), wood, (0, 0, 0), 0.02)
        cyl(0.135, 0.06, (1.05, -0.55, 0.03), red, (0, 0, 0))
        sphere(0.13, (1.05, -0.55, 0.53), wood)


def phone():
    body = mat("#2a2f37", 0.3, 0.7)
    scr = mat("#0e1a2d", 0.2, em="#10233d", ems=0.8)
    line = mat("#7e8da3", 0.5, em="#6a7c95", ems=0.9)
    blue = mat(BLUE, 0.5, em=BLUE, ems=1.2)
    org = mat(ORANGE, 0.5, em=ORANGE, ems=1.2)
    grey = mat("#8d97a6", 0.5, em="#8d97a6", ems=0.9)
    with group((0, 0, 0), (0, 0, math.radians(25))):
        with group((0, 0, 0), (math.radians(-13), 0, 0)):
            box((1.0, 0.1, 2.0), (0, 0, 1.0), body, 0.1, seg=6)
            box((0.9, 0.012, 1.88), (0, -0.052, 1.0), scr, 0.06, seg=4)
            box((0.2, 0.012, 0.045), (0, -0.062, 1.9), mat("#05080d", 0.3), 0.02)
            box((0.5, 0.01, 0.07), (-0.1, -0.062, 1.72), grey, 0.0)
            box((0.012, 0.01, 1.2), (-0.28, -0.062, 1.0), line, 0.0)
            ys = (1.4, 1.1, 0.8, 0.5)
            cols = (blue, blue, org, grey)
            for y, c in zip(ys, cols):
                sphere(0.06, (-0.28, -0.075, y), c, 24)
                box((0.4 if c != grey else 0.3, 0.01, 0.1), (0.04, -0.062, y), c, 0.012)
                box((0.2, 0.01, 0.035), (0.04, -0.07, y - 0.12), line, 0.0)
            box((0.64, 0.01, 0.1), (0, -0.062, 0.22), mat("#233a5b", 0.5, em="#2c4a76", ems=0.6), 0.02)


def route_pin():
    paper = mat("#ebe7da", 0.9)
    green = mat("#aebf9e", 0.9)
    water = mat("#9fb7c4", 0.5)
    road = mat("#fbfaf5", 0.8)
    org = mat(ORANGE, 0.5)
    ang = math.radians(10)
    segs = [
        [((-0.35, -0.5), (0.55, 0.15))],
        [((-0.55, 0.15), (0.55, 0.15))],
        [((-0.55, 0.15), (0.35, 0.6))],
    ]
    with group((0, 0, 0), (0, 0, math.radians(25))):
        for i in range(3):
            th = ang if i % 2 == 0 else -ang
            with group(((i - 1) * 1.083, 0, 0.14), (0, th, 0)):
                box((1.1, 1.7, 0.025), (0, 0, 0), paper, 0.004)
                if i == 0:
                    box((0.5, 0.6, 0.004), (-0.2, 0.5, 0.014), green, 0)
                    seg((-0.55, -0.15), (0.55, -0.6), 0.07, 0.014, road, 0.004)
                if i == 1:
                    seg((-0.55, 0.65), (0.55, 0.45), 0.2, 0.014, water, 0.004)
                    seg((0.0, -0.85), (0.0, 0.15), 0.06, 0.014, road, 0.004)
                if i == 2:
                    box((0.5, 0.5, 0.004), (0.2, -0.45, 0.014), green, 0)
                    seg((-0.5, -0.1), (0.55, -0.1), 0.06, 0.014, road, 0.004)
                for a, b in segs[i]:
                    seg(a, b, 0.075, 0.02, org, 0.006)
        pin = mat("#d9503b", 0.28, coat=0.5)
        with group((0, 0.15, 0.11)):
            cone(0.0, 0.2, 0.62, (0, 0, 0.31), pin)
            sphere(0.3, (0, 0, 0.84), pin)
            sphere(0.12, (0, -0.2, 0.88), mat("#f6f3ee", 0.4))


def yen_estimate():
    body = mat("#3b4350", 0.4)
    lcd = mat("#b8c8b0", 0.35)
    key = mat("#d8dce0", 0.4)
    keyd = mat("#8b95a3", 0.4)
    org = mat(ORANGE, 0.4)
    ink = mat("#27332c", 0.5)
    paper = mat("#f7f6f1", 0.85)
    line = mat("#a0a7b2", 0.8)
    with group((-0.35, 0, 0), (0, 0, math.radians(25))):
        with group((0, 0, 0), (math.radians(-14), 0, 0)):
            box((1.3, 0.14, 1.9), (0, 0, 0.95), body, 0.06, seg=4)
            box((1.1, 0.02, 0.38), (0, -0.075, 1.58), mat("#252b34", 0.3), 0.01)
            box((1.06, 0.01, 0.34), (0, -0.088, 1.58), lcd, 0.0)
            text("¥48,000", 0.22, (0.5, -0.1, 1.58), ink, align="RIGHT")
            for r in range(4):
                for c in range(4):
                    m = key
                    if c == 3:
                        m = keyd
                    if r == 3 and c == 3:
                        m = org
                    box((0.22, 0.05, 0.2), (-0.42 + c * 0.28, -0.085, 1.15 - r * 0.28), m, 0.03)
    with group((1.0, -0.05, 0), (math.radians(-9), 0, math.radians(8) + math.radians(25))):
        box((0.9, 0.012, 2.0), (0, 0, 1.0), paper, 0.004)
        box((0.5, 0.006, 0.05), (0, -0.01, 1.82), line, 0.0)
        for i in range(5):
            box((0.55 if i % 2 else 0.4, 0.006, 0.035), (-0.12, -0.01, 1.6 - i * 0.12), line, 0.0)
        box((0.7, 0.006, 0.015), (0, -0.01, 0.95), line, 0.0)
        text("¥48,000", 0.15, (0.3, -0.012, 0.75), ink, align="RIGHT", ext=0.003)
        box((0.22, 0.006, 0.04), (-0.22, -0.01, 0.75), line, 0.0)


def boxes_pallet():
    rnd = random.Random(11)
    with group((0, 0, 0), (0, 0, math.radians(25))):
        for y in (-0.6, 0, 0.6):
            box((1.8, 0.14, 0.04), (0, y, 0.02), mat(rnd.choice(WOOD), 0.8), 0.006)
        for x in (-0.8, 0, 0.8):
            for y in (-0.6, 0, 0.6):
                box((0.14, 0.14, 0.1), (x, y, 0.09), mat(WOOD[0], 0.85), 0.006)
        for i in range(5):
            box((1.8, 0.24, 0.045), (0, -0.58 + i * 0.29, 0.1625 + 0.0), mat(rnd.choice(WOOD), 0.8), 0.006)
        tape = mat("#d9c8a4", 0.55)
        lab = mat("#f3f2ee", 0.7)
        layout = [
            (0.19, [(-0.45, -0.34), (0.45, -0.34), (-0.45, 0.34), (0.45, 0.34)]),
            (0.19 + 0.62, [(-0.45, -0.34), (0.45, -0.34), (0.45, 0.34)]),
            (0.19 + 1.24, [(0.0, 0.0)]),
        ]
        for z, spots in layout:
            for (x, y) in spots:
                m = mat(rnd.choice(KRAFT), 0.85)
                w = (0.86, 0.66, 0.6) if z < 1.0 else (0.9, 0.7, 0.58)
                if z > 1.0:
                    w = (0.9, 0.7, 0.5)
                rz = rnd.uniform(-0.02, 0.02)
                box((w[0], w[1], 0.6 if z < 1.0 else w[2]), (x, y, z + (0.3 if z < 1.0 else w[2] / 2)), m, 0.02,
                    rot=(0, 0, rz))
                top = z + (0.6 if z < 1.0 else w[2])
                box((0.12, w[1] + 0.003, 0.004), (x, y, top), tape, 0, rot=(0, 0, rz))
                box((0.12, 0.004, 0.45 if z < 1.0 else 0.4), (x, y - w[1] / 2 - 0.0, z + 0.3), tape, 0, rot=(0, 0, rz))
                if rnd.random() < 0.6:
                    box((0.26, 0.004, 0.17), (x + 0.2, y - w[1] / 2 - 0.0, z + 0.34), lab, 0, rot=(0, 0, rz))


def empty_state():
    conc = mat("#9ba0a4", 0.9)
    line = mat("#dde4ea", 0.7)
    yel = mat("#c8a64a", 0.6)
    dark = mat("#2a2e34", 0.7)
    wall = mat("#bcc0c2", 0.85)
    TOP = 0.14
    with group((0, 0, 0), (0, 0, math.radians(0))):
        box((4.4, 3.4, TOP), (0, 0, TOP / 2), conc, 0.03)
        # 区画の白線
        z = TOP + 0.003
        box((0.09, 2.4, 0.006), (-1.1, -0.25, z), line, 0)
        box((0.09, 2.4, 0.006), (1.1, -0.25, z), line, 0)
        box((2.29, 0.09, 0.006), (0, 0.95, z), line, 0)
        for i in range(6):
            box((0.09, 0.42, 0.006), (-0.9 + i * 0.36, 0.5, z), yel, 0, rot=(0, 0, 0.6))
        # 車止めと柱
        box((1.6, 0.22, 0.12), (0, 0.75, TOP + 0.06), mat("#7c8186", 0.85), 0.02)
        for x in (-1.9, 1.9):
            cyl(0.1, 0.55, (x, 0.0, TOP + 0.275), yel, (0, 0, 0))
            cyl(0.101, 0.09, (x, 0.0, TOP + 0.4), dark, (0, 0, 0))
        # 奥の壁と閉じたシャッター
        box((4.4, 0.18, 1.7), (0, 1.55, TOP + 0.85), wall, 0.02)
        box((2.0, 0.06, 1.3), (0, 1.43, TOP + 0.7), mat("#adb2b6", 0.4, 0.7), 0.01)
        for i in range(8):
            box((1.98, 0.02, 0.012), (0, 1.395, TOP + 0.2 + i * 0.145), mat("#8d9398", 0.5, 0.6), 0)
        # 空のパレット 1 枚
        for y in (-0.38, 0, 0.38):
            box((0.9, 0.1, 0.04), (-0.0, y - 1.0, TOP + 0.02), mat(WOOD[1], 0.8), 0.006)
        for i in range(4):
            box((0.9, 0.18, 0.04), (0, -1.38 + i * 0.28 + 0.0, TOP + 0.13), mat(WOOD[i % 3], 0.8), 0.006)
        for x in (-0.38, 0, 0.38):
            for y in (-0.38, 0, 0.38):
                box((0.1, 0.1, 0.08), (x, y - 1.0, TOP + 0.08), mat(WOOD[0], 0.85), 0.005)


MODELS = {
    "truck_loaded": lambda: truck(True, BLUE),
    "truck_empty": lambda: truck(False, ORANGE),
    "clock_wait": clock,
    "dock": dock,
    "document": document,
    "phone_driver": phone,
    "route_pin": route_pin,
    "yen_estimate": yen_estimate,
    "boxes_pallet": boxes_pallet,
    "empty_state": empty_state,
}
