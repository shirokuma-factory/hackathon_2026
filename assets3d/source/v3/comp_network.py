"""背景（1 枚）と重ね合わせ層（EXR 連番）を合成する。ゆっくりした寄りとわずかな横の揺れも、ここで両方に同じ変換をかける。
使い方: python comp_network.py <out dir> frames [first last]        final/net_center/n0000.png ...（1920x1080）
        python comp_network.py <out dir> single <layout> <t> <W> <H>  final/net_<layout>_t<t>.png（寄りなし。EXR は事前に描く）
        python comp_network.py <out dir> encode                      video/hero_network.webm と .mp4"""
import os
import subprocess
import sys

os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import post_v3_center as pc  # noqa

OUT = os.path.abspath(sys.argv[1])
MODE = sys.argv[2]
FPS, NFR = 24, 288
GL = [float(x) for x in os.environ.get('GLOW', '0.22,0.14,0.06').split(',')]
DUR = NFR / FPS


def srgb(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def load_plate(layout):
    p = os.path.join(OUT, "raw", "still_%s_real_plate.png" % layout)
    im = cv2.imread(p, cv2.IMREAD_UNCHANGED)
    rgb = np.ascontiguousarray(im[..., ::-1])
    pc.FOCUS_Y = 0.27 if layout == "center" else 0.20
    return pc.process(rgb, "real").astype(np.float32)


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


def affine(src_w, dst_w, cx, cy, z, dx, dy):
    s = dst_w / src_w * z
    return np.array([[s, 0, cx - z * cx + dx], [0, s, cy - z * cy + dy]], np.float32)


def composite(plate, ov, size, z, dx, dy, ts=(0.5, 0.30)):
    W, H = size
    cx, cy = ts[0] * W, ts[1] * H
    Mp = affine(plate.shape[1], W, cx, cy, z, dx, dy)
    Mo = affine(ov.shape[1], W, cx, cy, z, dx, dy)
    pl = cv2.warpAffine(plate, Mp, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT) if (z != 1.0 or plate.shape[1] != W) else plate
    o = cv2.warpAffine(ov, Mo, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT) if (z != 1.0 or ov.shape[1] != W) else ov
    o = np.clip(o, 0, None)
    a = np.clip(o[..., 3:4], 0, 1)
    lin = o[..., :3]
    rgb = srgb(lin / (1.0 + 0.35 * lin))                       # 加算の色（表示用の値へ）
    sc = W / 1920.0
    glow = (cv2.GaussianBlur(rgb, (0, 0), 3.0 * sc) * GL[0] + cv2.GaussianBlur(rgb, (0, 0), 11.0 * sc) * GL[1] + cv2.GaussianBlur(rgb, (0, 0), 34.0 * sc) * GL[2])
    out = pl * (1 - a) + rgb + glow
    hi = 0.85
    out = np.where(out < hi, out, hi + (1 - hi) * np.tanh((out - hi) / (1 - hi)))
    return np.clip(out, 0, 1)


def save(img, p):
    cv2.imwrite(p, (np.clip(img, 0, 1)[..., ::-1] * 255 + 0.5).astype(np.uint8))


def frame_params(i):
    t = i / FPS
    z = 1.0 + 0.045 * np.sin(np.pi * t / DUR) ** 2
    dx = 7.0 * np.sin(2 * np.pi * t / DUR)
    dy = 3.0 * np.sin(2 * np.pi * t / DUR + 1.0) - 3.0 * np.sin(1.0)
    return z, dx, dy


if MODE == "frames":
    first = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    last = int(sys.argv[4]) if len(sys.argv) > 4 else NFR - 1
    plate = load_plate("center")
    d = os.path.join(OUT, "final", "net_center")
    os.makedirs(d, exist_ok=True)
    for i in range(first, last + 1):
        ov = load_exr(os.path.join(OUT, "raw", "net_center", "n%04d.exr" % i))
        z, dx, dy = frame_params(i)
        save(composite(plate, ov, (1920, 1080), z, dx, dy), os.path.join(d, "n%04d.png" % i))
    print("frames done", first, last)
elif MODE == "single":
    layout, t, W, H = sys.argv[3], float(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
    plate = load_plate(layout)
    ov = load_exr(os.path.join(OUT, "raw", "net_" + layout, "single_t%05.2f.exr" % t))
    ts = (0.5, 0.30) if layout == "center" else (0.5, 0.24)
    img = composite(plate, ov, (W, H), 1.0, 0.0, 0.0, ts)
    p = os.path.join(OUT, "final", "net_%s_t%05.2f.png" % (layout, t))
    save(img, p)
    print(p)
elif MODE == "encode":
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    fd = os.path.join(OUT, "final", "net_center")
    os.makedirs(os.path.join(OUT, "video"), exist_ok=True)
    src = ["-framerate", "24", "-i", os.path.join(fd, "n%04d.png")]
    vf = ["-vf", "format=yuv420p"]
    webm = os.path.join(OUT, "video", "hero_network.webm")
    mp4 = os.path.join(OUT, "video", "hero_network.mp4")
    crf9 = sys.argv[3] if len(sys.argv) > 3 else "36"
    crf4 = sys.argv[4] if len(sys.argv) > 4 else "24"
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *src, *vf, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", crf9,
                    "-row-mt", "1", "-deadline", "good", "-cpu-used", "2", "-g", "48", "-an", webm], check=True)
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *src, *vf, "-c:v", "libx264", "-crf", crf4, "-preset", "slow",
                    "-profile:v", "high", "-movflags", "+faststart", "-g", "48", "-an", mp4], check=True)
    for p in (webm, mp4):
        print(p, os.path.getsize(p))
