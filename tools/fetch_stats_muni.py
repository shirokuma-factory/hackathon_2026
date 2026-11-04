"""市区町村別の経済センサスと国勢調査の人口を e-Stat から取得し、小さな JSON にまとめる。

e-Stat の画面の内部呼び出しを再現しています。公式 API（アプリケーション ID が必要）ではありません。再取得は控え、作り直しは --no-download で行ってください。

取得する表（どちらも e-Stat の統計表、アプリケーション ID は不要。手順は tools/estat_dbview.py）:
  - 令和6年経済センサス‐基礎調査 甲調査 4-2 産業(小分類)、経営組織(4区分)別民営事業所数、従業者数及び常用雇用者数
    （統計表 ID 0004040080）。産業は 全産業・製造業・卸売業(50,51)・道路貨物運送業(44)・倉庫業(47) だけを選ぶ
  - 令和2年国勢調査 人口等基本集計 1-1-1 男女別人口－全国，都道府県，市区町村（統計表 ID 0003445078）。男女は総数だけ
生の CSV は data/raw/estat/（gitignore）に置く。コミットするのは抽出した JSON（data/stats/）だけ。
出典: 総務省統計局「令和6年経済センサス‐基礎調査」「令和2年国勢調査」（e-Stat）を加工して作成。
利用規約: 政府標準利用規約（第2.0版）。出典と加工の表示が必要。

使い方:
  python tools/fetch_stats_muni.py              # 取得して JSON を作る
  python tools/fetch_stats_muni.py --no-download  # data/raw/estat/ の CSV から JSON を作り直す
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import estat_dbview  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "estat"
OUT = ROOT / "data" / "stats"

ECON_SID = "0004040080"
POP_SID = "0003445078"
# 産業のコード: AR 全産業（S_公務を除く）、E 製造業、44 道路貨物運送業、47 倉庫業、
# 50〜55 は卸売業の中分類（大分類 I は小売業を含むので使わない）。足し合わせは src/shipcompare/muni.py で行う。
INDUSTRIES = ["AR", "E", "50", "51", "52", "53", "54", "55", "44", "47"]
ITEMS = {"102-2024": "est", "103-2024": "emp", "105-2024": "reg"}  # 事業所数、従業者数、うち常用雇用者
SOURCE_ECON = "総務省統計局「令和6年経済センサス‐基礎調査 甲調査（民営事業所） 4-2」（e-Stat 統計表 ID 0004040080）を加工して作成"
SOURCE_POP = "総務省統計局「令和2年国勢調査 人口等基本集計 1-1-1 男女別人口」（e-Stat 統計表 ID 0003445078）を加工して作成"
TERMS = "政府標準利用規約（第2.0版）。出典と加工の表示が必要"


def read_csv_rows(raw: bytes) -> tuple[list[str], list[list[str]]]:
    """VALUE の行より後ろだけを返す（見出し行, 値の行）。Shift-JIS（CP932）。"""
    text = raw.decode("cp932")
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == '"VALUE"')
    rows = list(csv.reader(io.StringIO("\n".join(lines[start + 1:]))))
    return rows[0], rows[1:]


def to_number(s: str) -> float | None:
    """数字なら数、それ以外（X 秘匿、*** 数字なし、- など）は None。0 にはしない。"""
    s = s.strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def extract_econ(raw: bytes) -> dict:
    head, rows = read_csv_rows(raw)
    ix = {h: i for i, h in enumerate(head)}
    areas: dict[str, dict] = {}
    names: dict[str, str] = {}
    for r in rows:
        item = ITEMS.get(r[ix["tab_code"]])
        if item is None:
            continue
        code, ind = r[ix["area_code"]], r[ix["cat01_code"]]
        a = areas.setdefault(code, {"name": r[ix["地域"]], "v": {}, "m": {}})
        names[ind] = r[ix["産業小分類"]]
        slot = a["v"].setdefault(ind, [None, None, None])
        mark = a["m"].setdefault(ind, [None, None, None])
        k = ["est", "emp", "reg"].index(item)
        val = to_number(r[ix["value"]])
        slot[k] = None if val is None else int(val)
        if val is None:
            mark[k] = r[ix["value"]].strip() or r[ix["annotation"]].strip() or "?"
    for a in areas.values():
        a["m"] = {c: m for c, m in a["m"].items() if any(x is not None for x in m)}
    return {
        "source": SOURCE_ECON, "terms": TERMS, "fetched_on": date.today().isoformat(),
        "note": "雇用者のいない個人経営の事業所を除く。値の順は [事業所数, 従業者数, うち常用雇用者]。"
                "null は元の表の記号が数字でないもの（*** 数字が得られないもの、X 秘匿）で、m に元の記号を残す。ここでは 0 に直さない。"
                "事業所数も null のときは該当の事業所がない場合（全国の合計との突き合わせで確認）、事業所数があって従業者数だけ null のときは秘匿",
        "industries": names, "items": ["est", "emp", "reg"], "areas": areas,
    }


def extract_pop(raw: bytes) -> dict:
    head, rows = read_csv_rows(raw)
    ix = {h: i for i, h in enumerate(head)}
    areas = {}
    for r in rows:
        val = to_number(r[ix["value"]])
        areas[r[ix["area_code"]]] = {"name": r[ix["全国，都道府県，市区町村（2000年市区町村含む）"]],
                                    "pop": None if val is None else int(val)}
    return {"source": SOURCE_POP, "terms": TERMS, "fetched_on": date.today().isoformat(),
            "unit": "人（2020 年 10 月 1 日、男女総数）", "areas": areas}


def write_json(path: Path, obj: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(f"保存 {path.name} {path.stat().st_size / 1024:.0f}KB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true", help="data/raw/estat/ の CSV から JSON を作り直す")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    jobs = [
        (ECON_SID, "00200552", {"産業": set(INDUSTRIES), "経営組織": {"0"}}),
        (POP_SID, "00200521", {"男女": {"0"}}),
    ]
    for sid, stat_code, select in jobs:
        path = RAW / f"{sid}.csv"
        if not args.no_download:
            data, info = estat_dbview.download_csv(sid, select, stat_code)
            path.write_bytes(data)
            print(f"取得 {sid} {len(data):,} バイト {info['fileList'][0]['fileName']}")
    write_json(OUT / "muni_econ_2024.json", extract_econ((RAW / f"{ECON_SID}.csv").read_bytes()))
    write_json(OUT / "muni_pop_2020.json", extract_pop((RAW / f"{POP_SID}.csv").read_bytes()))


if __name__ == "__main__":
    main()
