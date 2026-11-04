"""glb の中身（三角形数・ノード・描画呼び出し・アニメーション）を表にする。
使い方: python assets3d/source/glb_report.py [ディレクトリ] [--json]"""
import json
import os
import struct
import sys


def read_glb(p):
    b = open(p, "rb").read()
    n = struct.unpack_from("<I", b, 12)[0]
    js = json.loads(b[20:20 + n])
    off = 20 + n
    binlen = struct.unpack_from("<I", b, off)[0]
    return js, b[off + 8:off + 8 + binlen]


def acc_data(js, bin_, i):
    import numpy as np
    a = js["accessors"][i]
    bv = js["bufferViews"][a["bufferView"]]
    ct = {5126: "f4", 5123: "u2", 5125: "u4", 5121: "u1", 5122: "i2", 5120: "i1"}[a["componentType"]]
    nc = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    o = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    arr = np.frombuffer(bin_, dtype=ct, count=a["count"] * nc, offset=o)
    return arr.reshape(a["count"], nc) if nc > 1 else arr


def report(p):
    js, bin_ = read_glb(p)
    tris = prims = 0
    mats = set()
    for m in js.get("meshes", []):
        for pr in m["primitives"]:
            prims += 1
            mats.add(pr.get("material"))
            tris += js["accessors"][pr["indices"]]["count"] // 3
    # ノードごとのメッシュ参照を数える（インスタンス化される三角形）
    drawn = 0
    for nd in js["nodes"]:
        if "mesh" in nd:
            drawn += len(js["meshes"][nd["mesh"]]["primitives"])
    ch = keys = 0
    interp = set()
    loops = []
    for an in js.get("animations", []):
        for s in an["samplers"]:
            interp.add(s["interpolation"] if "interpolation" in s else "LINEAR")
        for c in an["channels"]:
            s = an["samplers"][c["sampler"]]
            ch += 1
            n = js["accessors"][s["input"]]["count"]
            keys += n
            v = acc_data(js, bin_, s["output"])
            loops.append(float(abs(v[0] - v[-1]).max()))
    t = None
    for an in js.get("animations", []):
        for s in an["samplers"]:
            a = js["accessors"][s["input"]]
            t = (a["min"][0], a["max"][0])
    return {"tris": tris, "nodes": len(js["nodes"]), "prims": prims, "drawn": drawn,
            "materials": len(js.get("materials", [])), "bytes": os.path.getsize(p),
            "channels": ch, "keys": keys, "interp": sorted(interp),
            "loop_maxdiff": max(loops) if loops else None, "time": t,
            "node_names": [n.get("name") for n in js["nodes"]],
            "anims": [a.get("name") for a in js.get("animations", [])]}


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else os.path.join("web", "models")
    rows = {f[:-4]: report(os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.endswith(".glb")}
    if "--json" in sys.argv:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print("name tris nodes prims draw mats bytes ch keys interp loopdiff time")
        for n, r in rows.items():
            print(n, r["tris"], r["nodes"], r["prims"], r["drawn"], r["materials"], r["bytes"], r["channels"], r["keys"],
                  ",".join(r["interp"]) or "-", r["loop_maxdiff"], r["time"])
