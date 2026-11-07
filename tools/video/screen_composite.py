"""グリーンバック素材（モニター／ノート PC）の緑の画面に、アプリの画面録画をはめ込む。

使い方（OpenCV が無ければ uv で足す）:
  uv run --with opencv-python-headless python tools/video/screen_composite.py --clip both
  uv run --with opencv-python-headless python tools/video/screen_composite.py --clip office \
      --screen outputs/video/rec6/R1g.mp4 --offset 4.0 --dive-cut-time 4.0

出力（outputs/video/stock/）: office_comp.mp4 laptop_comp.mp4 office_dive.mp4 laptop_dive.mp4
中間物と確認用の静止画は outputs/video/stock/_comp/ に出る。録画をやり直したら --screen を替えて再実行する。

処理の流れ:
 1. 各コマで緑を HSV で抜き、最大の緑領域の外周から 4 辺を直線にあてはめて画面の 4 隅を求める
    （外周だけを使うので、+ マーカーや手前の物は穴として無視される）。全コマ分を時間方向に平滑化する。
 2. アプリのコマを 4 隅へ射影変換し、画面らしい質感（黒の持ち上げ、緑の明暗を反映した陰影、反射、粒状感）を足す。
 3. 緑のマット（キー）で合成する。縁の緑かぶりは取り除く。手前の物は緑でない部分として残る。
 4. 1920x1080 / 30fps / H.264 crf14 で書き出す。dive 版は終盤で画面が全面に広がる。
"""
import argparse
import subprocess
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
FFMPEG = "C:/ai/bin/ffmpeg.exe"
STOCK = ROOT / "outputs/video/stock"
WORK = STOCK / "_comp"
OW, OH, OFPS = 1920, 1080, 30

CLIPS = {
    "office": dict(
        src=STOCK / "Logistics_Facility__Computer_Screen_Shows_Chroma_Key_Template_uhd_3112668.mp4",
        screen=ROOT / "outputs/video/rec6/R1g.mp4", screen_fallback=ROOT / "outputs/video/rec5/R3g.mp4",
        offset=4.0, cut=4.0,
        crop_x=0.5,  # 3840x2026 を高さ 1080 に合わせて拡大したあと、横を切る位置（0=左端, 1=右端）
    ),
    "laptop": dict(
        src=STOCK / "Woman_Using_Laptop_With_Chroma_Key_uhd_1220568.mp4",
        screen=ROOT / "outputs/video/rec6/R5ag.mp4", screen_fallback=ROOT / "outputs/video/rec5/R5ag.mp4",
        offset=1.0, cut=1.0, crop_x=0.5,
    ),
}


# ---------------------------------------------------------------- 入出力
def probe(path):
    cap = cv2.VideoCapture(str(path))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps, n = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return w, h, fps, n


def reader(path, w, h, start=0.0, fps=None):
    """ffmpeg で BGR の生コマを順に返す（色変換は ffmpeg に任せる）。"""
    cmd = [FFMPEG, "-v", "error", "-ss", f"{start:.4f}", "-i", str(path), "-an"]
    if fps:
        cmd += ["-vf", f"fps={fps}"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * 3 * 2)
    size = w * h * 3
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.kill()
        p.wait()


# ---------------------------------------------------------------- 画面の 4 隅を求める
def green_mask(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (35, 70, 50), (90, 255, 255))
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def order_quad(p):
    p = np.asarray(p, np.float32).reshape(4, 2)
    s, d = p.sum(1), p[:, 0] - p[:, 1]
    return np.array([p[np.argmin(s)], p[np.argmax(d)], p[np.argmax(s)], p[np.argmin(d)]], np.float32)  # TL TR BR BL


def line_intersect(l1, l2):
    (x1, y1, vx1, vy1), (x2, y2, vx2, vy2) = l1, l2
    a = np.array([[vx1, -vx2], [vy1, -vy2]], np.float64)
    t = np.linalg.solve(a, np.array([x2 - x1, y2 - y1], np.float64))
    return np.array([x1 + t[0] * vx1, y1 + t[0] * vy1], np.float32)


def initial_quad(contour):
    hull = cv2.convexHull(contour)
    peri = cv2.arcLength(hull, True)
    for eps in np.linspace(0.005, 0.1, 40) * peri:
        ap = cv2.approxPolyDP(hull, eps, True)
        if len(ap) == 4:
            return order_quad(ap)
    return order_quad(cv2.boxPoints(cv2.minAreaRect(hull)))


def fit_quad(mask, prev):
    """緑領域の外周に 4 本の直線をあてはめる。外れ値に強い Huber 損失。前コマの隅から辺の近くの点だけを使う。"""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None, 0.0
    c = max(cnts, key=cv2.contourArea)
    pts = c[:, 0, :].astype(np.float32)
    quad = initial_quad(c) if prev is None else prev.copy()
    support = 0.0
    for thr_k in (0.03, 0.012, 0.006):
        lines, support = [], 1e9
        for i in range(4):
            a, b = quad[i], quad[(i + 1) % 4]
            d = b - a
            ln = float(np.linalg.norm(d))
            u = d / ln
            n = np.array([-u[1], u[0]], np.float32)
            rel = pts - a
            t, dist = rel @ u / ln, rel @ n
            sel = (t > 0.12) & (t < 0.88) & (np.abs(dist) < max(5.0, thr_k * ln))
            support = min(support, sel.sum() / (0.76 * ln))  # 辺のどれだけが緑の縁で支えられているか
            if sel.sum() < 30:
                return None, 0.0
            vx, vy, x0, y0 = cv2.fitLine(pts[sel], cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
            lines.append((x0, y0, vx, vy))
        quad = np.array([line_intersect(lines[(i - 1) % 4], lines[i]) for i in range(4)], np.float32)
    return order_quad(quad), support


def _gauss_smooth(x, sigma):
    r = int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    xp = np.pad(x, ((r, r), (0, 0)), mode="edge")
    return np.stack([np.convolve(xp[:, j], k, mode="valid") for j in range(x.shape[1])], 1)


def _median5(x):
    xp = np.pad(x, ((2, 2), (0, 0)), mode="edge")
    return np.median(np.stack([xp[i:i + len(x)] for i in range(5)], 0), 0)


def smooth_track(q, valid, sigma):
    n = len(q)
    flat = q.reshape(n, 8).astype(np.float64)
    idx = np.arange(n)
    if valid.any():
        for k in range(8):  # 無効なコマは前後から補間
            flat[:, k] = np.interp(idx, idx[valid], flat[valid, k])
    flat = _median5(flat)  # 1 コマだけの外れ値を除く
    return _gauss_smooth(flat, sigma).reshape(n, 4, 2).astype(np.float32)


def track_clip(name, force=False):
    cfg = CLIPS[name]
    cache = WORK / f"track_{name}.npz"
    if cache.exists() and not force:
        d = np.load(cache)
        return d["quads"], d["raw"]
    w, h, fps, n = probe(cfg["src"])
    raw, valid, prev = [], [], None
    for fr in reader(cfg["src"], w, h):
        q, sup = fit_quad(green_mask(fr), prev)
        ok = q is not None and sup > 0.5
        if ok and prev is not None and np.abs(q - prev).max() > 40:
            ok = False
        if ok:
            prev = q
        raw.append(q if ok else (prev if prev is not None else np.zeros((4, 2), np.float32)))
        valid.append(ok)
    raw, valid = np.array(raw), np.array(valid)
    print(f"[{name}] 追跡 {len(raw)} コマ / 無効 {int((~valid).sum())} コマ")
    quads = smooth_track(raw, valid, sigma=2.0)
    WORK.mkdir(parents=True, exist_ok=True)
    np.savez(cache, quads=quads, raw=raw, valid=valid)
    return quads, raw


def aspect_of(quad, w, h):
    """画面が長方形だと仮定し、主点を中央として焦点距離を解いて縦横比を見積もる（確認用）。"""
    sq = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
    H = cv2.getPerspectiveTransform(sq, quad)
    cx, cy = w / 2, h / 2
    h1, h2 = H[:, 0], H[:, 1]
    a1 = np.array([h1[0] - cx * h1[2], h1[1] - cy * h1[2]])
    a2 = np.array([h2[0] - cx * h2[2], h2[1] - cy * h2[2]])
    den = h1[2] * h2[2]
    if abs(den) < 1e-12:
        return None
    f2 = -(a1 @ a2) / den
    if f2 <= 0:
        return None
    f = np.sqrt(f2)

    def k(v):
        return np.array([(v[0] - cx * v[2]) / f, (v[1] - cy * v[2]) / f, v[2]])
    return float(np.linalg.norm(k(h1)) / np.linalg.norm(k(h2)))


# ---------------------------------------------------------------- 合成
def smootherstep(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * x * (x * (x * 6 - 15) + 10)


def base_crop(w, h, crop_x):
    """元素材を高さ 1080 に合わせて拡大し、横を切る。ソース座標 → 出力座標。"""
    s = max(OW / w, OH / h)
    ox = (w * s - OW) * crop_x
    oy = (h * s - OH) * 0.5
    return np.array([[s, 0, -ox], [0, s, -oy], [0, 0, 1]], np.float64)


OUT_RECT = np.array([[0, 0], [OW, 0], [OW, OH], [0, OH]], np.float32)


def view_matrix(G0, quad, e):
    """e=0 で基本の切り出し、e=1 で画面の 4 隅が出力の 4 隅に一致する。ソース側の窓の 4 隅を直線補間する。"""
    r0 = cv2.perspectiveTransform(OUT_RECT.reshape(-1, 1, 2), np.linalg.inv(G0)).reshape(4, 2)
    r = r0 + (quad - r0) * e
    return cv2.getPerspectiveTransform(r.astype(np.float32), OUT_RECT).astype(np.float64)


def greenness(f):
    return f[..., 1] - np.maximum(f[..., 0], f[..., 2])


def make_matte(bg):
    """bg: 出力解像度の BGR(uint8)。柔らかいマットと、+ マーカーなどの穴を埋めた版を返す。"""
    f = bg.astype(np.float32)
    soft = np.clip((greenness(f) - 22.0) / 55.0, 0, 1)
    hsv = cv2.cvtColor(bg, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (35, 60, 40), (90, 255, 255))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return soft * 0, soft * 0
    c = max(cnts, key=cv2.contourArea)
    region = np.zeros(m.shape, np.uint8)
    cv2.drawContours(region, [c], -1, 255, -1)
    # 領域内の穴のうち小さいもの（+ マーカー）は画面の一部とみなす。大きい穴（手前の物）は残す。
    holes = (region > 0) & (m == 0)
    n, lab, st, _ = cv2.connectedComponentsWithStats(holes.astype(np.uint8), connectivity=8)
    area_lim = 0.004 * float(cv2.contourArea(c))
    small = np.zeros(m.shape, bool)
    for k in range(1, n):
        if st[k, 4] < area_lim:
            small |= lab == k
    inner = cv2.erode((((m > 0) | small) & (region > 0)).astype(np.uint8) * 255, np.ones((5, 5), np.uint8)) > 0
    inner_soft = cv2.GaussianBlur(inner.astype(np.float32), (0, 0), 1.0)
    near = cv2.dilate(region, np.ones((9, 9), np.uint8)) > 0  # 領域の外の緑（植物など）は拾わない
    alpha = np.maximum(soft * near, inner_soft)
    return np.clip(alpha, 0, 1), inner_soft


def despill(bg, alpha):
    """マットの縁とその周辺だけ、赤青より強い緑を削る（背景全体の色は変えない）。"""
    band = cv2.dilate((alpha > 0.002).astype(np.uint8), np.ones((9, 9), np.uint8)).astype(np.float32)
    band = cv2.GaussianBlur(band, (0, 0), 1.5)
    f = bg.astype(np.float32)
    g = f[..., 1]
    g2 = np.minimum(g, np.maximum(f[..., 0], f[..., 2]))
    f[..., 1] = g + (g2 - g) * band
    return f


class ScreenLook:
    """アプリの絵を実機の画面らしくする。"""

    def __init__(self, black_lift=0.035, glare=0.10, grain=1.6, sharpen=0.35, soften=0.5, glow=0.09):
        self.bl, self.glare, self.grain, self.sh, self.so, self.glow = black_lift, glare, grain, sharpen, soften, glow
        self.rng = np.random.default_rng(7)
        yy, xx = np.mgrid[0:OH, 0:OW].astype(np.float32)
        self.diag = np.clip(xx / OW * 0.6 + (1 - yy / OH) * 0.4, 0, 1)

    def shading(self, bg, alpha):
        """緑の画面の明暗（実際の照明と反射）を、マーカーを除いて低解像度で取り出す。"""
        f = bg.astype(np.float32)
        y = 0.114 * f[..., 0] + 0.587 * f[..., 1] + 0.299 * f[..., 2]
        full = alpha > 0.98
        if not full.any():
            return np.ones((OH, OW), np.float32)
        gn = greenness(f)
        core = full & (gn > 0.7 * np.percentile(gn[full], 60))
        w = core.astype(np.float32)
        s = 8
        ys = cv2.GaussianBlur(cv2.resize(y * w, (OW // s, OH // s), interpolation=cv2.INTER_AREA), (0, 0), 5)
        ws = cv2.GaussianBlur(cv2.resize(w, (OW // s, OH // s), interpolation=cv2.INTER_AREA), (0, 0), 5)
        sh = ys / np.maximum(ws, 1e-3)
        ok = ws > 0.05
        mean = sh[ok].mean() if ok.any() else 1.0
        sh = np.where(ws > 0.02, sh / max(mean, 1e-3), 1.0)
        return np.clip(cv2.resize(sh, (OW, OH), interpolation=cv2.INTER_CUBIC), 0.6, 1.6)

    def apply(self, app, bg, alpha):
        a = app.astype(np.float32) / 255.0
        blur = cv2.GaussianBlur(a, (0, 0), self.so)  # 少しなじませてから軽く鮮鋭化（モアレを出さない程度）
        a = blur + (a - blur) * (1.0 + self.sh * 2.0)
        sh = self.shading(bg, alpha)
        a = a * (np.clip(sh, 0.8, 1.25) ** 0.6)[..., None]
        a = self.bl + (1 - self.bl) * a
        refl = self.glare * np.clip(sh - 0.95, 0, 0.6)[..., None] + 0.018 * self.diag[..., None]
        a = 1 - (1 - a) * (1 - refl)  # スクリーン合成の反射
        a = a + self.rng.normal(0, self.grain / 255.0, a.shape[:2]).astype(np.float32)[..., None]
        return np.clip(a, 0, 1) * 255.0


def warp(img, M):
    return cv2.warpPerspective(img, M, (OW, OH), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def compose_frame(src, quad, app, look, G, quad_force=0.0):
    sw, sh_ = src.shape[1], src.shape[0]
    scale = float(np.sqrt(abs(np.linalg.det(G[:2, :2]))))
    if scale < 0.75:  # 縮小が強いときは先に面積平均で半分にしてから変換する（エイリアシング回避）
        srcm = cv2.resize(src, (sw // 2, sh_ // 2), interpolation=cv2.INTER_AREA)
        bg = warp(srcm, G @ np.diag([2.0, 2.0, 1.0]))
    else:
        bg = warp(src, G)
    alpha, _ = make_matte(bg)
    aw, ah = app.shape[1], app.shape[0]
    arect = np.array([[0, 0], [aw, 0], [aw, ah], [0, ah]], np.float32)
    Hs = cv2.getPerspectiveTransform(arect, quad.astype(np.float32)).astype(np.float64)
    M = G @ Hs
    if float(np.sqrt(abs(np.linalg.det(M[:2, :2])))) < 0.75:
        app = cv2.resize(app, (aw // 2, ah // 2), interpolation=cv2.INTER_AREA)
        M = M @ np.diag([2.0, 2.0, 1.0])
    app_o = warp(app, M)
    if quad_force > 0:  # dive の最後は画面の中を全部アプリにする（縁のにじみ・手前の物を出さない）
        qo = cv2.perspectiveTransform(quad.reshape(-1, 1, 2).astype(np.float32), G).reshape(4, 2)
        qm = np.zeros((OH, OW), np.float32)
        cv2.fillConvexPoly(qm, np.round(qo * 16).astype(np.int32), 1.0, lineType=cv2.LINE_AA, shift=4)
        alpha = np.maximum(alpha, qm * quad_force)
    scr = look.apply(app_o, bg, alpha)
    if quad_force > 0:  # 画面の質感も dive の最後で消し、続く録画と同じ絵にそろえる
        scr = scr * (1 - quad_force) + app_o.astype(np.float32) * quad_force
    bgd = despill(bg, alpha)
    a3 = alpha[..., None]
    out = bgd * (1 - a3) + scr * a3
    if look.glow > 0:  # 画面の光がほんの少し周りに回る
        out = out + cv2.GaussianBlur(scr * a3, (0, 0), 28) * look.glow * (1 - a3) * 0.6
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- 書き出し
def open_writer(path):
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OW}x{OH}", "-r", str(OFPS),
           "-i", "-", "-an", "-c:v", "libx264", "-crf", "14", "-preset", "slow",
           "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709,format=yuv420p",
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
           "-movflags", "+faststart", str(path)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def render(name, mode, screen, offset, cut, dive_len, push_start, dive_src_start, limit=None):
    cfg = CLIPS[name]
    w, h, fps, n = probe(cfg["src"])
    quads, _ = track_clip(name)
    G0 = base_crop(w, h, cfg["crop_x"])
    look = ScreenLook()
    aw, ah, afps, an = probe(screen)
    dur_src = n / fps
    if mode == "comp":
        out_n = int(round(min(dur_src, (an / afps) - offset) * OFPS))
        src_t0, app_t0, app_pre = 0.0, offset, 0.0
    else:
        out_n = int(round(dive_len * OFPS))
        src_t0 = dive_src_start if dive_src_start is not None else dur_src - dive_len - 0.05
        app_t0 = max(0.0, cut - dive_len)
        app_pre = max(0.0, dive_len - cut)  # cut より前に録画が足りない分は最初のコマを止める
    if limit:
        out_n = min(out_n, limit)
    src_i0 = int(round(src_t0 * fps))
    tmp = WORK / f"{name}_{mode}_tmp.mp4"
    wr = open_writer(tmp)
    srcs = reader(cfg["src"], w, h, start=src_i0 / fps)
    apps = reader(screen, aw, ah, start=app_t0, fps=OFPS)
    cur_idx, cur_src, first_app, last_app = src_i0 - 1, None, None, None
    for k in range(out_n):
        t = k / OFPS
        idx = min(int(round(src_i0 + t * fps)), n - 1)
        while cur_idx < idx:
            cur_src = next(srcs)
            cur_idx += 1
        if t < app_pre and first_app is not None:
            app = first_app
        else:
            app = next(apps, None)
            if app is None:
                app = last_app
            else:
                app = app.copy()
        if first_app is None:
            first_app = app
        last_app = app
        q = quads[idx]
        if mode == "comp":
            G, force = G0, 0.0
        else:
            e = smootherstep((t - push_start) / max(dive_len - 1.0 / OFPS - push_start, 1e-6))
            G = view_matrix(G0, q, e)
            force = smootherstep((e - 0.85) / 0.15)
        wr.stdin.write(compose_frame(cur_src, q, app, look, G, force).tobytes())
        if k % 60 == 0:
            print(f"  {name}/{mode} {k}/{out_n}", flush=True)
    wr.stdin.close()
    wr.wait()
    final = WORK / f"{name}_{mode}_partial.mp4" if limit else STOCK / f"{name}_{mode}.mp4"
    tmp.replace(final)
    print("書き出し:", final, f"{out_n / OFPS:.2f}s")
    return final


def sheet(video, out, cols=4, rows=3):
    cap = cv2.VideoCapture(str(video))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    tw, th = 640, 360
    img = np.zeros((th * rows, tw * cols, 3), np.uint8)
    for i in range(cols * rows):
        k = int((i + 0.5) * n / (cols * rows))
        cap.set(cv2.CAP_PROP_POS_FRAMES, k)
        ok, fr = cap.read()
        if ok:
            fr = cv2.resize(fr, (tw, th), interpolation=cv2.INTER_AREA)
            cv2.putText(fr, f"{k / OFPS:.2f}s", (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (60, 235, 255), 2)
            img[(i // cols) * th:(i // cols + 1) * th, (i % cols) * tw:(i % cols + 1) * tw] = fr
    cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 90])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", default="both", choices=["office", "laptop", "both"])
    ap.add_argument("--mode", default="all", choices=["all", "comp", "dive", "track"])
    ap.add_argument("--screen", help="アプリ録画（clip が 1 つのときだけ。省略時は既定、無ければ rec5）")
    ap.add_argument("--offset", type=float, help="comp 版でアプリを何秒目から流すか")
    ap.add_argument("--dive-cut-time", type=float, help="dive の最終コマに出すアプリ録画の時刻（この録画の続きへ切り替える位置）")
    ap.add_argument("--dive-len", type=float, default=4.0)
    ap.add_argument("--push-start", type=float, default=1.8, help="dive の中で押し込みを始める秒")
    ap.add_argument("--dive-src-start", type=float, help="dive に使う素材の開始秒（既定: 素材の終わりから逆算）")
    ap.add_argument("--retrack", action="store_true")
    ap.add_argument("--limit", type=int, help="デバッグ用: 先頭から N コマだけ")
    ap.add_argument("--sheets", action="store_true", help="一覧画像も作る")
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    names = ["office", "laptop"] if a.clip == "both" else [a.clip]
    for name in names:
        cfg = CLIPS[name]
        scr = Path(a.screen) if a.screen and len(names) == 1 else cfg["screen"]
        if not scr.exists():
            scr = cfg["screen_fallback"]
        off = a.offset if a.offset is not None and len(names) == 1 else cfg["offset"]
        cut = a.dive_cut_time if a.dive_cut_time is not None and len(names) == 1 else cfg["cut"]
        print(f"[{name}] アプリ録画: {scr}  offset={off}s  dive_cut={cut}s")
        quads, raw = track_clip(name, force=a.retrack)
        w, h, _, _ = probe(cfg["src"])
        asp = aspect_of(quads[len(quads) // 2], w, h)
        print(f"[{name}] 画面の縦横比の見積もり {asp if asp is None else round(asp, 3)}（16:9=1.778）"
              f" / 平滑化前後の隅のずれ 標準偏差 {np.std(raw - quads):.2f}px")
        if a.mode == "track":
            continue
        if a.mode in ("all", "comp"):
            v = render(name, "comp", scr, off, cut, a.dive_len, a.push_start, a.dive_src_start, a.limit)
            if a.sheets:
                sheet(v, WORK / f"{name}_comp_sheet.jpg")
        if a.mode in ("all", "dive"):
            v = render(name, "dive", scr, off, cut, a.dive_len, a.push_start, a.dive_src_start, a.limit)
            if a.sheets:
                sheet(v, WORK / f"{name}_dive_sheet.jpg")


if __name__ == "__main__":
    main()
