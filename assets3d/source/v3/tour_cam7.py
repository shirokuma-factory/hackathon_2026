"""カメラを tour_v7 の姿勢に合わせる（Blender の中だけ）。ロール（傾き）と被写界深度つき"""
import math
from mathutils import Vector, Quaternion


def apply(co, cam, c, W, H):
    dist = c["lens"] * c["width"] / 36.0
    az, el = math.radians(c["az"]), math.radians(c["pitch"])
    tgt = Vector((c["x"], c["y"], 0.8))
    d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    co.location = tgt + d * dist
    q = (tgt - co.location).to_track_quat('-Z', 'Y')
    q = q @ Quaternion(Vector((0, 0, 1)), math.radians(c["roll"]))
    co.rotation_mode = 'QUATERNION'
    co.rotation_quaternion = q
    cam.lens = c["lens"]
    cam.shift_x = 0.5 - c["nx"]
    cam.shift_y = (c["ny"] - 0.5) * H / W
    cam.clip_start = max(0.5, dist * 0.02)
    cam.clip_end = 8000
    cam.dof.use_dof = bool(c.get("fs", 0) > 0.05)
    cam.dof.focus_distance = float(dist)
    cam.dof.aperture_fstop = float(max(0.1, c.get("fs", 5.0)))
