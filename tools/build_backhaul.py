"""物流センサス 2021（3日間調査）表V-1-1 から、都道府県間の行きと帰りの差を計算する。

入力: data/stats/census2021_t11-050101.xlsx（e-Stat 統計表 ID 000040041500）
出力: data/stats/backhaul_2021.json
出典: 国土交通省「全国貨物純流動調査（物流センサス）」2021 年 3 日間調査 表V-1-1 を加工して作成
（e-Stat、政府標準利用規約 第2.0版）。xlsx は標準ライブラリで読む。
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "data" / "stats" / "census2021_t11-050101.xlsx"
OUT = ROOT / "data" / "stats" / "backhaul_2021.json"
POINTS = ROOT / "data" / "geo" / "municipality_points.json"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

# 使う表（ブックの中のシート名）
MODES = {"truck": "営業用トラック（小計）", "ferry": "フェリー", "rail": "鉄道コンテナ", "all": "合計"}

PREFS = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
         "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
         "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
         "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
         "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]
# 県庁所在地の市（東京都は新宿区）。政令市は区の代表点の平均を使う
CAPITALS = ["札幌市", "青森市", "盛岡市", "仙台市", "秋田市", "山形市", "福島市", "水戸市", "宇都宮市", "前橋市",
            "さいたま市", "千葉市", "新宿区", "横浜市", "新潟市", "富山市", "金沢市", "福井市", "甲府市", "長野市",
            "岐阜市", "静岡市", "名古屋市", "津市", "大津市", "京都市", "大阪市", "神戸市", "奈良市", "和歌山市",
            "鳥取市", "松江市", "岡山市", "広島市", "山口市", "徳島市", "高松市", "松山市", "高知市", "福岡市",
            "佐賀市", "長崎市", "熊本市", "大分市", "宮崎市", "鹿児島市", "那覇市"]
MIN_KM = 200


def read_sheets(path: Path) -> dict[str, list[list[str]]]:
    z = zipfile.ZipFile(path)
    shared = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t"))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    out = {}
    for s in wb.find("m:sheets", NS):
        sheet = ET.fromstring(z.read("xl/" + target[s.get(rid)].lstrip("/").removeprefix("xl/")))
        rows = []
        for r in sheet.find("m:sheetData", NS):
            vals = []
            for c in r:
                v = c.find("m:v", NS)
                x = v.text if v is not None else ""
                vals.append(shared[int(x)] if c.get("t") == "s" else x)
            rows.append(vals)
        out[s.get("name")] = rows
    return out


def matrix(rows: list[list[str]]) -> dict[str, dict[str, float]]:
    header = rows[1]
    cols = {i: h for i, h in enumerate(header) if h in PREFS}
    m: dict[str, dict[str, float]] = {}
    for r in rows:
        if r and r[0] in PREFS:
            m[r[0]] = {cols[i]: float(r[i] or 0) for i in cols if i < len(r)}
    assert len(m) == 47 and all(len(v) == 47 for v in m.values()), "47×47 になっていません"
    return m


def check_totals(rows: list[list[str]], m: dict[str, dict[str, float]]) -> float:
    """行の合計と表の行計の差の最大（トン）を返す。行計の列は「合計」の見出しで探す。"""
    header = rows[1]
    total_col = next((i for i, h in enumerate(header) if "計" in h and h not in PREFS), None)
    worst = 0.0
    if total_col is None:
        return float("nan")
    for r in rows:
        if r and r[0] in PREFS and total_col < len(r) and r[total_col]:
            worst = max(worst, abs(sum(m[r[0]].values()) - float(r[total_col])))
    return worst


def capitals() -> dict[str, tuple[float, float]]:
    pts = json.load(open(POINTS, encoding="utf-8"))["points"]
    out = {}
    for pref, cap in zip(PREFS, CAPITALS):
        hits = [p for p in pts.values() if p["pref"] == pref and (p["name"] == cap or p["name"].startswith(cap) and cap.endswith("市") and p["name"][len(cap):].endswith("区"))]
        assert hits, f"{pref} の県庁所在地が見つかりません"
        out[pref] = (sum(p["lon"] for p in hits) / len(hits), sum(p["lat"] for p in hits) / len(hits))
    return out


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def pairs(m: dict[str, dict[str, float]], cap: dict[str, tuple[float, float]]) -> list[dict]:
    rows = []
    for a_i, a in enumerate(PREFS):
        for b in PREFS[a_i + 1:]:
            ab, ba = m[a][b], m[b][a]
            big, small = max(ab, ba), min(ab, ba)
            if big <= 0:
                continue
            frm, to = (a, b) if ab >= ba else (b, a)
            rows.append({"from": frm, "to": to, "go_t": round(big, 1), "back_t": round(small, 1),
                         "ratio": round(small / big, 3), "shortfall_t": round(big - small, 1),
                         "km": round(km(cap[a], cap[b]), 1)})
    rows.sort(key=lambda r: -r["shortfall_t"])
    return rows


def main() -> None:
    sheets = read_sheets(XLSX)
    cap = capitals()
    result = {
        "source": "国土交通省「全国貨物純流動調査（物流センサス）」2021 年 3 日間調査 表V-1-1 都道府県間流動量（代表輸送機関別）重量（e-Stat 統計表 ID 000040041500）を加工して作成",
        "terms": "e-Stat 利用規約（政府標準利用規約 第2.0版 準拠）",
        "unit": "トン（2021 年 10 月 19〜21 日の 3 日間の出荷。年間換算ではない）",
        "distance": "県庁所在地の市（東京都は新宿区、政令市は区の代表点の平均）どうしの直線距離",
        "capitals": {p: [round(c[0], 4), round(c[1], 4)] for p, c in cap.items()},
        "modes": {},
    }
    for key, name in MODES.items():
        rows = sheets[name]
        m = matrix(rows)
        inter = sum(m[a][b] for a in PREFS for b in PREFS if a != b)
        pr = pairs(m, cap)
        short = sum(r["shortfall_t"] for r in pr)
        far = sum(r["shortfall_t"] for r in pr if r["km"] >= MIN_KM)
        balance = {p: round(sum(m[p][q] for q in PREFS if q != p) - sum(m[q][p] for q in PREFS if q != p), 1)
                   for p in PREFS}
        result["modes"][key] = {
            "table": name,
            "interpref_t": round(inter, 1),
            "shortfall_total_t": round(short, 1),
            "shortfall_far_t": round(far, 1),
            "row_total_max_diff_t": round(check_totals(rows, m), 3),
            "balance_out_minus_in_t": balance,
            "pairs": pr,
        }
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    t = result["modes"]["truck"]
    print("営業用トラック 県をまたぐ量", t["interpref_t"], "不足量の合計", t["shortfall_total_t"],
          "うち 200km 以上", t["shortfall_far_t"], "行計との差の最大", t["row_total_max_diff_t"])
    for r in [r for r in t["pairs"] if r["km"] >= MIN_KM][:5]:
        print(r)
    print("距離を問わない上位", [(r["from"], r["to"], r["shortfall_t"]) for r in t["pairs"][:3]])
    print("東京の出入の差", t["balance_out_minus_in_t"]["東京都"], "神奈川", t["balance_out_minus_in_t"]["神奈川県"])


if __name__ == "__main__":
    main()
