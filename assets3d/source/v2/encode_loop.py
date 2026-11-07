"""PNG 連番を Blender の書き出し機能で mp4(H.264) / webm(VP9) にする。ffmpeg の単体は使わない。
使い方: blender -b -P encode_loop.py -- <frames dir> <出力ファイル(拡張子で mp4 / webm)> [fps] [crf: LOWEST|LOW|MEDIUM|HIGH]"""
import bpy
import os
import sys

a = sys.argv[sys.argv.index("--") + 1:]
fdir, outp = os.path.abspath(a[0]), os.path.abspath(a[1])
fps = int(a[2]) if len(a) > 2 else 24
crf = a[3] if len(a) > 3 else "MEDIUM"
files = sorted(f for f in os.listdir(fdir) if f.endswith(".png"))
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
w, h = 960, 540
try:
    import struct
    with open(os.path.join(fdir, files[0]), "rb") as fp:
        d = fp.read(32)
    w, h = struct.unpack(">II", d[16:24])
except Exception:
    pass
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
sc.render.fps = fps
sc.frame_start, sc.frame_end = 1, len(files)
se = sc.sequence_editor_create()
st = se.strips.new_image("f", os.path.join(fdir, files[0]), 1, 1)
for f in files[1:]:
    st.elements.append(f)
try:
    sc.render.image_settings.media_type = 'VIDEO'
except Exception:
    pass
sc.render.image_settings.file_format = 'FFMPEG'
ff = sc.render.ffmpeg
if outp.endswith(".webm"):
    ff.format, ff.codec = 'WEBM', 'WEBM'
else:
    ff.format, ff.codec = 'MPEG4', 'H264'
ff.constant_rate_factor = crf
ff.ffmpeg_preset = 'GOOD'
ff.gopsize = fps
sc.render.filepath = os.path.splitext(outp)[0]
bpy.ops.render.render(animation=True)
print("ENCODED", outp)
