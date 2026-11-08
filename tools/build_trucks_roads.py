"""「いまの大型車」の道路の区間（web/data/trucks_roads.json）を作る。通信しない。

道路の形は OpenStreetMap の高速道路と国道の線（data/raw/roads/*.json。tools/fetch_osm_roads.py で取得。© OpenStreetMap contributors, ODbL）。
手順:
  1. 線を 30 m の許容で間引く（Douglas-Peucker）。上りと下りで別々に描かれた並行する線は 1 本にまとめる（70 m 以内で重なる線を捨てる）。
  2. 端点が同じ位置の線をつないでグラフにし、分岐のない道のりを 2〜8 km の「区間」に等分する。
  3. 観測点（web/data/trucks_now.json）を 300 m 以内の最も近い辺に結ぶ。結べなかった点は一覧にして残す（直線では結ばない）。
  2b. 並行する線を捨てたために切れた所（端点が 220 m 以内の同じ種類・共通の番号の道路）をつなぎ直す。
  4. 各観測点から同じ種類・同じ番号の道路の上を 50 km まで広げる。区間の真ん中から両側に観測点があれば、両側の最も近い観測点の間を
     距離の逆数の重み（= 道のりに沿った線形補間）で補う。片側だけなら最後の観測点から 25 km まで延ばす（d が 15 km を超えると画面で細く・薄くする）。
     どちらでもない区間は w を空にする（地図では灰色。0 台とは扱わない）。r は道路の格（3 高速道路、2 trunk、1 primary。画面の縮尺ごとの出し分けに使う）。
区間ごとの値は、画面で観測点の値（時間ごと）から重みづけして求める。このファイルには観測点の値を入れない。
座標は 1e-5 度の整数の差分（c = [x0, y0, dx1, dy1, ...]）。

使い方: uv run python tools/build_trucks_roads.py
"""

from __future__ import annotations

import glob
import heapq
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "data"
RAW = ROOT / "data" / "raw" / "roads"
OUT = DATA / "trucks_roads.json"
TOL_KM = 0.030
DEDUP_KM = 0.070
SNAP_KM = 0.300
REACH_KM = 50.0  # 観測点から道路の上を広げる最大の道のり
EXTRAP_KM = 25.0  # 片側にしか観測点がない所（最後の観測点の先）へ値を延ばす上限
SPAN_KM = 60.0  # 両側の観測点までの道のりの和がこれ以下なら、間を補う
JOIN_KM = 0.22  # 切れた道路をつなぎ直す距離
PIECE_KM = 6.0
SCALE = 100000


def kmd(a, b) -> float:
    return math.hypot((a[0] - b[0]) * 111.32 * math.cos(math.radians((a[1] + b[1]) / 2)), (a[1] - b[1]) * 111.32)


def simplify(pts, tol):
    """Douglas-Peucker（km 単位の平面に直して計算）。"""
    n = len(pts)
    if n <= 2:
        return pts
    c = math.cos(math.radians(pts[0][1])) * 111.32
    xy = [(p[0] * c, p[1] * 111.32) for p in pts]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = xy[i]
        bx, by = xy[j]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        best, bk = -1.0, -1
        for k in range(i + 1, j):
            px, py = xy[k]
            if ll == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
                d = math.hypot(px - ax - t * dx, py - ay - t * dy)
            if d > best:
                best, bk = d, k
        if best > tol:
            keep[bk] = True
            stack.append((i, bk))
            stack.append((bk, j))
    return [p for p, k in zip(pts, keep) if k]


def samples(pts, step=0.1):
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        d = kmd(a, b)
        n = int(d // step)
        for s in range(1, n + 1):
            t = s * step / d
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        out.append(b)
    return out


RANK = {"motorway": 3, "trunk": 2, "primary": 1}


def seg_dist_g(p, a, b):
    kx = 111.32 * math.cos(math.radians(p[1]))
    ax, ay = (a[0] - p[0]) * kx, (a[1] - p[1]) * 111.32
    bx, by = (b[0] - p[0]) * kx, (b[1] - p[1]) * 111.32
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    t = 0.0 if ll == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / ll))
    return math.hypot(ax + t * dx, ay + t * dy), t * math.sqrt(ll)


def load_ways():
    ways = {}
    for f in sorted(glob.glob(str(RAW / "*.json"))):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        for e in d["elements"]:
            if e.get("type") != "way" or len(e.get("geometry", [])) < 2:
                continue
            t = e.get("tags", {})
            hw = t.get("highway")
            refs = frozenset(x.strip() for x in re.split(r"[;,]", t.get("ref", "")) if x.strip())
            ways[e["id"]] = ([(g["lon"], g["lat"]) for g in e["geometry"]], 2 if hw == "motorway" else 1, RANK.get(hw, 1), refs)
    return ways


def compat(a, b) -> bool:
    """同じ道路か。番号が分からない側があれば同じとみなし、両方にあれば共通の番号が要る。"""
    return not a or not b or bool(a & b)


def main() -> int:
    tn = json.loads((DATA / "trucks_now.json").read_text(encoding="utf-8"))
    pts = tn["points"]
    keys = [f"{p['id']}|{p['type']}" for p in pts]
    xy = [(p["lon"], p["lat"]) for p in pts]

    ways = load_ways()
    n_way = len(ways)
    # 1. 間引きと、並行する線の整理（高速道路を先に）
    cell = 0.0009
    taken: dict[tuple[int, int], list[tuple[float, float]]] = {}
    kept = []
    for _wid, (g, k, r, refs) in sorted(ways.items(), key=lambda kv: -kv[1][1]):
        s = simplify(g, TOL_KM)
        sm = samples(s)
        hit = 0
        inner = sm[1:-1]  # 端点は別の道路の端点と重なるので数えない。短い線は必ず残す（ネットワークを切らない）
        for p in inner:
            cx, cy = int(p[0] / cell), int(p[1] / cell)
            ok = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for q in taken.get((cx + dx, cy + dy), ()):
                        if kmd(p, q) <= DEDUP_KM:
                            ok = True
                            break
                    if ok:
                        break
                if ok:
                    break
            hit += ok
        if len(inner) >= 3 and hit >= 0.6 * len(inner):
            continue
        kept.append((s, k, r, refs))
        for p in sm:
            taken.setdefault((int(p[0] / cell), int(p[1] / cell)), []).append(p)

    # 2. グラフ
    vid: dict[tuple[int, int], int] = {}
    verts: list[tuple[float, float]] = []

    def vertex(p):
        key = (round(p[0] * SCALE), round(p[1] * SCALE))
        if key not in vid:
            vid[key] = len(verts)
            verts.append((key[0] / SCALE, key[1] / SCALE))
        return vid[key]

    edges: dict[tuple[int, int], tuple[int, int, frozenset]] = {}  # (u, v) -> (種類 k, 格 r, 道路番号)
    for s, k, r, refs in kept:
        prev = None
        for p in s:
            v = vertex(p)
            if prev is not None and prev != v:
                e = (min(prev, v), max(prev, v))
                o = edges.get(e)
                edges[e] = (k, r, refs) if o is None else (max(o[0], k), max(o[1], r), o[2] | refs)
            prev = v

    # 2b. 並行する線を捨てたために切れた所を、近くの同じ道路（同じ種類・共通の番号）につなぎ直す
    parent: dict[int, int] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    inc: dict[int, set] = {}
    for e in edges:
        union(*e)
        inc.setdefault(e[0], set()).add(e)
        inc.setdefault(e[1], set()).add(e)
    GC = 0.003
    egrid: dict[tuple[int, int], set] = {}

    def reg(e):
        a, b = verts[e[0]], verts[e[1]]
        n = max(1, int(kmd(a, b) / 0.1))
        for s in range(n + 1):
            t = s / n
            egrid.setdefault((math.floor((a[0] + (b[0] - a[0]) * t) / GC), math.floor((a[1] + (b[1] - a[1]) * t) / GC)), set()).add(e)

    for e in edges:
        reg(e)
    dangling = [v for v, es in inc.items() if len(es) == 1]
    joined = 0
    comp_before = len({find(v) for v in inc})
    for v in dangling:
        if len(inc[v]) != 1:
            continue
        (e0,) = tuple(inc[v])
        a0 = edges[e0]
        p = verts[v]
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for e2 in egrid.get((math.floor(p[0] / GC) + dx, math.floor(p[1] / GC) + dy), ()):
                    a2 = edges.get(e2)
                    if a2 is None or a2[0] != a0[0] or not compat(a0[2], a2[2]) or find(e2[0]) == find(v):
                        continue
                    d, along = seg_dist_g(p, verts[e2[0]], verts[e2[1]])
                    if d <= JOIN_KM and (best is None or d < best[0]):
                        best = (d, e2, along)
        if best is None:
            continue
        _d, e2, along = best
        u2, w2 = e2
        el = kmd(verts[u2], verts[w2])
        if along < 0.04:
            tgt = u2
        elif along > el - 0.04:
            tgt = w2
        else:
            t = along / el
            tgt = vertex((verts[u2][0] + (verts[w2][0] - verts[u2][0]) * t, verts[u2][1] + (verts[w2][1] - verts[u2][1]) * t))
            if tgt not in (u2, w2):
                a2 = edges.pop(e2)
                for x in e2:
                    inc[x].discard(e2)
                for n1 in ((min(u2, tgt), max(u2, tgt)), (min(tgt, w2), max(tgt, w2))):
                    edges[n1] = a2
                    inc.setdefault(n1[0], set()).add(n1)
                    inc.setdefault(n1[1], set()).add(n1)
                    union(*n1)
                    reg(n1)
        if tgt == v:
            continue
        ne = (min(v, tgt), max(v, tgt))
        edges[ne] = a0
        inc.setdefault(ne[0], set()).add(ne)
        inc.setdefault(ne[1], set()).add(ne)
        union(v, tgt)
        reg(ne)
        joined += 1
    comp_after = len({find(v) for v in inc})

    elist = list(edges.keys())
    eattr = [edges[e] for e in elist]
    adj: dict[int, list[tuple[int, float, int]]] = {}
    for ei, (u, v) in enumerate(elist):
        d = kmd(verts[u], verts[v])
        adj.setdefault(u, []).append((v, d, ei))
        adj.setdefault(v, []).append((u, d, ei))

    # 3. 観測点を辺に結ぶ
    grid: dict[tuple[int, int], list[int]] = {}
    for ei, (u, v) in enumerate(elist):
        a, b = verts[u], verts[v]
        n = max(1, int(kmd(a, b) / 0.5))
        cells = set()
        for s in range(n + 1):
            t = s / n
            cells.add((math.floor((a[0] + (b[0] - a[0]) * t) * 100), math.floor((a[1] + (b[1] - a[1]) * t) * 100)))
        for c in cells:
            grid.setdefault(c, []).append(ei)

    # near[頂点][観測点がある側の隣の頂点] = [(道のり km, 観測点の番号), ...]（近い順に 3 つまで）。側が 2 つあれば観測点の間（内挿）、1 つなら外側（延長）
    near: dict[int, dict[int, list[tuple[float, int]]]] = {}

    def offer(vtx, d, pi, side):
        lst = near.setdefault(vtx, {}).setdefault(side, [])
        for i, (dd, pj) in enumerate(lst):
            if pj == pi:
                if d < dd:
                    lst[i] = (d, pi)
                    lst.sort()
                return
        lst.append((d, pi))
        lst.sort()
        del lst[3:]

    attached: list[int] = []
    unsnapped: list[int] = []
    snap_d: list[float] = []
    for pi, p in enumerate(xy):
        cx, cy = math.floor(p[0] * 100), math.floor(p[1] * 100)
        cand = {e for dx in (-1, 0, 1) for dy in (-1, 0, 1) for e in grid.get((cx + dx, cy + dy), [])}
        best = None
        for ei in cand:
            u, v = elist[ei]
            d, along = seg_dist_g(p, verts[u], verts[v])
            if d <= SNAP_KM and (best is None or d < best[0]):
                best = (d, ei, along)
        if best is None:
            unsnapped.append(pi)
            continue
        attached.append(pi)
        d, ei, along = best
        snap_d.append(d)
        u, v = elist[ei]
        kk, _r, srefs = eattr[ei]  # 値は同じ種類・同じ番号の道路の上だけに広げる
        el = kmd(verts[u], verts[v])
        dist = {u: d + along, v: d + max(0.0, el - along)}
        prv = {u: v, v: u}
        heap = [(dd, vv) for vv, dd in dist.items()]
        heapq.heapify(heap)
        while heap:
            dd, vv = heapq.heappop(heap)
            if dd > dist.get(vv, 1e9) or dd > REACH_KM:
                continue
            offer(vv, dd, pi, prv[vv])
            for nv, w, _e in adj.get(vv, []):
                if eattr[_e][0] != kk or not compat(srefs, eattr[_e][2]):
                    continue
                nd = dd + w
                if nd < dist.get(nv, 1e9) and nd <= REACH_KM:
                    dist[nv] = nd
                    prv[nv] = vv
                    heapq.heappush(heap, (nd, nv))

    def pick(vtx):
        """区間の真ん中の頂点に使う観測点と重み、観測点までの道のり d。観測点が両側にあれば間を直線で補う（重みは距離の逆数 = 線形補間）。片側だけなら EXTRAP_KM まで延ばす。"""
        groups = sorted(near.get(vtx, {}).values(), key=lambda l: l[0][0])
        if not groups:
            return [], None
        if len(groups) >= 2 and groups[0][0][0] + groups[1][0][0] <= SPAN_KM:
            sel, seen = [], set()
            for g in groups[:2]:
                for d, pi in g:
                    if d <= g[0][0] + 1.5 and pi not in seen:
                        seen.add(pi)
                        sel.append((d, pi))
            dd = min(groups[0][0][0], 14.0)  # 間を補った区間は、端から離れていても薄くしない
        else:
            g = groups[0]
            if g[0][0] > EXTRAP_KM:
                return [], None
            sel = [(d, pi) for d, pi in g if d <= g[0][0] + 1.5]
            dd = g[0][0]
        inv = [(1.0 / max(d, 0.5), pi) for d, pi in sel]
        s = sum(w for w, _ in inv)
        return [[pi, round(w / s, 3)] for w, pi in inv], round(dd, 1)

    # 4. 分岐のない道のりを区間に等分する
    used = [False] * len(elist)

    def deg(v):
        return len(adj.get(v, []))

    pieces = []

    def emit(chain, k, r):
        cum = [0.0]
        for a, b in zip(chain, chain[1:]):
            cum.append(cum[-1] + kmd(verts[a], verts[b]))
        total = cum[-1]
        n = max(1, math.ceil(total / PIECE_KM))
        cuts = [0]
        for s in range(1, n):
            target = total * s / n
            j = cuts[-1] + 1
            while j < len(chain) - 1 and cum[j] < target:
                j += 1
            if j < len(chain) - 1 and j > cuts[-1]:
                cuts.append(j)
        cuts.append(len(chain) - 1)
        for a, b in zip(cuts, cuts[1:]):
            if b <= a:
                continue
            sub = chain[a:b + 1]
            ln = cum[b] - cum[a]
            if ln < 0.02:
                continue
            half = cum[a] + ln / 2
            mid = sub[0]
            for t in range(a, b + 1):
                mid = chain[t]
                if cum[t] >= half:
                    break
            c = []
            px = py = 0
            for i, vv in enumerate(sub):
                x, y = round(verts[vv][0] * SCALE), round(verts[vv][1] * SCALE)
                c.extend([x, y] if i == 0 else [x - px, y - py])
                px, py = x, y
            w, dd = pick(mid)
            pieces.append({"c": c, "k": k, "r": r, "km": round(ln, 2), "w": w, "d": dd})

    for start in range(len(elist)):
        if used[start]:
            continue
        u, v = elist[start]
        k, r, _refs = eattr[start]
        used[start] = True
        chain = [u, v]
        for side in (0, 1):
            while True:
                end = chain[-1] if side == 0 else chain[0]
                if deg(end) != 2:
                    break
                nxt = [t for t in adj[end] if not used[t[2]]]
                if not nxt:
                    break
                nv, _w, ei = nxt[0]
                used[ei] = True
                k = max(k, eattr[ei][0])
                r = max(r, eattr[ei][1])
                if side == 0:
                    chain.append(nv)
                else:
                    chain.insert(0, nv)
        emit(chain, k, r)

    col_km = sum(p["km"] for p in pieces if p["w"])
    grey_km = sum(p["km"] for p in pieces if not p["w"])
    km_by = {f"k{kk}_{'coloured' if c else 'grey'}": round(sum(p["km"] for p in pieces if p["k"] == kk and bool(p["w"]) == c), 1) for kk in (1, 2) for c in (True, False)}
    out = {
        "note": "道路の区間。c は経度・緯度（1e-5 度の整数。先頭は絶対値、あとは前との差）、k は 1=国道、2=高速道路、r は道路の格（3=高速道路、2=trunk、1=primary）、km は長さ、w は [trucks_now.json の points の keys の番号, 重み]。観測点の間は線形に補い、最後の観測点の先は 25 km まで延ばす。w が空の区間は観測点につながらない（灰色）。d は最も近い観測点までの道のり km（延長の区間は 15 km を超えると画面で細く・薄くする。間を補った区間は 14 以下）。",
        "built_on": "2026-11-07",
        "scale": SCALE,
        "source": "道路の形: © OpenStreetMap contributors (ODbL)",
        "license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors（道路の形）",
        "keys": keys,
        "unsnapped": [keys[i] for i in unsnapped],
        "pieces": pieces,
        "stats": {"ways_raw": n_way, "ways_kept": len(kept), "vertices": len(verts), "edges": len(elist), "pieces": len(pieces),
                  "gap_joins": joined, "components_before": comp_before, "components_after": comp_after,
                  "points": len(pts), "snapped": len(attached), "unsnapped": len(unsnapped),
                  "snap_median_m": round(1000 * sorted(snap_d)[len(snap_d) // 2]) if snap_d else None,
                  "road_km": round(col_km + grey_km, 1), "coloured_km": round(col_km, 1), "grey_km": round(grey_km, 1), **km_by,
                  "with_data_pieces": sum(1 for p in pieces if p["w"]),
                  "grey_pieces": sum(1 for p in pieces if not p["w"])},
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(out["stats"], ensure_ascii=False), OUT.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
