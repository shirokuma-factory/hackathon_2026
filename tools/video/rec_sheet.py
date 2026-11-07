"""録画 mp4 の 12 コマの一覧画像（4x3）を作る。使い方: python tools/video/rec_sheet.py a.mp4 [b.mp4 ...]  → a_sheet.jpg"""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import focus_clips as fc  # noqa: E402

FFMPEG = fc.FFMPEG


def main(paths):
    for p in map(Path, paths):
        dur = fc.video_duration(p)
        tw, th = 640, 360
        img = Image.new("RGB", (tw * 4, th * 3))
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 26)
        for k in range(12):
            t = (k + 0.5) * dur / 12
            r = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", str(p), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                               capture_output=True)
            if len(r.stdout) < 1920 * 1080 * 3:
                continue
            a = np.frombuffer(r.stdout[:1920 * 1080 * 3], np.uint8).reshape(1080, 1920, 3)
            im = Image.fromarray(a).resize((tw, th), Image.LANCZOS)
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, 110, 32], fill=(0, 0, 0))
            d.text((6, 3), f"{t:5.2f}s", fill=(255, 235, 60), font=font)
            img.paste(im, ((k % 4) * tw, (k // 4) * th))
        out = p.with_name(p.stem + "_sheet.jpg")
        img.save(out, quality=88)
        print(out, f"{dur:.2f}s")


if __name__ == "__main__":
    main(sys.argv[1:])
