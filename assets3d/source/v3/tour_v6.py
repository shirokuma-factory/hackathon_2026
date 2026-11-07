"""v6 ツアー: 36 秒のカメラの動きと場面の定義（numpy だけ。Blender の外でも使える）。
カメラは「目標点（投影後の座標）・方位・仰角・画面の横幅（地面の単位）・目標点を置く画面位置」で決める。
各場面は、最初の TR 秒で前の場面の終わりの姿勢からなめらかに移り、残りで開始姿勢から終了姿勢へゆっくり動く。"""
import math
import numpy as np

LON0, LAT0 = 137.5, 36.5
S = 10.0
KX = math.cos(math.radians(36.5))
FPS, DUR = 24, 36.0
NFR = int(FPS * DUR)
TR = 2.0           # 場面の切り替えにかける秒数


def proj(lon, lat):
    return ((lon - LON0) * KX * S, (lat - LAT0) * S)


def P(lon, lat, **k):
    x, y = proj(lon, lat)
    return dict(x=x, y=y, **k)


# 姿勢: x, y（目標点）、az、pitch、width、ts（左から, 上から）
def pose(lon, lat, az, pitch, width, nx=0.5, ny=0.25, lens=50.0, fs=0.0):
    x, y = proj(lon, lat)
    return dict(x=x, y=y, az=az, pitch=pitch, width=width, nx=nx, ny=ny, lens=lens, fs=fs)


SEGS = [
    dict(key="national", label="全国", t0=0.0, t1=6.0,
         A=pose(138.2, 38.0, 36, 44, 212, 0.5, 0.22, 85, 0.5), B=pose(138.0, 37.6, 34, 42, 198, 0.5, 0.22, 85, 0.5), anchor=(137.0, 35.4)),
    dict(key="kanto", label="関東", t0=6.0, t1=13.0,
         A=pose(139.45, 35.78, 10, 30, 46, 0.5, 0.26, 50, 0.16), B=pose(139.60, 35.72, 30, 40, 38, 0.5, 0.26, 50, 0.16), anchor=(139.76, 35.60)),
    dict(key="kansai", label="関西", t0=13.0, t1=20.0,
         A=pose(137.0, 35.25, 14, 35, 72, 0.5, 0.27, 35, 0.2), B=pose(137.3, 35.15, 30, 42, 66, 0.5, 0.27, 35, 0.2), anchor=(137.4, 35.1)),
    dict(key="kyushu", label="九州", t0=20.0, t1=27.0,
         A=pose(130.95, 31.75, 18, 33, 60, 0.5, 0.27, 50, 0.16), B=pose(131.1, 33.1, 30, 41, 90, 0.5, 0.27, 50, 0.18), anchor=(130.9, 32.2)),
    dict(key="tohoku", label="地方（東北）", t0=27.0, t1=33.0,
         A=pose(140.4, 38.2, 28, 41, 102, 0.5, 0.27, 70, 0.25), B=pose(140.7, 39.3, 32, 40, 88, 0.5, 0.27, 70, 0.25), anchor=(141.0, 39.4)),
    dict(key="national2", label="全国", t0=33.0, t1=36.0,
         A=pose(138.0, 37.6, 34, 42, 198, 0.5, 0.22, 85, 0.5), B=pose(138.2, 38.0, 36, 44, 212, 0.5, 0.22, 85, 0.5), anchor=(137.0, 35.4)),
]


def sstep(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * x * (x * (x * 6 - 15) + 10)


def mixp(a, b, u):
    return {k: a[k] * (1 - u) + b[k] * u for k in a}


def cam_params(t):
    t = t % DUR
    for i, sg in enumerate(SEGS):
        if sg["t0"] <= t < sg["t1"] or i == len(SEGS) - 1:
            break
    t0, t1 = sg["t0"], sg["t1"]
    # 場面の中の動き（切り替えのあと）
    u = sstep((t - t0) / (t1 - t0))
    cur = mixp(sg["A"], sg["B"], u)
    if i > 0:
        prev = SEGS[i - 1]["B"]
        k = sstep((t - t0) / TR)
        # 切り替え中は、前の場面の終わり → この場面の「その時点の動き」へ
        return mixp(prev, cur, k) if t - t0 < TR else cur
    else:
        return cur


def cam_matrix(c, dist=None):
    dist = c['lens'] * c['width'] / 36.0
    az, el = math.radians(c["az"]), math.radians(c["pitch"])
    tgt = np.array([c["x"], c["y"], 0.8])
    d = np.array([math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])
    cam = tgt + d * dist
    f = (tgt - cam)
    f /= np.linalg.norm(f)
    r = np.cross(f, [0, 0, 1.0])
    r /= np.linalg.norm(r)
    u = np.cross(r, f)
    lens = c['lens']
    return cam, f, r, u, lens


def project(c, xyz, aspect=16 / 9):
    """地面の点（投影後の x, y, z）の画面位置（左から, 上から の 0..1）"""
    cam, f, r, u, lens = cam_matrix(c)
    p = np.asarray(xyz, float)
    v = p - cam
    zc = v @ f
    xi = (v @ r) / zc * lens / 36.0
    yi = (v @ u) / zc * lens / 36.0
    sx = 0.5 - (0.5 - c["nx"])
    # nx は目標点の位置。目標点では xi = yi = 0
    nx = c["nx"] + xi
    ny = c["ny"] - yi * aspect
    return nx, ny


if __name__ == "__main__":
    pts = {"九州南": (130.3, 31.3), "九州北": (130.4, 33.6), "北海道北東": (145.4, 44.2), "札幌": (141.3, 43.0), "東京": (139.75, 35.68), "大阪": (135.5, 34.69), "青森": (140.7, 40.8), "沖縄側": (129.0, 34.0)}
    for t in (0, 3, 6, 9, 12, 16, 20, 23, 27, 30, 33, 35.9):
        c = cam_params(t)
        s = " ".join("%s(%.2f,%.2f)" % (n, *project(c, (*proj(*ll), 1.0))) for n, ll in pts.items())
        print("t=%.1f w=%.0f %s" % (t, c["width"], s))
