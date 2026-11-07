"""v7 の主役の線を、画面上の太さが一定になるように合成側で描く（Blender の線は使わない）。
鹿屋 → 東京の実車は青 #5cc8ff の実線（北向き）、東京 → 鹿屋の空車は橙 #f49646 の点線（南向き）。関西 → 関東は 60% の太さ。
下に暗い縁（影）を敷き、色はフィルム調の処理のあとに載せる（色が変わらないように）。にじみはごく小さい。numpy と cv2 だけ。"""
import math
import os

import cv2
import numpy as np

import tour_v7 as TV

BLUE = np.array([0x5c, 0xc8, 0xff], np.float32) / 255.0
ORANGE = np.array([0xf4, 0x96, 0x46], np.float32) / 255.0
HERO = {"kagoshima": dict(win=(20.0, 28.6), w=5.0), "kansai": dict(win=(13.2, 21.0), w=3.0)}
GROW = 4.0
DASH, GAP, SPEED = 8.0, 10.0, 34.0     # 点線（画面の px、1920 幅のとき）と流れる速さ（px/秒）


def smooth(a, b, x):
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


class Lanes:
    def __init__(self, path):
        d = np.load(path)
        self.d = {k: d[k] for k in d.files}

    def get(self, key):
        return self.d.get(key + "_L"), self.d.get(key + "_E")


def _screen(c, P, W, H):
    nx, ny, zc = TV.project_pts(c, P, W / H)
    return np.stack([nx * W, ny * H], 1), zc


def _offset(xy, off):
    sm = np.stack([np.convolve(np.pad(xy[:, i], 6, mode="edge"), np.ones(13) / 13, "valid") for i in range(2)], 1)
    t = np.gradient(sm, axis=0)
    t /= np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-6)[:, None]
    return xy + np.stack([-t[:, 1], t[:, 0]], 1) * off


def _poly(mask, pts, th):
    if len(pts) < 2:
        return
    q = np.round(pts * 16).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(mask, [q], False, 255, int(th), cv2.LINE_AA, shift=4)


def draw(img, t, c, W, H, lanes, inside):
    """img: H x W x 3（0..1、RGB）。inside: H x W x 1（欄の中は 1）"""
    sc = W / 1920.0
    shadow = np.zeros((H, W), np.uint8)
    mb = np.zeros((H, W), np.uint8)
    mo = np.zeros((H, W), np.uint8)
    any_ = False
    for key, cfg in HERO.items():
        a, b = cfg["win"]
        gain = smooth(a, a + 0.9, t) * (1 - smooth(b - 0.9, b, t))
        if gain <= 0.001:
            continue
        Ls, Es = lanes.get(key)
        if Ls is None or Es is None:
            continue
        n = min(len(Ls), len(Es))
        s = Ls[:n, 0]
        ctr = 0.5 * (Ls[:n, 1:4] + Es[:n, 1:4])
        L = float(s[-1])
        g = smooth(0.0, 1.0, min(max((t - a) / GROW, 0.0), 1.0))
        xy, zc = _screen(c, ctr, W, H)
        ok = (zc > 1e-3) & (xy[:, 0] > -400) & (xy[:, 0] < W + 400) & (xy[:, 1] > -400) & (xy[:, 1] < H + 400)
        if ok.sum() < 3:
            continue
        w = cfg["w"] * sc
        off = w * 0.5 + 1.8 * sc
        pl = _offset(xy, +off)
        pe = _offset(xy, -off)
        # 青（実車）: 行き先側から東京へ伸びる
        sel = ok & (s >= L - g * L)
        for seg in _runs(sel):
            _poly(mb, pl[seg], round(w)); _poly(shadow, pl[seg], round(w) + 4)
        # 橙（空車）: 東京から行き先へ伸びる点線。画面上の長さで刻む
        sel = ok & (s <= g * L)
        for seg in _runs(sel):
            P = pe[seg]
            cum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(P, axis=0).T))])
            per = (DASH + GAP) * sc
            ph = (t * SPEED * sc) % per
            k0 = -1
            while True:
                a0 = k0 * per + ph
                a1 = a0 + DASH * sc
                k0 += 1
                if a0 > cum[-1]:
                    break
                if a1 < 0:
                    continue
                ia = np.searchsorted(cum, max(a0, 0.0)); ib = np.searchsorted(cum, min(a1, cum[-1]))
                pa = np.array([np.interp(max(a0, 0.0), cum, P[:, 0]), np.interp(max(a0, 0.0), cum, P[:, 1])])
                pb = np.array([np.interp(min(a1, cum[-1]), cum, P[:, 0]), np.interp(min(a1, cum[-1]), cum, P[:, 1])])
                pts = np.vstack([pa[None], P[ia:ib], pb[None]])
                _poly(mo, pts, round(w)); _poly(shadow, pts, round(w) + 4)
        any_ = True
        # この流れの強さ（フェード）は、描いた層全体に掛けるため、いったん平均を持つ
        HERO[key]["_g"] = gain
    if not any_:
        return img
    gmax = max(HERO[k].get("_g", 0.0) for k in HERO)
    free = (1 - inside[..., 0]) * gmax
    sh = cv2.GaussianBlur(shadow.astype(np.float32) / 255.0, (0, 0), 1.3 * sc) * 0.55 * free
    out = img * (1 - sh[..., None])
    As = []
    for m, col in ((mb, BLUE), (mo, ORANGE)):
        A = (m.astype(np.float32) / 255.0) * free
        As.append((A, col))
        out = out + cv2.GaussianBlur(A[..., None] * col[None, None], (0, 0), 3.0 * sc) * 0.08   # にじみはごく小さく、線の下だけ
    for A, col in As:                                                                               # 線の芯は色をそのまま載せる
        out = out * (1 - A[..., None]) + col[None, None] * A[..., None]
    for k in HERO:
        HERO[k]["_g"] = 0.0
    return np.clip(out, 0, 1)


def _runs(sel):
    idx = np.nonzero(sel)[0]
    if len(idx) == 0:
        return []
    cut = np.nonzero(np.diff(idx) > 1)[0] + 1
    return [r for r in np.split(idx, cut) if len(r) > 1]
