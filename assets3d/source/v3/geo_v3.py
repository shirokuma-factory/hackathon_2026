"""v3 共通: 投影、地形の高さ関数（numpy だけ）、都市と高速道路の座標。Blender の中でも普通の Python でも読める。"""
import math
import numpy as np

S = 10.0
LON0, LAT0 = 137.5, 36.5
KX = math.cos(math.radians(36.5))
# 距離場の範囲（投影後の単位）
FX0, FX1, FY0, FY1 = -100.0, 100.0, -75.0, 100.0
FW = 4096
FH = int(round(FW * (FY1 - FY0) / (FX1 - FX0)))   # 3584
KANTO = {7, 8, 9, 10, 11, 12, 13}
KANSAI = {23, 24, 25, 26, 27, 28, 29}

Z_SEA = 0.30
Z_COAST = 1.15
HMAX = 1.7


def proj(lon, lat):
    return ((lon - LON0) * KX * S, (lat - LAT0) * S)


# 高速道路のおおよその経路（神戸 - 大阪 - 京都南 - 名古屋 - 東海道 - 東京）
HIGHWAY = [(135.10, 34.72), (135.35, 34.72), (135.55, 34.78), (135.72, 34.93), (135.92, 35.00), (136.10, 34.96),
           (136.40, 34.97), (136.68, 35.07), (136.90, 35.17), (137.12, 35.04), (137.30, 34.88), (137.52, 34.76),
           (137.78, 34.72), (138.05, 34.77), (138.38, 34.95), (138.68, 35.13), (138.92, 35.22), (139.10, 35.33),
           (139.32, 35.43), (139.50, 35.52), (139.62, 35.64)]
# (経度, 緯度, 半径[単位], 高層の度合い 0..1)
CITIES = [
    ("東京", 139.76, 35.68, 3.5, 1.0), ("横浜", 139.62, 35.45, 1.9, 0.55), ("さいたま", 139.65, 35.90, 1.6, 0.35),
    ("千葉", 140.12, 35.61, 1.4, 0.35), ("宇都宮", 139.88, 36.56, 0.7, 0.15), ("水戸", 140.45, 36.37, 0.6, 0.1),
    ("前橋", 139.06, 36.39, 0.55, 0.1), ("仙台", 140.88, 38.27, 1.3, 0.4), ("新潟", 139.05, 37.92, 1.0, 0.2),
    ("名古屋", 136.90, 35.17, 2.5, 0.8), ("静岡", 138.38, 34.98, 0.9, 0.15), ("浜松", 137.73, 34.71, 0.9, 0.15),
    ("金沢", 136.65, 36.56, 0.8, 0.15), ("大阪", 135.50, 34.69, 3.0, 0.9), ("神戸", 135.20, 34.69, 1.5, 0.4),
    ("京都", 135.77, 35.01, 1.4, 0.2), ("奈良", 135.83, 34.69, 0.6, 0.1), ("岡山", 133.92, 34.66, 0.8, 0.15),
    ("広島", 132.46, 34.39, 1.3, 0.35), ("福岡", 130.40, 33.59, 1.7, 0.45), ("北九州", 130.88, 33.88, 1.0, 0.2),
    ("熊本", 130.74, 32.80, 0.8, 0.15), ("松山", 132.77, 33.84, 0.7, 0.1), ("高松", 134.05, 34.34, 0.5, 0.1),
    ("札幌", 141.35, 43.06, 1.8, 0.4), ("函館", 140.73, 41.77, 0.5, 0.05), ("青森", 140.74, 40.82, 0.5, 0.05),
    ("盛岡", 141.15, 39.70, 0.5, 0.05), ("秋田", 140.10, 39.72, 0.5, 0.05), ("福島", 140.47, 37.76, 0.5, 0.05),
    ("富山", 137.21, 36.70, 0.6, 0.1), ("甲府", 138.57, 35.66, 0.5, 0.05), ("岐阜", 136.76, 35.42, 0.7, 0.1),
    ("四日市", 136.62, 34.97, 0.6, 0.1), ("和歌山", 135.17, 34.23, 0.7, 0.1), ("姫路", 134.69, 34.82, 0.7, 0.1),
    ("津", 136.51, 34.72, 0.4, 0.05), ("鹿児島", 130.56, 31.60, 0.7, 0.1), ("長崎", 129.87, 32.75, 0.6, 0.1),
]
# (経度, 緯度, 半径[単位], 強さ)  山地の位置
RANGES = [
    (137.7, 36.2, 9, 1.0), (138.3, 36.0, 5, 0.8), (139.0, 36.8, 6, 0.7), (137.0, 35.9, 5, 0.6), (136.3, 35.5, 3, 0.4),
    (135.8, 34.1, 5.5, 0.65), (133.2, 35.1, 8, 0.5), (132.3, 34.8, 5, 0.4), (133.5, 33.8, 6, 0.8),
    (131.1, 32.8, 6, 0.7), (130.9, 33.2, 3.5, 0.5), (140.8, 40.5, 4.5, 0.7), (140.6, 39.2, 4.5, 0.75),
    (140.3, 38.2, 4.5, 0.7), (139.9, 37.2, 4.5, 0.65), (139.7, 39.0, 4.5, 0.6), (141.5, 39.6, 4.0, 0.5),
    (142.9, 43.4, 8, 0.9), (143.4, 42.9, 5, 0.6), (141.9, 42.4, 4, 0.4), (136.8, 37.1, 2.5, 0.3), (139.0, 35.9, 3.5, 0.8),
    (138.9, 34.9, 2.5, 0.6), (140.1, 35.3, 2.0, 0.3), (140.6, 36.9, 3.5, 0.5), (131.6, 34.2, 4, 0.3),
]
# 平野（山を低くする）
PLAINS = [
    (139.9, 36.1, 7, 0.95), (136.75, 35.1, 3.5, 0.9), (135.5, 34.7, 2.8, 0.95), (140.95, 38.2, 2.8, 0.9),
    (139.1, 37.7, 4, 0.85), (141.6, 43.1, 5, 0.9), (143.2, 42.9, 3.5, 0.8), (130.5, 33.2, 3, 0.9), (133.9, 34.7, 2.5, 0.7),
    (135.77, 35.0, 1.8, 0.8), (140.1, 35.5, 4, 0.7), (136.9, 36.7, 3, 0.7),
]


def _hash(ix, iy, seed):
    h = (ix.astype(np.int64) * 374761393 + iy.astype(np.int64) * 668265263 + seed * 1442695041) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h = h ^ (h >> 16)
    return (h & 0xFFFFFF) / float(0x1000000)


def vnoise(x, y, seed=0):
    xi, yi = np.floor(x), np.floor(y)
    fx, fy = x - xi, y - yi
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = _hash(xi, yi, seed), _hash(xi + 1, yi, seed)
    c, d = _hash(xi, yi + 1, seed), _hash(xi + 1, yi + 1, seed)
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def fbm(x, y, octs=5, seed=0):
    tot, amp, f, norm = 0.0, 1.0, 1.0, 0.0
    ca, sa = math.cos(0.7), math.sin(0.7)
    for o in range(octs):
        tot = tot + amp * vnoise(x * f, y * f, seed + o * 17)
        norm += amp
        x, y = x * ca - y * sa, x * sa + y * ca
        amp *= 0.5
        f *= 2.03
    return tot / norm


def smoothstep(a, b, x):
    t = np.clip((np.asarray(x, dtype=np.float64) - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


FIELD_REF = [None]


def push_inland(P, margin, iters=10):
    """海岸からの距離が margin 未満の点を、距離場の勾配の向き（内陸）へ押す"""
    F = FIELD_REF[0]
    P = P.copy()
    for _ in range(iters):
        sd = sample_field(F, P[:, 0], P[:, 1])
        gx = sample_field(F, P[:, 0] + 0.25, P[:, 1]) - sample_field(F, P[:, 0] - 0.25, P[:, 1])
        gy = sample_field(F, P[:, 0], P[:, 1] + 0.25) - sample_field(F, P[:, 0], P[:, 1] - 0.25)
        g = np.stack([gx, gy], 1)
        nr = np.hypot(g[:, 0], g[:, 1])
        g = g / np.maximum(nr, 1e-6)[:, None]
        need = np.clip(margin - sd, 0, None)
        P = P + g * (need * 0.7)[:, None]
    return P


def highway_xy(n=600):
    """経路を Catmull-Rom で滑らかにした点列（投影後）と、各点までの累積長"""
    P = np.array([proj(*p) for p in HIGHWAY])
    P = np.vstack([P[0] * 2 - P[1], P, P[-1] * 2 - P[-2]])
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for t in np.linspace(0, 1, 24, endpoint=False):
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(P[-2])
    pts = np.array(out)
    if FIELD_REF[0] is not None:
        pts = push_inland(pts, 3.4)
        k = 45
        pad = np.pad(pts, ((k, k), (0, 0)), mode='edge')
        ker = np.ones(2 * k + 1) / (2 * k + 1)
        pts = np.stack([np.convolve(pad[:, 0], ker, 'valid'), np.convolve(pad[:, 1], ker, 'valid')], 1)
    seg = np.hypot(*(pts[1:] - pts[:-1]).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    u = np.linspace(0, s[-1], n)
    return np.stack([np.interp(u, s, pts[:, 0]), np.interp(u, s, pts[:, 1])], 1), u


_HW = None


def corridor_dist(x, y):
    global _HW
    if _HW is None:
        _HW = highway_xy(400)[0]
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    best = np.full(x.shape, 1e9)
    for px, py in _HW[::2]:
        best = np.minimum(best, (x - px) ** 2 + (y - py) ** 2)
    return np.sqrt(best)


def city_weight(x, y):
    w = np.zeros(np.shape(x))
    for _, lo, la, r, _h in CITIES:
        cx, cy = proj(lo, la)
        w = np.maximum(w, np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (r * 1.1) ** 2))
    return w


def mountain_weight(x, y):
    w = np.zeros(np.shape(x))
    for lo, la, r, s in RANGES:
        cx, cy = proj(lo, la)
        w = w + s * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / r ** 2)
    return w


def relief(x, y, sdf, use_corridor=True):
    """陸の高さの上乗せ分（0 〜 HMAX 程度）。sdf は海岸からの距離（陸で正）"""
    x, y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    w = np.clip(0.12 + mountain_weight(x, y) + 0.35 * (fbm(x * 0.05, y * 0.05, 3, 7) - 0.5), 0, 1.3)
    n = fbm(x * 0.16, y * 0.16, 5, 3)
    ridge = 1 - np.abs(2 * fbm(x * 0.11 + 5, y * 0.11 - 3, 4, 11) - 1)
    shape = 0.30 + 0.45 * n + 0.45 * ridge ** 1.6
    coast = smoothstep(0.4, 5.0, sdf)
    damp = np.ones(x.shape)
    for lo, la, r, p in PLAINS:
        cx, cy = proj(lo, la)
        damp *= 1 - p * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / r ** 2)
    if use_corridor:
        damp *= smoothstep(3.8, 9.5, corridor_dist(x, y))
    damp *= 1 - 0.9 * city_weight(x, y)
    return HMAX * (w ** 1.15) * shape * coast * damp * 0.9


def sample_field(F, x, y):
    """距離場 F（FH x FW）を双一次補間で引く。範囲外は端の値"""
    u = (np.asarray(x) - FX0) / (FX1 - FX0) * (FW - 1)
    v = (np.asarray(y) - FY0) / (FY1 - FY0) * (FH - 1)
    u = np.clip(u, 0, FW - 1.001)
    v = np.clip(v, 0, FH - 1.001)
    u0, v0 = np.floor(u).astype(int), np.floor(v).astype(int)
    fu, fv = u - u0, v - v0
    a = F[v0, u0] * (1 - fu) + F[v0, u0 + 1] * fu
    b = F[v0 + 1, u0] * (1 - fu) + F[v0 + 1, u0 + 1] * fu
    return a * (1 - fv) + b * fv


def height(x, y, F):
    sdf = sample_field(F, x, y).astype(np.float64)
    return Z_COAST + 0.10 * smoothstep(0, 3, sdf) + relief(x, y, sdf), sdf
