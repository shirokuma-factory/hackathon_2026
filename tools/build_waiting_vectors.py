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
]


def build() -> dict:
    cases = []
    for c in CASES:
        c = dict(c)
        defaulted: list[str] = []
        notes: list[str] = []
        if c.get("paste"):
            rows, defaulted, notes = waiting.parse_paste_ex(c["text"])
        else:
            rows = c["rows"]
        res = waiting.calc(rows, c["year"])
        exp = {"rows": res["rows"], "by_partner": res["by_partner"], "errors": res["errors"]}
        if c.get("paste"):
            exp["input_rows"] = rows
            exp["defaulted"] = defaulted
            exp["notes"] = notes
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
