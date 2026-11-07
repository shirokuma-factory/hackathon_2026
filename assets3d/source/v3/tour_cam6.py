"""カメラを tour_v6 の姿勢に合わせる（Blender の中だけ）。焦点距離（35〜85mm 相当）から距離を決め、被写界深度は目標点に合わせる"""
import math
from mathutils import Vector


def apply(co, cam, c, W, H):
    dist = c["lens"] * c["width"] / 36.0
    az, el = math.radians(c["az"]), math.radians(c["pitch"])
    tgt = Vector((c["x"], c["y"], 0.8))
    d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    co.location = tgt + d * dist
    co.rotation_euler = (tgt - co.location).to_track_quat('-Z', 'Y').to_euler()
    cam.lens = c["lens"]
    cam.shift_x = 0.5 - c["nx"]
    cam.shift_y = (c["ny"] - 0.5) * H / W
    cam.clip_start = max(0.5, dist * 0.02)
    cam.clip_end = 6000
    cam.dof.use_dof = c.get("fs", 0) > 0
    cam.dof.focus_distance = dist
    cam.dof.aperture_fstop = max(0.1, c.get("fs", 5.0))
