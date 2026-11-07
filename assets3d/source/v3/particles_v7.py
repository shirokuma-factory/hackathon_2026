"""v7: 交通量の「流体」レイヤー（高い所から見た光の流れ）。道路の木（実データの経路）に沿って数万個の粒を動かし、尾を引かせて、画面空間で光として足す。
粒の数と明るさ・幅は県間トラック輸送トン数に比例（往きと帰りで別）。速度は経路の所要時間（幹線は速く、都市の近くは遅い）から決まり、
遅い所は自然に密になる。横に少し広がり（拡散）、ハブの近くでは小さく渦を巻く。粒の位置は時刻の周期関数（36 秒で一巡）なのでつなぎ目が合う。
使い方: ParticleField(cache_dir) を作り、render(t, cam, W, H) で float32 の RGB（加算用、表示用の値）を得る。"""
import math
import os

import numpy as np
import cv2

DUR = 36.0
PERIOD_SWIRL = 9           # 渦の回転数（36 秒あたり）


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


class ParticleField:
    def __init__(self, cache_dir, total_alive=45000, seed=11):
        d = np.load(os.path.join(cache_dir, "paths_v6.npz"))
        off = d["off"]
        J = len(off) - 1
        self.J = J
        self.x, self.y, self.z, self.acc, self.tn = d["x"], d["y"], d["z"], d["acc"], d["tn"]
        wo, wi = d["wo"].astype(float) + 300.0, d["wi"].astype(float) + 300.0
        ref = np.percentile(np.concatenate([wo, wi]), 99)
        self.wo = np.clip(wo / ref, 0, 1) ** 0.5
        self.wi = np.clip(wi / ref, 0, 1) ** 0.5
        # 経路ごとのキー（全体で単調に増える）
        self.key = np.empty_like(self.acc, dtype=np.float64)
        self.tot = np.zeros(J)
        for j in range(J):
            s, e = off[j], off[j + 1]
            self.key[s:e] = j * 1000.0 + self.acc[s:e]
            self.tot[j] = self.acc[e - 1]
        # 接線の法線
        tx = np.gradient(self.x.astype(np.float64)); ty = np.gradient(self.y.astype(np.float64))
        nrm = np.hypot(tx, ty) + 1e-9
        self.nx, self.ny = -ty / nrm, tx / nrm
        # 放出の枠（経路 × 向き）
        rs = np.random.RandomState(seed)
        raw = []
        for j in range(J):
            for dr, w in ((0, self.wo[j]), (1, self.wi[j])):
                raw.append((j, dr, w, self.tot[j]))
        raw = np.array(raw)
        alive_unit = np.sum(raw[:, 2] ** 1.0 * raw[:, 3] / DUR)
        A = total_alive / max(alive_unit, 1e-6)
        pj, pd, pk, pph, pw = [], [], [], [], []
        for j, dr, w, tot in raw:
            n = max(2, int(round(A * w)))
            ph = rs.rand() * DUR
            for k in range(n):
                pj.append(int(j)); pd.append(int(dr)); pk.append(k); pph.append((ph + k * DUR / n) % DUR); pw.append(w)
        self.pj = np.array(pj); self.pd = np.array(pd); self.pph = np.array(pph); self.pw = np.array(pw)
        N = len(pj)
        self.N = N
        self.lat = rs.randn(N)                     # 横の拡散の乱数
        self.h1 = rs.rand(N)                       # 昼夜で出し入れする乱数
        self.h2 = rs.rand(N)                       # 色（帰りの空車の割合）
        self.sphase = rs.rand(N) * 2 * math.pi
        self.sdir = np.where(rs.rand(N) < 0.5, 1.0, -1.0)
        self.srad = 0.35 + 0.5 * rs.rand(N)
        self.tot_p = self.tot[self.pj]
        # 2 周目（所要時間が 36 秒を超える経路）
        self.copies = (self.tot_p > DUR)
        self.hubs = np.array([(139.76, 35.68, 3.0), (135.50, 34.69, 2.6), (136.90, 35.17, 2.0), (130.40, 33.59, 1.8), (139.62, 35.45, 1.4), (140.88, 38.27, 1.2)])
        self.hubs_xy = None

    EDDIES = [(139.76, 35.68, 2.8, 9000), (135.50, 34.69, 2.4, 6500), (136.90, 35.17, 1.9, 3800), (130.40, 33.59, 1.7, 3800),
              (139.62, 35.45, 1.4, 2500), (140.88, 38.27, 1.1, 1500), (141.35, 43.06, 1.3, 1500)]

    def set_hubs(self, proj):
        self.hubs_xy = np.array([(*proj(lo, la), s) for lo, la, s in self.hubs])
        rs = np.random.RandomState(5)
        hx, hy, R, ph, sg, nk, hzv, w, dr, h2, tn = [], [], [], [], [], [], [], [], [], [], []
        for lo, la, rad, n in self.EDDIES:
            cx, cy = proj(lo, la)
            kk = int(np.argmin(np.hypot(self.x - cx, self.y - cy)))
            u = rs.rand(n)
            hx += [cx] * n; hy += [cy] * n; R += list(rad * u ** 0.75); ph += list(rs.rand(n) * 2 * math.pi)
            sg += [1.0 if lo > 138 else -1.0] * n
            nk += list(np.round(2 + 9 * (1 - u)))
            hzv += [float(self.z[kk])] * n
            w += list(0.45 + 0.4 * rs.rand(n))
            dr += list((rs.rand(n) < 0.55).astype(int))
            h2 += list(rs.rand(n))
            tn += [float(self.tn[kk])] * n
        self.eddy = dict(hx=np.array(hx), hy=np.array(hy), r=np.array(R), ph=np.array(ph), sg=np.array(sg), nk=np.array(nk), z=np.array(hzv),
                         w=np.array(w), dr=np.array(dr), h2=np.array(h2), tn=np.array(tn))

    def _positions(self, age, sel):
        """age（出発からの経過時間）の粒の座標・成長時刻・向き。sel は粒の添字"""
        j = self.pj[sel]
        dr = self.pd[sel]
        tot = self.tot[j]
        acc_t = np.where(dr == 0, age, tot - age)
        acc_t = np.clip(acc_t, 0, tot)
        k = j * 1000.0 + acc_t
        X = np.interp(k, self.key, self.x)
        Y = np.interp(k, self.key, self.y)
        Z = np.interp(k, self.key, self.z)
        TN = np.interp(k, self.key, self.tn)
        NX = np.interp(k, self.key, self.nx)
        NY = np.interp(k, self.key, self.ny)
        return X, Y, Z, TN, NX, NY, acc_t

    def sample(self, t, dens, ntrail=10, dtr=0.10):
        """全ての粒（と尾の点）の世界座標と明るさ・色を返す"""
        pts = []
        for m in (0, 1):
            sel = np.arange(self.N) if m == 0 else np.where(self.copies)[0]
            age0 = ((t - self.pph[sel]) % DUR) + DUR * m
            alive = age0 < self.tot[self.pj[sel]]
            sel2 = sel[alive]
            age = age0[alive]
            if len(sel2) == 0:
                continue
            gate = smoothstep(self.h1[sel2] - 0.12, self.h1[sel2], dens)
            for i in range(ntrail):
                a = age - i * dtr
                ok = a >= 0
                if not ok.any():
                    continue
                ss, aa, gg = sel2[ok], a[ok], gate[ok]
                X, Y, Z, TN, NX, NY, AC = self._positions(aa, ss)
                w = self.pw[ss]
                wvar = 0.60 + 0.65 * np.sin(0.33 * AC + self.pj[ss] * 1.7) ** 2
                sig = (0.045 + 0.12 * w) * (0.35 + 0.65 * np.minimum(aa / 12.0, 1.0)) * wvar
                X = X + NX * self.lat[ss] * sig
                Y = Y + NY * self.lat[ss] * sig
                # ハブの近くの渦
                if self.hubs_xy is not None:
                    c = np.zeros_like(X)
                    for hx, hy, hs in self.hubs_xy:
                        c = np.maximum(c, np.exp(-((X - hx) ** 2 + (Y - hy) ** 2) / (2 * hs ** 2)))
                    th = self.sphase[ss] + self.sdir[ss] * 2 * math.pi * PERIOD_SWIRL * (t - i * dtr) / DUR
                    X = X + c * self.srad[ss] * np.cos(th) * 1.5
                    Y = Y + c * self.srad[ss] * np.sin(th) * 1.5
                pts.append((X, Y, Z, TN, w, gg * math.exp(-0.30 * i), self.pd[ss], self.h2[ss]))
        ed = getattr(self, "eddy", None)
        if ed is not None:
            gate = smoothstep(ed["h2"] - 0.12, ed["h2"], dens)
            for i in range(ntrail):
                th = ed["ph"] + ed["sg"] * 2 * math.pi * ed["nk"] * (t - i * dtr) / DUR
                X = ed["hx"] + ed["r"] * np.cos(th)
                Y = ed["hy"] + ed["r"] * np.sin(th) * 0.9
                pts.append((X, Y, ed["z"], ed["tn"], ed["w"], gate * math.exp(-0.30 * i) * 0.06, ed["dr"], ed["h2"]))
        if not pts:
            return None
        return [np.concatenate([p[k] for p in pts]) for k in range(8)]


def cam_basis(c):
    az, el = math.radians(c["az"]), math.radians(c["pitch"])
    dist = c["lens"] * c["width"] / 36.0
    tgt = np.array([c["x"], c["y"], 0.8])
    d = np.array([math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])
    cam = tgt + d * dist
    f = (tgt - cam); f /= np.linalg.norm(f)
    r = np.cross(f, [0, 0, 1.0]); r /= np.linalg.norm(r)
    u = np.cross(r, f)
    return cam, f, r, u, dist


def render(pf, t, c, W, H, day, gain, growth=True, bscale=1.0):
    """粒の層（W×H×3、表示用の値で加算）。DOF は粒の深さから 3 つのぼかしに振り分ける"""
    dens = 0.55 + 0.45 * day
    S = pf.sample(t, dens)
    out = np.zeros((H, W, 3), np.float32)
    if S is None:
        return out
    X, Y, Z, TN, w, bright, dr, h2 = S
    cam, f, r, u, dist = cam_basis(c)
    P = np.stack([X, Y, Z], 1) - cam
    zc = P @ f
    ok = zc > 1e-3
    xi = (P @ r) / np.maximum(zc, 1e-3) * c["lens"] / 36.0
    yi = (P @ u) / np.maximum(zc, 1e-3) * c["lens"] / 36.0
    cr, sr = math.cos(math.radians(c.get("roll", 0.0))), math.sin(math.radians(c.get("roll", 0.0)))
    xi, yi = xi * cr - yi * sr, xi * sr + yi * cr
    aspect = W / H
    px = (c["nx"] + xi) * W
    py = (c["ny"] - yi * aspect) * H
    ok &= (px > -8) & (px < W + 8) & (py > -8) & (py < H + 8)
    if growth:
        ok &= (t > TN)
    if not ok.any():
        return out
    px, py, zc, w, bright, dr, h2, TN = px[ok], py[ok], zc[ok], w[ok], bright[ok], dr[ok], h2[ok], TN[ok]
    uu, vv = px / W, py / H
    inside = smoothstep(0.27, 0.33, uu) * (1 - smoothstep(0.67, 0.73, uu)) * smoothstep(0.35, 0.41, vv) * (1 - smoothstep(0.86, 0.93, vv))
    bright = bright * (1 - inside)
    fadein = smoothstep(0.0, 0.5, t - TN)
    b = bright * fadein * (0.30 + 0.70 * w) * gain * bscale
    # 色: 帰り（向き 0 = 東京から外へ）の一部は空車の橙。それ以外は青〜青白
    blue = np.array([0.26, 0.58, 1.0], np.float32)
    white = np.array([0.45, 0.74, 1.0], np.float32)
    orange = np.array([1.0, 0.58, 0.22], np.float32)
    is_or = (dr == 0) & (h2 < 0.55)
    cyan = np.array([0.36, 0.82, 0.96], np.float32)      # 一般の交通量は、主役の青・橙と混ざらない淡いシアンにそろえる
    col = np.tile(cyan[None], (len(dr), 1))
    # 被写界深度の錯乱円（画面の幅に対する割合 → px）
    fl = c["lens"] / 1000.0
    fs = max(c.get("fs", 5.0), 0.1)
    coc = (fl * fl / (fs * dist)) * np.abs(zc - dist) / zc / 0.036 * W
    band = np.where(coc < 1.2, 0, np.where(coc < 3.2, 1, 2))
    sig_core = (0.5, 1.0, 2.0)
    sc = W / 1920.0
    for bi in range(3):
        m = band == bi
        if not m.any():
            continue
        x0, y0 = px[m], py[m]
        ix, iy = np.floor(x0).astype(int), np.floor(y0).astype(int)
        fx, fy = x0 - ix, y0 - iy
        bb = b[m][:, None] * col[m]
        buf = np.zeros((H * W, 3), np.float32)
        for dx, dy, ww in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
            xx, yy = ix + dx, iy + dy
            v = (xx >= 0) & (xx < W) & (yy >= 0) & (yy < H)
            idx = yy[v] * W + xx[v]
            for ch in range(3):
                buf[:, ch] += np.bincount(idx, weights=(bb[v, ch] * ww[v]), minlength=H * W).astype(np.float32)
        buf = buf.reshape(H, W, 3)
        out += cv2.GaussianBlur(buf, (0, 0), sig_core[bi] * sc)
    return out
