"""v3 の後処理（通常の Python、numpy + OpenCV + Pillow）: ティルトシフト風のぼかし、ブルーム、霞、色調整、ビネット。
Blender の描画結果（16bit PNG）を受け取り、8bit にして返す。コマごとに同じ処理をかけるので、動画でもちらつかない。
使い方: python post_v3.py <out dir> still <variant> | loop <variant> | file <in.png> <out.png> <variant>"""
import os
import sys
import cv2
import numpy as np

GRADE = {
    "real": dict(gain=(1.03, 1.0, 0.95), sat=0.94, con=0.07, haze=((0.84, 0.80, 0.76), 0.30), bloom=0.38, vig=0.14, lift=0.0),
    # gain: 色の倍率（暖かさ）、sat: 彩度、con: コントラスト、haze: 霞の色と量（上側ほど強い）、bloom: ブルームの強さ、focus: 焦点の縦位置（0..1）
    "golden": dict(gain=(1.05, 1.0, 0.92), sat=1.10, con=0.14, haze=((0.97, 0.84, 0.70), 0.10), bloom=0.55, vig=0.20, lift=0.0),
    "morning": dict(gain=(0.99, 1.0, 1.03), sat=1.04, con=0.10, haze=((0.86, 0.92, 0.98), 0.09), bloom=0.35, vig=0.12, lift=0.0),
    "dusk": dict(gain=(1.0, 0.92, 1.06), sat=1.10, con=0.16, haze=((0.72, 0.60, 0.80), 0.14), bloom=0.9, vig=0.26, lift=0.0),
}
FOCUS_Y = float(os.environ.get("FOCUS_Y", "0.27"))       # 焦点の縦位置
FOCUS_BAND = 0.13   # ぼかしが始まるまでの縦の幅（画面の高さに対する割合）
BLUR_MAX = 4.2      # 1920 幅での最大のぼかし（シグマ、px）


def smooth(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def tilt_shift(img, w):
    H = img.shape[0]
    sc = w / 1920.0
    sig = [0, 1.3 * sc, 2.6 * sc, 4.2 * sc, BLUR_MAX * sc]
    layers = [img] + [cv2.GaussianBlur(img, (0, 0), s) for s in sig[1:]]
    y = (np.arange(H) + 0.5) / H
    d = np.abs(y - FOCUS_Y)
    d = np.where(y < FOCUS_Y, d / 0.62, d / 0.42)     # 上側（遠景）は下側より緩やかに
    t = smooth(FOCUS_BAND, 1.0, d) ** 0.9 * (len(sig) - 1)
    i0 = np.clip(np.floor(t).astype(int), 0, len(sig) - 2)
    f = (t - i0)[:, None, None].astype(np.float32)
    out = np.empty_like(img)
    for k in range(len(sig) - 1):
        rows = np.where(i0 == k)[0]
        if len(rows) == 0:
            continue
        ff = f[rows]
        out[rows] = layers[k][rows] * (1 - ff) + layers[k + 1][rows] * ff
    return out


def process(rgb16, variant, dither_seed=7):
    g = GRADE[variant]
    img = rgb16.astype(np.float32) / 65535.0 if rgb16.dtype == np.uint16 else rgb16.astype(np.float32) / 255.0
    H, W = img.shape[:2]
    # ぼかしの前に明るい部分を抜き出す
    lum = img @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    bright = np.clip(img - 0.74, 0, None) * smooth(0.74, 0.95, lum)[..., None] * 2.2
    img = tilt_shift(img, W)
    b1 = cv2.GaussianBlur(bright, (0, 0), 0.004 * W)
    b2 = cv2.GaussianBlur(bright, (0, 0), 0.016 * W)
    img = 1 - (1 - img) * (1 - np.clip(g["bloom"] * (0.55 * b1 + 0.45 * b2), 0, 1))
    # 霞（遠いほど強い。画面の上ほど遠い）
    y = ((np.arange(H) + 0.5) / H)[:, None, None]
    hz = np.array(g["haze"][0], np.float32)[None, None, :]
    amt = g["haze"][1] * np.clip(1.0 - y / 0.30, 0, 1) ** 1.4 * 0.55 + 0.02
    img = img * (1 - amt) + hz * amt
    # 色調整
    img = img * np.array(g["gain"], np.float32)[None, None, :]
    l2 = img @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    img = l2[..., None] + (img - l2[..., None]) * g["sat"]
    img = np.clip(img, 0, 1)
    sc = img * img * (3 - 2 * img)
    img = img * (1 - g["con"]) + sc * g["con"]
    # ビネット
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.hypot((xx / W - 0.5) * 1.15, (yy / H - 0.45) * 1.05)
    img = img * (1 - g["vig"] * smooth(0.35, 0.85, r))[..., None]
    img = np.clip(img, 0, 1)
    rng = np.random.RandomState(dither_seed)
    noise = (rng.rand(H, W, 1).astype(np.float32) - 0.5) / 255.0 * 1.6
    return np.clip(img + noise, 0, 1)


def run_file(src, dst, variant):
    im = cv2.imread(src, cv2.IMREAD_UNCHANGED)
    rgb = im[..., ::-1]
    out = process(np.ascontiguousarray(rgb), variant)
    cv2.imwrite(dst, (out[..., ::-1] * 255 + 0.5).astype(np.uint8))


if __name__ == "__main__":
    out = os.path.abspath(sys.argv[1])
    mode = sys.argv[2]
    if mode == "still":
        v = sys.argv[3]
        os.makedirs(os.path.join(out, "final"), exist_ok=True)
        run_file(os.path.join(out, "raw", "still_%s.png" % v), os.path.join(out, "final", "still_%s_2560.png" % v), v)
    elif mode == "loop":
        v = sys.argv[3]
        src = os.path.join(out, "raw", "loop_%s" % v)
        dst = os.path.join(out, "final", "loop_%s" % v)
        os.makedirs(dst, exist_ok=True)
        for f in sorted(os.listdir(src)):
            if f.endswith(".png"):
                run_file(os.path.join(src, f), os.path.join(dst, f), v)
        print("loop post done")
    elif mode == "file":
        run_file(sys.argv[3], sys.argv[4], sys.argv[5])
