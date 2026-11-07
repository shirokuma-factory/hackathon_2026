"""スマートフォンの画面の録画（縦長）を、暗い背景の 3840x2160 の中央に置いた映像にする（飾りは丸い角の枠だけ。ブランドや名前は入れない）。
使い方: python tools/video/compose_phone.py outputs/video/rec7/DRIVER.mp4 outputs/video/rec7/DRIVER_4kbg.mp4 [--screen-h 1900]
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

FFMPEG = os.environ.get("FFMPEG", "C:/ai/bin/ffmpeg.exe")
W, H = 3840, 2160


def probe_duration(path):
    import re
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    h, m, sec = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr).groups()
    return int(h) * 3600 + int(m) * 60 + float(sec)


def probe_size(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    import re
    m = re.search(r"Video:.*?, (\d{3,5})x(\d{3,5})", r.stderr)
    return int(m.group(1)), int(m.group(2))


def make_layers(sw, sh, x, y, tmp):
    """背景（暗い放射状のグラデーション）と、端末の枠（画面の部分は透明）の 2 枚を作る。"""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H / 2) / (H * 0.80)) ** 2)
    k = np.clip(1 - d, 0, 1) ** 1.4
    top = np.array([27, 33, 46], np.float32)
    bot = np.array([5, 7, 11], np.float32)
    bg = (bot[None, None, :] + (top - bot)[None, None, :] * k[..., None]).astype(np.uint8)
    Image.fromarray(bg).save(tmp / "bg.png")
    bez = 26
    body_r, scr_r = 96, 62
    S = 2   # 角をなめらかにするため 2 倍で描いて縮める
    lay = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    dr = ImageDraw.Draw(lay)
    dr.rounded_rectangle([(x - bez) * S, (y - bez) * S, (x + sw + bez) * S - 1, (y + sh + bez) * S - 1], radius=body_r * S, fill=(34, 38, 48, 255))
    dr.rounded_rectangle([(x - bez) * S + 3, (y - bez) * S + 3, (x + sw + bez) * S - 4, (y + sh + bez) * S - 4], radius=body_r * S - 3, outline=(66, 72, 86, 255), width=5)
    hole = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(hole).rounded_rectangle([x * S, y * S, (x + sw) * S - 1, (y + sh) * S - 1], radius=scr_r * S, fill=255)
    a = np.array(lay.split()[3], np.uint8)
    a[np.array(hole) > 0] = 0
    lay.putalpha(Image.fromarray(a))
    lay = lay.resize((W, H), Image.LANCZOS)
    lay.save(tmp / "bezel.png")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--screen-h", type=int, default=1900)
    a = ap.parse_args()
    vw, vh = probe_size(a.src)
    sh = a.screen_h - a.screen_h % 2
    sw = int(round(sh * vw / vh)) // 2 * 2
    x, y = (W - sw) // 2, (H - sh) // 2
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        make_layers(sw, sh, x, y, tmp)
        fc = (f"[2:v]scale={sw}:{sh}:flags=lanczos[s];[0:v][s]overlay={x}:{y}[a];[a][1:v]overlay=0:0,format=yuv420p")
        cmd = [FFMPEG, "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(tmp / "bg.png"), "-loop", "1", "-framerate", "30", "-i", str(tmp / "bezel.png"),
               "-i", a.src, "-filter_complex", fc, "-t", f"{probe_duration(a.src):.3f}", "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-r", "30", "-movflags", "+faststart", a.dst]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            print(r.stderr[-1500:])
            return 1
    print("保存:", a.dst, f"画面 {sw}x{sh} 位置 ({x},{y})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
