"""荷待ちの計算の試験表 tests/data/waiting_vectors.json を waiting.py の出力から作る（SPEC §23.7、G82）。

入力の行と貼り付けの文字列は、このファイルの CASES に書く。期待値は waiting.py が返した rows / by_partner / errors / input_rows。
Python の試験（tests/test_v237_vectors.py）と、web/waiting.html の ?selftest=1（JS）の両方が、同じ表を通す。
使い方: uv run python tools/build_waiting_vectors.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import waiting  # noqa: E402

OUT = ROOT / "tests" / "data" / "waiting_vectors.json"


def row(**kw):
    base = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A運輸", "arrive": "09:00", "start": "10:00",
            "end": "", "handler": "", "method": "", "shipper_fault": "はい"}
    base.update(kw)
    return base


def example_rows() -> list[dict]:
    """web/own.js と web/waiting.html の「例を入れる」と同じ 11 行（8 行は 52 分、2 行は 75 分、1 行は指定の到着時刻つき）。"""
    days = [1, 2, 5, 6, 7, 8, 9, 13, 14, 15, 16]
    out = []
    for i, d in enumerate(days):
        base = {"date": f"2026-10-{d:02d}", "vehicle_class": "中型", "partner": "架空 元請A物流", "place": "江東区 地点07", "shipper_fault": "はい"}
        if i in (2, 7):
            out.append({**base, "arrive": "09:00", "start": "10:15", "depart": "10:25"})
        elif i == 10:
            out.append({**base, "arrive": "08:10", "appointed": "08:30", "start": "10:15", "end": "10:55", "depart": "11:05",
                        "handler": "運転者", "method": "手積み", "side_work": "検品・仕分け", "side_min": "20"})
        else:
            out.append({**base, "arrive": "09:00", "start": "09:30", "end": "09:52", "depart": "10:00", "handler": "荷主側"})
    return out


HEADER = "\t".join(["日付", "車番", "車種", "契約の相手", "場所", "到着", "荷役開始", "荷役終了", "出発", "荷役をした人",
                    "荷役の方法", "荷主等の都合の待機か", "指定の到着時刻", "附帯業務の内容", "附帯業務の分"])
LINE1 = "10/5\t品川100あ1\t小型（2t）\tA運輸\t川崎\t09:00\t11:15\t\t\t\t\tはい"
LINE2 = "10/6\t\t中型\tB商事\t\t8:00\t8:40\t9:10\t9:30\t運転者\tフォークリフト等\tはい\t07:45\t検品\t15"

CASES: list[dict] = [
    {"name": "q29_small_1h_is_1680", "year": 2026, "rows": [row(start="10:00")]},
    {"name": "q29_small_2h15_is_7050", "year": 2026, "rows": [row(start="11:15")]},
    {"name": "within_first_30_min", "year": 2026, "rows": [row(start="09:30"), row(start="09:31")]},
    {"name": "medium_52min_x11_is_19360", "year": 2026,
     "rows": [row(vehicle_class="中型", date=f"2026-10-{d:02d}", start="09:52") for d in range(1, 12)]},
    {"name": "shipper_handling_counts_as_waiting", "year": 2026, "rows": [row(start="09:20", end="10:10", handler="荷主側")]},
    {"name": "driver_handling_across_2h_boundary", "year": 2026,
     "rows": [row(start="10:50", end="11:20", handler="運転者", method="フォークリフト等")]},
    {"name": "driver_handling_after_boundary_only", "year": 2026,
     "rows": [row(vehicle_class="大型", start="11:10", end="12:00", handler="運転者", method="手積み")]},
    {"name": "not_shipper_fault_wait_is_zero", "year": 2026, "rows": [row(start="11:15", shipper_fault="いいえ")]},
    {"name": "not_shipper_fault_driver_handling_still_priced", "year": 2026,
     "rows": [row(start="10:30", end="10:50", handler="運転者", method="手積み", shipper_fault="いいえ")]},
    {"name": "midnight_crossing", "year": 2026,
     "rows": [row(arrive="23:30", start="00:40", end="01:10", depart="01:30", handler="運転者", method="手積み")]},
    {"name": "midnight_crossing_depart_only", "year": 2026,
     "rows": [row(arrive="22:00", start="22:40", end="23:10", depart="00:20", handler="荷主側")]},
    {"name": "appointed_early_arrival_counts_from_appointed", "year": 2026,
     "rows": [row(arrive="08:10", appointed="08:30", start="09:30"), row(arrive="08:10", start="09:30")]},
    {"name": "appointed_late_arrival_counts_from_arrival", "year": 2026, "rows": [row(arrive="08:50", appointed="08:30", start="09:50")]},
    {"name": "appointed_none_and_equal", "year": 2026, "rows": [row(start="10:00"), row(appointed="09:00", start="10:00")]},
    {"name": "appointed_after_handling_start_is_zero_wait", "year": 2026, "rows": [row(arrive="08:00", appointed="09:30", start="09:00")]},
    {"name": "appointed_across_midnight", "year": 2026,
     "rows": [row(arrive="23:50", appointed="00:10", start="01:10"), row(arrive="00:10", appointed="23:50", start="01:10")]},
    {"name": "appointed_boundary_for_driver_handling", "year": 2026,
     "rows": [row(vehicle_class="中型", arrive="08:10", appointed="08:30", start="10:15", end="10:55", handler="運転者", method="手積み"),
              row(vehicle_class="中型", arrive="08:10", start="10:15", end="10:55", handler="運転者", method="手積み")]},
    {"name": "appointed_with_shipper_handling", "year": 2026,
     "rows": [row(arrive="08:10", appointed="08:30", start="08:50", end="09:40", handler="荷主側")]},
    {"name": "side_work_recorded_not_priced", "year": 2026,
     "rows": [row(start="10:00", side_work="パレットの積み替え", side_min="25"), row(start="10:00", side_work="検品"),
              row(start="10:00", side_min="10")]},
    {"name": "example_rows_11_at_chiten07", "year": 2026, "rows": example_rows()},
    {"name": "multiple_partners_and_months", "year": 2026,
     "rows": [row(start="10:00"), row(date="2026-11-02", start="11:15"), row(partner="B商事", start="10:00"),
              row(partner="B商事", date="2026-09-30", vehicle_class="トレーラー", start="12:00")]},
    {"name": "class_and_method_aliases", "year": 2026,
     "rows": [row(vehicle_class="小型（2t）", start="10:00"), row(vehicle_class="4t", start="10:00"), row(vehicle_class="10トン", start="10:00"),
              row(vehicle_class="トレーラ", start="10:00", end="10:30", handler="運転者", method="フォークリフト"),
              row(start="10:00", end="10:30", handler="荷主", shipper_fault="yes")]},
    {"name": "md_date_with_year", "year": 2026, "rows": [row(date="10/5"), row(date="2026/10/6")]},
    {"name": "md_date_without_year_is_error", "year": None, "rows": [row(date="10/5"), row(date="2026-10-05")]},
    {"name": "errors_with_reasons", "year": 2026,
     "rows": [row(arrive="25:00"), row(start="abc"), row(partner=""), row(date=""), row(vehicle_class="軽"), row(shipper_fault=""),
              row(handler="運転者", end="10:30", method=""), row(date="13/40"), row(date="2026-02-30"),
              row(handler="運転手長", end="10:30"), row(handler="運転者", end="10:30", method="てこ"), row(shipper_fault="たぶん"),
              row(appointed="9時"), row(side_min="abc"), row(end="10:30"), row(method="手積み"), row()]},
    {"name": "paste_with_header", "year": 2026, "text": "\r\n".join([HEADER, LINE1, LINE2]) + "\r\n", "paste": True},
    {"name": "paste_without_header", "year": 2026, "text": "\n".join([LINE1, LINE2]) + "\n", "paste": True},
    {"name": "paste_header_in_other_order", "year": 2026, "paste": True,
     "text": "契約の相手\t日付\t車種\t到着\t荷役開始\t荷主等の都合の待機か\t指定の到着時刻\t附帯業務の内容\t附帯業務の分\n"
             "A運輸\t10/5\t小型\t09:00\t10:00\tはい\t09:20\t検品\t30"},
    {"name": "paste_with_appointed_and_side_columns_at_the_end", "year": 2026, "paste": True,
     "text": "\t".join(["10/5", "", "中型", "A運輸", "", "08:10", "09:30", "", "", "", "", "はい", "08:30", "検品", "20"])},
]


def build() -> dict:
    cases = []
    for c in CASES:
        c = dict(c)
        rows = waiting.parse_paste(c["text"]) if c.get("paste") else c["rows"]
        res = waiting.calc(rows, c["year"])
        exp = {"rows": res["rows"], "by_partner": res["by_partner"], "errors": res["errors"]}
        if c.get("paste"):
            exp["input_rows"] = rows
        c["expect"] = exp
        cases.append(c)
    return {"note": "tools/build_waiting_vectors.py が waiting.py の出力から作る。手で直さない（SPEC §23.7 G82）", "cases": cases}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(build(), f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print(f"保存: {OUT}  {OUT.stat().st_size} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
