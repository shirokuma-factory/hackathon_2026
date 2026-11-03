"""線路の図（グラフ）の純粋な関数（SPEC §17.1）。通信もファイルの読み書きもしない。

節点 = 線の頂点を約 30m の格子にまとめたもの。辺の重み = 2 点の直線距離 km（haversine）。
最短経路は標準ライブラリの heapq による Dijkstra。
"""

from __future__ import annotations

import heapq
import math
from typing import Any, Iterable

EARTH_R_M = 6371008.8
M_PER_DEG_LAT = 111_320.0
LonLat = tuple[float, float]


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    rad = math.pi / 180
    dlat, dlon = (lat2 - lat1) * rad, (lon2 - lon1) * rad
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(h))


def polyline_km(coords: list[list[float]] | list[LonLat]) -> float:
    return sum(haversine_m(a[0], a[1], b[0], b[1]) for a, b in zip(coords, coords[1:])) / 1000


# ---------------------------------------------------------------- 点と線分の距離・簡略化

def _local(lat0: float) -> tuple[float, float]:
    """緯度 lat0 付近の 1 度あたりのメートル (経度方向, 緯度方向)。"""
    return M_PER_DEG_LAT * math.cos(math.radians(lat0)), M_PER_DEG_LAT


def point_segment_distance_m(p: LonLat, a: LonLat, b: LonLat) -> float:
    """点 p と線分 ab の距離 m（近傍の平面近似。数十 km までの線分で使う）。"""
    kx, ky = _local(p[1])
    px, py = 0.0, 0.0
    ax, ay = (a[0] - p[0]) * kx, (a[1] - p[1]) * ky
    bx, by = (b[0] - p[0]) * kx, (b[1] - p[1]) * ky
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(ax + t * dx - px, ay + t * dy - py)


def simplify(coords: list[list[float]], tolerance_m: float) -> list[list[float]]:
    """Douglas-Peucker。両端は必ず残す。再帰を使わない（長い線でも安全）。"""
    n = len(coords)
    if n <= 2 or tolerance_m <= 0:
        return [list(c) for c in coords]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = coords[i], coords[j]
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            d = point_segment_distance_m(coords[k], a, b)
            if d > best:
                best, bi = d, k
        if best > tolerance_m:
            keep[bi] = True
            stack.append((i, bi))
            stack.append((bi, j))
    return [list(c) for c, k in zip(coords, keep) if k]


def distance_to_lines_m(p: LonLat, lines: Iterable[list[list[float]]], max_m: float = 1e9) -> float:
    """点から複数の線までの最短距離 m。緯度経度の箱で先に絞る。"""
    kx, ky = _local(p[1])
    dlon, dlat = max_m / kx, max_m / ky
    best = max_m
    for line in lines:
        for a, b in zip(line, line[1:]):
            if (min(a[0], b[0]) - dlon > p[0] or max(a[0], b[0]) + dlon < p[0]
                    or min(a[1], b[1]) - dlat > p[1] or max(a[1], b[1]) + dlat < p[1]):
                continue
            d = point_segment_distance_m(p, (a[0], a[1]), (b[0], b[1]))
            if d < best:
                best = d
    return best


# ---------------------------------------------------------------- 図

class Graph:
    """節点の座標と、辺（隣の節点, 重み km）の一覧。"""

    def __init__(self, snap_m: float = 30.0) -> None:
        self.snap_m = snap_m
        self.coords: list[LonLat] = []
        self.adj: list[list[tuple[int, float]]] = []
        self._cell: dict[tuple[int, int], list[int]] = {}
        self._dlat = snap_m / M_PER_DEG_LAT
        self._dlon = snap_m / (M_PER_DEG_LAT * math.cos(math.radians(36.0)))
        self._grid: dict[tuple[int, int], list[int]] | None = None

    @property
    def node_count(self) -> int:
        return len(self.coords)

    @property
    def edge_count(self) -> int:
        return sum(len(a) for a in self.adj) // 2

    def node_for(self, lon: float, lat: float) -> int:
        """snap_m 以内に既にある節点（いちばん近いもの）を返す。なければ作る。
        格子の境目をまたぐ近い点も同じ節点になるよう、隣の格子も調べる。"""
        cx, cy = math.floor(lon / self._dlon), math.floor(lat / self._dlat)
        best, best_d = -1, self.snap_m
        kx = M_PER_DEG_LAT * math.cos(math.radians(lat))
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for i in self._cell.get((gx, gy), ()):
                    x, y = self.coords[i]
                    d = math.hypot((x - lon) * kx, (y - lat) * M_PER_DEG_LAT)
                    if d <= best_d:
                        best, best_d = i, d
        if best >= 0:
            return best
        i = len(self.coords)
        self._cell.setdefault((cx, cy), []).append(i)
        self.coords.append((lon, lat))
        self.adj.append([])
        self._grid = None
        return i

    def add_edge(self, a: int, b: int) -> None:
        if a == b:
            return
        w = haversine_m(*self.coords[a], *self.coords[b]) / 1000
        if any(n == b for n, _ in self.adj[a]):
            return
        self.adj[a].append((b, w))
        self.adj[b].append((a, w))

    def add_line(self, line: list[list[float]]) -> None:
        prev = -1
        for p in line:
            cur = self.node_for(p[0], p[1])
            if prev >= 0:
                self.add_edge(prev, cur)
            prev = cur


def build(lines: Iterable[list[list[float]]], snap_m: float = 30.0) -> Graph:
    g = Graph(snap_m)
    for line in lines:
        g.add_line(line)
    return g


_GRID_DEG = 0.02


def _build_grid(g: Graph, only_below: int | None = None) -> dict[tuple[int, int], list[int]]:
    grid: dict[tuple[int, int], list[int]] = {}
    n = g.node_count if only_below is None else only_below
    for i in range(n):
        x, y = g.coords[i]
        grid.setdefault((int(x // _GRID_DEG), int(y // _GRID_DEG)), []).append(i)
    return grid


def _nearest_in_grid(g: Graph, grid: dict[tuple[int, int], list[int]], lon: float, lat: float,
                     max_m: float) -> tuple[int, float] | None:
    kx, ky = _local(lat)
    cx, cy = int(lon // _GRID_DEG), int(lat // _GRID_DEG)
    reach = int(max_m / (_GRID_DEG * min(kx, ky))) + 1
    best: tuple[int, float] | None = None
    for r in range(0, reach + 1):
        for gx in range(cx - r, cx + r + 1):
            for gy in range(cy - r, cy + r + 1):
                if max(abs(gx - cx), abs(gy - cy)) != r:
                    continue
                for i in grid.get((gx, gy), ()):
                    d = haversine_m(lon, lat, *g.coords[i])
                    if best is None or d < best[1]:
                        best = (i, d)
        # r 周目までで見つかった最良が、これ以上外側の格子より近ければ打ち切る
        if best is not None and best[1] <= r * _GRID_DEG * min(kx, ky):
            break
    if best is None or best[1] > max_m:
        return None
    return best


def snap(g: Graph, lon: float, lat: float, max_m: float = 5000.0) -> tuple[int, float] | None:
    """最も近い節点の (節点番号, 距離 m)。max_m 以内になければ None。"""
    if g._grid is None:
        g._grid = _build_grid(g)
    return _nearest_in_grid(g, g._grid, lon, lat, max_m)


def connect_lines(g: Graph, lines: Iterable[list[list[float]]], radius_m: float = 150.0) -> list[list[list[float]]]:
    """既にある図（呼ぶ時点の節点）に、新しい線を足す。新しい線の両端が、もとからある節点の
    radius_m 以内にあれば辺でつなぐ。つないだ短い線（両端の座標）の一覧を返す。"""
    base = g.node_count
    base_grid = _build_grid(g, base)
    lines = list(lines)
    for line in lines:
        g.add_line(line)
    connectors: list[list[list[float]]] = []
    for line in lines:
        if len(line) < 2:
            continue
        for end in (line[0], line[-1]):
            node = g.node_for(end[0], end[1])
            if node < base:
                continue
            hit = _nearest_in_grid(g, base_grid, end[0], end[1], radius_m)
            if hit is not None:
                g.add_edge(node, hit[0])
                connectors.append([list(g.coords[node]), list(g.coords[hit[0]])])
    return connectors


def components(g: Graph) -> list[int]:
    """連結成分の節点数（大きい順）。"""
    seen = bytearray(g.node_count)
    sizes: list[int] = []
    for s in range(g.node_count):
        if seen[s]:
            continue
        seen[s] = 1
        stack, n = [s], 0
        while stack:
            u = stack.pop()
            n += 1
            for v, _ in g.adj[u]:
                if not seen[v]:
                    seen[v] = 1
                    stack.append(v)
        sizes.append(n)
    return sorted(sizes, reverse=True)


def component_labels(g: Graph) -> list[int]:
    lab = [-1] * g.node_count
    c = 0
    for s in range(g.node_count):
        if lab[s] >= 0:
            continue
        lab[s] = c
        stack = [s]
        while stack:
            u = stack.pop()
            for v, _ in g.adj[u]:
                if lab[v] < 0:
                    lab[v] = c
                    stack.append(v)
        c += 1
    return lab


def dijkstra(g: Graph, src: int, dst: int | None = None) -> tuple[dict[int, float], dict[int, int]]:
    """src からの最短距離 km と直前の節点。dst を渡すとそこに着いた時点で止める。"""
    dist: dict[int, float] = {src: 0.0}
    prev: dict[int, int] = {}
    done: set[int] = set()
    heap: list[tuple[float, int]] = [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        if u == dst:
            break
        for v, w in g.adj[u]:
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    return dist, prev


def path(prev: dict[int, int], src: int, dst: int) -> list[int] | None:
    """dijkstra の prev から src→dst の節点列。つながっていなければ None。"""
    if src == dst:
        return [src]
    if dst not in prev:
        return None
    out = [dst]
    while out[-1] != src:
        out.append(prev[out[-1]])
    out.reverse()
    return out


def path_coords(g: Graph, nodes: list[int]) -> list[list[float]]:
    return [[g.coords[i][0], g.coords[i][1]] for i in nodes]


def shortest_path(g: Graph, src: int, dst: int) -> tuple[float, list[list[float]]] | None:
    """(距離 km, 座標の列)。つながっていなければ None。"""
    dist, prev = dijkstra(g, src, dst)
    p = path(prev, src, dst)
    if p is None:
        return None
    return dist[dst], path_coords(g, p)


def graph_summary(g: Graph) -> dict[str, Any]:
    sizes = components(g)
    return {"nodes": g.node_count, "edges": g.edge_count, "components": len(sizes), "largest": sizes[:5]}


def chains(g: Graph, keep: Iterable[int] = ()) -> list[list[list[float]]]:
    """図を、分岐・行き止まり・keep の節点で区切った線の列にする。線の両端は元の節点の座標のまま。
    各線を simplify して保存し build で組み直すと、つながり方は元の図と同じになる。"""
    keepset = set(keep)
    stop = [len(a) != 2 or i in keepset for i, a in enumerate(g.adj)]
    seen: set[tuple[int, int]] = set()
    out: list[list[list[float]]] = []

    def walk(start: int, nxt: int) -> None:
        line = [start, nxt]
        seen.add((min(start, nxt), max(start, nxt)))
        prev, cur = start, nxt
        while not stop[cur]:
            a, b = g.adj[cur][0][0], g.adj[cur][1][0]
            n = b if a == prev else a
            e = (min(cur, n), max(cur, n))
            if e in seen:
                break
            seen.add(e)
            line.append(n)
            prev, cur = cur, n
        out.append(path_coords(g, line))

    for s in range(g.node_count):
        if stop[s]:
            for v, _ in g.adj[s]:
                if (min(s, v), max(s, v)) not in seen:
                    walk(s, v)
    for s in range(g.node_count):  # 分岐のない輪
        for v, _ in g.adj[s]:
            if (min(s, v), max(s, v)) not in seen:
                walk(s, v)
    return out


def bridge_to_largest(g: Graph, node: int, max_m: float = 1000.0) -> float | None:
    """node がいちばん大きい連結成分に入っていなければ、node の成分の節点のうち最大の成分にいちばん近い
    節点どうしを辺でつなぐ（max_m 以内のときだけ）。つないだ距離 m を返す。すでに入っている・届かないときは None。"""
    labels = component_labels(g)
    sizes: dict[int, int] = {}
    for lab in labels:
        sizes[lab] = sizes.get(lab, 0) + 1
    big = max(sizes, key=lambda k: sizes[k])
    if labels[node] == big:
        return None
    grid: dict[tuple[int, int], list[int]] = {}
    for i, lab in enumerate(labels):
        if lab == big:
            x, y = g.coords[i]
            grid.setdefault((int(x // _GRID_DEG), int(y // _GRID_DEG)), []).append(i)
    best: tuple[float, int, int] | None = None
    for i, lab in enumerate(labels):
        if lab != labels[node]:
            continue
        hit = _nearest_in_grid(g, grid, g.coords[i][0], g.coords[i][1], max_m)
        if hit is not None and (best is None or hit[1] < best[0]):
            best = (hit[1], i, hit[0])
    if best is None:
        return None
    g.add_edge(best[1], best[2])
    return best[0]
