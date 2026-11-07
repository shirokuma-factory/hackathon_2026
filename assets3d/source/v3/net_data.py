"""物流網の実データ（web/data の OSRM 経路・貨物鉄道・ハブ・物流センサスの流量）から、成長するネットワークの帯メッシュ用の配列を作る。
通常の Python（numpy）。使い方: python net_data.py <out dir>   出力: <out dir>/cache/network.npz
道路: routes_<起点>.json（7 つの関東の起点から 46 都道府県への OSRM 経路）を格子でまとめ、起点から先へ伸びる木にする。
      重みは market.json の都道府県間のトラック流量（起点の県 → 行き先の県）。
鉄道: routes_rail.json の貨物鉄道の線を、近い大きなハブからの距離で成長させる。
ハブ: hubs.json の貨物ターミナル（stations）。到達した時刻に小さく光る。"""
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import geo_v4_center as G  # noqa

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.abspath(sys.argv[1])
DATA = os.path.join(ROOT, "web", "data")
OUTNAME = sys.argv[2] if len(sys.argv) > 2 else "network.npz"
WM = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
FIELD = np.load(os.path.join(OUT, "cache", "sdf.npy")).astype(np.float32)
G.FIELD_REF[0] = FIELD
G.DEM_REF[0] = np.load(os.path.join(OUT, "cache", "dem.npy"))
G.set_exag(float(os.environ.get("DIO_EXAG", "7.5")))
G.Z_COAST = float(os.environ.get("DIO_ZCOAST", "0.42"))
G.load_demhi(os.path.join(OUT, "cache"))

ORIGINS = {"08201": 7, "09201": 8, "10201": 9, "11107": 10, "12101": 11, "13104": 12, "14104": 13}   # 起点 -> 県の番号
V_ROAD = 14.0      # 道路の成長の速さ（単位/秒）。1 単位 = 0.1 度
V_RAIL = 7.0
T0 = 1.0           # 成長の開始（秒）
CELL = 0.16
BOX = (-85, 82, -62, 95)     # 画面に入る範囲（投影後）


def decode(c):
    x, y = c[0], c[1]
    pts = [(x / 1e4, y / 1e4)]
    for i in range(2, len(c) - 1, 2):
        x += c[i]
        y += c[i + 1]
        pts.append((x / 1e4, y / 1e4))
    return pts


def resample(P, step):
    P = np.asarray(P, float)
    seg = np.hypot(*(P[1:] - P[:-1]).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    if s[-1] < step:
        return P, s
    u = np.arange(0, s[-1], step)
    u = np.append(u, s[-1])
    return np.stack([np.interp(u, s, P[:, 0]), np.interp(u, s, P[:, 1])], 1), u


def in_box(p):
    return BOX[0] <= p[0] <= BOX[1] and BOX[2] <= p[1] <= BOX[3]


class Graph:
    def __init__(self):
        self.idx = {}
        self.pos = []
        self.cnt = []
        self.T = []
        self.edges = {}     # (a,b) 向きは最初に現れた向き -> [重み, 種別]

    def node(self, p, t):
        k = (int(round(p[0] / CELL)), int(round(p[1] / CELL)))
        i = self.idx.get(k)
        if i is None:
            i = len(self.pos)
            self.idx[k] = i
            self.pos.append([p[0], p[1]])
            self.cnt.append(1)
            self.T.append(t)
        else:
            n = self.cnt[i]
            self.pos[i][0] = (self.pos[i][0] * n + p[0]) / (n + 1)
            self.pos[i][1] = (self.pos[i][1] * n + p[1]) / (n + 1)
            self.cnt[i] = n + 1
            if t < self.T[i]:
                self.T[i] = t
        return i

    def add_edge(self, a, b, w):
        if a == b:
            return
        k = (a, b) if (b, a) not in self.edges else (b, a)
        if k in self.edges:
            self.edges[k][0] += w
        else:
            self.edges[k] = [w, 0]


def pref_weights():
    m = json.load(open(os.path.join(DATA, "market.json"), encoding="utf-8"))
    W = {}
    for p in m["modes"]["truck"]["pairs"]:
        W[(int(p[0]), int(p[1]))] = float(p[2])
    return m["prefs"], W


def build_road(paths=None):
    prefs, W = pref_weights()
    g = Graph()
    for oc, oi in ORIGINS.items():
        d = json.load(open(os.path.join(DATA, "routes_%s.json" % oc), encoding="utf-8"))
        for dn, v in d["dests"].items():
            if dn not in prefs:
                continue
            di = prefs.index(dn)
            w = W.get((oi, di), 0.0)
            w_in = W.get((di, oi), 0.0)
            tr = v["m"].get("truck")
            if not tr:
                continue
            pts = []
            for leg in tr["legs"]:
                if leg["m"] == "t":
                    pts += decode(leg["c"])
            if len(pts) < 2:
                continue
            P = np.array([G.proj(*q) for q in pts])
            R, s = resample(P, 0.07)
            prev = None
            run = []
            for q, sv in zip(R, s):
                if not in_box(q):
                    prev = None
                    if paths is not None and len(run) > 1:
                        paths.append((run, w, w_in))
                    run = []
                    continue
                i = g.node(q, T0 + sv / V_ROAD)
                if prev is not None:
                    g.add_edge(prev, i, w + 800.0)
                if not run or run[-1] != i:
                    run.append(i)
                prev = i
            if paths is not None and len(run) > 1:
                paths.append((run, w, w_in))
    pos = np.array(g.pos)
    T = np.array(g.T)
    ed = np.array(list(g.edges.keys()))
    wt = np.array([g.edges[tuple(k)][0] for k in ed])
    print("road nodes", len(pos), "edges", len(ed))
    vis = (pos[:, 0] > -70) & (pos[:, 0] < 70) & (pos[:, 1] > -50) & (pos[:, 1] < 85)
    T = T0 + (T - T0) * (5.0 / (T[vis].max() - T0))
    return pos, T, ed, wt, g


def build_rail(hubs_xy):
    r = json.load(open(os.path.join(DATA, "routes_rail.json"), encoding="utf-8"))
    g = Graph()
    for c in r["lines"]:
        if len(c) < 4:
            continue
        pts = decode(c)
        P = np.array([G.proj(*q) for q in pts])
        if len(P) < 2:
            continue
        R, s = resample(P, 0.08)
        prev = None
        for q in R:
            if not in_box(q):
                prev = None
                continue
            i = g.node(q, 0.0)
            if prev is not None:
                g.add_edge(prev, i, 1.0)
            prev = i
    pos = np.array(g.pos)
    ed = np.array(list(g.edges.keys()))
    dmin = np.full(len(pos), 1e9)
    for hx, hy in hubs_xy:
        dmin = np.minimum(dmin, np.hypot(pos[:, 0] - hx, pos[:, 1] - hy))
    T = T0 + 0.3 + dmin / V_RAIL
    vis = (pos[:, 0] > -70) & (pos[:, 0] < 70) & (pos[:, 1] > -50) & (pos[:, 1] < 85)
    T = T0 + 0.3 + (T - T0 - 0.3) * (4.7 / (T[vis].max() - T0 - 0.3))
    print("rail nodes", len(pos), "edges", len(ed), "Tmax %.1f" % T.max())
    return pos, T, ed


def ribbons(pos, T, ed, wt, z_off, width, kind, W):
    """各辺を細長い四角にする。両端を少し延ばして継ぎ目を隠す。戻り値: 頂点、面、角ごとの [T, W, S, 種別]"""
    a, b = ed[:, 0], ed[:, 1]
    pa, pb = pos[a], pos[b]
    d = pb - pa
    L = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)
    t = d / L[:, None]
    nrm = np.stack([-t[:, 1], t[:, 0]], 1)
    ext = np.minimum(0.05, L * 0.3)[:, None]
    pa2, pb2 = pa - t * ext, pb + t * ext
    hw = (width[:, None] / 2) * nrm
    v0, v1, v2, v3 = pa2 + hw, pb2 + hw, pb2 - hw, pa2 - hw
    V = np.stack([v0, v1, v2, v3], 1).reshape(-1, 2)
    z = np.max([G.height(V[:, 0] + dx, V[:, 1] + dy, FIELD)[0] for dx, dy in ((0, 0), (0.15, 0), (-0.15, 0), (0, 0.15), (0, -0.15))], axis=0) + z_off
    V3 = np.column_stack([V, z])
    n = len(ed)
    F = (np.array([0, 1, 2, 3])[None] + (np.arange(n) * 4)[:, None])
    Ta, Tb = T[a], T[b]
    S = np.stack([Ta, Tb, Tb, Ta], 1) * V_ROAD
    Tc = np.stack([Ta, Tb, Tb, Ta], 1)
    Wn = np.repeat(W[:, None], 4, 1)
    attr = np.stack([Tc, Wn, S, np.full_like(Tc, kind)], -1)       # n x 4 x 4
    return V3.astype(np.float32), F, attr.reshape(-1, 4).astype(np.float32)



CENTERS = [(139.76, 35.68, 2.6), (135.50, 34.69, 2.3), (136.90, 35.17, 1.9), (130.40, 33.59, 1.4), (140.88, 38.27, 1.2), (141.35, 43.06, 1.4), (139.62, 35.45, 1.5)]


def build_flow(g, ed, paths, width_mul=1.0):
    """流量（県間トラック輸送のトン数を木の辺ごとに足したもの）と、粒の進む時間 theta（幹線は速く、都市の近くは遅い）から、流れ用の帯を作る。
    theta は起点から辺をたどる所要時間。粒は theta でそろえた位相で動くので、遅い所は自然に密になる。"""
    pos = np.array(g.pos)
    n = len(pos)
    eidx = {}
    for k, (a, b) in enumerate(ed):
        eidx[(int(a), int(b))] = (k, 1)
        eidx[(int(b), int(a))] = (k, -1)
    Wab = np.zeros(len(ed)); Wba = np.zeros(len(ed))
    for run, wo, wi in paths:
        wo, wi = wo + 300.0, wi + 300.0
        for p_, q_ in zip(run[:-1], run[1:]):
            k, sg = eidx[(p_, q_)]
            if sg > 0:
                Wab[k] += wo; Wba[k] += wi
            else:
                Wba[k] += wo; Wab[k] += wi
    tot = Wab + Wba
    ref = np.percentile(tot, 99.0)
    wn = np.clip(tot / ref, 0, 1) ** 0.5
    mid = (pos[ed[:, 0]] + pos[ed[:, 1]]) / 2
    cong = np.zeros(len(ed))
    for lo, la, sig in CENTERS:
        cx, cy = G.proj(lo, la)
        cong = np.maximum(cong, np.exp(-((mid[:, 0] - cx) ** 2 + (mid[:, 1] - cy) ** 2) / (2 * sig ** 2)))
    speed = (1.5 + 3.6 * wn) * (1 - 0.72 * cong)
    length = np.hypot(*(pos[ed[:, 1]] - pos[ed[:, 0]]).T)
    dt = length / speed
    theta = np.full(n, 1e9)
    for run, wo, wi in paths:
        acc = 0.0
        theta[run[0]] = min(theta[run[0]], 0.0)
        for p_, q_ in zip(run[:-1], run[1:]):
            k, sg = eidx[(p_, q_)]
            acc += dt[k]
            theta[q_] = min(theta[q_], acc)
    theta[theta > 1e8] = 0.0
    fwd_is_ab = theta[ed[:, 1]] >= theta[ed[:, 0]]
    Wf = np.where(fwd_is_ab, Wab, Wba)
    Wb = np.where(fwd_is_ab, Wba, Wab)
    wf = np.clip(Wf / ref, 0, 1) ** 0.5
    wb = np.clip(Wb / ref, 0, 1) ** 0.5
    width = (0.035 + 0.06 * wn) * width_mul
    return theta, wf, wb, width, dt, eidx


def flow_ribbons(pos, theta, T, ed, wf, wb, width, z_off):
    a, b = ed[:, 0], ed[:, 1]
    pa, pb = pos[a], pos[b]
    d = pb - pa
    L = np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-6)
    t = d / L[:, None]
    nrm = np.stack([-t[:, 1], t[:, 0]], 1)
    ext = np.minimum(0.04, L * 0.3)[:, None]
    pa2, pb2 = pa - t * ext, pb + t * ext
    hw = (width[:, None] / 2) * nrm
    V = np.stack([pa2 + hw, pb2 + hw, pb2 - hw, pa2 - hw], 1).reshape(-1, 2)
    z = np.max([G.height(V[:, 0] + dx, V[:, 1] + dy, FIELD)[0] for dx, dy in ((0, 0), (0.15, 0), (-0.15, 0), (0, 0.15), (0, -0.15))], axis=0) + z_off
    V3 = np.column_stack([V, z])
    nq = len(ed)
    F = (np.array([0, 1, 2, 3])[None] + (np.arange(nq) * 4)[:, None])
    th = np.stack([theta[a], theta[b], theta[b], theta[a]], 1)
    Tt = np.stack([T[a], T[b], T[b], T[a]], 1)
    A = np.stack([th, np.repeat(wf[:, None], 4, 1), np.repeat(wb[:, None], 4, 1), Tt], -1)
    return V3.astype(np.float32), F, A.reshape(-1, 4).astype(np.float32)


def save_paths(g, T, paths, dt_e, eidx):
    """粒の流れ用: 経路ごとの座標・累積時間 acc・成長時刻・往き帰りの重み（トン数）"""
    pos = np.array(g.pos)
    off, px, py, pz, acc, tn, wo, wi = [0], [], [], [], [], [], [], []
    for run, w_out, w_in in paths:
        if len(run) < 3:
            continue
        a = [0.0]
        for p_, q_ in zip(run[:-1], run[1:]):
            k, sg = eidx[(p_, q_)]
            a.append(a[-1] + dt_e[k])
        xy = pos[run]
        z = G.height(xy[:, 0], xy[:, 1], FIELD)[0] + 0.15
        px += list(xy[:, 0]); py += list(xy[:, 1]); pz += list(z); acc += a; tn += list(T[run])
        off.append(len(px)); wo.append(w_out); wi.append(w_in)
    np.savez_compressed(os.path.join(OUT, "cache", "paths_v6.npz"), off=np.array(off), x=np.array(px, np.float32), y=np.array(py, np.float32),
                        z=np.array(pz, np.float32), acc=np.array(acc, np.float32), tn=np.array(tn, np.float32), wo=np.array(wo), wi=np.array(wi))
    print("paths", len(wo), "nodes", len(px), "acc max %.1f" % max(acc))


def main():
    hubs = json.load(open(os.path.join(DATA, "hubs.json"), encoding="utf-8"))["stations"]
    st = [(h[0], G.proj(h[1], h[2])) for h in hubs]
    major = [xy for n, xy in st if n in ("東京貨物ターミナル", "百済貨物ターミナル", "名古屋貨物ターミナル駅", "福岡貨物ターミナル", "仙台貨物ターミナル", "岡山貨物ターミナル")]
    if len(major) < 3:
        major = [xy for n, xy in st][:6]
    print("major hubs", len(major), [n for n, _ in st][:40])
    paths = []
    rp, rT, re_, rw, graph = build_road(paths)
    wn = np.sqrt(rw / rw.max())
    width = (0.055 + 0.14 * wn) * WM
    V1, F1, A1 = ribbons(rp, rT, re_, rw, 0.14, width, 0.0, wn)
    kp, kT, ke = build_rail(major)
    V2, F2, A2 = ribbons(kp, kT, ke, np.ones(len(ke)), 0.10, np.full(len(ke), 0.04 * WM), 1.0, np.full(len(ke), 0.25))
    theta, wf, wb, fw, dt_e, eidx = build_flow(graph, re_, paths)
    save_paths(graph, rT, paths, dt_e, eidx)
    V3, F3, A3 = flow_ribbons(rp, theta, rT, re_, wf, wb, fw, 0.12)
    V4, F4, A4 = flow_ribbons(rp, theta, rT, re_, wf, wb, fw * 3.4, 0.12)
    hub_xy = np.array([xy for n, xy in st if in_box(xy)])
    hub_T = []
    for hx, hy in hub_xy:
        dmin = np.min(np.hypot(rp[:, 0] - hx, rp[:, 1] - hy))
        near = np.argmin(np.hypot(rp[:, 0] - hx, rp[:, 1] - hy))
        hub_T.append(rT[near] + 0.2)
    hub_z = G.height(hub_xy[:, 0], hub_xy[:, 1], FIELD)[0] + 0.16
    np.savez_compressed(os.path.join(OUT, "cache", OUTNAME), V1=V1, F1=F1, A1=A1, V2=V2, F2=F2, A2=A2,
                        V3=V3, F3=F3, A3=A3, V4=V4, F4=F4, A4=A4, hub_xy=hub_xy, hub_T=np.array(hub_T), hub_z=hub_z, Tmax=max(rT.max(), kT.max()))
    print("saved; road quads", len(F1), "rail quads", len(F2), "hubs", len(hub_xy), "Tmax", max(rT.max(), kT.max()))


if __name__ == "__main__":
    main()
