"""v6 の合成: 背景（Blender、被写界深度つき）→ 軽い霞とブルーム → 路線層（EXR、トラックなど）→ 粒の流れ → フィルム調の仕上げ
（ゆるい S 字、暖かい明部と冷たい暗部、太陽方向の光の筋、粒状ノイズ、ビネット）。複数プロセスで回す。
使い方: python comp_v6.py <out dir> frames [step]
        python comp_v6.py <out dir> one <plate.png> <overlay.exr> <t> <out.png> [W H]   （縦長などは camjson を環境変数 CAMJSON で渡す）
        python comp_v6.py <out dir> encode [crf_vp9 crf_x264]"""
import json
import math
import os
import subprocess
import sys

os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import post_v3_center as pc  # noqa
import tour_v6 as TV  # noqa
import particles_v6 as PV  # noqa
import comp_tour_v5 as cn  # noqa  （EXR の読み込み）

DUR = 36.0


def smooth(a, b, x):
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def day_coef(root):
    d = json.load(open(os.path.join(root, "web", "data", "jartic.json"), encoding="utf-8"))
    acc = np.zeros(24); cnt = np.zeros(24)
    for p in d["points"]:
        for key in ("up", "down"):
            for i, v in enumerate(p[key]):
                if v is not None:
                    acc[i % 24] += v; cnt[i % 24] += 1
    prof = acc / np.maximum(cnt, 1)
    prof = prof / prof.max()
    h = np.arange(24)
    return [(np.mean(prof * np.cos(2 * np.pi * k * h / 24)), np.mean(prof * np.sin(2 * np.pi * k * h / 24))) for k in range(0, 4)]


def day_value(coef, t):
    hr = (t / DUR) * 24.0
    v = coef[0][0]
    for k in range(1, 4):
        v += 2 * (coef[k][0] * math.cos(2 * math.pi * k * hr / 24) + coef[k][1] * math.sin(2 * math.pi * k * hr / 24))
    return float(np.clip(v, 0.0, 1.0))


def flow_gain(t):
    base = 1.0 - 0.15 * smooth(6.0, 8.0, t) + 0.15 * smooth(31.5, 33.5, t)
    return base * (1.0 - smooth(34.2, 36.0, t))


def srgb(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


GRADE_BASE = dict(gain=(1.0, 1.0, 1.0), sat=1.0, con=0.0, haze=((0.86, 0.78, 0.70), 0.34), bloom=0.30, vig=0.0, lift=0.0)


def base_plate(path, focus=0.27):
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    rgb = np.ascontiguousarray(im[..., ::-1])
    pc.GRADE["cine"] = GRADE_BASE
    old = pc.tilt_shift
    pc.tilt_shift = lambda img, w: img          # ぼかしは Blender の被写界深度に任せる
    try:
        out = pc.process(rgb, "cine", dither_seed=3).astype(np.float32)
    finally:
        pc.tilt_shift = old
    return out


def sun_screen(c, W, H, az=148.0, el=11.0):
    """太陽の画面位置（0..1）。後ろにあるときは None"""
    cam, f, r, u, dist = PV.cam_basis(c)
    toward = np.array([math.cos(math.radians(el)) * math.cos(math.radians(az)), math.cos(math.radians(el)) * math.sin(math.radians(az)), math.sin(math.radians(el))])
    p = cam + toward * 6000.0
    v = p - cam
    zc = v @ f
    if zc <= 0:
        return None
    xi = (v @ r) / zc * c["lens"] / 36.0
    yi = (v @ u) / zc * c["lens"] / 36.0
    return c["nx"] + xi, c["ny"] - yi * (W / H)


def god_rays(img, c, strength=0.22):
    H, W = img.shape[:2]
    s = sun_screen(c, W, H)
    if s is None:
        return np.zeros_like(img)
    cx, cy = s[0] * W, s[1] * H
    lum = img @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    mask = np.clip(lum - 0.52, 0, None)[..., None] * img * 2.2
    small = cv2.resize(mask, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
    acc = np.zeros_like(small)
    n = 20
    for i in range(1, n + 1):
        sc = 1.0 - i * 0.012
        M = np.array([[sc, 0, (1 - sc) * cx / 2], [0, sc, (1 - sc) * cy / 2]], np.float32)
        acc += cv2.warpAffine(small, M, (W // 2, H // 2), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT) * (1 - i / (n + 1)) ** 1.5
    acc = cv2.resize(acc / n, (W, H), interpolation=cv2.INTER_LINEAR)
    return acc * strength * np.array([1.0, 0.82, 0.62], np.float32)


def film(img, seed):
    """ゆるい S 字、暖かい明部と冷たい暗部、粒状ノイズ、ビネット"""
    H, W = img.shape[:2]
    x = np.clip(img, 0, 1)
    s = x * x * (3 - 2 * x)
    x = x * 0.80 + s * 0.20
    lum = (x @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None]
    sh = np.clip(1 - lum * 2.2, 0, 1)
    hi = np.clip(lum * 1.6 - 0.35, 0, 1)
    x = x * (1 + sh * np.array([-0.05, 0.0, 0.06], np.float32) + hi * np.array([0.05, 0.015, -0.06], np.float32))
    l2 = (x @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None]
    x = l2 + (x - l2) * 0.96
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.hypot((xx / W - 0.5) * 1.1, (yy / H - 0.46) * 1.0)
    x = x * (1 - 0.30 * np.clip((r - 0.32) / 0.55, 0, 1) ** 1.6)[..., None]
    rng = np.random.RandomState(seed)
    g = rng.randn(H, W, 1).astype(np.float32) * 0.007 * (0.6 + 0.8 * (1 - np.abs(l2 - 0.45)))
    return np.clip(x + g, 0, 1)


class Ctx:
    pf = None
    coef = None
    root = None


def init(root, cache):
    Ctx.root = root
    Ctx.coef = day_coef(os.path.abspath(os.path.join(HERE, "..", "..", "..")))
    Ctx.pf = PV.ParticleField(cache, total_alive=110000)
    Ctx.pf.set_hubs(TV.proj)


def compose(plate, ov, t, c, W, H, frame_seed):
    o = np.clip(ov, 0, None)
    a = np.clip(o[..., 3:4], 0, 1)
    lin = o[..., :3]
    rgb = srgb(lin / (1.0 + 0.30 * lin))
    day = day_value(Ctx.coef, t)
    gain = flow_gain(t)
    L = PV.render(Ctx.pf, t, c, W, H, day, gain, bscale=0.55 * (0.75 + 0.25 * day))
    sc = W / 1920.0
    lay = rgb + L
    glow = cv2.GaussianBlur(lay, (0, 0), 5 * sc) * 0.35 + cv2.GaussianBlur(lay, (0, 0), 16 * sc) * 0.22 + cv2.GaussianBlur(lay, (0, 0), 44 * sc) * 0.10
    out = plate * (1 - a) + lay + glow
    m = out.max(axis=2, keepdims=True)
    hi = 0.88
    k = np.where(m > hi, (hi + (1 - hi) * np.tanh((m - hi) / (1 - hi))) / np.maximum(m, 1e-6), 1.0)
    out = out * k
    out = out + god_rays(plate, c)
    return film(out, frame_seed)


def work(args):
    out, f = args
    pp = os.path.join(out, "raw", "tour_plate", "p%04d.png" % f)
    op = os.path.join(out, "raw", "tour_ov", "o%04d.exr" % f)
    if not (os.path.exists(pp) and os.path.exists(op)):
        return f, False
    t = f / 24.0
    c = TV.cam_params(t)
    plate = base_plate(pp)
    img = compose(plate, cn.load_exr(op), t, c, 1920, 1080, 1000 + f)
    cv2.imwrite(os.path.join(out, "final", "tour", "t%04d.png" % f), (img[..., ::-1] * 255 + 0.5).astype(np.uint8))
    return f, True


def _init_worker(out):
    init(out, os.path.join(out, "cache"))


if __name__ == "__main__":
    OUT = os.path.abspath(sys.argv[1])
    MODE = sys.argv[2]
    if MODE == "frames":
        step = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        os.makedirs(os.path.join(OUT, "final", "tour"), exist_ok=True)
        import multiprocessing as mp
        with mp.Pool(6, initializer=_init_worker, initargs=(OUT,)) as pool:
            res = pool.map(work, [(OUT, f) for f in range(0, 864, step)], chunksize=3)
        print("done", sum(1 for _, ok in res if ok), "of", len(res))
    elif MODE == "one":
        init(OUT, os.path.join(OUT, "cache"))
        t = float(sys.argv[5])
        W = int(sys.argv[7]) if len(sys.argv) > 7 else 1920
        H = int(sys.argv[8]) if len(sys.argv) > 8 else 1080
        cj = os.environ.get("CAMJSON")
        if cj:
            vals = [float(v) for v in cj.split(",")]
            c = dict(zip(("x", "y", "az", "pitch", "width", "nx", "ny", "lens", "fs"), vals))
        else:
            c = TV.cam_params(t)
        plate = base_plate(sys.argv[3])
        img = compose(plate, cn.load_exr(sys.argv[4]), t, c, W, H, 7)
        cv2.imwrite(sys.argv[6], (img[..., ::-1] * 255 + 0.5).astype(np.uint8))
    elif MODE == "encode":
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        fd = os.path.join(OUT, "final", "tour")
        os.makedirs(os.path.join(OUT, "video"), exist_ok=True)
        src = ["-framerate", "24", "-i", os.path.join(fd, "t%04d.png")]
        vf = ["-vf", "format=yuv420p"]
        webm, mp4 = os.path.join(OUT, "video", "hero_v6.webm"), os.path.join(OUT, "video", "hero_v6.mp4")
        c9 = sys.argv[3] if len(sys.argv) > 3 else "36"
        c4 = sys.argv[4] if len(sys.argv) > 4 else "24"
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *src, *vf, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", c9,
                        "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", "-g", "72", "-an", webm], check=True)
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *src, *vf, "-c:v", "libx264", "-crf", c4, "-preset", "slow",
                        "-profile:v", "high", "-movflags", "+faststart", "-g", "72", "-an", mp4], check=True)
        for p in (webm, mp4):
            print(p, os.path.getsize(p))
