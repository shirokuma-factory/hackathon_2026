"""連番 PNG から webm(VP9) と mp4(H.264) を作る。ffmpeg は imageio-ffmpeg 同梱の実行ファイルを使う。
使い方: python encode_v3.py <frames dir> <out dir> [fps] [vp9 crf] [x264 crf]"""
import os
import subprocess
import sys
import imageio_ffmpeg

fdir, out = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
fps = sys.argv[3] if len(sys.argv) > 3 else "24"
crf9 = sys.argv[4] if len(sys.argv) > 4 else "34"
crf4 = sys.argv[5] if len(sys.argv) > 5 else "20"
ff = imageio_ffmpeg.get_ffmpeg_exe()
os.makedirs(out, exist_ok=True)
src = ["-framerate", fps, "-i", os.path.join(fdir, "f%04d.png")]
vf = ["-vf", "scale=1920:1080:flags=lanczos,format=yuv420p"]
webm = os.path.join(out, "hero_loop_v3.webm")
mp4 = os.path.join(out, "hero_loop_v3.mp4")
subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *src, *vf, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", crf9,
                "-row-mt", "1", "-deadline", "good", "-cpu-used", "1", "-g", "48", "-an", webm], check=True)
subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", *src, *vf, "-c:v", "libx264", "-crf", crf4, "-preset", "slow",
                "-profile:v", "high", "-movflags", "+faststart", "-g", "48", "-an", mp4], check=True)
for p in (webm, mp4):
    print(p, os.path.getsize(p))
