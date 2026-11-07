"""v7 ツアー: 36 秒、継ぎ目のない周回の飛行。高い所をなめらかに飛び、少し傾く（ロール）。
カメラの姿勢は x, y（目標点）、az、pitch、width（画面の横幅が地面の何単位か）、lens、roll、fs（被写界深度）、ny（目標点の画面の高さ）。
全体を周期的なエルミート補間で結ぶので、速度も連続（止まらない）。numpy だけ。"""
import math
import numpy as np

LON0, LAT0 = 137.5, 36.5
S = 10.0
KX = math.cos(math.radians(36.5))
FPS, DUR = 24, 36.0
NFR = int(FPS * DUR)


def proj(lon, lat):
    return ((lon - LON0) * KX * S, (lat - LAT0) * S)


# (時刻, 経度, 緯度, az, pitch, width, lens, roll, fs, ny)
K = [
    (0.0, 138.2, 38.6, 36, 22, 215, 35, 0.0, 0.0, 0.24),
    (4.2, 139.2, 37.1, 30, 24, 112, 35, 2.5, 0.0, 0.24),
    (7.8, 139.55, 36.1, 14, 27, 54, 35, -1.5, 0.45, 0.15),
    (11.2, 138.3, 35.6, 22, 28, 62, 35, -2.5, 0.45, 0.09),
    (15.5, 136.1, 35.15, 24, 28, 64, 35, 1.5, 0.45, 0.09),
    (19.2, 133.6, 34.5, 34, 27, 82, 35, 3.0, 0.45, 0.09),
    (23.6, 131.05, 32.1, 22, 27, 58, 35, -2.0, 0.45, 0.15),
    (26.6, 131.4, 33.9, 30, 25, 90, 35, 1.0, 0.0, 0.24),
    (28.8, 136.0, 37.0, 34, 22, 232, 35, 3.5, 0.0, 0.24),
    (31.2, 140.4, 38.5, 28, 27, 84, 35, -2.0, 0.45, 0.15),
    (33.6, 138.9, 38.6, 34, 22, 190, 35, 0.8, 0.0, 0.24),
]
SCENES = [
    dict(key="national", label="全国", t0=0.0, t1=6.0, anchor=(137.2, 35.5)),
    dict(key="kanto", label="関東", t0=6.0, t1=13.0, anchor=(139.6, 35.9)),
    dict(key="kansai", label="関西", t0=13.0, t1=20.0, anchor=(136.4, 35.0)),
    dict(key="kyushu", label="九州", t0=20.0, t1=27.0, anchor=(130.95, 31.9)),
    dict(key="tohoku", label="地方（東北）", t0=27.0, t1=33.0, anchor=(140.8, 39.2)),
    dict(key="national2", label="全国", t0=33.0, t1=36.0, anchor=(137.2, 35.5)),
]

_names = ("x", "y", "az", "pitch", "width", "lens", "roll", "fs", "ny")


def _vec(k):
    t, lo, la, az, pi, w, ln, rl, fs, ny = k
    x, y = proj(lo, la)
    return t, np.array([x, y, az, pi, math.log(w), ln, rl, fs, ny], float)


_T, _P = zip(*[_vec(k) for k in K])
_T = np.array(_T); _P = np.array(_P)
_N = len(_T)


def cam_params(t):
    t = t % DUR
    # 周期的なエルミート補間
    tt = np.concatenate([_T[-1:] - DUR, _T, _T[:1] + DUR, _T[1:2] + DUR])
    pp = np.vstack([_P[-1:], _P, _P[:1], _P[1:2]])
    # 区間を探す
    i = int(np.searchsorted(tt, t, side="right")) - 1
    i = max(1, min(i, len(tt) - 3))
    t0, t1 = tt[i], tt[i + 1]
    p0, p1 = pp[i], pp[i + 1]
    m0 = (pp[i + 1] - pp[i - 1]) / (tt[i + 1] - tt[i - 1])
    m1 = (pp[i + 2] - pp[i]) / (tt[i + 2] - tt[i])
    h = t1 - t0
    u = (t - t0) / h
    h00 = 2 * u ** 3 - 3 * u ** 2 + 1
    h10 = u ** 3 - 2 * u ** 2 + u
    h01 = -2 * u ** 3 + 3 * u ** 2
    h11 = u ** 3 - u ** 2
    v = h00 * p0 + h10 * h * m0 + h01 * p1 + h11 * h * m1
    c = dict(zip(_names, v))
    c["width"] = math.exp(c["width"])
    c["nx"] = 0.5
    c["fs"] = max(0.0, c["fs"])
    return c


def cam_basis(c):
    az, el = math.radians(c["az"]), math.radians(c["pitch"])
    dist = c["lens"] * c["width"] / 36.0
    tgt = np.array([c["x"], c["y"], 0.8])
    d = np.array([math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])
    cam = tgt + d * dist
    f = tgt - cam
    f /= np.linalg.norm(f)
    r = np.cross(f, [0, 0, 1.0]); r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return cam, f, r, u, dist


def project_pts(c, P, aspect=16 / 9):
    """点の配列（N x 3）→ 画面（左から, 上から）と奥行き"""
    cam, f, r, u, dist = cam_basis(c)
    v = np.asarray(P, float) - cam
    zc = v @ f
    xi = (v @ r) / np.maximum(zc, 1e-6) * c["lens"] / 36.0
    yi = (v @ u) / np.maximum(zc, 1e-6) * c["lens"] / 36.0
    cr, sr = math.cos(math.radians(c["roll"])), math.sin(math.radians(c["roll"]))
    xr, yr = xi * cr - yi * sr, xi * sr + yi * cr
    return c["nx"] + xr, c["ny"] - yr * aspect, zc


def project(c, xyz):
    nx, ny, _ = project_pts(c, np.array([xyz]))
    return float(nx[0]), float(ny[0])


if __name__ == "__main__":
    for t in (0, 2, 4.2, 6, 7.8, 10, 11.2, 14, 15.5, 18, 19.2, 22, 23.6, 25, 26.6, 28.8, 30, 31.2, 33.6, 35.9):
        c = cam_params(t)
        print("t=%5.1f w=%5.0f az=%4.1f pitch=%4.1f roll=%4.1f alt=%5.0f" % (t, c["width"], c["az"], c["pitch"], c["roll"], c["lens"] * c["width"] / 36.0 * math.sin(math.radians(c["pitch"]))))
