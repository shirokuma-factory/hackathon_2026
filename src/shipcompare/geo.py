"""地図の画面で使う純粋な関数（SPEC §14.6）。通信はしない。データは data/geo の読み込みだけ。"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

from .estimate import kashikiri_candidates, kashikiri_fares
from .loadplan import LoadItem
from .tariffs import data_dir, load_ferry_only

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


# 離島・フェリー（SPEC §15.3）。OSRM の車の経路はフェリー航路を含むことがある。
# 運転時間に対する距離の平均速度がこの値より小さい着地を「フェリーを含む経路の可能性」とする
FERRY_MAX_AVG_KMH = 25


def is_ferry_route(km: float | None, minutes: float | None) -> bool:
    """平均速度（km ÷ 時間）が 25km/h 未満なら True（ちょうど 25km/h は False）。
    経路がない（None）、時間が 0 以下（出発地と同じ市区町村）は False。"""
    if km is None or minutes is None:
        return False
    k, m = Decimal(str(km)), Decimal(str(minutes))
    if m <= 0:
        return False
    return k * 60 < FERRY_MAX_AVG_KMH * m


_FERRY_ONLY_CACHE: dict[str, Any] = {}


def is_ferry_only_destination(dest_code: str | None) -> bool:
    """着地が道路で本州とつながっていないか（北海道・沖縄県の全域と、島しょの市町村の一覧。
    data/geo/ferry_only_destinations.json。洗い出し N2）。フェリー利用料は運賃と別の実費なので、貸切の額と日数を出さない。"""
    if not dest_code:
        return False
    data = _FERRY_ONLY_CACHE.get("data")
    if data is None:
        data = _FERRY_ONLY_CACHE["data"] = load_ferry_only()
    return dest_code[:2] in data["prefectures"] or dest_code in data["municipalities"]


def is_ferry_destination(dest_code: str | None, km: float | None, minutes: float | None) -> bool:
    """フェリーを含む経路か。着地が一覧の地域、または平均速度が 25km/h 未満のとき True。経路がない（km が None）ときは False。"""
    if km is None:
        return False  # 経路がない着地は別扱い（no_route）
    return is_ferry_only_destination(dest_code) or is_ferry_route(km, minutes)


def drive_info(origin_code: str, dest_code: str, base: Path | None = None) -> dict[str, Any] | None:
    """出発地から着地への {km, minutes, no_route, ferry, days}。着地のデータが無ければ None。
    経路なし・フェリーの可能性のときは days を None にする（運転日数と貸切の額を出さない）。"""
    d = load_osrm(origin_code, base).get(dest_code)
    if d is None:
        return None
    km, minutes = d.get("km"), d.get("minutes")
    ferry = is_ferry_destination(dest_code, km, minutes)
    no_route = km is None
    return {"km": km, "minutes": minutes, "no_route": no_route, "ferry": ferry,
            "days": None if (no_route or ferry) else drive_days(minutes)}


def fare_layer(origin_code: str, total_kg: float | None, tariff: dict[str, Any],
               codes: list[str] | None = None, base: Path | None = None,
               items: list[LoadItem] | None = None) -> list[int | None]:
    """codes の順に、貸切の目安額（OSRM の距離、載る最小の車格）。items があれば荷の形も見る（カードと同じ選び方）。
    経路なし・フェリーの可能性・200km未満・積載量超過は None。"""
    osrm = load_osrm(origin_code, base)
    out: list[int | None] = []
    for c in codes if codes is not None else sorted(load_points(base)):
        d = osrm.get(c) or {}
        if is_ferry_destination(c, d.get("km"), d.get("minutes")):
            out.append(None)
            continue
        kf = kashikiri_for_distance(d.get("km"), total_kg, tariff, items)
        out.append(kf[1] if kf else None)
    return out


def kashikiri_for_distance(km: float | None, total_kg: float | None, tariff: dict[str, Any],
                           items: list[LoadItem] | None = None) -> tuple[str, int] | None:
    """道路距離と合計重量から (車格, 額)。載る最小の車格。total_kg が None なら 2t とする。
    km が None、基本距離（200km）未満、最大積載量を超えるときは None。式は estimate.kashikiri_fares と共通。
    items（荷物の行）があれば、荷台に載るかも見て、載る候補のうち最も安いものを返す（estimate_kashikiri と同じ選び方）。
    台数が複数の候補のときは車格を '10t×2' の形で返す。"""
    if km is None or km < tariff["base_km"]:
        return None
    if items is not None:
        usable = [c for c in kashikiri_candidates(Decimal(str(km)), items, Decimal(str(total_kg or 0)), tariff)
                  if c["amount"] is not None]
        if not usable:
            return None
        c = min(usable, key=lambda x: (x["amount"], x["max_kg"]))
        return (c["class"] if c["vehicles"] == 1 else f"{c['class']}×{c['vehicles']}"), c["amount"]
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


def demo_route_for(origin_code: str | None, dest_code: str | None) -> str | None:
    """デモの区間の組（DEMO_ROUTES の発地と着地が両方一致）のときだけ区間 ID を返す。それ以外は None。
    貸切の額は、デモの区間でもそうでなくても OSRM の道路距離で出す。区間 ID は宅配・カゴ台車の代表区間の選択に使う。"""
    for spec in DEMO_ROUTES.values():
        if spec["origin"] == origin_code and spec["dest"] == dest_code:
            return spec["route_id"]
    return None


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


# ---------------------------------------------------------------- 貨物駅・港（SPEC §15.5）

def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """2 点の直線（大圏）距離 km。道路や線路の長さではない。"""
    r, rad = 6371.0088, math.pi / 180
    dlat, dlon = (lat2 - lat1) * rad, (lon2 - lon1) * rad
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1 * rad) * math.cos(lat2 * rad) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def load_hubs(kind: str, base: Path | None = None) -> list[dict[str, Any]]:
    """OSM から取得した拠点。kind は 'freight_stations' / 'ferry_terminals' / 'ferry_routes'。"""
    return _read(geo_dir(base) / f"osm_{kind}.json")["items"]


def nearest_hub(lon: float, lat: float, hubs: list[dict[str, Any]]) -> tuple[int, float] | None:
    """最も近い拠点の (hubs の番号, 直線距離 km)。拠点が空なら None。同じ距離なら番号の小さい方。"""
    best: tuple[int, float] | None = None
    for i, h in enumerate(hubs):
        d = haversine_km(lon, lat, h["lon"], h["lat"])
        if best is None or d < best[1]:
            best = (i, d)
    return best


PASSENGER_ONLY_WORDS = ("観光", "桟橋", "渡船", "遊覧", "クルーズ", "水上バス")


def major_terminals(terminals: list[dict[str, Any]], routes: list[dict[str, Any]], radius_km: float = 5.0,
                    exclude: tuple[str, ...] = PASSENGER_ONLY_WORDS) -> list[int]:
    """航路（30km 以上）の端から radius_km 以内にある港の番号。地図の縮小時と最寄りの港の計算に使う。
    名前に観光・桟橋・渡船などを含むものは除く（名前による目安で、車両を積めるかは確かめていない）。"""
    ends = []
    for r in routes:
        if r.get("path"):
            ends += [r["path"][0], r["path"][-1]]
    out = []
    for i, t in enumerate(terminals):
        if any(w in (t.get("name") or "") for w in exclude):
            continue
        if any(haversine_km(t["lon"], t["lat"], e[0], e[1]) <= radius_km for e in ends):
            out.append(i)
    return out


# フェリーのカードの「最寄りの港」の候補（貨物を載せる長距離航路の端の港だけ）。
# 旅客・遊覧・イベント用の船着場が航路の端の近くにあるため、名前でも除く（名前による目安）。
FERRY_PORT_MIN_ROUTE_KM = 100.0
FERRY_PORT_RADIUS_KM = 3.0
NON_FREIGHT_PORT_WORDS = PASSENGER_ONLY_WORDS + ("客船", "竹芝", "ウォーターズ", "ユニバーサル", "船着場", "水上バス", "乗り場", "のりば")
# 航路の端の近くには駅名や建物名の点も多いので、港・ターミナルを名乗る名前だけを残す
FERRY_PORT_NAME_WORDS = ("フェリー", "港", "ターミナル", "埠頭", "さんふらわあ")


def freight_ferry_ports(terminals: list[dict[str, Any]], routes: list[dict[str, Any]]) -> list[int]:
    """100km 以上の航路の端から 3km 以内にある港の番号。名前に旅客・遊覧などの語を含むものは除き、
    港・ターミナルなどを名乗る名前だけを残す（名前による目安で、車両を積めるかは確かめていない）。"""
    long_routes = [r for r in routes if r.get("km", 0) >= FERRY_PORT_MIN_ROUTE_KM]
    ok = major_terminals(terminals, long_routes, radius_km=FERRY_PORT_RADIUS_KM, exclude=NON_FREIGHT_PORT_WORDS)
    return [i for i in ok if any(w in (terminals[i].get("name") or "") for w in FERRY_PORT_NAME_WORDS)]
