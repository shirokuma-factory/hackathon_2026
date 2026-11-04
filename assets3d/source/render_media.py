"""既存の glb（web/models, web/models/props）から、UI 用サムネイル・ターンテーブル動画・ラインナップ静止画・確認用一覧を作る。

Blender（headless、Cycles + GPU）で撮る段と、Pillow で整える段に分かれる。
  blender --background --python assets3d/source/render_media.py -- <mode> [--only a,b] [--preview]
  python assets3d/source/render_media.py post            （Pillow だけ。bpy は使わない）

mode（Blender 側）
  info      各モデルの寸法とアニメーション長を表示する
  thumbs    outputs/thumbs_png/<name>.png を 640x400 の透過で撮る（35 種）
  turn      outputs/turntables/<name>.mp4（1920x1080 24fps）と、確認用の静止画 4 枚（0/90/180/270 度）を作る（主要 18 種）
  lineup    outputs/turntables/lineup_4k.png（3840x2160）
mode（Pillow 側）
  post      thumbs の PNG を WebP 320x200 にして web/img/models に置き、index.json と確認用一覧を作る

前方 = glTF の +Z = Blender の -Y。原点は地面の中心、単位 m。
"""
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
MODELS_DIR = os.path.join(ROOT, "web", "models")
PROPS_DIR = os.path.join(MODELS_DIR, "props")
OUT = os.path.join(ROOT, "outputs")
THUMB_PNG = os.path.join(OUT, "thumbs_png")
TURN_DIR = os.path.join(OUT, "turntables")
STILL_DIR = os.path.join(TURN_DIR, "_stills")
IMG_DIR = os.path.join(ROOT, "web", "img", "models")
ASSET_DIR = os.path.join(ROOT, "docs", "assets")
DATE = "2026-11-04"

# 主要 18 種（contact_sheet.py の ORDER + ORDER2 と同じ並び）
MAIN = ["truck", "freight_train", "ferry", "cargo_plane", "warehouse", "terminal", "port_crane",
        "kei_van", "box_2t", "truck_4t", "trailer", "container_12ft", "container_ship", "roro_ship",
        "passenger_jet", "cold_storage", "air_cargo_terminal", "container_yard"]
ANIMATED = ["truck", "truck_4t", "port_crane", "container_yard", "air_cargo_terminal"]
BG_HEX = "#0b1220"
FPS = 24


def props_list():
    with open(os.path.join(PROPS_DIR, "index.json"), encoding="utf-8") as f:
        return [p["name"] for p in json.load(f)["props"]]


def glb_path(name):
    p = os.path.join(MODELS_DIR, name + ".glb")
    return p if os.path.exists(p) else os.path.join(PROPS_DIR, name + ".glb")


# ----------------------------------------------------------------------------------------------
# Blender 側
# ----------------------------------------------------------------------------------------------
try:
    import bpy
    from mathutils import Vector, Matrix
except ImportError:  # Pillow 側（post）で使うときは bpy が無い
    bpy = None


def hexcol(h, a=1.0):
    """#rrggbb（sRGB）をリニアの RGBA にする。"""
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return (c[0], c[1], c[2], a)


def setup_gpu():
    """OptiX があれば OptiX、なければ CUDA。使った装置名を返す。"""
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for kind in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = kind
            prefs.refresh_devices()
        except Exception:
            continue
        devs = [d for d in prefs.devices if d.type == kind]
        if devs:
            for d in prefs.devices:
                d.use = (d.type == kind and "NVIDIA" in d.name)
            return f"{kind}: " + ", ".join(d.name for d in devs if d.use)
    return "CPU"


def new_scene(res=(1920, 1080), samples=64, transparent=False):
    """空のシーンを作り、Cycles/GPU とノイズ除去を設定する。"""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    dev = setup_gpu()
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "GPU"
    sc.cycles.samples = samples
    sc.cycles.use_adaptive_sampling = True
    sc.cycles.adaptive_threshold = 0.02
    sc.cycles.use_denoising = True
    sc.cycles.denoiser = "OPTIX" if dev.startswith("OPTIX") else "OPENIMAGEDENOISE"
    sc.cycles.max_bounces = 6
    sc.cycles.glossy_bounces = 3
    sc.cycles.transparent_max_bounces = 6
    sc.render.use_persistent_data = True
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.fps = FPS
    sc.render.film_transparent = transparent
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    return sc, dev


def import_glb(path):
    """glb を読み込み、親を持たない（ルートの）オブジェクトを返す。"""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    return [o for o in new if o.parent is None or o.parent not in new], new


def anim_end(objs):
    """読み込んだオブジェクトのアクションの最終フレーム。無ければ 0。"""
    end = 0
    for o in objs:
        ad = o.animation_data
        if ad and ad.action:
            end = max(end, int(round(ad.action.frame_range[1])))
    return end


def corners(sc, objs, frames):
    """指定フレームでの全メッシュのワールド座標の外接ボックスの角（保守的な点群）。"""
    pts = []
    for f in frames:
        sc.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        for o in objs:
            if o.type != "MESH":
                continue
            eo = o.evaluated_get(dg)
            mw = eo.matrix_world
            for c in eo.bound_box:
                pts.append(mw @ Vector(c))
    return pts


def make_floor(size, radius, base="#1f2d4d", edge=BG_HEX, rough=0.3, name="_floor"):
    """中心が少し明るく、端が背景色に溶ける床。反射は弱め。"""
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, 0))
    fl = bpy.context.active_object
    fl.name = name
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    ln = nt.nodes.new("ShaderNodeVectorMath")
    ln.operation = "LENGTH"
    dv = nt.nodes.new("ShaderNodeMath")
    dv.operation = "DIVIDE"
    dv.inputs[1].default_value = radius
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = hexcol(base)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = hexcol(edge)
    nt.links.new(geo.outputs["Position"], ln.inputs[0])
    nt.links.new(ln.outputs["Value"], dv.inputs[0])
    nt.links.new(dv.outputs["Value"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Specular IOR Level"].default_value = 0.25
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    fl.data.materials.append(mat)
    return fl


def add_sun(name, source_dir, strength, angle_deg, color=(1, 1, 1)):
    """source_dir は光が来る方向（モデルから光源へ向かうベクトル）。"""
    d = bpy.data.lights.new(name, "SUN")
    d.energy = strength
    d.angle = math.radians(angle_deg)
    d.color = color
    o = bpy.data.objects.new(name, d)
    bpy.context.scene.collection.objects.link(o)
    travel = -Vector(source_dir).normalized()
    o.rotation_euler = travel.to_track_quat("-Z", "Y").to_euler()
    return o


def set_world(color, strength):
    w = bpy.data.worlds.new("W")
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs[0].default_value = color
    bg.inputs[1].default_value = strength
    bpy.context.scene.world = w


def add_camera(lens, direction, pts, margin, aspect_shift=(0.0, 0.0)):
    """direction は注視点からカメラへ向かう向き。pts 全体が収まる位置へ置く。"""
    sc = bpy.context.scene
    cd = bpy.data.cameras.new("_cam")
    cd.lens = lens
    cd.sensor_fit = "HORIZONTAL"
    cd.clip_start, cd.clip_end = 0.05, 20000
    cam = bpy.data.objects.new("_cam", cd)
    sc.collection.objects.link(cam)
    sc.camera = cam
    back = Vector(direction).normalized()
    cam.rotation_euler = (-back).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    flat = [v for p in pts for v in (p.x, p.y, p.z)]
    co, _ = cam.camera_fit_coords(bpy.context.evaluated_depsgraph_get(), flat)
    ctr = sum(pts, Vector()) / len(pts)
    dist = (co - ctr).length
    cam.location = co + back * dist * margin
    return cam


def render_still(path):
    sc = bpy.context.scene
    sc.render.image_settings.media_type = "IMAGE"
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGBA" if sc.render.film_transparent else "RGB"
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)


def only_names(args, default):
    if "--only" in args:
        return args[args.index("--only") + 1].split(",")
    return default


# ---- サムネイル ----
def render_thumbs(names, preview):
    os.makedirs(THUMB_PNG, exist_ok=True)
    times = []
    dev = ""
    for n in names:
        res = (320, 200) if preview else (640, 400)
        sc, dev = new_scene(res, samples=32 if preview else 64, transparent=True)
        roots, allo = import_glb(glb_path(n))
        pts = corners(sc, allo, [1])
        sc.frame_set(1)
        # 中立の照明: 柔らかい主光（前左上）+ 補助光（右）+ 弱い逆光 + 灰色の環境光
        set_world(hexcol("#8a8f98"), 0.55)
        add_sun("key", (-0.55, -0.7, 0.85), 3.0, 9)
        add_sun("fill", (0.9, -0.35, 0.35), 0.9, 20)
        add_sun("rim", (-0.2, 0.9, 0.5), 0.8, 10)
        size = max((max(p[k] for p in pts) - min(p[k] for p in pts)) for k in range(3))
        fl = make_floor(size * 40, size * 12)
        fl.is_shadow_catcher = True
        add_camera(75, (0.75, -1.0, 0.62), pts, 0.10)
        t0 = time.time()
        render_still(os.path.join(THUMB_PNG, n + ".png"))
        dt = time.time() - t0
        times.append(dt)
        print(f"MEDIA thumb {n} {dt:.1f}s", flush=True)
    return dev, times


# ---- ターンテーブル ----
def build_turntable(name, res, samples, frames_override=None):
    """ターンテーブル用のシーンを組む。(sc, dev, start, end, pivot, n) を返す。"""
    sc, dev = new_scene(res, samples=samples, transparent=False)
    roots, allo = import_glb(glb_path(name))
    aend = anim_end(allo)
    n = max(96, aend - 1) if aend else 96
    if frames_override:
        n = frames_override
    sc.frame_start, sc.frame_end = 1, n
    pivot = bpy.data.objects.new("_pivot", None)
    sc.collection.objects.link(pivot)
    for o in roots:
        o.parent = pivot
    sample_frames = [1] if not aend else sorted({1, max(1, aend // 4), max(1, aend // 2), max(1, 3 * aend // 4)})
    base = corners(sc, allo, sample_frames)
    sc.frame_set(1)
    pts = []
    for k in range(8):
        r = Matrix.Rotation(math.radians(45 * k), 3, "Z")
        pts += [r @ p for p in base]
    size = max((max(p[i] for p in pts) - min(p[i] for p in pts)) for i in range(3))
    rad = max(math.hypot(p.x, p.y) for p in pts)
    set_world(hexcol(BG_HEX), 1.0)
    add_sun("key", (-0.55, -0.7, 0.85), 3.4, 8, (1.0, 0.97, 0.93))
    add_sun("fill", (0.9, -0.35, 0.35), 1.0, 20, (0.85, 0.92, 1.0))
    add_sun("rim", (-0.2, 0.9, 0.5), 2.2, 6, (0.8, 0.9, 1.0))
    make_floor(rad * 60, max(rad * 3.2, size * 2.2), base="#243659", rough=0.4)
    add_camera(45, (0.0, -1.0, 0.46), pts, 0.06)
    # 回転は pivot の Z 回転を線形補間で 1 周。最後のフレームは 360 度の 1 コマ手前にして、ループでつなげる。
    bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
    pivot.rotation_euler = (0, 0, 0)
    pivot.keyframe_insert("rotation_euler", index=2, frame=1)
    pivot.rotation_euler = (0, 0, math.radians(360))
    pivot.keyframe_insert("rotation_euler", index=2, frame=n + 1)
    return sc, dev, 1, n, pivot, n


def render_turn(names, preview):
    os.makedirs(TURN_DIR, exist_ok=True)
    os.makedirs(STILL_DIR, exist_ok=True)
    res = (960, 540) if preview else (1920, 1080)
    samples = 24 if preview else 96
    report = []
    dev = ""
    for name in names:
        sc, dev, a, b, pivot, n = build_turntable(name, res, samples, frames_override=(8 if preview else None))
        tmp = os.path.join(TURN_DIR, "_tmp_" + name)
        os.makedirs(tmp, exist_ok=True)
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        sc.render.image_settings.media_type = "VIDEO"  # Blender 5 では動画かどうかを先に選ぶ
        sc.render.image_settings.file_format = "FFMPEG"
        sc.render.ffmpeg.format = "MPEG4"
        sc.render.ffmpeg.codec = "H264"
        sc.render.ffmpeg.constant_rate_factor = "HIGH"
        sc.render.ffmpeg.ffmpeg_preset = "GOOD"
        sc.render.filepath = os.path.join(tmp, "t_")
        t0 = time.time()
        bpy.ops.render.render(animation=True)
        dt = time.time() - t0
        mp4 = [f for f in os.listdir(tmp) if f.endswith(".mp4")][0]
        dst = os.path.join(TURN_DIR, name + ".mp4")
        if os.path.exists(dst):
            os.remove(dst)
        os.replace(os.path.join(tmp, mp4), dst)
        os.rmdir(tmp)
        report.append((name, n, dt))
        print(f"MEDIA turn {name} frames={n} total={dt:.1f}s per_frame={dt / n:.2f}s", flush=True)
        # 確認用の静止画: 0/90/180/270 度（アニメーション付きは同じ位置のコマ）
        sc.render.resolution_x, sc.render.resolution_y = 640, 360
        sc.cycles.samples = 48
        for k in range(4):
            sc.frame_set(1 + int(round(k * n / 4)))
            render_still(os.path.join(STILL_DIR, f"{name}_{k}.png"))
    return dev, report


# ---- ラインナップ ----
# (名前, x, y, 向き(度, 正で時計回り上から見て), 拡大率)。実寸は船 190m とトラック 12m で差が大きすぎるので、
# 見栄え優先で各モードの大きさを揃えて並べた表示用の拡大率。
LINEUP_BACK = [
    ("truck", -50.0, 0.0, -22, 3.4),
    ("freight_train", 0.0, 30.0, -22, 1.0),
    ("roro_ship", 32.0, 12.0, -22, 0.36),
    ("cargo_plane", 72.0, 4.0, -22, 0.8),
]
LINEUP_FRONT = [
    ("kei_van", -54.0, -30.0, -22, 3.2),
    ("box_2t", -40.0, -34.0, -22, 3.2),
    ("truck_4t", -22.0, -36.0, -22, 3.2),
    ("forklift", -4.0, -38.0, -22, 3.2),
    ("pallet_stack", 8.0, -40.0, -22, 3.2),
    ("container_20ft", 22.0, -38.0, -22, 3.2),
]


def render_lineup(preview, items=None):
    os.makedirs(TURN_DIR, exist_ok=True)
    res = (1280, 720) if preview else (3840, 2160)
    sc, dev = new_scene(res, samples=48 if preview else 192, transparent=False)
    allpts = []
    for name, x, y, yaw, s in (items or (LINEUP_BACK + LINEUP_FRONT)):
        roots, allo = import_glb(glb_path(name))
        piv = bpy.data.objects.new("_p_" + name, None)
        sc.collection.objects.link(piv)
        for o in roots:
            o.parent = piv
        piv.scale = (s, s, s)
        piv.rotation_euler = (0, 0, math.radians(yaw))
        piv.location = (x, y, 0)
        bpy.context.view_layer.update()
        allpts += corners(sc, allo, [1])
    sc.frame_set(1)
    set_world(hexcol(BG_HEX), 1.0)
    add_sun("key", (-0.5, -0.65, 0.8), 3.6, 6, (1.0, 0.97, 0.93))
    add_sun("fill", (0.9, -0.3, 0.35), 1.1, 20, (0.85, 0.92, 1.0))
    add_sun("rim", (-0.2, 0.9, 0.5), 2.4, 6, (0.8, 0.9, 1.0))
    make_floor(2400, 210, base="#22335a", rough=0.55)
    add_camera(40, (0.0, -1.0, 0.5), allpts, 0.02)
    t0 = time.time()
    render_still(os.path.join(TURN_DIR, "lineup_4k.png" if not preview else "lineup_preview.png"))
    dt = time.time() - t0
    print(f"MEDIA lineup {dt:.1f}s", flush=True)
    return dev, dt


def render_info(names):
    for n in names:
        sc, _ = new_scene((320, 200), 8)
        roots, allo = import_glb(glb_path(n))
        pts = corners(sc, allo, [1])
        dims = [max(p[k] for p in pts) - min(p[k] for p in pts) for k in range(3)]
        print(f"MEDIA info {n} dims(x,y,z)={[round(v, 2) for v in dims]} anim_end={anim_end(allo)}", flush=True)


def blender_main(args):
    mode = args[0]
    preview = "--preview" in args
    t0 = time.time()
    if mode == "info":
        render_info(only_names(args, MAIN + props_list()))
    elif mode == "thumbs":
        dev, times = render_thumbs(only_names(args, MAIN + props_list()), preview)
        print(f"MEDIA SUMMARY thumbs device={dev} n={len(times)} mean={sum(times) / len(times):.1f}s total={sum(times):.0f}s")
    elif mode == "turn":
        dev, rep = render_turn(only_names(args, MAIN), preview)
        fr = sum(r[1] for r in rep)
        tt = sum(r[2] for r in rep)
        print(f"MEDIA SUMMARY turn device={dev} frames={fr} total={tt:.0f}s per_frame={tt / fr:.2f}s")
    elif mode == "lineup":
        dev, dt = render_lineup(preview)
        print(f"MEDIA SUMMARY lineup device={dev} {dt:.0f}s")
    print(f"MEDIA elapsed {time.time() - t0:.0f}s")


# ----------------------------------------------------------------------------------------------
# Pillow 側
# ----------------------------------------------------------------------------------------------
def font(size):
    from PIL import ImageFont
    for f in (r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def labels_ja():
    """主要 18 種は contact_sheet.py の LABEL、小物は props/index.json の label_ja をそのまま写す。"""
    sys.path.insert(0, HERE)
    from contact_sheet import LABEL
    out = {}
    for k, v in LABEL.items():
        out[k] = v.split("  ", 1)[1] if "  " in v else v
    with open(os.path.join(PROPS_DIR, "index.json"), encoding="utf-8") as f:
        for p in json.load(f)["props"]:
            out[p["name"]] = p["label_ja"]
    return out


def to_webp(png, dst, limit=25000):
    from PIL import Image
    im = Image.open(png).convert("RGBA").resize((320, 200), Image.LANCZOS)
    for q in (90, 85, 80, 75, 70, 65, 60, 55, 50, 45, 40):
        im.save(dst, "WEBP", quality=q, method=6, alpha_quality=q)
        if os.path.getsize(dst) <= limit:
            return q
    return 40


def post_main():
    from PIL import Image, ImageDraw
    os.makedirs(IMG_DIR, exist_ok=True)
    os.makedirs(ASSET_DIR, exist_ok=True)
    ja = labels_ja()
    names = MAIN + props_list()
    items = []
    for n in names:
        src = os.path.join(THUMB_PNG, n + ".png")
        dst = os.path.join(IMG_DIR, n + ".webp")
        q = to_webp(src, dst)
        items.append({"name": n, "label_ja": ja[n], "file": n + ".webp", "bytes": os.path.getsize(dst)})
        print(f"MEDIA webp {n} q={q} {items[-1]['bytes']}B")
    with open(os.path.join(IMG_DIR, "index.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"note": "サムネイル 320x200 透過 WebP。render_media.py が作る。", "items": items}, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # サムネイルの確認用一覧（アプリと同じ暗い背景）
    W, H, COLS, ROWS = 1920, 1080, 7, 5
    bg = (11, 18, 32)
    sheet = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(sheet)
    f1, f2 = font(15), font(13)
    cw, ch = W // COLS, H // ROWS
    for i, it in enumerate(items):
        x, y = (i % COLS) * cw, (i // COLS) * ch
        im = Image.open(os.path.join(IMG_DIR, it["file"])).convert("RGBA")
        tw = cw - 20
        im = im.resize((tw, int(tw * 200 / 320)), Image.LANCZOS)
        sheet.paste(im, (x + 10, y + 6), im)
        ty = y + 6 + im.size[1] + 2
        d.text((x + 10, ty), it["name"], fill=(241, 245, 249), font=f1)
        d.text((x + 10, ty + 20), it["label_ja"], fill=(148, 163, 184), font=f2)
    sheet.save(os.path.join(ASSET_DIR, f"{DATE}_thumbs_contact.png"))

    # ターンテーブルの確認用一覧: 1 枚に 9 種（3x3）、各モデルは 4 コマを 2x2 に並べる
    for sh in range(2):
        grp = MAIN[sh * 9:(sh + 1) * 9]
        sheet = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(sheet)
        cw, ch = W // 3, H // 3
        for i, n in enumerate(grp):
            x, y = (i % 3) * cw, (i // 3) * ch
            d.text((x + 10, y + 4), f"{n}  {ja[n]}", fill=(241, 245, 249), font=f1)
            fw, fh = (cw - 30) // 2, (ch - 30) // 2
            for k in range(4):
                p = os.path.join(STILL_DIR, f"{n}_{k}.png")
                if not os.path.exists(p):
                    continue
                im = Image.open(p).convert("RGB").resize((fw, fh), Image.LANCZOS)
                sheet.paste(im, (x + 10 + (k % 2) * (fw + 4), y + 28 + (k // 2) * (fh + 4)))
                d.text((x + 14 + (k % 2) * (fw + 4), y + 30 + (k // 2) * (fh + 4)), f"{k * 90}°", fill=(148, 163, 184), font=f2)
        for k in range(1, 3):
            d.line((k * cw, 0, k * cw, H), fill=(30, 41, 59))
            d.line((0, k * ch, W, k * ch), fill=(30, 41, 59))
        sheet.save(os.path.join(ASSET_DIR, f"{DATE}_turntable_contact_{sh + 1}.png"))

    # ラインナップの縮小版
    big = os.path.join(TURN_DIR, "lineup_4k.png")
    if os.path.exists(big):
        im = Image.open(big).convert("RGB").resize((1920, 1080), Image.LANCZOS)
        dst = os.path.join(ASSET_DIR, f"{DATE}_lineup.png")
        im.save(dst, optimize=True)
        if os.path.getsize(dst) > 1_500_000:
            im.quantize(256, dither=Image.FLOYDSTEINBERG).save(dst, optimize=True)
        print(f"MEDIA lineup copy {os.path.getsize(dst)}B")


if __name__ == "__main__":
    if bpy is not None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
        blender_main(argv)
    elif len(sys.argv) > 1 and sys.argv[1] == "post":
        post_main()
    else:
        print(__doc__)
