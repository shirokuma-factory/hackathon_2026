"""全国の交通の流れ（粒子）の道路の線を作る。出力は web/data/flow_lines.json。

県庁所在地どうしの区間（market.json の営業用トラックの区間）ごとに、既にある道路の線
（routes_<出発地>.json の 47 県庁所在地への道路、routes_pairs.json の 60 区間）をつないだ道路網の上で
最短の経路を求め、間引いて保存する。量は market.json から読む（ここには書かない）。
道路網に無い県（沖縄県）は含めない。経路は OSRM の線をつないだ概算で、実際の走行経路ではない。
"""
import glob
import heapq
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "web" / "data"
GRID = 0.02          # 度。この大きさの格子で近い点をまとめて、別々の線をつなぐ
COVER = 0.985        # 量の多い区間から順に、全体のこの割合まで保存する
TOL = 0.012          # 度。間引きの許容


def dec(flat):
    out, x, y = [], 0, 0
    for i in range(0, len(flat), 2):
        x += flat[i]
        y += flat[i + 1]
        out.append((x / 1e4, y / 1e4))
    return out


def hav(a, b):
    r = math.pi / 180
    dx = (b[0] - a[0]) * r * math.cos((a[1] + b[1]) * r / 2)
    dy = (b[1] - a[1]) * r
    return 6371.0 * math.hypot(dx, dy)


def simplify(pts, tol):
    n = len(pts)
    if n <= 2:
        return list(pts)
    k0 = math.cos(math.radians(pts[0][1]))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a][0] * k0, pts[a][1]
        dx, dy = pts[b][0] * k0 - ax, pts[b][1] - ay
        l2 = dx * dx + dy * dy or 1e-12
        md, mi = 0.0, -1
        for i in range(a + 1, b):
            px, py = pts[i][0] * k0 - ax, pts[i][1] - ay
            t = max(0.0, min(1.0, (px * dx + py * dy) / l2))
            d = math.hypot(px - t * dx, py - t * dy)
            if d > md:
                md, mi = d, i
        if md > tol and mi > 0:
            keep[mi] = True
            stack += [(a, mi), (mi, b)]
    return [p for p, k in zip(pts, keep) if k]


def polylines():
    for f in sorted(glob.glob(str(DATA / "routes_[0-9]*.json"))):
        o = json.loads(Path(f).read_text(encoding="utf-8"))
        for d in o["dests"].values():
            m = (d.get("m") or {}).get("truck")
            if m:
                for lg in m["legs"]:
                    yield dec(lg["c"])
    for d in json.loads((DATA / "routes_pairs.json").read_text(encoding="utf-8")).values():
        m = (d.get("m") or {}).get("truck")
        if m:
            for lg in m["legs"]:
                yield dec(lg["c"])


def main():
    mk = json.loads((DATA / "market.json").read_text(encoding="utf-8"))
    cap, pairs = mk["cap"], mk["modes"]["truck"]["pairs"]
    cell, pos, adj = {}, [], []

    def node(p):
        k = (round(p[0] / GRID), round(p[1] / GRID))
        if k not in cell:
            cell[k] = len(pos)
            pos.append([p[0], p[1], 1])
            adj.append({})
        else:
            q = pos[cell[k]]
            q[0] = (q[0] * q[2] + p[0]) / (q[2] + 1)
            q[1] = (q[1] * q[2] + p[1]) / (q[2] + 1)
            q[2] += 1
        return cell[k]

    for pl in polylines():
        prev = None
        for p in pl:
            i = node(p)
            if prev is not None and prev != i:
                w = hav(pos[prev], pos[i])
                if i not in adj[prev] or adj[prev][i] > w:
                    adj[prev][i] = adj[i][prev] = w
            prev = i
    # 位置が動いたあとの距離で重みを入れ直す
    for u in range(len(pos)):
        for v in adj[u]:
            adj[u][v] = hav(pos[u], pos[v])
    print("nodes", len(pos), "edges", sum(len(a) for a in adj) // 2)

    def nearest(p):
        return min(range(len(pos)), key=lambda i: hav(pos[i], p))

    src = {}
    for i, c in enumerate(cap):
        if i == 46:   # 沖縄県は道路網に無い
            continue
        s = nearest(c)
        if hav(pos[s], c) > 30:
            continue
        dist = {s: 0.0}
        prev = {}
        h = [(0.0, s)]
        while h:
            d, u = heapq.heappop(h)
            if d > dist.get(u, 1e18):
                continue
            for v, w in adj[u].items():
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(h, (nd, v))
        src[i] = (s, prev)

    tot = sum(p[2] + p[3] for p in pairs)
    order = sorted(range(len(pairs)), key=lambda k: -(pairs[k][2] + pairs[k][3]))
    out, acc, skipped = [], 0.0, 0
    for k in order:
        p = pairs[k]
        fi, ti = p[0], p[1]
        if acc >= COVER * tot:
            break
        if fi not in src or ti not in src:
            skipped += 1
            continue
        s, prev = src[fi]
        t = nearest(cap[ti])
        if t != s and t not in prev:
            skipped += 1
            continue
        path = [t]
        while path[-1] != s:
            path.append(prev[path[-1]])
        path.reverse()
        pts = [tuple(cap[fi])] + [(pos[i][0], pos[i][1]) for i in path] + [tuple(cap[ti])]
        pts = simplify(pts, TOL)
        flat, x, y = [], 0, 0
        for lon, lat in pts:
            xi, yi = round(lon * 1000), round(lat * 1000)
            flat += [xi - x, yi - y]
            x, y = xi, yi
        out.append([fi, ti, flat])
        acc += p[2] + p[3]
    print("pairs", len(out), "skipped", skipped, "cover", round(acc / tot, 4))
    res = {"note": "県庁所在地どうしの区間の道路の線（OSRM の線をつないだ概算）。経度・緯度は 1/1000 度の差分", "scale": 1000, "lines": out}
    with open(DATA / "flow_lines.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
