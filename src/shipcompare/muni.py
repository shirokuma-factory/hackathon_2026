"""市区町村の推定と物流の集積（SPEC §18）。通信はしない。データは data/ の読み込みだけ。

- 18.1 県の営業用トラックの出荷 Out_i・入荷 In_i（物流センサス 2021）を、県内の市区町村に重みで分けた推定
- 18.2 道路貨物運送業（中分類 44）と倉庫業（47）の集積（事業所数・従業者数・面積あたり）
- 点がどの市区町村に入るかの判定（OSM の点の集計に使う）

秘匿・数字なしの値は 0 にしない。None のまま持ち、理由を status に残す。
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .tariffs import data_dir

# ---------------------------------------------------------------- 都道府県（backhaul_2021.json と同じ名前）
PREFS = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
         "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
         "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
         "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
         "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]
PREF_CODE = {p: f"{i + 1:02d}" for i, p in enumerate(PREFS)}

WHOLESALE = ("50", "51", "52", "53", "54", "55")  # 卸売業の中分類。出荷の重みには使わない（本社機能が都心に偏るため）
EARTH_RADIUS_KM = 6371.0088

FORMULA = (
    "出荷の重み w_out(m) = 市区町村 m の（製造業 + 道路貨物運送業 + 倉庫業）の従業者数 ÷ 県全体の同じ合計（県の行の値）。"
    "入荷の重み w_in(m) = 市区町村 m の人口 ÷ 県全体の人口（県の行の値）。"
    "卸売業は本社機能が都心に偏るため重みに入れない（2026-11-04 の本線の判断）。"
    "従業者数が秘匿などで重みを出せない市区町村は None（灰色）にし、その分は配らずに未配分として県ごとに記録する。"
    "推定の差 B(m) = Out_i × w_out(m) − In_i × w_in(m)（正は出荷が多い側）。"
    "Out_i = Σ_{j≠i} F(i→j)、In_i = Σ_{j≠i} F(j→i)。F は物流センサス 2021 表V-1-1 の営業用トラックの都道府県間の量（トン、3 日間）。"
)
DISCLAIMER = "推定です。公開の統計に市区町村単位の流動はありません。県の値を業種の従業者数と人口で分けた目安です"


# ---------------------------------------------------------------- 読み込み
def _load(rel: str, base: Path | None = None) -> Any:
    with open((base or data_dir()) / rel, encoding="utf-8") as f:
        return json.load(f)


def load_municipalities(base: Path | None = None) -> list[dict[str, Any]]:
    """行政区域の多角形を [{code, pref, name, polys}] にする。コードの無い所属未定地は除く。"""
    out = []
    for f in _load("geo/municipalities_N03-21_s0010.geojson", base)["features"]:
        p = f["properties"]
        code = p.get("N03_007")
        if not code:
            continue
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        out.append({"code": code, "pref": p["N03_001"], "name": (p.get("N03_003") or "") + (p.get("N03_004") or ""),
                    "polys": polys})
    return out


# ---------------------------------------------------------------- 幾何
def ring_area_km2(ring: list[list[float]]) -> float:
    """球面上の閉じた輪の面積（Chamberlain–Duquette の式、符号つき）。経緯度は度。"""
    s = 0.0
    n = len(ring)
    for k in range(n - 1):
        lon1, lat1 = math.radians(ring[k][0]), math.radians(ring[k][1])
        lon2, lat2 = math.radians(ring[k + 1][0]), math.radians(ring[k + 1][1])
        s += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))
    return s * EARTH_RADIUS_KM ** 2 / 2


def polygons_area_km2(polys: list[list[list[list[float]]]]) -> float:
    """多角形の集まり（外周から穴を引く）の面積 km²。球面上の面積で、地球を半径 6371.0088 km の球とみなす。"""
    total = 0.0
    for poly in polys:
        total += abs(ring_area_km2(poly[0])) - sum(abs(ring_area_km2(h)) for h in poly[1:])
    return total


def _in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class Locator:
    """点がどの市区町村の中にあるかを調べる。外接する四角形で絞り、格子で候補を減らし、光線法で確かめる。"""

    CELL = 0.2  # 度

    def __init__(self, munis: list[dict[str, Any]]):
        self.items: list[tuple[str, list[tuple[tuple[float, float, float, float], list]]]] = []
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for m in munis:
            parts = []
            for poly in m["polys"]:
                xs = [p[0] for p in poly[0]]
                ys = [p[1] for p in poly[0]]
                parts.append(((min(xs), min(ys), max(xs), max(ys)), poly))
            idx = len(self.items)
            self.items.append((m["code"], parts))
            cells = set()
            for (x0, y0, x1, y1), _ in parts:
                for cx in range(int(x0 // self.CELL), int(x1 // self.CELL) + 1):
                    for cy in range(int(y0 // self.CELL), int(y1 // self.CELL) + 1):
                        cells.add((cx, cy))
            for c in cells:
                self.grid[c].append(idx)

    def locate(self, lon: float, lat: float) -> str | None:
        for idx in self.grid.get((int(lon // self.CELL), int(lat // self.CELL)), ()):
            code, parts = self.items[idx]
            for (x0, y0, x1, y1), poly in parts:
                if x0 <= lon <= x1 and y0 <= lat <= y1 and _in_ring(lon, lat, poly[0]):
                    if not any(_in_ring(lon, lat, hole) for hole in poly[1:]):
                        return code
        return None


# ---------------------------------------------------------------- 県のトラックの出荷・入荷（§18.1）
def pref_flows(base: Path | None = None) -> dict[str, dict[str, float]]:
    """backhaul_2021.json の営業用トラックの組から、県ごとの Out（県外への出荷）と In（県外からの入荷）をトンで返す。

    組の表は 2 県ずつ「多い向き go_t」と「少ない向き back_t」で持っているので、全 47×47 の行列に戻して足す。
    両方向とも 0 の組は表にないので 0 として足りる。
    """
    pairs = _load("stats/backhaul_2021.json", base)["modes"]["truck"]["pairs"]
    out = {p: 0.0 for p in PREFS}
    inn = {p: 0.0 for p in PREFS}
    for r in pairs:
        a, b = r["from"], r["to"]
        out[a] += r["go_t"]
        inn[b] += r["go_t"]
        out[b] += r["back_t"]
        inn[a] += r["back_t"]
    return {"out": out, "in": inn}


# ---------------------------------------------------------------- 経済センサスの値の読み取り
def _emp_est(area: dict | None, ind: str) -> tuple[int | None, int | None, str]:
    """（従業者数, 事業所数, 状態）。状態: ok / none（事業所がない）/ secret（事業所はあるが従業者数が秘匿）/ missing（表にない）。

    元の表は「事業所数」も「従業者数」も *** のとき、その産業の事業所がない場合。全国・県の合計と
    市区町村の合計を突き合わせて確かめた（倉庫業の従業者数: 県の合計 = 全国 = 233,950）。
    事業所数があって従業者数だけ数字がない場合は秘匿として None のまま持つ。
    """
    if area is None or ind not in area["v"]:
        return None, None, "missing"
    est, emp, _reg = area["v"][ind]
    if est is None and emp is None:
        return 0, 0, "none"
    if emp is None:
        return None, est, "secret"
    return emp, est, "ok"


def _sum_parts(area: dict | None, inds: tuple[str, ...]) -> tuple[int | None, int | None, str]:
    emps, ests, states = [], [], []
    for ind in inds:
        e, n, s = _emp_est(area, ind)
        emps.append(e)
        ests.append(n)
        states.append(s)
    if "missing" in states:
        return None, None, "missing"
    est = sum(n or 0 for n in ests)
    if "secret" in states:
        return None, est, "secret"
    return sum(emps), est, ("none" if all(s == "none" for s in states) else "ok")


def concentration(area: dict | None, ind: str, area_km2: float | None) -> dict[str, Any]:
    emp, est, state = _emp_est(area, ind)
    km2 = area_km2 if area_km2 and area_km2 > 0 else None
    return {
        "status": state, "establishments": est, "employees": emp,
        "establishments_per_km2": None if est is None or km2 is None else round(est / km2, 4),
        "employees_per_km2": None if emp is None or km2 is None else round(emp / km2, 3),
    }


# ---------------------------------------------------------------- 推定の本体
def allocate(weights: dict[str, float | None], total: float, denom: float | None = None) -> dict[str, float | None]:
    """total を weights ÷ denom の比で分ける。重みが None の市区町村は None（0 にしない）。

    denom は県全体の重みの合計（県の行の値）。省略すると、重みのある市区町村の合計を使う。
    denom を渡すと、重みが None の市区町村の分は配られず、total との差（未配分）として残る。
    """
    known = {k: v for k, v in weights.items() if v is not None}
    s = denom if denom else sum(known.values())
    out: dict[str, float | None] = {k: None for k in weights}
    if not s or s <= 0:
        return out
    for k, v in known.items():
        out[k] = total * v / s
    return out


def build_estimate(base: Path | None = None) -> dict[str, Any]:
    munis = load_municipalities(base)
    econ = _load("stats/muni_econ_2024.json", base)["areas"]
    popd = _load("stats/muni_pop_2020.json", base)["areas"]
    flows = pref_flows(base)
    by_pref: dict[str, list[dict]] = defaultdict(list)
    for m in munis:
        by_pref[m["pref"]].append(m)

    rows: dict[str, dict[str, Any]] = {}
    pref_info: dict[str, Any] = {}
    for pref in PREFS:
        ms = by_pref[pref]
        w_out: dict[str, float | None] = {}
        w_in: dict[str, float | None] = {}
        info: dict[str, dict] = {}
        for m in ms:
            code = m["code"]
            a = econ.get(code)
            parts = {"manufacturing": _emp_est(a, "E"), "trucking": _emp_est(a, "44"), "warehouse": _emp_est(a, "47")}
            states = [p[2] for p in parts.values()]
            if "missing" in states:
                wo, ostate = None, "missing"
            elif "secret" in states:
                wo, ostate = None, "secret"
            else:
                wo, ostate = float(sum(p[0] for p in parts.values())), "ok"
            pop = (popd.get(code) or {}).get("pop")
            wi = None if pop is None else float(pop)
            w_out[code], w_in[code] = wo, wi
            info[code] = {"out_state": ostate, "in_state": "ok" if wi is not None else "missing",
                          "mfg_trucking_warehouse_employees": None if wo is None else int(wo), "population": pop}
        out_i, in_i = flows["out"][pref], flows["in"][pref]
        # 分母は県の行の値（県全体の合計）。市区町村の合計ではない。重みが None の市区町村の分は未配分として残る
        pref_row = econ.get(f"{PREF_CODE[pref]}000")
        d_parts = [_emp_est(pref_row, "E")[0], _emp_est(pref_row, "44")[0], _emp_est(pref_row, "47")[0]]
        d_out = None if any(v is None for v in d_parts) else float(sum(d_parts))
        d_in = (popd.get(f"{PREF_CODE[pref]}000") or {}).get("pop")
        a_out, a_in = allocate(w_out, out_i, d_out), allocate(w_in, in_i, d_in)
        un_out = out_i - sum(v for v in a_out.values() if v is not None)
        un_in = in_i - sum(v for v in a_in.values() if v is not None)
        for m in ms:
            code = m["code"]
            o, i = a_out[code], a_in[code]
            reason = None
            if o is None or i is None:
                reason = _reason(info[code])
            rows[code] = {
                "name": m["name"], "pref": pref, **info[code],
                "out_t": None if o is None else round(o, 1), "in_t": None if i is None else round(i, 1),
                "balance_t": None if o is None or i is None else round(o - i, 1), "gray_reason": reason,
            }
        pref_info[pref] = {"out_t": round(out_i, 1), "in_t": round(in_i, 1),
                           "unallocated_out_t": round(un_out, 1), "unallocated_in_t": round(un_in, 1),
                           "n_municipalities": len(ms),
                           "n_gray": sum(1 for m in ms if rows[m["code"]]["balance_t"] is None)}

    # 集積（§18.2）。面積は行政区域（簡略化済み）の球面上の面積
    for m in munis:
        km2 = polygons_area_km2(m["polys"])
        r = rows[m["code"]]
        r["area_km2"] = round(km2, 2)
        a = econ.get(m["code"])
        r["trucking_44"] = concentration(a, "44", km2)
        r["warehousing_47"] = concentration(a, "47", km2)

    return {
        "source": "国土交通省「全国貨物純流動調査（物流センサス）」2021 年 3 日間調査 表V-1-1（営業用トラック）、"
                  "総務省統計局「令和6年経済センサス‐基礎調査」「令和2年国勢調査」（いずれも e-Stat）、"
                  "行政区域は国土数値情報（行政区域データ N03-21）を簡略化したもの、を加工して作成",
        "terms": "e-Stat 利用規約（政府標準利用規約 第2.0版 準拠）、国土数値情報 利用約款（商用可）。出典と加工の表示が必要",
        "kind": "推定です。公開の統計に市区町村単位の流動はない",
        "disclaimer": DISCLAIMER,
        "formula": FORMULA,
        "unit": "トン（物流センサス 2021 の 3 日間の量。年間換算ではない）",
        "area_method": "球面上の多角形面積（Chamberlain–Duquette の式、地球半径 6371.0088 km）。行政区域は簡略化されているので数 % ずれる",
        "status_notes": {
            "ok": "数字がある", "none": "その産業の事業所がない（表の事業所数・従業者数がともに ***。全国と県の合計との突き合わせで 0 と確認）",
            "secret": "事業所はあるが従業者数が秘匿（表で ***）。None のままにする",
            "missing": "表に該当する市区町村がない（北方領土、2024 年に区が再編された浜松市など）",
        },
        "prefectures": pref_info,
        "municipalities": rows,
    }


def _reason(info: dict) -> str:
    if info["out_state"] == "secret":
        return "従業者数が秘匿の産業があるため、出荷の重みを出せません"
    if info["out_state"] == "missing":
        return "経済センサス（令和6年）に対応する市区町村がありません（合併・区の再編など）"
    return "国勢調査（令和2年）に人口がありません"


def load_estimate(base: Path | None = None) -> dict[str, Any]:
    return _load("stats/muni_estimate.json", base)


def write_estimate(base: Path | None = None) -> Path:
    base = base or data_dir()
    result = build_estimate(base)
    path = base / "stats" / "muni_estimate.json"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    return path


if __name__ == "__main__":  # uv run python -m shipcompare.muni
    out = write_estimate()
    print(f"保存 {out.name} {out.stat().st_size / 1024:.0f}KB")
