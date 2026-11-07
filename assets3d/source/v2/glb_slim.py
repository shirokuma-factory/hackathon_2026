"""glb の後処理: 使わない頂点属性（COLOR_1 など）を除き、参照されないアクセサ・バッファを詰めて小さくする。
使い方: python assets3d/source/v2/glb_slim.py <glb|ディレクトリ> ...
"""
import json
import os
import struct
import sys

DROP = {"COLOR_1", "TEXCOORD_1", "TANGENT"}


def slim(path):
    b = open(path, "rb").read()
    n = struct.unpack_from("<I", b, 12)[0]
    js = json.loads(b[20:20 + n])
    off = 20 + n
    bl = struct.unpack_from("<I", b, off)[0]
    bin_ = b[off + 8:off + 8 + bl]
    for m in js.get("meshes", []):
        for p in m["primitives"]:
            for k in list(p["attributes"]):
                if k in DROP:
                    del p["attributes"][k]
    used = []

    def use(i):
        if i not in used:
            used.append(i)
        return used.index(i)
    for m in js.get("meshes", []):
        for p in m["primitives"]:
            p["attributes"] = {k: use(v) for k, v in p["attributes"].items()}
            if "indices" in p:
                p["indices"] = use(p["indices"])
    for an in js.get("animations", []):
        for s in an["samplers"]:
            s["input"], s["output"] = use(s["input"]), use(s["output"])
    for sk in js.get("skins", []):
        if "inverseBindMatrices" in sk:
            sk["inverseBindMatrices"] = use(sk["inverseBindMatrices"])
    new_acc = [js["accessors"][i] for i in used]
    bvs, nb, out = [], {}, bytearray()
    for a in new_acc:
        bv = a["bufferView"]
        if bv not in nb:
            v = js["bufferViews"][bv]
            while len(out) % 4:
                out.append(0)
            data = bin_[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]
            nv = dict(v)
            nv["byteOffset"] = len(out)
            out += data
            nb[bv] = len(bvs)
            bvs.append(nv)
        a["bufferView"] = nb[bv]
    js["accessors"], js["bufferViews"] = new_acc, bvs
    while len(out) % 4:
        out.append(0)
    js["buffers"] = [{"byteLength": len(out)}]
    jb = json.dumps(js, separators=(",", ":")).encode()
    jb += b" " * (-len(jb) % 4)
    tot = 12 + 8 + len(jb) + 8 + len(out)
    res = struct.pack("<III", 0x46546C67, 2, tot) + struct.pack("<II", len(jb), 0x4E4F534A) + jb + struct.pack("<II", len(out), 0x004E4942) + bytes(out)
    open(path, "wb").write(res)
    return len(res)


if __name__ == "__main__":
    for a in sys.argv[1:]:
        ps = [os.path.join(a, f) for f in sorted(os.listdir(a)) if f.endswith(".glb")] if os.path.isdir(a) else [a]
        for p in ps:
            print(p, slim(p))
