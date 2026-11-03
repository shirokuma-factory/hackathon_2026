"""地図の画面で使う純粋な関数（SPEC §14.6）。通信はしない。データは data/geo の読み込みだけ。"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

from .estimate import kashikiri_fares
from .tariffs import data_dir

# 出発地（SPEC §14.2）。関東運輸局の標準的運賃しか持たないので関東の7か所に限る
ORIGINS: dict[str, str] = {
    "13104": "東京都新宿区",
    "14104": "神奈川県横浜市中区",
    "11107": "埼玉県さいたま市浦和区",
    "12101": "千葉県千葉市中央区",
    "08201": "茨城県水戸市",
    "09201": "栃木県宇都宮市",
    "10201": "群馬県前橋市",
}

# デモの区間（SPEC §14.3）。キーは「出発地コード-着地コード」
DEMO_ROUTES: dict[str, dict[str, str]] = {
    "13104-27128": {"route_id": "kanto_kansai", "origin": "13104", "dest": "27128"},
    "13104-04101": {"route_id": "kanto_tohoku", "origin": "13104", "dest": "04101"},
    "01101-13104": {"route_id": "sapporo_tokyo", "origin": "01101", "dest": "13104"},
}

# 着地の都府県コード（市区町村コードの先頭2桁）と区間 ID の対応（SPEC §14.5）
PREF_TO_ROUTE = {"27": "kanto_kansai", "04": "kanto_tohoku"}

# 日数の数え方（SPEC §14.5、厚生労働省の改善基準告示（トラック））
MAX_DRIVE_MIN_PER_DAY = 9 * 60  # 1日の運転は9時間まで
BREAK_EVERY_MIN = 4 * 60  # 連続運転4時間ごとに
BREAK_MIN = 30  # 30分の休憩

GRAY = [200, 200, 200, 140]
_LIGHT = (222, 235, 247)  # 小さい値（薄い青）
_DARK = (8, 48, 107)  # 大きい値（濃い青）。赤系は使わない
_ALPHA = 180  # OSM のタイルの上でも濃淡が見える濃さ


# ---------------------------------------------------------------- 読み込み

def _read(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def geo_dir(base: Path | None = None) -> Path:
    return (base or data_dir()) / "geo"


def load_points(base: Path | None = None) -> dict[str, dict[str, Any]]:
    """コードごとの代表点 {code: {name, pref, lon, lat}}。"""
    return _read(geo_dir(base) / "municipality_points.json")["points"]


def load_osrm(origin_code: str, base: Path | None = None) -> dict[str, dict[str, float | None]]:
    """出発地から各市区町村への {code: {km, minutes}}。経路がないものは両方 None。"""
    return _read(geo_dir(base) / f"osrm_{origin_code}.json")["destinations"]


def load_routes(base: Path | None = None) -> dict[str, dict[str, Any]]:
    """デモの区間の道路の線 {出発地-着地: {route_id, km, minutes, coordinates}}。"""
    return _read(geo_dir(base) / "routes.json")["routes"]


# ---------------------------------------------------------------- 代表点

def _ring_centroid(ring: list[list[float]]) -> tuple[float, float, float]:
    """閉じた輪の (面積の絶対値, 重心の経度, 重心の緯度)。面積 0 なら頂点の平均。"""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if a == 0:
        return 0.0, sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
    return abs(a) / 2, cx / (3 * a), cy / (3 * a)


def _inside_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def _inside_polygon(x: float, y: float, polygon: list[list[list[float]]]) -> bool:
    """外側の輪の中で、穴の中ではない。"""
    return _inside_ring(x, y, polygon[0]) and not any(_inside_ring(x, y, h) for h in polygon[1:])


def representative_point(geometry: dict[str, Any]) -> tuple[float, float]:
    """代表点 (lon, lat)。MultiPolygon は面積が最大の多角形の重心。
    重心が多角形の外（穴の中を含む）なら、その多角形の頂点のうち重心に最も近い点。"""
    kind = geometry["type"]
    if kind == "Polygon":
        polygons = [geometry["coordinates"]]
    elif kind == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        raise ValueError(f"未対応の形です: {kind}")
    polygon = max(polygons, key=lambda p: _ring_centroid(p[0])[0])
    _, cx, cy = _ring_centroid(polygon[0])
    if _inside_polygon(cx, cy, polygon):
        return cx, cy
    best = min(polygon[0], key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
    return best[0], best[1]


def municipality_name(props: dict[str, Any]) -> str:
    """属性から市区町村の名前。政令市・郡は N03_003 を前に付ける（例: 横浜市中区、〇〇郡△△町）。"""
    return (props.get("N03_003") or "") + (props.get("N03_004") or "")


# ---------------------------------------------------------------- 日数・額・色・区間

def drive_days(minutes: float | None) -> int | None:
    """運転時間（分）から日数の目安。1日の運転は9時間まで。連続4時間ごとの30分休憩は
    1日の運転時間に入れない（休憩は運転の合間に取るので日数は増えない）。
    9時間ちょうどは1日、9時間1分は2日。None は None。積込み・荷待ち・荷下ろしは含まない。"""
    if minutes is None:
        return None
    return max(1, math.ceil(minutes / MAX_DRIVE_MIN_PER_DAY))


def break_minutes(minutes: float | None) -> int | None:
    """運転時間に必要な休憩の合計（分）。連続4時間ごとに30分で、4時間ちょうどでは取らない。"""
    if minutes is None:
        return None
    return BREAK_MIN * max(0, math.ceil(minutes / BREAK_EVERY_MIN) - 1)


def kashikiri_for_distance(km: float | None, total_kg: float | None, tariff: dict[str, Any]) -> tuple[str, int] | None:
    """道路距離と合計重量から (車格, 額)。載る最小の車格。total_kg が None なら 2t とする。
    km が None、基本距離（200km）未満、最大積載量を超えるときは None。式は estimate.kashikiri_fares と共通。"""
    if km is None or km < tariff["base_km"]:
        return None
    fares = kashikiri_fares(Decimal(str(km)), Decimal(str(total_kg or 0)), tariff)
    if not fares:
        return None
    return fares[0]["class"], fares[0]["amount"]


def color_for(value: float | None, vmin: float, vmax: float) -> list[int]:
    """単色の濃淡 [r, g, b, a]。薄い=小さい値。None は灰色（最小の色にしない）。"""
    if value is None:
        return list(GRAY)
    t = 0.5 if vmax <= vmin else min(1.0, max(0.0, (value - vmin) / (vmax - vmin)))
    return [round(a + (b - a) * t) for a, b in zip(_LIGHT, _DARK)] + [_ALPHA]


def route_for(origin_code: str | None, dest_code: str | None) -> str | None:
    """着地に対応する区間 ID。大阪府→kanto_kansai、宮城県→kanto_tohoku、それ以外→other。
    出発地が関東の7か所でない、または着地が不正なら None（地図の対象外）。"""
    if origin_code not in ORIGINS or not dest_code or len(dest_code) != 5 or not dest_code.isdigit():
        return None
    return PREF_TO_ROUTE.get(dest_code[:2], "other")


CLASS_COUNT = 7


def quantile_edges(values: list[float], n: int = CLASS_COUNT) -> list[float]:
    """分位数で n 階級に分ける境界。先頭が最小、末尾が最大。同じ値の境界は 1 つにまとめる（階級が減る）。"""
    vals = sorted(values)
    if not vals:
        return []
    edges = [vals[0]]
    for k in range(1, n + 1):
        v = vals[min(len(vals) - 1, math.ceil(k * len(vals) / n) - 1)]
        if v > edges[-1]:
            edges.append(v)
    return edges if len(edges) > 1 else [vals[0], vals[0]]


def class_of(value: float, edges: list[float]) -> int:
    """edges の階級の番号。階級 i は (edges[i], edges[i+1]]（最初の階級は最小値を含む）。"""
    for i in range(1, len(edges)):
        if value <= edges[i]:
            return i - 1
    return len(edges) - 2


def class_color(index: int, n_classes: int) -> list[int]:
    """階級の色。薄い（小さい階級）から濃い（大きい階級）。"""
    return color_for(index, 0, n_classes - 1)
