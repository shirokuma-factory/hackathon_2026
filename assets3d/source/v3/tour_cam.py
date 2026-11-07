"""カメラを tour_v5 の姿勢に合わせる（Blender の中だけ）"""
import math
from mathutils import Vector


def apply(co, cam, c, W, H, dist=600.0):
    az, el = math.radians(c["az"]), math.radians(c["pitch"])
    tgt = Vector((c["x"], c["y"], 0.8))
    d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    co.location = tgt + d * dist
    co.rotation_euler = (tgt - co.location).to_track_quat('-Z', 'Y').to_euler()
    cam.lens = 36.0 * dist / c["width"]
    cam.shift_x = 0.5 - c["nx"]
    cam.shift_y = (c["ny"] - 0.5) * H / W
