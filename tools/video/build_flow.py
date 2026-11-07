"""docs/video/motion/FLOW_JP.html と FLOW_KAGO.html を作る（全国貨物純流動調査 2021 のトラックの流れ）。

使い方: uv run python tools/video/build_flow.py
データは web/data/market.json と web/data/pref.geojson から読む（書き換えない）。
地図の輪郭は等距円筒図法で 1920x1080 の座標に直し、間引いて HTML に埋め込む。
動きは HTML の中の JS が window.__seek(ms) で時刻だけから計算する（乱数は固定シード）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "video" / "motion"
MAP_TOP, MAP_H = 24.0, 766.0  # 地図を置く縦の範囲（下 280px は字幕の場所）
LAT0, LAT1 = 30.0, 45.6
LON_MIN, LON_MAX = 122.5, 149.0
COS = math.cos(math.radians(36.0))


def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        dm, im = -1.0, -1
        for i in range(a + 1, b):
            d = abs(dy * (pts[i][0] - ax) - dx * (pts[i][1] - ay)) / L
            if d > dm:
                dm, im = d, i
        if dm > eps:
            keep[im] = True
            stack += [(a, im), (im, b)]
    return [p for p, k in zip(pts, keep) if k]


def main() -> None:
    mk = json.loads((ROOT / "web/data/market.json").read_text(encoding="utf-8"))
    geo = json.loads((ROOT / "web/data/pref.geojson").read_text(encoding="utf-8"))
    prefs = mk["prefs"]
    k = MAP_H / (LAT1 - LAT0)  # 緯度1度あたりのpx

    def proj(lon, lat):
        return ((lon - 122.5) * k * COS + 150.0, MAP_TOP + (LAT1 - lat) * k)

    rings, cent = [], {}
    for f in geo["features"]:
        name = f["properties"]["n"]
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        best = (-1.0, None)
        for poly in polys:
            outer = poly[0]
            if not any(LON_MIN <= x <= LON_MAX and LAT0 <= y <= LAT1 for x, y in outer):
                continue
            px = [proj(x, y) for x, y in outer]
            a = c_x = c_y = 0.0
            for (x0, y0), (x1, y1) in zip(px, px[1:] + px[:1]):
                cr = x0 * y1 - x1 * y0
                a += cr
                c_x += (x0 + x1) * cr
                c_y += (y0 + y1) * cr
            if abs(a) > best[0] and a:
                best = (abs(a), (c_x / (3 * a), c_y / (3 * a)))
            ring = px[:-1] if px[0] == px[-1] else px
            h = len(ring) // 2
            s = rdp(ring[: h + 1], 0.3)[:-1] + rdp(ring[h:] + ring[:1], 0.3)[:-1]
            if len(s) >= 3 and abs(a) / 2 > 1.5:
                rings.append([[round(x, 1), round(y, 1)] for x, y in s])
        cent[name] = best[1]
    pos = [cent[n] or [0, 0] for n in prefs]
    missing = {i for i, n in enumerate(prefs) if cent[n] is None}  # 範囲外（沖縄県）は描かない

    pairs = mk["modes"]["truck"]["pairs"]
    far = [p for p in pairs if p[6] >= 150]
    far.sort(key=lambda p: p[2] + p[3], reverse=True)
    top = far[:60]
    ik, it = prefs.index("鹿児島県"), prefs.index("東京都")
    kago = next(p for p in pairs if {p[0], p[1]} == {ik, it})
    lanes = []

    def add(a, b, t, kago_flag=0):
        lanes.append({"a": a, "b": b, "t": t, "k": kago_flag})

    top = [p for p in far if p[0] not in missing and p[1] not in missing][:60]
    for p in top:
        i, j, f, r = p[0], p[1], p[2], p[3]
        if {i, j} == {ik, it}:
            continue
        add(i, j, f)
        add(j, i, r)
    # 鹿児島県→東京都 / 東京都→鹿児島県。向きは「鹿児島→東京」を行きとして取り出す
    fw = kago[2] if kago[0] == ik else kago[3]
    rv = kago[3] if kago[0] == ik else kago[2]
    assert round(fw) == 1060 and round(rv) == 190, (fw, rv)
    add(ik, it, fw, 1)
    add(it, ik, rv, 2)
    data = {
        "prefs": prefs, "pos": [[round(x, 1), round(y, 1)] for x, y in pos], "rings": rings,
        "lanes": lanes, "kagoFwd": fw, "kagoRev": rv,
        "tmax": max(l["t"] for l in lanes if not l["k"]),
        "source": mk.get("source", ""),
    }
    js = (Path(__file__).parent / "flow_engine.js").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for name, dur, mode in (("FLOW_JP", 12.0, "jp"), ("FLOW_KAGO", 9.0, "kago")):
        html = TEMPLATE.replace("__NAME__", name).replace("__DUR__", str(dur)).replace(
            "__MODE__", mode).replace("__DATA__", payload).replace("__JS__", js)
        with open(OUT / f"{name}.html", "w", encoding="utf-8", newline="\n") as fp:
            fp.write(html)
        print(f"書き込み: {OUT / (name + '.html')}  {len(html) // 1024} KB")
    print(f"レーン数 {len(lanes)}  鹿児島→東京 {fw} t / 東京→鹿児島 {rv} t  最大 {data['tmax']} t")


TEMPLATE = """<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>__NAME__</title>
<style>
:root{--bg:#05080c;--warn:#f0c04a;--hl:#6cb6ff}
html,body{margin:0;width:1920px;height:1080px;background:var(--bg);overflow:hidden}
canvas{position:absolute;left:0;top:0}
</style></head><body data-duration="__DUR__" data-mode="__MODE__">
<canvas id="c" width="1920" height="1080"></canvas>
<script>const FLOW_DATA=__DATA__;</script>
<script>
__JS__
</script></body></html>
"""

if __name__ == "__main__":
    main()
