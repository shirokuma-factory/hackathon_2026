"""v2 共通部品: 材質、メッシュ部品（箱・断面押し出し・ロフト・車輪）、頂点 AO の焼き込み、glb 書き出し。

向きは build_models.py と同じ（+Z 上、前方 = -Y、原点は接地面の中心、単位 m）。
"""
import bpy
import bmesh
import math
import os
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

# 落ち着いた低彩度の色 + 差し色 1 色（橙）。暗い地図の上で形が読めるよう明度は高めにする
PAL = {
    "body": ("#d3d9e2", 0.0, 0.62),      # 荷箱・車体（明るい灰青）
    "mid": ("#8d99ab", 0.1, 0.6),        # 翼・屋根の筋・中間色
    "dark": ("#2c3544", 0.15, 0.65),     # 台枠
    "hull": ("#5a6e8a", 0.05, 0.7),      # 船体（紺灰）
    "deck": ("#aab3c0", 0.0, 0.8),       # 甲板・床
    "wood": ("#c4b99f", 0.0, 0.85),      # 荷台の床
    "glass": ("#3b4b62", 0.4, 0.22),
    "rubber": ("#1b2230", 0.0, 0.9),
    "hub": ("#9aa3b0", 0.6, 0.4),
    "accent": ("#e08a4c", 0.0, 0.55),    # 差し色 橙（地図の --orange に近づけた）
    "acc_ship": ("#4fa6d6", 0.0, 0.55),  # 船の差し色（水色を抑えた）
    "acc_rail": ("#5bb78c", 0.0, 0.55),  # 鉄道（緑）
    "acc_air": ("#9d90dc", 0.0, 0.55),   # 航空（薄紫）
    "c1": ("#7f93ad", 0.0, 0.7),         # コンテナ 5 色（彩度を抑える）
    "c2": ("#a69f8e", 0.0, 0.7),
    "c3": ("#869c90", 0.0, 0.7),
    "c4": ("#b3b9c4", 0.0, 0.7),
    "c5": ("#6c7a8f", 0.0, 0.7),
}
KEYS = list(PAL)
CONT = ["c1", "c2", "c3", "c4", "c5"]


def hexcol(h):
    h = h.lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return (f(r), f(g), f(b), 1.0)


def get_mat(name):
    m = bpy.data.materials.get("v2_" + name)
    if m is None:
        m = bpy.data.materials.new("v2_" + name)
        m.use_nodes = True
        col, me, ro = PAL[name]
        b = m.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = hexcol(col)
        b.inputs["Metallic"].default_value = me
        b.inputs["Roughness"].default_value = ro
        m.diffuse_color = hexcol(col)
        m.use_backface_culling = True
    return m


class Mdl:
    def __init__(self, name, lod=False):
        self.name, self.lod = name, lod
        self.bm = bmesh.new()

    def _tag(self, faces, mat):
        i = KEYS.index(mat)
        for f in faces:
            f.material_index = i

    def _bevel(self, verts, mat, r):
        if r <= 0 or self.lod:
            return
        vs = set(verts)
        edges = {e for v in vs for e in v.link_edges if e.verts[0] in vs and e.verts[1] in vs}
        res = bmesh.ops.bevel(self.bm, geom=list(edges), offset=r, segments=1, affect='EDGES')
        self._tag(res["faces"], mat)

    def box(self, mat, x0, x1, y0, y1, z0, z1, r=0.05):
        r = min(r, (x1 - x0) / 2.2, (y1 - y0) / 2.2, (z1 - z0) / 2.2)
        res = bmesh.ops.create_cube(self.bm, size=1.0)
        vs = res["verts"]
        for v in vs:
            v.co = Vector((x0 if v.co.x < 0 else x1, y0 if v.co.y < 0 else y1, z0 if v.co.z < 0 else z1))
        self._tag({f for v in vs for f in v.link_faces}, mat)
        self._bevel(vs, mat, r)

    def mbox(self, mat, x0, x1, *a, **k):  # 左右対称に 2 つ置く（x0..x1 と -x1..-x0）
        self.box(mat, x0, x1, *a, **k)
        self.box(mat, -x1, -x0, *a, **k)

    def _prism(self, mat, rings, r):
        vs = rings[0] + rings[1]
        n = len(rings[0])
        fs = [self.bm.faces.new(rings[0][::-1]), self.bm.faces.new(rings[1])]
        for i in range(n):
            j = (i + 1) % n
            fs.append(self.bm.faces.new((rings[0][i], rings[0][j], rings[1][j], rings[1][i])))
        self._tag(fs, mat)
        self._bevel(vs, mat, r)

    def prism_yz(self, mat, pts, x0, x1, r=0.06):
        """側面の輪郭（y, z の凸多角形）を x 方向に押し出す。"""
        self._prism(mat, [[self.bm.verts.new((x, y, z)) for y, z in pts] for x in (x0, x1)], r)

    def prism_xy(self, mat, pts, z0, z1, r=0.05):
        """上から見た輪郭（x, y の凸多角形）を z 方向に押し出す。"""
        self._prism(mat, [[self.bm.verts.new((x, y, z)) for x, y in pts] for z in (z0, z1)], r)

    def loft(self, mat, secs, power=2.4, n=None):
        """断面（y, 半幅, 半高, 中心 z）を並べた胴体。断面は角の丸い楕円（超楕円）。"""
        n = n or (8 if self.lod else 14)
        rings = []
        for (y, hw, hh, zc) in secs:
            ring = []
            for k in range(n):
                a = 2 * math.pi * k / n
                c, s = math.cos(a), math.sin(a)
                px = hw * math.copysign(abs(c) ** (2 / power), c)
                pz = hh * math.copysign(abs(s) ** (2 / power), s)
                ring.append(self.bm.verts.new((px, y, zc + pz)))
            rings.append(ring)
        fs = []
        for r in range(len(rings) - 1):
            for k in range(n):
                j = (k + 1) % n
                fs.append(self.bm.faces.new((rings[r][k], rings[r][j], rings[r + 1][j], rings[r + 1][k])))
        fs.append(self.bm.faces.new(rings[0][::-1]))
        fs.append(self.bm.faces.new(rings[-1]))
        self._tag(fs, mat)
        for f in fs:
            f.smooth = True

    def cyl_x(self, mat, x, y, z, r, w, seg=None):
        seg = seg or (8 if self.lod else 14)
        res = bmesh.ops.create_cone(self.bm, cap_ends=True, segments=seg, radius1=r, radius2=r, depth=w)
        vs = res["verts"]
        bmesh.ops.rotate(self.bm, verts=vs, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, 'Y'))
        bmesh.ops.translate(self.bm, verts=vs, vec=(x, y, z))
        fs = {f for v in vs for f in v.link_faces}
        self._tag(fs, mat)
        for f in fs:
            if len(f.verts) == 4:
                f.smooth = True

    def cyl_z(self, mat, x, y, z0, z1, r, seg=None):
        seg = seg or (8 if self.lod else 12)
        res = bmesh.ops.create_cone(self.bm, cap_ends=True, segments=seg, radius1=r, radius2=r, depth=z1 - z0)
        bmesh.ops.translate(self.bm, verts=res["verts"], vec=(x, y, (z0 + z1) / 2))
        self._tag({f for v in res["verts"] for f in v.link_faces}, mat)

    def wheel(self, x, y, r=0.5, w=0.3, hub=True, seg=None):
        self.cyl_x("rubber", x, y, r, r, w, seg)
        if hub:
            self.cyl_x("hub", x + (0.02 if x > 0 else -0.02), y, r, r * 0.55, w + 0.02, seg)

    def wheels_pair(self, x, y, r=0.5, w=0.3, **k):
        self.wheel(x, y, r, w, **k)
        self.wheel(-x, y, r, w, **k)


# ------------------------------------------------------------ 頂点 AO
def bake_ao(m, ground=True, maxd=None, samples=28, floor=0.5, subdiv=None):
    """頂点ごとに半球へレイを飛ばして遮蔽率を求め、COLOR_0 に灰色で焼く（1 = 明るい、floor = 最も暗い）。"""
    bm = m.bm
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    pts = [v.co for v in bm.verts]
    L = max(max(p[k] for p in pts) - min(p[k] for p in pts) for k in range(3))
    maxd = maxd or L * 0.10
    if subdiv and not m.lod:
        for _ in range(2):
            es = [e for e in bm.edges if e.calc_length() > subdiv]
            if not es:
                break
            bmesh.ops.subdivide_edges(bm, edges=es, cuts=1, use_grid_fill=True)
        bm.normal_update()
    bm.verts.ensure_lookup_table()
    verts = [tuple(v.co) for v in bm.verts]
    polys = [[v.index for v in f.verts] for f in bm.faces]
    if ground:
        g = L * 3
        base = len(verts)
        verts += [(-g, -g, -0.001), (g, -g, -0.001), (g, g, -0.001), (-g, g, -0.001)]
        polys.append([base, base + 1, base + 2, base + 3])
    tree = BVHTree.FromPolygons(verts, polys, all_triangles=False)
    dirs = []
    for i in range(samples):
        t = (i + 0.5) / samples
        phi = i * 2.399963
        dirs.append(Vector((math.sqrt(t) * math.cos(phi), math.sqrt(t) * math.sin(phi), math.sqrt(1 - t))))
    lay = bm.loops.layers.color.get("Col") or bm.loops.layers.color.new("Col")
    vals = {}
    for v in bm.verts:
        n = v.normal.normalized() if v.normal.length > 0 else Vector((0, 0, 1))
        t = n.cross(Vector((0, 0, 1)) if abs(n.z) < 0.9 else Vector((1, 0, 0))).normalized()
        b = n.cross(t)
        hit = 0.0
        o = v.co + n * 0.03
        for d in dirs:
            w = t * d.x + b * d.y + n * d.z
            r = tree.ray_cast(o, w, maxd)
            if r[0] is not None:
                hit += 1 - r[3] / maxd * 0.5  # 近い遮蔽ほど強く
        vals[v.index] = floor + (1 - floor) * (1 - hit / samples) ** 1.3
    for f in bm.faces:
        for l in f.loops:
            a = vals[l.vert.index]
            l[lay] = (a, a, a, 1.0)


def finish(m, ao=True, subdiv=None):
    bm = m.bm
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    if ao:
        bake_ao(m, subdiv=subdiv)
    me = bpy.data.meshes.new(m.name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(m.name, me)
    bpy.context.scene.collection.objects.link(ob)
    for k in KEYS:
        me.materials.append(get_mat(k))
    bpy.context.view_layer.objects.active = ob
    for o in bpy.data.objects:
        o.select_set(o is ob)
    bpy.ops.object.material_slot_remove_unused()
    return ob


def export_glb(ob, path):
    for o in bpy.data.objects:
        o.select_set(o is ob)
    bpy.context.view_layer.objects.active = ob
    kw = dict(filepath=path, export_format='GLB', use_selection=True, export_yup=True, export_apply=True,
              export_materials='EXPORT', export_cameras=False, export_lights=False)
    props = bpy.ops.export_scene.gltf.get_rna_type().properties
    if "export_vertex_color" in props:
        kw["export_vertex_color"] = 'ACTIVE'
    if "export_active_vertex_color_when_no_material" in props:
        kw["export_active_vertex_color_when_no_material"] = True
    bpy.ops.export_scene.gltf(**kw)
    return os.path.getsize(path)
