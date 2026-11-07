"""アプリ画面の録画から「注目クリップ」を作る。

録画にはオレンジ色の強調枠が出る。その枠を検出して、仮想カメラ（常に16:9の切り出し窓）が
枠の位置へなめらかに寄り、止まり、次の枠へ移る映像を 1920x1080 / 30fps / H.264 で書き出す。

使い方:  python tools/video/focus_clips.py [P1 P2 ...]   （引数なしは全部）
出力:    outputs/video/rec4/focus/<piece_id>.mp4 と <piece_id>_sheet.jpg（12コマの一覧）
"""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi

FFMPEG = os.environ.get("FFMPEG", "C:/ai/bin/ffmpeg.exe")
REC = Path(__file__).resolve().parents[2] / "outputs" / "video" / "rec4"
OUT = REC / "focus"
W, H, FPS = 1920, 1080, 30

TRANS = 0.7          # 移動にかける秒
PUSH = 0.02          # 保持中のゆっくりした寄り（1秒あたり）
PUSH_CAP = 1.12      # 1つの保持で寄る上限
MIN_BLOB = 150       # 枠として認める最小の画素数

# manual: take 時間（秒）の t0,t1 と box=(x0,y0,x1,y1) または c=(cx,cy),z
# （強調枠が出ていない区間のカメラ指定）。drop_from: この take 時間以降の検出は使わない
PIECES = {
    "P1": dict(take="R2", t_in=2.4, t_out=14.4),
    "P2": dict(take="R1", t_in=4.0, t_out=11.8, drop_from=11.5),
    "P3": dict(take="R2", t_in=16.0, t_out=26.2, zmax=1.6),
    "P4": dict(take="R3", t_in=4.0, t_out=14.0,
               manual=[dict(t0=4.0, t1=9.0, c=(960, 258), z=1.8)]),
    "P5": dict(take="R4", t_in=3.0, t_out=8.0),
    "P6": dict(take="R4", t_in=14.0, t_out=32.0, manual=[
        dict(t0=14.0, t1=19.0, c=(420, 930), z=1.9),               # 貼り付け欄とボタン
        dict(t0=19.0, t1=21.1, box=(846, 366, 1889, 481)),         # 相手ごとの表
        dict(t0=28.0, t1=29.4, box=(846, 366, 1889, 481)),         # 書面へスクロールするまで表を保持
        dict(t0=29.4, t1=30.8, c=(1180, 230), z=1.9),              # 書面のタイトル（スクロール後）
        dict(t0=30.8, t1=32.1, box=(862, 778, 1589, 885)),         # 合計 33,550円 の枠
    ]),
    "P7": dict(take="R5a", t_in=1.0, t_out=7.0),
    "P8": dict(take="R5b", t_in=1.5, t_out=9.37),
    "P9": dict(take="R5c", t_in=0.0, t_out=2.63, drop_from=0.0,
               manual=[dict(t0=0.0, t1=2.63, c=(1250, 560), z=1.3)]),
    "P10": dict(take="R5c", t_in=2.0, t_out=6.5, drop_from=6.1),
    "P11": dict(take="R5c", t_in=10.0, t_out=16.5),
    "P12": dict(take="R5c", t_in=6.0, t_out=14.5),
    # 神戸（28110）・加古川（28210）の録り直し分。R5c は神戸だと 7 便とも載らないので、載る場面は加古川の録画（R5c_kako）から取る
    "P7k": dict(take="R5a_kobe", t_in=2.5, t_out=8.5),
    "P8k": dict(take="R5b_kobe", t_in=1.8, t_out=9.67),
    "P7ka": dict(take="R5a_kako", t_in=2.5, t_out=8.5),
    "P8ka": dict(take="R5b_kako", t_in=1.8, t_out=9.67),
    "P9k": dict(take="R5c_kako", t_in=0.0, t_out=2.63, drop_from=0.0,
                manual=[dict(t0=0.0, t1=2.63, c=(1250, 560), z=1.3)]),
    "P10k": dict(take="R5c_kako", t_in=2.0, t_out=6.5, drop_from=6.1),
    "P11k": dict(take="R5c_kako", t_in=10.0, t_out=16.5),
    "P12k": dict(take="R5c_kako", t_in=6.0, t_out=14.5),
    "P11kobe": dict(take="R5c_kobe", t_in=4.5, t_out=11.0),
    "P13": dict(take="R4_3x", t_in=0.0, t_out=18.0, speed=3, zmin=1.2, zmax=1.6, trans=0.5),
    # 鹿児島版（r5）。録画は outputs/video/rec5/、出力は rec5/focus/。区間は旧 P1〜P13 と同じ
    "P1g": dict(rec="rec5", take="R2g", t_in=2.4, t_out=14.4),
    "P2g": dict(rec="rec5", take="R1g", t_in=4.0, t_out=11.8, drop_from=11.5),
    "P3g": dict(rec="rec5", take="R2g", t_in=16.0, t_out=26.2, zmax=1.6),
    "P4g": dict(rec="rec5", take="R3g", t_in=4.0, t_out=14.0,
                manual=[dict(t0=4.0, t1=9.0, c=(960, 258), z=1.8)]),
    "P5g": dict(rec="rec5", take="R4g", t_in=3.0, t_out=8.0),
    "P6g": dict(rec="rec5", take="R4g", t_in=14.0, t_out=32.0, manual=[
        dict(t0=14.0, t1=19.0, c=(420, 930), z=1.9),
        dict(t0=19.0, t1=21.1, box=(846, 346, 1889, 459)),
        dict(t0=28.0, t1=29.4, box=(846, 346, 1889, 459)),
        dict(t0=29.4, t1=30.8, c=(1180, 230), z=1.9),
        dict(t0=30.8, t1=32.1, box=(862, 758, 1589, 865)),
    ]),
    "P7g": dict(rec="rec5", take="R5ag", t_in=1.0, t_out=7.0),
    "P8g": dict(rec="rec5", take="R5bg", t_in=1.5, t_out=9.37),
    "P9g": dict(rec="rec5", take="R5cg", t_in=0.0, t_out=2.63, drop_from=0.0,
                manual=[dict(t0=0.0, t1=2.63, c=(1250, 560), z=1.3)]),
    "P10g": dict(rec="rec5", take="R5cg", t_in=2.0, t_out=6.5, drop_from=6.1),
    "P11g": dict(rec="rec5", take="R5cg", t_in=10.0, t_out=16.5),
    "P12g": dict(rec="rec5", take="R5cg", t_in=6.0, t_out=14.5),
    "P13g": dict(rec="rec5", take="R4_3xg", t_in=0.0, t_out=18.0, speed=3, zmin=1.2, zmax=1.6, trans=0.5),
}


# 高解像度版（r6）。録画は outputs/video/rec6/（3840x2160）、出力は rec6/focus/（1920x1080）。区間は g と同じ。
# 元が 4K なので 2 倍までの切り出しは元の解像度のまま（S=2）
for _k in range(1, 14):
    PIECES[f"P{_k}h"] = dict(PIECES[f"P{_k}g"], rec="rec6", S=2)
# 使い始め方の流れ。FLOW0.mp4 は concat_flow0.py で FLOW0a〜d をつないだもの。やさしい構図（1.0〜1.6 倍）で、文字をくっきり見せる
PIECES["FLOW0h"] = dict(rec="rec6", S=2, take="FLOW0", t_in=0.0, t_out=47.4, zmin=1.0, zmax=1.6, wide_z=1.0, trans=0.9)
# 協議の書面。題 → 記録一覧 → 参考額の合計。スクロール中は書面の列（x≈1226）を中央に置く
PIECES["DOCh"] = dict(rec="rec6", S=2, take="DOC", t_in=0.4, t_out=13.9, zmin=1.6, zmax=2.2, trans=0.9, manual=[
    dict(t0=3.5, t1=10.3, c=(1226, 520), z=1.85)])


def read_frames(path, t0, dur, S=1, down=False):
    """S: 元動画の倍率（4K 録画なら 2）。down=True なら 1920x1080 に縮小して読む（枠の検出用）。"""
    vf = ["-vf", f"scale={W}:{H}:flags=area"] if (down and S != 1) else []
    cmd = [FFMPEG, "-v", "error", "-ss", f"{t0}", "-t", f"{dur}", "-i", str(path), *vf,
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    sw, sh = (W, H) if (down or S == 1) else (W * S, H * S)
    n = sw * sh * 3
    try:
        while True:
            b = pr.stdout.read(n)
            if len(b) < n:
                break
            yield np.frombuffer(b, np.uint8).reshape(sh, sw, 3)
    finally:
        pr.stdout.close()
        pr.wait()


def video_duration(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    for ln in r.stderr.splitlines():
        if "Duration:" in ln:
            h, m, s = ln.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 1e9


def detect(f):
    """オレンジ枠の bbox (x0,y0,x1,y1)。なければ None。
    枠の色は実測で (230,88,10) 付近。地図のオレンジ塗りは g が大きいので色で除外される。"""
    m = (f[..., 0] > 215) & (f[..., 1] > 70) & (f[..., 1] < 108) & (f[..., 2] < 35)
    if m.sum() < MIN_BLOB:
        return None
    lab, k = ndi.label(ndi.binary_dilation(m, iterations=4))
    if k == 0:
        return None
    cnt = ndi.sum(m, lab, range(1, k + 1))
    j = int(np.argmax(cnt)) + 1
    if cnt[j - 1] < MIN_BLOB:
        return None
    ys, xs = np.nonzero((lab == j) & m)
    box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    if box[2] - box[0] < 30 or box[3] - box[1] < 12:   # 画面外へはみ出した枠の縁だけ
        return None
    return box


def content_center(frames):
    """暗くない領域の bbox の中心。空の暗い領域を避けるために使う。"""
    nd = np.zeros((H, W), bool)
    for f in frames:
        nd |= f.max(2) > 45
    cols = np.nonzero(nd.mean(0) > 0.02)[0]
    rows = np.nonzero(nd.mean(1) > 0.02)[0]
    if len(cols) == 0 or len(rows) == 0:
        return W / 2, H / 2
    return (cols[0] + cols[-1]) / 2, (rows[0] + rows[-1]) / 2


def target_from_box(box, zmin, zmax):
    bw, bh = box[2] - box[0], box[3] - box[1]
    fill = 0.65 if bw <= 600 else (0.8 if bw <= 1000 else 0.88)   # 横長の枠は余白を詰める
    cw = max(bw / fill, (bh / 0.8) * 16 / 9)
    z = min(max(W / cw, zmin), zmax)
    return dict(cx=(box[0] + box[2]) / 2, cy=(box[1] + box[3]) / 2, z=z)


def similar(a, b):
    return abs(a["cx"] - b["cx"]) < 25 and abs(a["cy"] - b["cy"]) < 25 and abs(a["z"] - b["z"]) < 0.05


def build_events(p, dets, zmin, zmax):
    """dets: [(take時間, box|None)] → 強調イベント（take時間）。手動指定を重ね、似た目標は結合する。"""
    segs, cur, miss = [], None, 0
    for t, box in dets:
        if box is None:
            miss += 1
            if cur and miss > 3:
                segs.append(cur)
                cur = None
            continue
        miss = 0
        if cur and max(abs(a - b) for a, b in zip(box, cur["boxes"][0])) <= 15:
            cur["boxes"].append(box)
            cur["b"] = t + 0.1
        else:
            if cur:
                segs.append(cur)
            cur = dict(a=t, b=t + 0.1, boxes=[box])
    if cur:
        segs.append(cur)
    ev = []
    for s in segs:
        med = tuple(int(np.median([b[i] for b in s["boxes"]])) for i in range(4))
        ev.append(dict(a=s["a"], b=s["b"], **target_from_box(med, zmin, zmax)))
    df = p.get("drop_from")
    if df is not None:
        ev = [e for e in ev if e["a"] < df]
    man = []
    for m in p.get("manual", []):
        tg = target_from_box(m["box"], zmin, zmax) if "box" in m else dict(cx=m["c"][0], cy=m["c"][1], z=m["z"])
        man.append(dict(a=m["t0"], b=m["t1"], **tg))
    kept = []
    for e in ev:
        for m in man:
            if e["a"] < m["b"] and e["b"] > m["a"]:
                if e["a"] >= m["a"]:
                    e["a"] = m["b"]
                else:
                    e["b"] = m["a"]
        if e["b"] - e["a"] > 0.15:
            kept.append(e)
    merged = []
    for e in sorted(kept + man, key=lambda e: e["a"]):
        if merged and similar(merged[-1], e) and e["a"] - merged[-1]["b"] < 1.2:
            merged[-1]["b"] = max(merged[-1]["b"], e["b"])
        else:
            merged.append(e)
    return merged


def plan_camera(events, wide, dur):
    """出力時間での (移動開始秒, 目標) の列と、t=0 の状態。
    強調が1.5秒以内に来るなら先にそちらへ向かい、そうでなければ広い構図に戻る。"""
    lz = lambda t: dict(cx=t["cx"], cy=t["cy"], lz=math.log(t["z"]))
    keys = []
    if not events:
        return lz(wide), keys
    first = events[0]
    if first["a"] <= 1.5:
        init = lz(first)
    else:
        init = lz(wide)
        keys.append((max(first["a"] - 1.0, 0.2), lz(first)))
    prev_b = first["b"]
    last_s = keys[-1][0] if keys else 0.0
    for e in events[1:]:
        g = e["a"] - prev_b
        if g <= 0.5:
            s = e["a"] - 0.3
        elif g <= 1.5:
            s = prev_b + 0.15
        else:
            w = prev_b + 0.3
            if e["a"] - 1.0 - w >= 0.9:
                keys.append((w, lz(wide)))
                s = e["a"] - 1.0
            else:
                s = max(prev_b + 0.15, e["a"] - 1.0)
        s = max(s, last_s + 0.2)
        keys.append((s, lz(e)))
        last_s = s
        prev_b = e["b"]
    if dur - prev_b > 1.8:
        keys.append((max(prev_b + 0.3, last_s + 0.2), lz(wide)))
    return init, keys


def ease(u):
    """三次の ease-in-out（smoothstep。立ち上がりと止まりが傾き 0）"""
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def camera_factory(init, keys, trans, zmax):
    """区間ごとに、移動開始時の状態から目標へ補間する。保持中は +2%/秒で寄る。"""
    segs = [(0.0, init, init)]

    def raw(j, t):
        s, F, T = segs[j]
        e = ease((t - s) / trans)
        push = math.log(min(1 + PUSH * max(t - s, 0), PUSH_CAP))
        return dict(cx=F["cx"] + (T["cx"] - F["cx"]) * e, cy=F["cy"] + (T["cy"] - F["cy"]) * e,
                    lz=F["lz"] + (T["lz"] - F["lz"]) * e + push)

    for s, T in keys:
        segs.append((s, raw(len(segs) - 1, s), T))
    starts = [x[0] for x in segs]

    def at(t):
        j = max(i for i, s in enumerate(starts) if s <= t)
        st = raw(j, t)
        z = max(min(math.exp(st["lz"]), zmax), 1.0)
        cw = W / z
        ch = cw * 9 / 16
        x0 = min(max(st["cx"] - cw / 2, 0), W - cw)
        y0 = min(max(st["cy"] - ch / 2, 0), H - ch)
        return x0, y0, z
    return at


def render_frame(f, x0, y0, z):
    """f は (H*S, W*S) の元コマ。(x0, y0, z) は 1920x1080 の座標系。S>1 でも元の解像度のまま切り出す。"""
    S = f.shape[1] // W
    if S > 1 and S / z > 1.0:
        # 縮小になるときは、先に面積平均で (W*z, H*z) に縮めてから切り出す（間引きによるにじみを避ける）
        f = cv2.resize(f, (int(round(W * z)), int(round(H * z))), interpolation=cv2.INTER_AREA)
        sc = z
    else:
        sc = float(S)
    k = sc / z   # 出力 1 画素あたりの元画素数（<=1 なら拡大）
    M = np.array([[k, 0, sc * x0 + 0.5 * k - 0.5], [0, k, sc * y0 + 0.5 * k - 0.5]], np.float32)
    return cv2.warpAffine(f, M, (W, H), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)


def sheet(tiles, path):
    tw, th = 640, 360
    img = Image.new("RGB", (tw * 4, th * 3))
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    for i, (t, a) in enumerate(tiles):
        im = Image.fromarray(a).resize((tw, th), Image.LANCZOS)
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 110, 32], fill=(0, 0, 0))
        d.text((6, 3), f"{t:5.2f}s", fill=(255, 235, 60), font=font)
        img.paste(im, ((i % 4) * tw, (i // 4) * th))
    img.save(path, quality=88)


def make(pid):
    p = PIECES[pid]
    rec = REC.parent / p["rec"] if "rec" in p else REC
    OUT = rec / "focus"
    src = rec / f"{p['take']}.mp4"
    S = p.get("S", 1)   # 元動画の倍率（rec6 は 3840x2160 なので 2）
    speed = p.get("speed", 1)
    t_in, t_out = p["t_in"], p["t_out"]
    zmin, zmax = p.get("zmin", 1.15), p.get("zmax", 2.6)
    trans = p.get("trans", TRANS)
    dur = (t_out - t_in) / speed
    n = int(round(dur * FPS))
    # 1回目: 枠の検出（3フレームごと）。t_out の 1.5 秒先まで見て、寄りの先読みに使う
    scan_end = min(t_out + 1.5, video_duration(src))
    dets, samples = [], []
    mid = int(round((t_out - t_in) * FPS / 2))
    for i, f in enumerate(read_frames(src, t_in, scan_end - t_in, S, down=True)):
        if i % 3 == 0:
            dets.append((t_in + i / FPS, detect(f)))
        if i in (0, mid):
            samples.append(f.copy())
    ev = build_events(p, dets, zmin, zmax)
    for e in ev:   # 出力時間に直す
        e["a"] = (e["a"] - t_in) / speed
        e["b"] = (e["b"] - t_in) / speed
    cx, cy = content_center(samples)
    wide = dict(cx=cx, cy=cy, z=p.get("wide_z", max(1.15, zmin)))
    init, keys = plan_camera(ev, wide, dur)
    at = camera_factory(init, keys, trans, zmax)
    print(pid, p["take"], f"{t_in}-{t_out}s", "events(out s, zoom):",
          [(round(e["a"], 1), round(e["b"], 1), round(e["z"], 2)) for e in ev] or "none (wide only)")

    OUT.mkdir(parents=True, exist_ok=True)
    outp = OUT / f"{pid}.mp4"
    enc = subprocess.Popen([
        FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-", "-an", "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-r", str(FPS), "-movflags", "+faststart",
        str(outp)], stdin=subprocess.PIPE)
    pick = {int((k + 0.5) * n / 12) for k in range(12)}
    tiles, log, last, i = [], [], None, 0
    for k, f in enumerate(read_frames(src, t_in, dur * speed + 0.2, S)):
        if k % speed or i >= n:
            continue
        x0, y0, z = at(i / FPS)
        out = render_frame(f, x0, y0, z)
        last = f
        enc.stdin.write(out.tobytes())
        log.append((x0, y0, z))
        if i in pick:
            tiles.append((i / FPS, out))
        i += 1
    while i < n and last is not None:   # 元が短いときは最後のコマを保持
        x0, y0, z = at(i / FPS)
        out = render_frame(last, x0, y0, z)
        enc.stdin.write(out.tobytes())
        log.append((x0, y0, z))
        if i in pick:
            tiles.append((i / FPS, out))
        i += 1
    enc.stdin.close()
    enc.wait()
    sheet(tiles, OUT / f"{pid}_sheet.jpg")
    a = np.array(log)
    dz = np.abs(np.diff(np.log(a[:, 2]))).max()
    dc = np.abs(np.diff(a[:, :2], axis=0)).max()
    print(f"  -> {outp.name} frames={i}/{n} zoom {a[:, 2].min():.2f}-{a[:, 2].max():.2f} "
          f"max step: crop-origin {dc:.1f}px/frame, logzoom {dz:.4f}/frame")
    return dict(id=pid, frames=i, zoom=(float(a[:, 2].min()), float(a[:, 2].max())))


if __name__ == "__main__":
    ids = sys.argv[1:] or list(PIECES)
    res = [make(x) for x in ids]
    if len(ids) == len(PIECES):
        with open(OUT / "focus_report.json", "w", encoding="utf-8", newline="\n") as fh:
            json.dump(res, fh)
