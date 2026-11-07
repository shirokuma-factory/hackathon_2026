"""v5 ツアーの流れ（実データの OSRM 経路から、載せた車の車線と空車の車線を作る）。numpy と json だけ。
地形に沿わせる（DEM を引いて z を決める）。呼ぶ前に geo_v4_center の FIELD_REF と DEM_REF を設定すること。"""
import json
import os

import numpy as np

import geo_v4_center as G

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DATA = os.path.join(ROOT, "web", "data")
STEP = 0.10


def gsmooth(a, sig):
    """端を伸ばしたガウス平滑（a は N または N x k）"""
    a = np.asarray(a, float)
    if sig <= 0:
        return a
    r = int(3 * sig)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sig) ** 2)
    k /= k.sum()
    pad = np.pad(a, [(r, r)] + [(0, 0)] * (a.ndim - 1), mode="edge")
    if a.ndim == 1:
        return np.convolve(pad, k, "valid")
    return np.stack([np.convolve(pad[:, i], k, "valid") for i in range(a.shape[1])], 1)


def decode(c):
    x, y = c[0], c[1]
    pts = [(x / 1e4, y / 1e4)]
    for i in range(2, len(c) - 1, 2):
        x += c[i]
        y += c[i + 1]
        pts.append((x / 1e4, y / 1e4))
    return pts


def resample(P, step):
    seg = np.hypot(*(P[1:] - P[:-1]).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    u = np.append(np.arange(0, s[-1], step), s[-1])
    return np.stack([np.interp(u, s, P[:, 0]), np.interp(u, s, P[:, 1])], 1)


def load_center(dest, origin="13104"):
    d = json.load(open(os.path.join(DATA, "routes_%s.json" % origin), encoding="utf-8"))
    leg = d["dests"][dest]["m"]["truck"]["legs"]
    pts = []
    for lg in leg:
        if lg["m"] == "t":
            pts += decode(lg["c"])
    P = np.array([G.proj(*q) for q in pts])
    return resample(P, STEP)          # 先頭が東京（新宿）、末尾が行き先


def make_lane(center, off, sig_geom=4.0, sig_head=14.0, z_off=0.09):
    """中心線から横にずらした車線。戻り値: s（累積長）、x, y, z、進行方向（滑らか）、傾き"""
    c = gsmooth(center, sig_geom)
    t = np.gradient(gsmooth(c, 6.0), axis=0)
    t /= np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-9)[:, None]
    n = np.stack([-t[:, 1], t[:, 0]], 1)
    xy = c + n * off
    s = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
    z = G.height(xy[:, 0], xy[:, 1], G.FIELD_REF[0])[0]
    z = gsmooth(z, 1.5) + z_off
    zh = gsmooth(z, 8.0)
    h = np.gradient(gsmooth(xy, sig_head), axis=0)
    h /= np.maximum(np.hypot(h[:, 0], h[:, 1]), 1e-9)[:, None]
    ds = np.maximum(np.gradient(s), 1e-6)
    slope = np.gradient(zh) / ds
    return dict(s=s, x=xy[:, 0], y=xy[:, 1], z=z, hx=h[:, 0], hy=h[:, 1], slope=slope, L=float(s[-1]))


def at(lane, s):
    s = float(np.clip(s, 0, lane["L"]))
    g = lambda k: float(np.interp(s, lane["s"], lane[k]))
    hx, hy = g("hx"), g("hy")
    nrm = max(np.hypot(hx, hy), 1e-9)
    return np.array([g("x"), g("y"), g("z")]), np.array([hx / nrm, hy / nrm, np.clip(g("slope"), -0.2, 0.2)])


# 流れの定義。seg: 主に見せる場面、win: 描く時間の範囲（秒）、trucks: 載せた車の台数、empties: 空車の台数、
# tail: その流れの行き先側の何単位に車を走らせるか（None は全線）
KANOYA_WP = [(130.56, 31.60), (130.74, 31.74), (130.92, 31.64), (130.90, 31.48), (130.85, 31.38)]


def load_center_kanoya():
    """東京 → 鹿児島（OSRM の実経路）に、鹿児島市 → 鹿屋（湾の東岸）の区間を滑らかにつないだ中心線"""
    P = load_center("鹿児島県")
    wp = np.array([G.proj(*q) for q in KANOYA_WP])
    wp[0] = P[-1]
    ext = []
    pts = np.vstack([wp[0] * 2 - wp[1], wp, wp[-1] * 2 - wp[-2]])
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for t in np.linspace(0, 1, 30, endpoint=False):
            ext.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    ext.append(wp[-1])
    ext = G.push_inland(np.array(ext), 0.35)
    return resample(np.vstack([P, ext[1:]]), STEP)


FLOWS = [
    dict(key="kagoshima", dest="鹿屋", off=0.24, wl=0.22, we=0.26, win=(19.0, 27.9), trucks=5, empties=2, tail=62.0, boost=1.3),
    dict(key="kansai", dest="大阪府", off=0.16, wl=0.14, we=0.17, win=(12.2, 20.9), trucks=3, empties=2, tail=None, boost=0.9),
    dict(key="kanto_miyagi", dest="宮城県", off=0.13, wl=0.0, we=0.15, win=(5.4, 13.9), trucks=0, empties=0, tail=None, boost=0.8),
    dict(key="kanto_niigata", dest="新潟県", off=0.13, wl=0.0, we=0.15, win=(5.4, 13.9), trucks=0, empties=0, tail=None, boost=0.8),
    dict(key="kanto_nagano", dest="長野県", off=0.13, wl=0.0, we=0.15, win=(5.4, 13.9), trucks=0, empties=0, tail=None, boost=0.8),
    dict(key="kanto_aichi", dest="愛知県", off=0.13, wl=0.0, we=0.15, win=(5.4, 13.9), trucks=0, empties=0, tail=None, boost=0.8),
    dict(key="kanto_tochigi", dest="栃木県", off=0.13, wl=0.0, we=0.15, win=(5.4, 13.9), trucks=0, empties=0, tail=None, boost=0.8),
    dict(key="ky_fukuoka", dest="福岡県", off=0.12, wl=0.10, we=0.15, win=(19.4, 27.9), trucks=1, empties=0, tail=36.0, boost=0.7),
    dict(key="ky_kumamoto", dest="熊本県", off=0.12, wl=0.09, we=0.14, win=(19.6, 27.9), trucks=1, empties=0, tail=36.0, boost=0.6),
    dict(key="to_miyagi", dest="宮城県", off=0.13, wl=0.10, we=0.17, win=(26.2, 33.9), trucks=1, empties=0, tail=28.0, boost=0.7),
    dict(key="to_iwate", dest="岩手県", off=0.13, wl=0.09, we=0.16, win=(26.4, 33.9), trucks=1, empties=0, tail=28.0, boost=0.65),
    dict(key="to_akita", dest="秋田県", off=0.13, wl=0.09, we=0.15, win=(26.6, 33.9), trucks=1, empties=0, tail=28.0, boost=0.6),
    dict(key="to_aomori", dest="青森県", off=0.13, wl=0.08, we=0.15, win=(26.6, 33.9), trucks=0, empties=0, tail=28.0, boost=0.6),
]


def build_flows():
    cache = {}
    out = []
    for f in FLOWS:
        if f["dest"] not in cache:
            cache[f["dest"]] = load_center_kanoya() if f["dest"] == "鹿屋" else load_center(f["dest"])
        c = cache[f["dest"]]
        d = dict(f)
        d["loaded"] = make_lane(c, +f["off"]) if f["wl"] > 0 else None
        d["empty"] = make_lane(c, -f["off"])
        d["L"] = d["empty"]["L"]
        out.append(d)
    return out


def queue_site(hub_xy, length=12.5):
    """配送センターと待ち行列の車線（直線）。海岸から離れ、まっすぐ取れる向きを選ぶ"""
    best = None
    for ang in np.radians(list(range(-60, 31, 10)) + list(range(150, 211, 10))):
        d = np.array([np.cos(ang), np.sin(ang)])
        pts = [np.array(hub_xy) + d * s for s in np.linspace(-0.5, length + 0.5, 24)]
        sd = min(float(G.sample_field(G.FIELD_REF[0], p[0], p[1])) for p in pts)
        zz = [float(G.height(np.array([p[0]]), np.array([p[1]]), G.FIELD_REF[0])[0][0]) for p in pts]
        score = sd - 3.0 * (max(zz) - min(zz))
        if best is None or score > best[0]:
            best = (score, d)
    d = best[1]
    gate = np.array(hub_xy)
    tail = gate + d * length
    P = np.stack([np.linspace(tail[0], gate[0], 100), np.linspace(tail[1], gate[1], 100)], 1)
    lane = make_lane(P, 0.0, sig_geom=0.1, sig_head=1.0, z_off=0.07)
    return dict(lane=lane, gate=gate, dir=d, sd=best[0])


def queue_fixed(gate_xy, d, length=10.5):
    """向きと長さを指定した待ち行列の車線（先頭が門）"""
    d = np.asarray(d, float) / np.hypot(*d)
    gate = np.array(gate_xy)
    tail = gate + d * length
    P = np.stack([np.linspace(tail[0], gate[0], 100), np.linspace(tail[1], gate[1], 100)], 1)
    lane = make_lane(P, 0.0, sig_geom=0.1, sig_head=1.0, z_off=0.07)
    return dict(lane=lane, gate=gate, dir=d, sd=0.0)
