"""中央パネル版の仕上げ。使い方: python finalize_center.py <out dir> stills | loop | encode | sheet
stills : raw/still_*_real.png に後処理をかけ、final/ と webp/ を作る
loop   : raw/loop_center_real/*.png に後処理をかけ final/loop_center/ に出す
encode : final/loop_center から video/hero_center.webm（VP9）と .mp4（H.264）
sheet  : sheet_center.jpg（今の版、欄の位置の目安つき、等倍の切り出し、縦長、ループ 8 コマ）"""
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("FOCUS_Y", "0.27")
out = os.path.abspath(sys.argv[1])
mode = sys.argv[2]
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))


def post(src, dst, focus):
    os.environ["FOCUS_Y"] = str(focus)
    import importlib
    import post_v3_center as pc
    pc.FOCUS_Y = focus
    pc.run_file(src, dst, "real")


if mode == "stills":
    os.makedirs(os.path.join(out, "final"), exist_ok=True)
    os.makedirs(os.path.join(out, "webp"), exist_ok=True)
    post(os.path.join(out, "raw", "still_center_real.png"), os.path.join(out, "final", "still_center_2560.png"), 0.27)
    post(os.path.join(out, "raw", "still_portrait_real.png"), os.path.join(out, "final", "still_portrait_1080x1920.png"), 0.20)
    im = Image.open(os.path.join(out, "final", "still_center_2560.png")).convert("RGB")
    for w in (2560, 1920, 1600, 800):
        h = round(w * 9 / 16)
        p = os.path.join(out, "webp", "hero_center_%d.webp" % w)
        (im if w == 2560 else im.resize((w, h), Image.LANCZOS)).save(p, "WEBP", quality=90, method=6)
        print(p, os.path.getsize(p))
    pm = Image.open(os.path.join(out, "final", "still_portrait_1080x1920.png")).convert("RGB")
    for (w, h) in ((1080, 1920), (720, 1280)):
        p = os.path.join(out, "webp", "hero_portrait_%dx%d.webp" % (w, h))
        (pm if w == 1080 else pm.resize((w, h), Image.LANCZOS)).save(p, "WEBP", quality=90, method=6)
        print(p, os.path.getsize(p))
elif mode == "loop":
    src = os.path.join(out, "raw", "loop_center_real")
    dst = os.path.join(out, "final", "loop_center")
    os.makedirs(dst, exist_ok=True)
    for f in sorted(os.listdir(src)):
        if f.endswith(".png"):
            post(os.path.join(src, f), os.path.join(dst, f), 0.27)
    print("loop post done")
elif mode == "encode":
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    fd = os.path.join(out, "final", "loop_center")
    os.makedirs(os.path.join(out, "video"), exist_ok=True)
    src = ["-framerate", "24", "-i", os.path.join(fd, "f%04d.png")]
    vf = ["-vf", "scale=1920:1080:flags=lanczos,format=yuv420p"]
    webm = os.path.join(out, "video", "hero_center.webm")
    mp4 = os.path.join(out, "video", "hero_center.mp4")
    crf9 = sys.argv[3] if len(sys.argv) > 3 else "34"
    crf4 = sys.argv[4] if len(sys.argv) > 4 else "22"
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *src, *vf, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", crf9,
                    "-row-mt", "1", "-deadline", "good", "-cpu-used", "1", "-g", "48", "-an", webm], check=True)
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *src, *vf, "-c:v", "libx264", "-crf", crf4, "-preset", "slow",
                    "-profile:v", "high", "-movflags", "+faststart", "-g", "48", "-an", mp4], check=True)
    for p in (webm, mp4):
        print(p, os.path.getsize(p))
elif mode == "sheet":
    F = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 22)
    BG = (18, 22, 30)
    FG = (236, 240, 248)
    TW, TH = 960, 540
    old = Image.open(os.path.join(ROOT, "outputs", "hero_v3_2026-11-07", "final", "still_golden_2560.png")).convert("RGB")
    new = Image.open(os.path.join(out, "final", "still_center_2560.png")).convert("RGB")
    por = Image.open(os.path.join(out, "final", "still_portrait_1080x1920.png")).convert("RGB")
    PH = 960
    PW = round(PH * 9 / 16)
    rowH = TH + 40
    sheet = Image.new("RGB", (TW * 2, rowH * 2 + (PH + 40) + rowH * 4), BG)
    d = ImageDraw.Draw(sheet)
    # 行 1: 今の版 / 新しい版（欄の目安つき）
    d.text((12, 8), "今の版", font=F, fill=FG)
    sheet.paste(old.resize((TW, TH), Image.LANCZOS), (0, 40))
    d.text((TW + 12, 8), "新しい版 + 欄の位置（目安）", font=F, fill=FG)
    nv = new.resize((TW, TH), Image.LANCZOS).convert("RGBA")
    ov = Image.new("RGBA", nv.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    s = TW / 1920
    od.rounded_rectangle((600 * s, 430 * s, 1320 * s, 940 * s), radius=int(24 * s), fill=(12, 16, 24, 150), outline=(255, 255, 255, 120), width=2)
    nv = Image.alpha_composite(nv, ov).convert("RGB")
    sheet.paste(nv, (TW, 40))
    ImageDraw.Draw(sheet).text((TW + 12 + 400 * 0 + 0, 40 + 440 * s + 8), "", font=F)
    od2 = ImageDraw.Draw(sheet)
    od2.text((TW + 600 * s + 14, 40 + 430 * s + 10), "欄の位置（目安）", font=F, fill=(255, 255, 255))
    # 行 2: 等倍の切り出し / 縦長
    y2 = rowH
    d.text((12, y2 + 8), "経路の等倍の切り出し（2560 版、1:1）", font=F, fill=FG)
    cx, cy = 780, 300
    sheet.paste(new.crop((cx, cy, cx + TW, cy + TH)), (0, y2 + 40))
    d.text((TW + 12, y2 + 8), "縦長（1080x1920）+ 下 58% に欄の模型", font=F, fill=FG)
    pv = por.resize((PW, PH), Image.LANCZOS).convert("RGBA")
    ov = Image.new("RGBA", pv.size, (0, 0, 0, 0))
    ImageDraw.Draw(ov).rounded_rectangle((12, PH * 0.42, PW - 12, PH - 12), radius=18, fill=(12, 16, 24, 150), outline=(255, 255, 255, 120), width=2)
    pv = Image.alpha_composite(pv, ov).convert("RGB")
    sheet.paste(pv, (TW, y2 + 40))
    # 行 3 以降: ループ 8 コマ
    y3 = rowH * 2 + (PH + 40) - (PH + 40) + 0
    y3 = max(y2 + 40 + PH, y2 + rowH) + 10
    fd = os.path.join(out, "final", "loop_center")
    for k, f in enumerate([0, 24, 48, 72, 96, 120, 144, 168]):
        x, y = (k % 2) * TW, y3 + (k // 2) * rowH
        if y + rowH > sheet.size[1]:
            sheet = sheet.crop((0, 0, sheet.size[0], y + rowH))
        im = Image.open(os.path.join(fd, "f%04d.png" % f)).convert("RGB").resize((TW, TH), Image.LANCZOS)
        sheet.paste(im, (x, y + 40))
        ImageDraw.Draw(sheet).text((x + 12, y + 8), "ループ コマ %d / 192（%.2f 秒）" % (f, f / 24), font=F, fill=FG)
    sheet.crop((0, 0, sheet.size[0], y3 + 4 * rowH)).save(os.path.join(out, "sheet_center.jpg"), quality=86)
    print("sheet done", sheet.size)
