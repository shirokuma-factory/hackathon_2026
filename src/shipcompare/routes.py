"""実際の線の形の経路を組み立てる関数（SPEC §17.1）。

- 経路 = 区間（leg）の列。区間は手段（truck / rail / ferry / air）、座標の列、距離 km、
  所要時間 分（トラックだけ。電車・船・飛行機は時刻表を使えないので None）を持つ。
- route_on_demand: その場で OSRM に問い合わせ、data/routes/cache/ に保存する（1 秒 1 件、失敗は None）。
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .railgraph import haversine_m, point_segment_distance_m, polyline_km, simplify
from .tariffs import data_dir

OSRM_BASE = "https://router.project-osrm.org"
USER_AGENT = "shipcompare-hackathon/0.3 (non-commercial demo)"
MODES = ("truck", "rail", "ferry", "air")
SIMPLIFY_TOLERANCE_M = 75.0  # 保存する線の簡略化（SPEC §17.1: 約 50〜100m）
AIR_MIN_KM = 300.0  # これより近い空港間は飛行機の経路を作らない
AIR_POINTS = 64  # 大圏の弧の補間点の数（両端を含めて 65 点）

LICENSE_FIELDS = {
    "sources": [
        "トラック・第一/最終区間: OSRM 公開デモ（OpenStreetMap のデータ）",
        "電車: 国土数値情報（鉄道データ）（国土交通省）を加工して作成（CC BY 4.0）、貨物専用線は OpenStreetMap",
        "船: OpenStreetMap のフェリー航路",
        "飛行機: OpenStreetMap の空港の位置を結んだ大圏の弧",
    ],
    "license": "国土数値情報 N02: CC BY 4.0。OSM・OSRM のデータ: ODbL 1.0",
    "attribution": "国土数値情報（鉄道データ）（国土交通省）を加工して作成。© OpenStreetMap contributors",
}


class RouteError(RuntimeError):
    pass


class NoRoute(RouteError):
    """OSRM が「道がつながっていない」と答えた。取り直しても同じなので再試行しない。"""


# ---------------------------------------------------------------- 幾何

def great_circle(a: tuple[float, float], b: tuple[float, float], n: int = AIR_POINTS) -> list[list[float]]:
    """a から b への大圏の弧。n 区間に分け、両端を含む n+1 点を返す（n >= 2）。"""
    n = max(2, n)
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    x1, y1, z1 = math.cos(lat1) * math.cos(lon1), math.cos(lat1) * math.sin(lon1), math.sin(lat1)
    x2, y2, z2 = math.cos(lat2) * math.cos(lon2), math.cos(lat2) * math.sin(lon2), math.sin(lat2)
    dot = max(-1.0, min(1.0, x1 * x2 + y1 * y2 + z1 * z2))
    omega = math.acos(dot)
    out: list[list[float]] = []
    for i in range(n + 1):
        t = i / n
        if omega < 1e-12:
            x, y, z = x1, y1, z1
        else:
            s = math.sin(omega)
            f1, f2 = math.sin((1 - t) * omega) / s, math.sin(t * omega) / s
            x, y, z = f1 * x1 + f2 * x2, f1 * y1 + f2 * y2, f1 * z1 + f2 * z2
        out.append([math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))])
    out[0], out[-1] = [a[0], a[1]], [b[0], b[1]]
    return out


def _round(coords: list[list[float]]) -> list[list[float]]:
    return [[round(x, 4), round(y, 4)] for x, y in coords]


def make_leg(mode: str, coords: list[list[float]], *, km: float | None = None, minutes: float | None = None,
             label: str | None = None, tolerance_m: float = SIMPLIFY_TOLERANCE_M, simplify_line: bool = True,
             ) -> dict[str, Any]:
    """区間を作る。km を渡さなければ元の線の長さ。座標は簡略化して小数 4 桁（約 10m）にする。"""
    if mode not in MODES:
        raise ValueError(f"手段が不明です: {mode}")
    length = polyline_km(coords) if km is None else km
    line = simplify(coords, tolerance_m) if simplify_line else [list(c) for c in coords]
    if len(line) < 3 <= len(coords):
        # 直線に近い短い区間でも、元の線の途中の点を 1 つ残して 2 点だけの線にしない（G42）
        a, b = line[0], line[-1]
        mid = max(coords[1:-1], key=lambda p: point_segment_distance_m((p[0], p[1]), (a[0], a[1]), (b[0], b[1])))
        line = [line[0], list(mid), line[-1]]
    leg: dict[str, Any] = {"mode": mode, "km": round(length, 1),
                           "minutes": None if minutes is None else round(minutes, 1),
                           "coordinates": _round(line)}
    if label:
        leg["label"] = label
    return leg


def make_route(legs: list[dict[str, Any]]) -> dict[str, Any]:
    """区間の列から経路。minutes はトラック区間の合計（電車・船・飛行機の時間は含めない）。"""
    return {"km": round(sum(l["km"] for l in legs), 1),
            "truck_minutes": round(sum(l["minutes"] or 0 for l in legs if l["mode"] == "truck"), 1),
            "legs": legs}


def is_two_point_line(route: dict[str, Any]) -> bool:
    """どれかの区間が直線の 2 点だけでできているか（G42）。"""
    return any(len(l["coordinates"]) <= 2 for l in route["legs"])


# ---------------------------------------------------------------- OSRM

class OsrmClient:
    """OSRM の route サービス。要求の間隔と再試行を守る。sleep・clock・urlopen は試験で差し替える。"""

    def __init__(self, min_interval: float = 1.1, retries: int = 3, retry_wait: float = 5.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 urlopen: Callable[..., Any] = urllib.request.urlopen) -> None:
        self.min_interval, self.retries, self.retry_wait = min_interval, retries, retry_wait
        self.sleep, self.clock, self.urlopen = sleep, clock, urlopen
        self.requests = 0
        self._last = -math.inf
        self._lock = threading.Lock()

    def _wait(self) -> None:
        with self._lock:
            gap = self.min_interval - (self.clock() - self._last)
            if gap > 0:
                self.sleep(gap)
            self._last = self.clock()

    def _get(self, url: str) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._wait()
            self.requests += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with self.urlopen(req, timeout=90) as res:
                    data = json.loads(res.read().decode("utf-8"))
                if data.get("code") in ("NoRoute", "NoSegment"):
                    raise NoRoute(f"経路がありません: {data.get('code')}")
                if data.get("code") != "Ok" or not data.get("routes"):
                    raise RouteError(f"OSRM の応答が Ok ではありません: {data.get('code')}")
                return data
            except NoRoute:
                raise
            except urllib.error.HTTPError as e:
                try:
                    code = json.loads(e.read().decode("utf-8")).get("code")
                except Exception:  # noqa: BLE001
                    code = None
                if code in ("NoRoute", "NoSegment"):
                    raise NoRoute(f"経路がありません: {code}") from e
                last = e
            except (urllib.error.URLError, OSError, ValueError, KeyError, RouteError) as e:
                last = e
            if attempt < self.retries:
                self.sleep(self.retry_wait)
        raise RouteError(f"{self.retries}回失敗しました ({last})")

    def route(self, a: tuple[float, float], b: tuple[float, float]) -> dict[str, Any]:
        """a→b の車の経路 {km, minutes, coordinates（overview=full）}。失敗は RouteError。"""
        url = (f"{OSRM_BASE}/route/v1/driving/{a[0]:.6f},{a[1]:.6f};{b[0]:.6f},{b[1]:.6f}"
               "?overview=full&geometries=geojson")
        r = self._get(url)["routes"][0]
        return {"km": r["distance"] / 1000, "minutes": r["duration"] / 60, "coordinates": r["geometry"]["coordinates"]}

    def chain(self, points: list[tuple[float, float]]) -> list[dict[str, Any]]:
        """points を順に通る 1 回の問い合わせで、隣り合う 2 点ごとの区間 {km, minutes, coordinates} を返す
        （第一/最終区間のように短い区間をまとめて取り、要求の数を減らすために使う）。"""
        coords = ";".join(f"{p[0]:.6f},{p[1]:.6f}" for p in points)
        url = (f"{OSRM_BASE}/route/v1/driving/{coords}"
               "?overview=false&steps=true&geometries=geojson&continue_straight=false")
        r = self._get(url)["routes"][0]
        if len(r["legs"]) != len(points) - 1:
            raise RouteError("応答の区間の数が要求と合いません")
        out = []
        for leg in r["legs"]:
            line: list[list[float]] = []
            for step in leg["steps"]:
                for c in step["geometry"]["coordinates"]:
                    if not line or line[-1] != c:
                        line.append(c)
            out.append({"km": leg["distance"] / 1000, "minutes": leg["duration"] / 60, "coordinates": line})
        return out


_ON_DEMAND_CLIENT = OsrmClient(min_interval=1.0, retries=2, retry_wait=2.0)


def cache_dir(base: Path | None = None) -> Path:
    return (base or data_dir()) / "routes" / "cache"


def _cache_name(origin: tuple[float, float], dest: tuple[float, float]) -> str:
    return f"{origin[0]:.4f}_{origin[1]:.4f}__{dest[0]:.4f}_{dest[1]:.4f}.json"


def route_on_demand(origin_lonlat: tuple[float, float], dest_lonlat: tuple[float, float], *,
                    base: Path | None = None, client: OsrmClient | None = None,
                    tolerance_m: float = 50.0) -> dict[str, Any] | None:
    """OSRM の道路の経路 {km, minutes, coordinates, cached}。同じ組は cache から返し、二度取らない。
    失敗したら None（呼ぶ側が直線の弧に戻して「経路を取得できませんでした」と出す）。"""
    cdir = cache_dir(base)
    path = cdir / _cache_name(origin_lonlat, dest_lonlat)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return {**json.load(f), "cached": True}
        except (OSError, ValueError):
            pass  # 壊れた cache は取り直す
    try:
        r = (client or _ON_DEMAND_CLIENT).route(origin_lonlat, dest_lonlat)
    except Exception:  # 通信・応答の失敗はすべて None にする
        return None
    result = {"km": round(r["km"], 1), "minutes": round(r["minutes"], 1),
              "coordinates": _round(simplify(r["coordinates"], tolerance_m))}
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    except OSError:
        pass  # 保存できなくても経路は返す
    return {**result, "cached": False}


def fallback_arc(origin_lonlat: tuple[float, float], dest_lonlat: tuple[float, float]) -> dict[str, Any]:
    """取得に失敗したときの直線の弧（大圏）。画面では「経路を取得できませんでした」と出す。"""
    return {"km": round(haversine_m(*origin_lonlat, *dest_lonlat) / 1000, 1), "minutes": None,
            "coordinates": great_circle(origin_lonlat, dest_lonlat), "fallback": True}


# ---------------------------------------------------------------- 読み込み

def routes_dir(base: Path | None = None) -> Path:
    return (base or data_dir()) / "routes"


def load_route_file(name: str, base: Path | None = None) -> dict[str, Any]:
    with open(routes_dir(base) / name, encoding="utf-8") as f:
        return json.load(f)
