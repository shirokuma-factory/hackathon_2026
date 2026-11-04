"""国土数値情報の P31 物流拠点（2013 年）と C28 空港（2021 年）を取得する。

P31 は利用約款で「非商用」、複製物の再配布ができない。そのため
  - 取得した ZIP と変換後の JSON は data/raw/p31/ に置く（data/raw/ は .gitignore。コミットしない）
  - このスクリプト（取得の台本）だけをリポジトリに置く
C28 は「商用可」なので、変換した点だけを data/geo/c28_airports_2021.json に置いてコミットしてよい。

出典: 国土交通省「国土数値情報（物流拠点データ P31-13）」「国土数値情報（空港データ C28-21）」を加工して作成
  P31: https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P31.html
  C28: https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-C28-2021.html
利用約款: https://nlftp.mlit.go.jp/ksj/other/agreement_02.html

使い方: python tools/fetch_p31.py [--no-download]
標準ライブラリだけで ZIP・shapefile（点）・dbf を読む。
"""

from __future__ import annotations

import argparse
import json
import struct
import time
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_P31 = ROOT / "data" / "raw" / "p31"
GEO = ROOT / "data" / "geo"
USER_AGENT = "shipcompare-hackathon/0.3 (non-commercial demo)"
P31_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/P31/P31-13/P31-13_{pref:02d}.zip"
C28_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/C28/C28-21/C28-21_GML.zip"

# 種別コード（https://nlftp.mlit.go.jp/ksj/gml/codelist/DistributionCenterCd.html）
KIND = {1: "コンテナターミナル", 2: "航空貨物ターミナル", 3: "鉄道貨物駅", 4: "保税地域",
        5: "トラックターミナル", 6: "倉庫", 7: "卸売市場"}
OPERATOR = {1: "公営", 2: "民営", 3: "その他"}


def download(url: str, dest: Path) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as res:
        data = res.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return len(data)


def read_dbf(b: bytes, encoding: str = "cp932") -> list[dict]:
    n, header_len, rec_len = struct.unpack("<xxxxIHH", b[:12])
    fields, i = [], 32
    while b[i] != 0x0D:
        fields.append((b[i:i + 11].split(b"\0")[0].decode("ascii"), b[i + 16]))
        i += 32
    rows = []
    for k in range(n):
        rec = b[header_len + k * rec_len: header_len + (k + 1) * rec_len]
        pos, row = 1, {}
        for name, ln in fields:
            row[name] = rec[pos:pos + ln].decode(encoding, errors="replace").strip()
            pos += ln
        rows.append(row)
    return rows


def read_shp_points(b: bytes) -> list[tuple[float, float]]:
    """shapefile の点（形状型 1）だけを読む。x が経度、y が緯度。"""
    out, pos = [], 100
    while pos + 8 <= len(b):
        (length_words,) = struct.unpack(">xxxxi", b[pos:pos + 8])
        body = b[pos + 8: pos + 8 + length_words * 2]
        (stype,) = struct.unpack("<i", body[:4])
        if stype == 1:
            x, y = struct.unpack("<dd", body[4:20])
            out.append((x, y))
        else:
            out.append((float("nan"), float("nan")))
        pos += 8 + length_words * 2
    return out


def num(s: str) -> int | float | None:
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f == int(f) else f


def convert_p31() -> list[dict]:
    items = []
    for pref in range(1, 48):
        z = zipfile.ZipFile(RAW_P31 / f"P31-13_{pref:02d}.zip")
        names = z.namelist()
        dbf = next(n for n in names if n.endswith(".dbf"))
        shp = next(n for n in names if n.endswith(".shp"))
        rows, pts = read_dbf(z.read(dbf)), read_shp_points(z.read(shp))
        assert len(rows) == len(pts), f"{pref}: 属性と点の数が合いません"
        for r, (lon, lat) in zip(rows, pts):
            kind = num(r["P31_002"])
            items.append({
                "name": r["P31_001"], "kind": kind, "kind_name": KIND.get(kind, "不明"),
                "class": num(r["P31_003"]), "pref": f"{int(num(r['P31_004'])):02d}", "address": r["P31_005"],
                "operator": OPERATOR.get(num(r["P31_006"]), "不明"), "year": num(r["P31_007"]),
                "scale": num(r["P31_008"]), "note": r["P31_009"],
                "lon": round(lon, 6), "lat": round(lat, 6),
            })
    return items


def convert_c28(zip_path: Path) -> list[dict]:
    """空港ごとに 1 件。位置は空港の基準点（AirportReferencePoint）。"""
    z = zipfile.ZipFile(zip_path)
    air = json.loads(z.read("UTF-8/C28-21_Airport.geojson"))["features"]
    ref = {f["properties"]["C28_000"]: f["geometry"]["coordinates"]
           for f in json.loads(z.read("UTF-8/C28-21_AirportReferencePoint.geojson"))["features"]}
    out = []
    for f in air:
        p = f["properties"]
        pt = ref.get(p.get("C28_101", "").lstrip("#"))
        if pt is None:
            continue
        out.append({"name": p["C28_005"], "status": p["C28_004"], "category": p["C28_008"],
                    "muni": p["C28_001"], "open": p["C28_009"], "close": p["C28_010"],
                    "lon": round(pt[0], 6), "lat": round(pt[1], 6)})
    return out


def write_json(path: Path, obj) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(f"保存 {path.name} {path.stat().st_size / 1024:.0f}KB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()
    RAW_P31.mkdir(parents=True, exist_ok=True)
    if not args.no_download:
        total = 0
        for pref in range(1, 48):
            url = P31_URL.format(pref=pref)
            size = download(url, RAW_P31 / f"P31-13_{pref:02d}.zip")
            total += size
            print(f"取得 {url} {size:,} バイト")
            time.sleep(1)
        size = download(C28_URL, RAW_P31.parent / "c28" / "C28-21_GML.zip")
        print(f"取得 {C28_URL} {size:,} バイト（P31 の合計 {total:,} バイト）")
    items = convert_p31()
    write_json(RAW_P31 / "p31_points.json", {
        "source": "国土交通省「国土数値情報（物流拠点データ P31-13）」（2013 年）を加工して作成",
        "terms": "国土数値情報 利用約款（非商用）。複製物の再配布不可。リポジトリにコミットしない",
        "fetched_on": date.today().isoformat(), "kinds": KIND, "items": items})
    kinds: dict[str, int] = {}
    for it in items:
        kinds[it["kind_name"]] = kinds.get(it["kind_name"], 0) + 1
    print("P31 件数", len(items), kinds)
    airports = convert_c28(RAW_P31.parent / "c28" / "C28-21_GML.zip")
    write_json(GEO / "c28_airports_2021.json", {
        "source": "国土交通省「国土数値情報（空港データ C28-21）」（2021 年度）を加工して作成",
        "terms": "国土数値情報 利用約款（商用可）。出典と加工の表示が必要",
        "fetched_on": date.today().isoformat(), "items": airports})
    print("C28 空港", len(airports))


if __name__ == "__main__":
    main()
