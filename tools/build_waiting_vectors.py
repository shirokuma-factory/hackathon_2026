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
    """以前の「例を入れる」だった 11 行（8 行は 52 分、2 行は 75 分、1 行は指定の到着時刻つき）。今の例は web/waiting_calc.js にあり、試験表にはこの 11 行をそのまま残す。"""
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


def page_example_rows() -> list[dict]:
    """web/waiting.html（と Artifact の版）の開いた直後の例（SPEC §27.4）。テンプレートの EXAMPLE と同じ 18 行。"""
    a, b, p7, p12, p3 = "架空 元請A物流", "架空 B食品", "江東区 地点07", "川崎市 地点12", "市川市 地点03"
    spec = [
        (1, a, p7, "中型", "08:00", "09:25", "", "", "", "", "08:00"), (2, a, p7, "中型", "08:00", "09:20", "", "", "", "", "08:00"),
        (5, a, p7, "中型", "08:00", "09:30", "", "", "", "", "08:00"), (6, a, p7, "中型", "08:00", "09:40", "", "", "", "", "08:00"),
        (7, a, p7, "中型", "08:00", "10:15", "10:55", "11:05", "運転者", "手積み", "08:00", "検品・仕分け", "20"),
        (15, a, p7, "中型", "09:00", "09:30", "09:52", "10:00", "荷主側"),
        (8, a, p7, "中型", "10:05", "10:20"), (9, a, p7, "中型", "10:10", "10:25"), (13, a, p7, "中型", "10:15", "10:35"), (14, a, p7, "中型", "10:00", "10:10"),
        (8, a, p12, "中型", "13:05", "13:45"), (16, a, p12, "中型", "13:20", "14:00"),
        (2, b, p3, "大型", "08:00", "09:30", "", "", "", "", "08:00"), (5, b, p3, "大型", "08:00", "09:40", "", "", "", "", "08:00"),
        (9, b, p3, "大型", "08:00", "09:20", "", "", "", "", "08:00"),
        (13, b, p3, "大型", "10:10", "10:25"), (14, b, p3, "大型", "10:20", "10:35"),
        (15, b, p12, "大型", "13:05", "13:20", "", "", "", "", "", "", "", "いいえ"),
    ]
    keys = ("handler", "method", "appointed", "side_work", "side_min")
    out = []
    for t in spec:
        d, partner, place, vclass, arrive, start = t[:6]
        end, depart = (t[6], t[7]) if len(t) > 7 else ("", "")
        rest = list(t[8:13]) + [""] * (5 - len(t[8:13]))
        fault = t[13] if len(t) > 13 else "はい"
        out.append({"date": f"2026-10-{d:02d}", "vehicle_no": "", "vehicle_class": vclass, "partner": partner, "place": place, "arrive": arrive,
                    "start": start, "end": end, "depart": depart, **dict(zip(keys, rest)), "shipper_fault": fault, "post_wait": ""})
    return out


HEADER = "\t".join(["日付", "車番", "車種", "契約の相手", "場所", "到着", "荷役開始", "荷役終了", "出発", "荷役をした人",
                    "荷役の方法", "荷主等の都合の待機か", "指定の到着時刻", "附帯業務の内容", "附帯業務の分"])
LINE1 = "10/5\t品川100あ1\t小型（2t）\tA運輸\t川崎\t09:00\t11:15\t\t\t\t\tはい"
LINE2 = "10/6\t\t中型\tB商事\t\t8:00\t8:40\t9:10\t9:30\t運転者\tフォークリフト等\tはい\t07:45\t検品\t15"

DEFAULTS = {"vehicle_class": "中型", "method": "手積み"}   # 画面が既定として出す車種と荷役の方法（SPEC §41）
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
    {"name": "example_waiting_page_18_rows", "year": 2026, "rows": page_example_rows()},
    # ---- SPEC §27 (G113): どこで・いつ待っているか
    {"name": "hotspots_g113_basic", "year": 2026,
     "rows": [row(partner="A", place="X", arrive="08:10", start="09:20"), row(partner="A", place="X", arrive="08:40", start="09:20"),
              row(partner="A", place="Y", arrive="13:05", start="14:05", shipper_fault="いいえ")]},
    {"name": "hotspots_empty_place_ordering_and_rounding", "year": 2026,
     "rows": [row(partner="B", place="", arrive="10:00", start="10:50"), row(partner="B", place="  ", arrive="10:30", start="11:21"),
              row(partner="B", place="Z", arrive="9:05", start="9:35"), row(partner="B", place="Z", arrive="09:50", start="10:50"),
              row(partner="A", place="Z", arrive="09:10", start="10:10"), row(partner="A", place="Z", arrive="09:00", start="09:30"),
              row(partner="A", place="Z", arrive="23:50", start="00:50"), row(partner="A", place="Y", arrive="09:00", start="10:00"),
              row(partner="A", place="Y", arrive="09:10", start="09:30"), row(partner="A", place="Y", arrive="09:20", start="09:55")]},
    # ---- SPEC §28 (G119・G120): 注記の条件の回数と、場所ごと・月ごと
    {"name": "hotspots_designated_and_shipper_handling_counts", "year": 2026,
     "rows": [row(partner="A", place="X", arrive="07:55", appointed="08:00", start="09:00"), row(partner="A", place="X", arrive="07:50", start="09:00"),
              row(partner="A", place="X", arrive="07:58", start="09:00", end="09:30", handler="荷主側"), row(partner="A", place="X", arrive="07:59", appointed="08:00", start="09:20"),
              row(partner="B", place="Y", arrive="10:00", appointed="10:00", start="11:00", end="11:20", handler="荷主側", shipper_fault="いいえ"),
              row(partner="B", place="Y", arrive="10:10", start="11:00", end="11:20", handler="運転者", method="手積み")]},
    {"name": "by_place_month_basic", "year": 2026,
     "rows": [row(partner="A", place="X", date="2026-10-05", start="10:00"), row(partner="A", place="X", date="2026-10-06", start="09:51"),
              row(partner="B", place="X", date="2026-11-02", start="11:15"), row(partner="A", place="", date="2026-10-07", start="09:40"),
              row(partner="A", place="Y", date="2026-09-30", start="12:00"), row(partner="A", place="Y", date="2026-10-01", start="09:20", shipper_fault="いいえ"),
              row(partner="A", place="  ", date="2026-11-03", start="10:00")]},
    # ---- SPEC §29 (G124): 定期報告と同じ定義の荷待ち（report_wait_min）
    {"name": "report_wait_early_arrival_shipper_handling_post_wait", "year": 2026,
     "rows": [row(place="X", arrive="07:50", appointed="08:00", start="09:00"),
              row(place="X", arrive="08:30", start="09:00", end="09:40", handler="荷主側", shipper_fault="いいえ"),
              row(place="X", arrive="09:00", start="09:30", end="09:50", depart="10:30", handler="荷主側", post_wait="はい"),
              row(place="X", arrive="10:00", start="10:20", end="10:40", depart="11:40", handler="運転者", method="手積み", post_wait="はい"),
              row(place="X", arrive="08:00", appointed="08:30", start="08:20")]},
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
    {"name": "paste_realistic_excel_header_other_order", "year": 2026, "paste": True,
     "text": "荷主\t運行日\t備考\t車両番号\t車格\t着時刻\t指定時刻\t積込開始時刻\t積込終了時刻\t出発時刻\t荷役をした人\t荷役の方法\t荷主等の都合\n"
             "B商事\t10/7\t雨\t神戸100あ1234\t4t\t08:10\t08:30\t10:15\t10:55\t11:05\t運転者\t手積み\tはい\n"
             "B商事\t10/8\t\t神戸100あ1234\t中型\t09:00\t\t10:00\t\t10:10\t\t\tはい"},
    {"name": "paste_with_appointed_and_side_columns_at_the_end", "year": 2026, "paste": True,
     "text": "\t".join(["10/5", "", "中型", "A運輸", "", "08:10", "09:30", "", "", "", "", "はい", "08:30", "検品", "20"])},
    # ---- SPEC §25: 実際の Excel の書き方（全角・半角、和暦、曜日、秒つき、車種の書き方、はい/いいえの書き方）
    {"name": "real_dates_all_families", "year": 2026,
     "rows": [row(date=d) for d in ("2026-10-05", "2026/10/05", "2026/10/5", "2026.10.5", "2026年10月5日", "10/5", "10月5日", "10月5日（月）", "10/5(月)",
                                    "R8.10.5", "R8/10/5", "r8.10.05", "令和8年10月5日", "令和８年１０月５日", "H38.10.5", "2026/10/05 0:00:00", "２０２６／１０／５")]},
    {"name": "real_dates_era_boundaries_and_errors", "year": 2026,
     "rows": [row(date="令和元年5月1日"), row(date="R1.5.1"), row(date="平成31年4月30日"), row(date="昭和64年1月7日"), row(date="10月32日"), row(date="R8.2.30"),
              row(date="来週"), row(date="10月5")]},
    {"name": "real_dates_without_year_need_a_year", "year": None,
     "rows": [row(date="10月5日"), row(date="令和8年10月5日"), row(date="2026/10/05")]},
    {"name": "real_times_all_families", "year": 2026,
     "rows": [row(arrive=a, start=b) for a, b in (("9:00:00", "10:00:00"), ("9:00", "10:00"), ("09時00分", "10時00分"), ("9時", "10時"), ("9時0分", "10時5分"),
                                                  ("0900", "1000"), ("900", "1000"), ("９：００", "１０：００"), ("９時００分", "１０時００分"), ("０９００", "１０００"),
                                                  ("2026/10/5 9:00:00", "2026/10/5 10:00:00"), (" 9:00 ", "　10:00　"))]},
    {"name": "real_times_errors", "year": 2026,
     "rows": [row(arrive="24:00"), row(arrive="9:60"), row(arrive="9:00:61"), row(arrive="25時"), row(arrive="9.30"), row(arrive="2400"), row(arrive="9:0"), row(arrive="午前")]},
    {"name": "real_vehicle_classes", "year": 2026,
     "rows": [row(vehicle_class=v) for v in ("4t車", "4トン", "4トン車", "４ｔ", "4T", "中型車", "中型", "中型（4t）", "大型車", "10t", "10トン車", "２ｔ", "2t", "2トン", "2t車",
                                              "小型車", "トレーラ", "トレーラー", "トレーラ車", "セミトレーラ", "4tウイング", "10t平ボディ", "　4t車　")] +
             [row(vehicle_class=v) for v in ("20t", "3t", "軽")]},
    {"name": "real_yes_no_words", "year": 2026,
     "rows": [row(shipper_fault=v) for v in ("○", "〇", "◯", "有", "はい", "荷主都合", "荷主の都合", "Y", "yes", "×", "無", "いいえ", "渋滞", "なし", "x", "ｘ", "　はい　", "たぶん")]},
    {"name": "real_whitespace_and_fullwidth_in_other_columns", "year": 2026,
     "rows": [row(handler="　運転者 ", method=" 手積み ", end="１０：３０", side_min="　２０ ", side_work="検品", start="１０：００", shipper_fault="　はい　", appointed="　０８：３０　",
                  arrive="８：１０", partner="　A運輸　"),
              row(handler="荷主 ", end="9:52", start="9:30", method="")]},
    # ---- SPEC §25: 荷役のあとの待ち（荷役終了 → 出発）を荷主等の都合として数える。既定は数えない
    {"name": "post_wait_off_by_default", "year": 2026,
     "rows": [row(start="09:30", end="09:52", depart="12:52", handler="荷主側")]},
    {"name": "post_wait_on_adds_depart_minus_end", "year": 2026,
     "rows": [row(start="09:30", end="09:52", depart="12:52", handler="荷主側", post_wait="はい"),
              row(start="09:30", end="09:52", depart="10:22", handler="荷主側", post_wait="はい"),
              row(start="09:30", end="09:52", depart="10:22", handler="荷主側", post_wait="有"),
              row(start="09:30", end="09:52", depart="10:22", handler="荷主側", post_wait="いいえ"),
              row(start="09:30", end="09:52", depart="10:22", handler="荷主側", post_wait="")]},
    {"name": "post_wait_with_driver_handling_and_appointed", "year": 2026,
     "rows": [row(vehicle_class="中型", arrive="08:10", appointed="08:30", start="10:15", end="10:55", depart="12:00", handler="運転者", method="手積み", post_wait="はい"),
              row(vehicle_class="中型", arrive="08:10", appointed="08:30", start="10:15", end="10:55", depart="12:00", handler="運転者", method="手積み")]},
    {"name": "post_wait_only_counts_when_shippers_fault", "year": 2026,
     "rows": [row(start="09:30", end="09:52", depart="12:52", handler="荷主側", post_wait="はい", shipper_fault="いいえ")]},
    {"name": "post_wait_across_midnight", "year": 2026,
     "rows": [row(arrive="22:00", start="22:30", end="23:30", depart="00:40", handler="荷主側", post_wait="はい")]},
    {"name": "post_wait_needs_end_and_depart", "year": 2026,
     "rows": [row(start="09:30", post_wait="はい"), row(start="09:30", end="09:52", handler="荷主側", post_wait="はい"),
              row(start="09:30", end="09:52", depart="10:30", handler="荷主側", post_wait="たぶん")]},
    # ---- SPEC §25: 実際の Excel の貼り付け。荷主等の都合の欄が無い見出しは はい として計算し、defaulted に印を付ける
    {"name": "paste_realistic_japanese_block_zero_errors", "year": 2026, "paste": True,
     "text": ("運行日\t車番\t車格\t荷主\t納品先\t着時刻\t積込開始時刻\t積込終了時刻\t出発時刻\t荷役をした人\t荷役の方法\n"
              "10月5日（月）\t品川100あ1\t4t車\tA運輸\t川崎\t9:00:00\t9:40:00\t10:00:00\t10:20:00\t荷主\t\n"
              "１０／６\t品川100あ1\t中型車\tA運輸\t川崎\t９：００\t１０：１５\t\t１０：２５\t\t\n"
              "2026/10/07\t品川100あ2\t4トン\tA運輸\t横浜\t0900\t1015\t1055\t1105\t運転者\t手積み\n"
              "R8.10.8\t品川100あ3\t2t\tＢ商事　\t船橋\t9時00分\t10時15分\t\t10時25分\t\t\n"
              "令和8年10月9日\t品川100あ4\t大型車\tB商事\t船橋\t09:00\t11:00\t11:30\t11:40\t運転者\tフォークリフト\n"
              "10月10日（土）\t品川100あ5\tトレーラ\tB商事\t千葉\t900\t1000\t\t1010\t\t\n")},
    {"name": "paste_with_fault_column_is_not_defaulted", "year": 2026, "paste": True,
     "text": ("運行日\t車格\t荷主\t着時刻\t積込開始時刻\t荷主等の都合\t荷役のあとの待ちを荷主等の都合として数える\t積込終了時刻\t出発時刻\t荷役をした人\n"
              "10/5\t4t\tA運輸\t09:00\t09:30\t渋滞\t\t09:52\t10:00\t荷主側\n"
              "10/6\t4t\tA運輸\t09:00\t09:30\t荷主都合\tはい\t09:52\t11:52\t荷主側\n")},
    {"name": "paste_without_header_and_without_fault_column_is_defaulted", "year": 2026, "paste": True,
     "text": "10/5\t\t中型\tA運輸\t\t09:00\t10:00\n10/6\t\t中型\tA運輸\t\t09:00\t09:20\n"},
    # ---- SPEC §23.8: 輸送安全規則 第 8 条第 1 項の言葉の見出し（乗務員氏名・荷主の確認の有無の列つき）
    {"name": "paste_statutory_headings_zero_errors", "year": 2026, "paste": True,
     "text": ("運行日TT乗務員氏名TT車両登録番号TT車種TT荷主TT集荷地点等TT指定到着日時TT到着日時TT荷役作業の開始日時TT荷役作業の終了日時TT出発日時TT荷役作業等の内容TT荷主の確認の有無TT荷主都合NN"
              "10/5TT山田 太郎TT姫路100あ1234TT4t車TTA運輸TT加古川DCTT2026/10/05 8:30TT2026/10/05 8:10TT2026/10/05 10:15TT2026/10/05 10:55TT2026/10/05 11:05TT手積みTT有TT○NN"
              "10/6TT山田 太郎TT姫路100あ1234TT4t車TTA運輸TT加古川DCTT10/6 9:00TT10/6 9:00TT10/6 10:00TT10/6 10:00TT10/6 10:10TTTT有TT×NN"
              "10/7TT鈴木 花子TT神戸100い5678TT大型車TTB商事TT三木DCTTTT10/7 9:00TT10/7 11:30TT10/7 12:00TT10/7 12:10TTフォークリフトTT無TT○NN").replace("TT", "\t").replace("NN", "\n")},
    {"name": "paste_statutory_headings_date_only_in_datetime_cells", "year": 2026, "paste": True,
     "text": ("乗務員氏名TT車両登録番号TT車種TT荷主TT荷待ち地点TT到着の日時TT荷役開始日時TT荷役終了日時TT出発の日時TT荷役作業の内容TT荷主の確認の有無NN"
              "山田 太郎TT姫路100あ1234TT中型TTA運輸TT加古川DCTT2026/10/05 9:00TT2026/10/05 10:20TT2026/10/05 10:40TT2026/10/05 10:50TT手荷役TT有NN"
              "山田 太郎TT姫路100あ1234TT中型TTA運輸TT加古川DCTTR8.10.6 9時00分TTR8.10.6 10時30分TTTTR8.10.6 10時40分TTTT有NN").replace("TT", "\t").replace("NN", "\n")},
    {"name": "paste_unknown_headings_are_reported_by_name", "year": 2026, "paste": True,
     "text": ("日付TT車種TT契約の相手TT到着TT荷役開始TTXYZTT荷主等の都合TT天気TTXYZNN"
              "10/5TT中型TTA運輸TT09:00TT10:00TTaaTTはいTTTT雨NN").replace("TT", "\t").replace("NN", "\n")},
    {"name": "paste_few_known_headings_still_read_by_name", "year": 2026, "paste": True,
     "text": ("日付TT車種TT契約の相手TT到着TT荷役開始TT謎の列NN"
              "10/5TT中型TTA運輸TT09:00TT10:00TTaa").replace("TT", "\t").replace("NN", "\n")},
    {"name": "paste_no_header_says_position_order", "year": 2026, "paste": True,
     "text": "10/5\t\t中型\tA運輸\t\t09:00\t10:00\t\t\t\t\tはい"},
    # 荷役をした人の列が無く、荷役終了がある: 荷役作業等の内容があれば運転者、無ければ行は残して荷役の時間を入れない
    {"name": "paste_no_handler_column_work_content_means_driver", "year": 2026, "paste": True,
     "text": ("運行日TT車種TT荷主TT到着日時TT荷役開始日時TT荷役終了日時TT荷役作業等の内容TT荷主都合NN"
              "10/5TT4t車TTA運輸TT09:00TT10:15TT10:55TT手積みTT○NN"
              "10/6TT4t車TTA運輸TT09:00TT10:15TT10:55TTフォークリフトでパレット積みTT○NN"
              "10/7TT4t車TTA運輸TT09:00TT10:15TT10:55TT検品TT○").replace("TT", "\t").replace("NN", "\n")},
    {"name": "paste_no_handler_column_no_work_content_keeps_row_with_note", "year": 2026, "paste": True,
     "text": ("運行日TT車種TT荷主TT到着日時TT荷役開始日時TT荷役終了日時TT出発日時TT荷主都合NN"
              "10/5TT4t車TTA運輸TT09:00TT10:15TT10:55TT11:05TT○NN"
              "10/6TT4t車TTA運輸TT09:00TT10:15TTTTTT○").replace("TT", "\t").replace("NN", "\n")},
    # 荷主都合の欄があるのに読めない値の行は、はい にせず、値を名指しした誤りにする。欄が無いときだけ はい
    {"name": "paste_unreadable_shipper_fault_values_are_errors", "year": 2026, "paste": True,
     "text": ("運行日TT車種TT荷主TT到着TT荷役開始TT荷主都合の有無NN"
              "10/5TT中型TTA運輸TT09:00TT10:00TT○NN"
              "10/6TT中型TTA運輸TT09:00TT10:00TT要確認NN"
              "10/7TT中型TTA運輸TT09:00TT10:00TTNN"
              "10/8TT中型TTA運輸TT09:00TT10:00TT×").replace("TT", "\t").replace("NN", "\n")},
    {"name": "paste_header_without_shipper_fault_column_is_defaulted_with_note", "year": 2026, "paste": True,
     "text": ("運行日TT車種TT荷主TT到着日時TT荷役開始日時NN"
              "10/5TT中型TTA運輸TT09:00TT10:00").replace("TT", "\t").replace("NN", "\n")},
    # ---- SPEC §38 (2026-11-06, 洗い出し r1): 日付つきの日時、場所の正規化、元号の始まり、荷役のあとの待ちの 2 時間の境
    {"name": "dated_cells_use_actual_difference", "year": 2026,
     "rows": [row(arrive="2026/10/05 9:00", start="2026/10/05 10:00"),
              row(arrive="2026/10/05 9:00", start="2026/10/06 10:00"),
              row(arrive="10/5 9:00", start="10/7 10:00"),
              row(arrive="2026/10/05 23:30", start="2026/10/06 0:40", end="2026/10/06 1:10", depart="2026/10/06 1:30", handler="運転者", method="手積み"),
              row(arrive="9:00", start="10/6 10:00"),
              row(arrive="2026/10/05 9:00", start="10:00")]},
    {"name": "dated_start_before_arrival_is_input_error", "year": 2026,
     "rows": [row(arrive="2026/10/05 9:00", start="2026/10/05 8:00"), row(arrive="10/5 9:00", start="10/4 23:00"),
              row(arrive="9:00", start="10/5 8:00"),
              row(arrive="2026/10/05 9:00", start="2026/10/05 10:00", end="2026/10/05 9:30", handler="荷主側"),
              row(arrive="2026/10/05 9:00", start="2026/10/05 10:00", end="2026/10/05 10:30", depart="2026/10/05 10:10", handler="荷主側", post_wait="はい"),
              row(arrive="2026/10/05 9:00", start="2026/10/05 10:00", depart="2026/10/05 9:50")]},
    {"name": "undated_start_before_arrival_overnight_kept_but_over_12h_is_error", "year": 2026,
     "rows": [row(arrive="09:00", start="08:00"), row(arrive="23:30", start="00:40")]},
    {"name": "dated_appointed_uses_actual_difference", "year": 2026,
     "rows": [row(arrive="2026/10/05 8:00", appointed="2026/10/05 8:30", start="2026/10/05 10:00"),
              row(arrive="2026/10/05 23:50", appointed="2026/10/06 0:10", start="2026/10/06 1:10"),
              row(arrive="2026/10/05 9:00", appointed="2026/10/04 20:00", start="2026/10/05 10:00")]},
    {"name": "dated_year_end", "year": 2026,
     "rows": [row(date="2026-12-31", arrive="12/31 23:00", start="1/1 1:00"), row(date="2026-12-31", arrive="12/31 23:00", start="12/31 22:00")]},
    {"name": "multi_day_wait_gets_a_note", "year": 2026,
     "rows": [row(arrive="2026/10/05 9:00", start="2026/10/06 9:00"), row(arrive="2026/10/05 9:00", start="2026/10/06 8:59")]},
    {"name": "place_is_nfkc_normalized", "year": 2026,
     "rows": [row(partner="A", place="Ａ倉庫", start="10:00"), row(partner="A", place="A倉庫", start="10:00"), row(partner="A", place="　A　倉庫 ", start="10:00")]},
    {"name": "era_dates_before_the_era_began_are_errors", "year": 2026,
     "rows": [row(date="R1.1.5"), row(date="令和元年4月1日"), row(date="令和元年4月30日"), row(date="令和元年5月1日"), row(date="H1.1.7"), row(date="平成元年1月8日"),
              row(date="平成31年5月1日"), row(date="S1.12.24"), row(date="昭和元年12月25日"), row(date="S64.1.7")]},
    {"name": "post_wait_boundary_counts_from_start_of_waiting_including_handling", "year": 2026,
     "rows": [row(vehicle_class="中型", start="09:30", end="11:30", depart="12:30", handler="運転者", method="手積み", post_wait="はい"),
              row(vehicle_class="中型", start="09:30", end="09:50", depart="11:30", handler="運転者", method="手積み", post_wait="はい"),
              row(vehicle_class="中型", start="09:30", end="10:10", depart="12:30", handler="荷主側", post_wait="はい"),
              row(vehicle_class="中型", arrive="08:10", appointed="08:30", start="09:00", end="10:00", depart="11:10", handler="運転者", method="手積み", post_wait="はい"),
              row(vehicle_class="中型", start="09:30", end="11:30", depart="12:30", handler="運転者", method="手積み", post_wait="はい", shipper_fault="いいえ")]},
    # ---- SPEC §41 (2026-11-06, 実務の効き目 r1): ドライバーの LINE の文、現場の見出し、時刻の言い方、既定の車種・方法、早着
    {"name": "paste_driver_line_text_many_blocks", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": ("[LINE] 山田 太郎\n"
              "荷待ち記録 11/10 江東区 ○○倉庫 着 7:50 受付 8:05 荷役開始 9:40 終了 10:30 出発 10:40 車番 1234 相手 ○○\n"
              "10:41\n"
              "荷待ち記録 11/10 川崎市 △△センター 着 13:05 荷役開始 13:45 出発 13:55 車番 1234 相手 ○○ 車種 大型\n"
              "荷待ち記録 11/11 江東区 ○○倉庫 着 8:30 指定 8:30 荷役開始 8:50 終了 9:20 荷役 荷主側 相手 ○○ 車番 1234\n"
              "荷待ち記録 11/12 市川市 着 5時半 荷役開始 10時半 荷役 運転者 終了 11時20分 相手 □□")},
    {"name": "paste_driver_text_one_line_and_bad_values", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "荷待ち記録 11/10(火) 江東区 着 7:50 荷役開始 9:40 相手 ○○ 荷待ち記録 11/10 川崎 着 abc 荷役開始 9:00 相手 ○○ 荷待ち記録 江東 着 9:00 荷役開始 10:00 相手 ○○"},
    {"name": "paste_memo_headings_and_japanese_times", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": ("日付\t荷主\t場所\t着\t受付\t積み始め\t積み終わり\t出発\t積んだ人\t備考\n"
              "11/10\t○○\t江東区 ○○倉庫\t5時半\t7時50分\t10時半\t11時20分\t11時半\t運転者\t雨\n"
              "11/11\t○○\t江東区 ○○倉庫\t7:50\t8:05\t9:40\t\t9:50\t\t\n"
              "11/12\t○○\t江東区 ○○倉庫\t8:30\t\t9:00\t9:30\t9:40\t自分\t\n")},
    {"name": "defaults_fill_missing_vehicle_class_and_method", "year": 2026, "defaults": DEFAULTS,
     "rows": [row(vehicle_class="", start="10:00"), row(vehicle_class="", start="10:30", end="11:00", handler="運転者", method=""),
              row(start="10:30", end="11:00", handler="運転者", method=""), row(vehicle_class="大型", start="10:00"),
              row(vehicle_class="軽", start="10:00"), row(vehicle_class="", start="10:00", end="10:20", handler="荷主側")]},
    {"name": "without_defaults_missing_class_and_method_are_errors", "year": 2026,
     "rows": [row(vehicle_class="", start="10:00"), row(start="10:30", end="11:00", handler="運転者", method="")]},
    {"name": "provisional_partner_for_rows_without_partner", "year": 2026, "defaults": {**DEFAULTS, "provisional_partner": True},
     "rows": [row(partner="", start="10:00"), row(partner="", start="10:30", date="2026-10-06"), row(partner="B商事", start="10:00")]},
    {"name": "provisional_partner_also_for_memo_no_partner_rows", "year": 2026, "defaults": {**DEFAULTS, "provisional_partner": True},
     "rows": [row(partner="（相手未記入）", start="10:00"), row(partner="B商事", start="10:00")]},
    {"name": "without_provisional_partner_rows_without_partner_are_errors", "year": 2026, "defaults": DEFAULTS,
     "rows": [row(partner="", start="10:00"), row(partner="B商事", start="10:00")]},
    {"name": "early_arrival_rules", "year": 2026,
     "rows": [row(arrive="07:50", start="09:00"), row(arrive="05:00", start="06:00"), row(arrive="04:59", start="06:00"), row(arrive="08:00", start="09:30"),
              row(arrive="07:50", appointed="08:00", start="09:00"), row(arrive="07:50", appointed="07:30", start="09:00"),
              row(arrive="07:50", start="09:00", shipper_fault="いいえ"), row(partner="B商事", arrive="06:10", start="08:40")]},
    {"name": "paste_reception_only_means_arrival_for_a_shippers_own_records", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": ("日付\t運送会社\t施設\t受付\t荷役開始\n"
              "10/5\tX運輸\tA工場\t8:10\t9:00\n10/6\tY運送\tA工場\t8:20\t9:30\n11/2\tX運輸\tB倉庫\t13:00\t13:20\n")},
    {"name": "reception_column_with_arrival_is_kept_for_the_record_list", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "日付\t契約の相手\t着\t受付\t荷役開始\n10/5\tA運輸\t7:50\t8:05\t9:00\n10/6\tA運輸\t9:00\t9時10分\t10:00\n10/7\tA運輸\t9:00\tabc\t10:00\n"},
    # SPEC §42: ドライバーの手書きメモの見出し（呼ばれ・行き先）、荷主の受付記録（受付・呼出・作業開始）、日付の無い逆転の時刻
    {"name": "paste_memo_headings_yobare_ikisaki", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "日付\t行き先\t荷主\t着\t受付\t呼ばれ\t積み終わり\n11/10\t江東区 ○○倉庫\t○○\t5時10分\t6時\t8時15分\t9時半\n"},
    {"name": "paste_destination_aliases", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "日付\t納品先\t荷主\t着\t荷役開始\n11/10\t船橋\t□□\t9:00\t10:00\n11/11\t加古川\t□□\t9:00\t10:00\n"},
    {"name": "paste_destination_alias_tsumichi", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "日付\t積地\t荷主\t着\t荷役開始\n11/12\t姫路\t□□\t9:00\t10:00\n"},
    {"name": "paste_shipper_reception_log", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "日付\t運送会社\t施設\t車番\t受付\t呼出\t作業開始\n10/5\tA運輸\t江東区 第1倉庫\t1234\t7:50\t8:30\t8:50\n10/5\tB急送\t江東区 第1倉庫\t5678\t8:10\t9:00\t9:40\n"},
    {"name": "paste_call_is_start_without_other_start_column", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "日付\t運送会社\t施設\t受付\t呼出\n10/5\tA運輸\t江東区\t7:50\t8:30\n"},
    {"name": "undated_reverse_start_over_12h_is_error", "year": 2026,
     "rows": [row(arrive="10:00", start="09:00", end="10:00", handler="荷主側")]},
    {"name": "undated_reverse_end_over_12h_is_error", "year": 2026,
     "rows": [row(arrive="08:00", start="09:00", end="08:30", handler="運転者", method="手積み")]},
    {"name": "undated_reverse_depart_over_12h_is_error", "year": 2026,
     "rows": [row(arrive="08:00", start="09:00", end="09:30", depart="09:10", handler="荷主側")]},
    {"name": "undated_overnight_wait_under_12h_is_kept", "year": 2026,
     "rows": [row(arrive="23:00", start="02:00", end="02:30", depart="02:40", handler="荷主側")]},
    # SPEC §43: 受付の時刻があるときは受付から数えた待機も出す（書面の主な額）。荷主の受付記録の見出し（受付日・受付時刻・入場・退場）。スマホのメモ
    {"name": "reception_wait_main_figure", "year": 2026,
     "rows": [row(arrive="06:10", reception="06:20", start="08:45"), row(date="2026-10-06", arrive="07:50", reception="08:05", start="09:40", end="10:30", handler="荷主側"),
              row(date="2026-10-07", arrive="09:00", reception="09:00", start="10:00"), row(date="2026-10-08", arrive="09:30", reception="09:00", start="10:30"),
              row(date="2026-10-09", arrive="06:00", reception="11:00", start="10:30"), row(date="2026-10-10", arrive="2026-10-10 06:00", reception="06:30", start="2026-10-10 09:00"),
              row(date="2026-10-11", arrive="07:00", reception="9時", start="10:00", appointed="08:00"), row(date="2026-10-12", arrive="07:00", reception="あ", start="10:00")]},
    {"name": "paste_shipper_reception_header_ukeke_nyujo_taijo", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "受付日\t運送会社\t施設\t車番\t受付時刻\t入場\t退場\n10/5\tA運輸\t江東区 第1倉庫\t1234\t7:50\t8:30\t10:40\n10/6\tB急送\t江東区 第1倉庫\t5678\t8:10\t9:00\t10:10\n"},
    {"name": "paste_phone_memo_without_label", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "11/10 江東 着7:50 呼ばれ9:40 終わり10:30\n10/5 尼崎 ○○物流DC 着6:10 受付6:20 呼ばれ8:45 相手 ○○\nあいう\n11/12 船橋 着8:00\n13/45 x 着8:00 呼ばれ9:00\n11/13(木) 東京 着5時半 積み始め9時半 出発11:00 車番1234\n"},
    {"name": "paste_phone_memo_all_unreadable_falls_to_positional", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "あいう\nえお\n"},
    # SPEC §46: 手書きメモの「車1234」「車 1234」、単独の車種（大型・4t）、日付の次の語を相手、略字（受・呼・終・積込終）、時刻が先の書き方、受付が到着より前
    {"name": "paste_phone_memo_plate_class_partner_abbrev", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": ("11/2 東和電機 加古川工場 着7時40分 受付8:00 呼ばれ9:50 終わり10:40 車１２３４ 大型 フォークで手積み手伝った\n"
              "11/3 東和電機 加古川工場 6:10着 6:25受付 9:00呼 9:40終 車 5678 4t\n"
              "11/4 江東 着7:50 呼ばれ9:40 積込終10:30 車番 9999 中型\n"
              "11/5 尼崎 ○○物流DC 着7:00 受7:30 呼8:40 相手 ○○\n"
              "11/6 加古川 着7:00 受6:20 呼8:00\n")},
    # r6a: 時刻が先の書き方（5:10着 6:40開始 7:35終）と、コロンの無い 3〜4 桁の時刻（着520・520着）。車番の数字は時刻にしない
    {"name": "paste_phone_memo_time_then_label_order", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "11/6 ﾄｳﾜ春日部 5:10着 6:40開始 7:35終\n11/7 ﾄｳﾜ春日部 5:10着 6:40開始 7:35終了\n"},
    {"name": "paste_phone_memo_compact_times", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": "11/6 江東 着520 受付540 呼ばれ755 終830\n11/7 江東 520着 540受付 755呼ばれ 830終了\n11/8 江東 車1234 大型 着1530 開始1630 終1730\n"},
    # r6a: 表の見出し「終了」「終了時刻」「作業終了」を荷役終了として読む（「開始」も同じく荷役開始）
    {"name": "paste_table_header_shuryo_variants", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": ("日付\t車番\t車種\t契約の相手\t場所\t到着\t開始\t終了\t荷役をした人\t荷役の方法\n10/5\t1234\t中型\tA社\t江東\t8:00\t9:00\t9:40\t運転者\t手積み\n")},
    {"name": "paste_table_header_shuryo_jikoku", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": ("日付\t車番\t車種\t契約の相手\t場所\t到着\t荷役開始\t終了時刻\t荷役をした人\t荷役の方法\n10/6\t1234\t中型\tA社\t江東\t8:00\t9:00\t9:40\t運転者\t手積み\n")},
    {"name": "paste_table_header_sagyo_shuryo", "year": 2026, "paste": True, "defaults": DEFAULTS,
     "text": ("日付\t車番\t車種\t契約の相手\t場所\t到着\t荷役開始\t作業終了\t荷役をした人\t荷役の方法\n10/5\t1234\t中型\tA社\t江東\t8:00\t9:00\t9:40\t運転者\t手積み\n")},
    # R7A-1: 夜中をまたぐ受付（23:50 着・受付 0:20）も受付から数える。受付に日付があれば日付で差を取る
    {"name": "reception_after_midnight_main_figure", "year": 2026,
     "rows": [row(arrive="23:50", reception="00:20", start="02:00"), row(date="2026-10-06", arrive="2026-10-06 23:50", reception="2026-10-07 00:20", start="2026-10-07 02:00"),
              row(date="2026-10-07", arrive="2026-10-07 23:50", reception="00:20", start="2026-10-08 02:00", end="2026-10-08 02:40", handler="荷主側"),
              row(date="2026-12-31", arrive="23:50", reception="00:20", start="02:00"), row(date="2026-10-09", arrive="23:50", reception="2026-10-11 00:20", start="2026-10-11 02:00")]},
    {"name": "times_with_han", "year": 2026,
     "rows": [row(arrive="5時半", start="10時半"), row(arrive="9時半", start="2026/10/5 10時半"), row(arrive="9時 半", start="10時半"), row(arrive="9時半30", start="10時")]},
]


def build() -> dict:
    cases = []
    for c in CASES:
        c = dict(c)
        defaulted: list[str] = []
        notes: list[str] = []
        det: dict = {}
        if c.get("paste"):
            det = waiting.parse_paste_detail(c["text"])
            rows, defaulted, notes = det["rows"], det["defaulted"], det["notes"]
        else:
            rows = c["rows"]
        res = waiting.calc(rows, c["year"], defaults=c.get("defaults"))
        exp = {"rows": res["rows"], "by_partner": res["by_partner"], "hotspots": res["hotspots"], "by_place_month": res["by_place_month"], "errors": res["errors"]}
        if c.get("paste"):
            exp["input_rows"] = rows
            exp["defaulted"] = defaulted
            exp["notes"] = notes
            exp["asks"] = det["asks"]
            exp["shipper_log"] = det["shipper_log"]
            if "memo_read" in det:
                exp["memo_read"] = det["memo_read"]
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
