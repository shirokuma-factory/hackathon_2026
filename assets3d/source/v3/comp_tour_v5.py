"""v5 ツアーの合成。背景（毎コマ、Blender 出力）に後処理をかけ、重ね合わせ層（EXR）を加算で重ねる。複数プロセスで回す。
使い方: python comp_tour_v5.py <out dir> frames [step]      final/tour/t0000.png ...
        python comp_tour_v5.py <out dir> one <plate.png> <overlay.exr> <out.png> <focus_y>
        python comp_tour_v5.py <out dir> encode [crf_vp9 crf_x264]"""
import os
import subprocess
import sys

os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import post_v3_center as pc  # noqa

GL = (0.22, 0.14, 0.06)


def srgb(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def load_exr(p):
    """Blender が書いた EXR（半精度、ZIPS 圧縮、1 本ずつの走査線）を読む最小の読み込み。戻り値は RGBA の float32"""
    import struct
    import zlib
    b = open(p, "rb").read()
    pos = 8
    attrs = {}
    while b[pos] != 0:
        e = b.index(b"\0", pos)
        name = b[pos:e].decode()
        e2 = b.index(b"\0", e + 1)
        sz = struct.unpack("<i", b[e2 + 1:e2 + 5])[0]
        attrs[name] = b[e2 + 5:e2 + 5 + sz]
        pos = e2 + 5 + sz
    pos += 1
    x0, y0, x1, y1 = struct.unpack("<4i", attrs["dataWindow"])
    w, h = x1 - x0 + 1, y1 - y0 + 1
    ch = attrs["channels"]
    names, q = [], 0
    while ch[q] != 0:
        e = ch.index(b"\0", q)
        names.append(ch[q:e].decode())
        ptype = struct.unpack("<i", ch[e + 1:e + 5])[0]
        assert ptype == 1, "half 以外は未対応"
        q = e + 1 + 16
    nc = len(names)
    pos += 8 * h
    out = np.zeros((h, nc, w), np.float16)
    for _ in range(h):
        y, size = struct.unpack("<ii", b[pos:pos + 8])
        raw = zlib.decompress(b[pos + 8:pos + 8 + size]) if size < w * nc * 2 else b[pos + 8:pos + 8 + size]
        pos += 8 + size
        r = np.frombuffer(raw, np.uint8)
        n = len(r)
        v = (np.cumsum(np.concatenate([r[:1].astype(np.int64), r[1:].astype(np.int64) - 128])) & 255).astype(np.uint8)
        t = np.empty(n, np.uint8)
        t[0::2] = v[:(n + 1) // 2]
        t[1::2] = v[(n + 1) // 2:]
        out[y - y0] = t.view(np.float16).reshape(nc, w)
    d = {n_: out[:, i, :].astype(np.float32) for i, n_ in enumerate(names)}
    im = np.dstack([d["R"], d["G"], d["B"], d["A"]])
    return np.nan_to_num(im, nan=0.0, posinf=0.0, neginf=0.0)



def load_plate(p, focus):
    im = cv2.imread(p, cv2.IMREAD_UNCHANGED)
    rgb = np.ascontiguousarray(im[..., ::-1])
    pc.FOCUS_Y = focus
    return pc.process(rgb, "real").astype(np.float32)


def composite(plate, ov):
    H, W = plate.shape[:2]
    o = np.clip(ov, 0, None)
    a = np.clip(o[..., 3:4], 0, 1)
    lin = o[..., :3]
    rgb = srgb(lin / (1.0 + 0.30 * lin))
    sc = W / 1920.0
    glow = cv2.GaussianBlur(rgb, (0, 0), 3.0 * sc) * GL[0] + cv2.GaussianBlur(rgb, (0, 0), 11.0 * sc) * GL[1] + cv2.GaussianBlur(rgb, (0, 0), 34.0 * sc) * GL[2]
    out = plate * (1 - a) + rgb + glow
    m = out.max(axis=2, keepdims=True)
    hi = 0.88
    scale = np.where(m > hi, (hi + (1 - hi) * np.tanh((m - hi) / (1 - hi))) / np.maximum(m, 1e-6), 1.0)
    return np.clip(out * scale, 0, 1)        # 色相を保ったまま明るさだけ圧縮する


def save(img, p):
    cv2.imwrite(p, (np.clip(img, 0, 1)[..., ::-1] * 255 + 0.5).astype(np.uint8))


def work(args):
    out, f = args
    pp = os.path.join(out, "raw", "tour_plate", "p%04d.png" % f)
    op = os.path.join(out, "raw", "tour_ov", "o%04d.exr" % f)
    if not (os.path.exists(pp) and os.path.exists(op)):
        return f, False
    plate = load_plate(pp, 0.27)
    save(composite(plate, load_exr(op)), os.path.join(out, "final", "tour", "t%04d.png" % f))
    return f, True


if __name__ == "__main__":
    OUT = os.path.abspath(sys.argv[1])
    MODE = sys.argv[2]
    if MODE == "frames":
        step = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        os.makedirs(os.path.join(OUT, "final", "tour"), exist_ok=True)
        import multiprocessing as mp
        with mp.Pool(6) as pool:
            res = pool.map(work, [(OUT, f) for f in range(0, 864, step)], chunksize=4)
        print("done", sum(1 for _, ok in res if ok), "of", len(res))
    elif MODE == "one":
        plate = load_plate(sys.argv[3], float(sys.argv[6]))
        save(composite(plate, load_exr(sys.argv[4])), sys.argv[5])
    elif MODE == "encode":
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        fd = os.path.join(OUT, "final", "tour")
        os.makedirs(os.path.join(OUT, "video"), exist_ok=True)
        src = ["-framerate", "24", "-i", os.path.join(fd, "t%04d.png")]
        vf = ["-vf", "format=yuv420p"]
        webm, mp4 = os.path.join(OUT, "video", "hero_tour.webm"), os.path.join(OUT, "video", "hero_tour.mp4")
        c9 = sys.argv[3] if len(sys.argv) > 3 else "36"
        c4 = sys.argv[4] if len(sys.argv) > 4 else "26"
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *src, *vf, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", c9,
                        "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", "-g", "72", "-an", webm], check=True)
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *src, *vf, "-c:v", "libx264", "-crf", c4, "-preset", "slow",
                        "-profile:v", "high", "-movflags", "+faststart", "-g", "72", "-an", mp4], check=True)
        for p in (webm, mp4):
            print(p, os.path.getsize(p))
