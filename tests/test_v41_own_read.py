"""SPEC §41 自社の運行（own）を、利用者自身の Excel の配車表・デジタコで読めるようにする。

実務の効き目 r1 の採点者が作った 4 つの形（題名の行と日だけの見出しと曜日の行、縦の 1 行 1 便、デジタコの日ごとの集計、別の会社のデジタコの明細）と、
重さの書き方、車番、重複、年、丸めた文を、Python（src/shipcompare/own.py）と JS（web/own_calc.js）の両方に通す。
入力は examples/own/ の架空のファイルと、この中の小さな表。Edge が無い環境だけ、JS の試験を飛ばす（起動の時間切れは失敗）。
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import own  # noqa: E402

EX = ROOT / "examples" / "own"
NEW_EXAMPLES = ["haisha_excel_2026-10.csv", "haisha_vertical_2026-10.csv", "digitaco_summary_9001.csv", "digitaco_detail_5678.csv",
                "haisha_intra_pref.csv", "haisha_same_last4.csv", "digitaco_unknown_cols_9012.csv"]


def rows_of(name: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO((EX / name).read_text(encoding="utf-8")), strict=False))


def text_of(name: str) -> str:
    return (EX / name).read_text(encoding="utf-8")


def as_json(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


# ---------------------------------------------------------------- 共通の入力表（Python と JS の両方に通す）

# (セルの文字, 区間の一覧 [(frm, to, empty, arr, weight_kg)], 理由の一覧, 重さが複数のときの kg の一覧)
CELL_VECTORS = [
    ("加古川→江東区　ﾄｳﾜ電機 5:30着 9.2t", [("加古川", "江東区", False, "05:30", 9200.0)], [], None),  # 着の後ろの 9.2t を時刻にしない
    ("加古川→江東 着5:30 4.6t", [("加古川", "江東", False, "05:30", 4600.0)], [], None),
    ("加古川→江東 着530 1.8t", [("加古川", "江東", False, "05:30", 1800.0)], [], None),
    ("加古川→江東 5:30着 5トン", [("加古川", "江東", False, "05:30", 5000.0)], [], None),
    ("加古川→江東 5ｔ", [("加古川", "江東", False, None, 5000.0)], [], None),
    ("加古川→江東 5時半着", [("加古川", "江東", False, "05:30", None)], [], None),
    ("加古川→江東5:30着 9.2t", [("加古川", "江東", False, "05:30", 9200.0)], [], None),  # 降ろし地に時刻が続く
    ("加古川 → 江東 4t車 2.5t", [("加古川", "江東", False, None, 2500.0)], [], None),  # 4t は車の大きさ
    ("加古川 → 江東 4tウイング 3.2t", [("加古川", "江東", False, None, 3200.0)], [], None),
    ("加古川 → 江東 10トン車", [("加古川", "江東", False, None, None)], [], None),  # 車の大きさだけ。重さは無い
    ("加古川→江東 着5:30 3t 4t", [("加古川", "江東", False, "05:30", None)], [], [3000.0, 4000.0]),  # 2 つ → 確認
    ("加古川→江東 着5:30 3t 3t", [("加古川", "江東", False, "05:30", 3000.0)], [], None),  # 同じ値は 1 つ
    ("加古川→江東 着5:30 0t", [("加古川", "江東", False, "05:30", 0.0)], [], None),
    ("江東→加古川 着8時半ごろ", [("江東", "加古川", False, None, None)], [], None),  # SPEC §42: 時刻だけ捨てて区間は残す（warnings に出る）
    ("江東→加古川 空車", [("江東", "加古川", True, None, None)], [], None),
    ("加古川→江東 -1t", [("加古川", "江東", False, None, None)], [own.WEIGHT_SIGN_ERR], None),
    ("加古川→江東 6.5ton", [("加古川", "江東", False, None, None)], [], None),
]


def _legs_of(res: dict) -> list[tuple]:
    return [(l["frm"], l["to"], l["empty"], l["arr"], l["weight_kg"]) for l in res["legs"]]


@pytest.mark.parametrize("text,legs,errs,multi", CELL_VECTORS)
def test_python_parse_cell_real_world_cells(text, legs, errs, multi):
    r = own.parse_cell(text)
    assert _legs_of(r) == legs and r["errors"] == errs
    assert (r["legs"][0].get("weights") if r["legs"] else None) == multi


# 見出しの別名 → 役割
HEAD_VECTORS = [
    ("車番", "plate"), ("車両No.", "plate"), ("車両NO", "plate"), ("車両番号", "plate"), ("ナンバー", "plate"), ("車両", "plate2"), ("号車", "plate3"), ("No", "plate3"),
    ("車格", "type"), ("トン数", "type"), ("車種", "type"), ("乗務員", "skip"), ("運転者", "skip"), ("合計", "skip"), ("備考", "skip"), ("稼働日数", "skip"),
    ("日付", "date"), ("発地", "from"), ("積地", "from"), ("着地", "to"), ("卸地", "to"), ("重量(t)", "weight"), ("重さ", "weight"), ("時刻", "time"), ("区分", "mark"),
    ("10/1(木)", None), ("1", None), ("", None),
]


@pytest.mark.parametrize("head,role", HEAD_VECTORS)
def test_python_header_aliases(head, role):
    assert own.haisha_role(head) == role


# ---------------------------------------------------------------- Python: 実務の形の配車表

def test_excel_shape_title_rows_day_numbers_weekday_row_ignored_columns_and_total_row():
    rows = rows_of("haisha_excel_2026-10.csv")
    lay = own.detect_haisha(rows)
    assert lay["kind"] == "wide" and lay["header_row"] == 2 and lay["weekday_row"] is True and lay["title_month"] == [2026, 10]
    assert lay["head"][lay["plate_col"]] == "車両No."  # 号車ではなく車両No. を車番にする
    r = own.read_haisha(rows, 2026)
    assert r["layout"]["header_line"] == 3 and r["layout"]["data_start_line"] == 5
    assert r["stats"]["date_cols"] == 31 and r["stats"]["bad_cols"] == 0 and r["stats"]["groups"] == 3 and r["skipped_total_rows"] == 1
    assert r["unreadable"] == [] and r["extra_cols"] == []  # 号車・乗務員・稼働日数・備考は、読まない列として黙って外す
    assert [p["plate"] for p in r["plates"]] == ["9001", "1234", "5678"]
    dates = {x["date"] for x in r["rows"]}
    assert "2026-10-05" in dates and not any(d.startswith("2026-1") and not d.startswith("2026-10") for d in dates)
    # 5:30着 9.2t の行がそのまま残る
    assert any(x["text"] == "加古川→江東区　ﾄｳﾜ電機 5:30着 9.2t" for x in r["rows"])


def test_excel_shape_month_comes_from_title_option_or_is_asked():
    rows = [["架空 配車表", "", "", ""], ["車両No.", "車種", "1", "2", "3"], ["", "", "木", "金", "土"], ["1234", "4t", "加古川→江東", "", ""]]
    lay = own.detect_haisha(rows, None, None)
    assert lay["needs_month"] is True and lay["kind"] == "wide"
    assert own.read_haisha(rows, 2026)["needs_month"] is True
    assert [x["reason"] for x in own.read_haisha(rows, 2026)["unreadable"]] == ["月が分かりません"]
    lay = own.detect_haisha(rows, None, 10)
    assert lay["needs_month"] is False and lay["heads"][2:5] == ["10/1(木)", "10/2(金)", "10/3(土)"]
    r = own.read_haisha(rows, 2026, lay)
    assert [x["date"] for x in r["rows"]] == ["2026-10-01"]
    rows[0][0] = "10月分 配車表"
    assert own.detect_haisha(rows)["title_month"] == [None, 10]


def test_excel_shape_year_is_assumed_from_the_weekday_row_when_the_title_has_no_year():
    rows = [["10月分 配車表", "", "", "", ""], ["車両No.", "車種", "1", "2", "3"], ["", "", "木", "金", "土"], ["1234", "4t", "加古川→江東", "", ""]]
    yc = own.infer_year(own.haisha_heads(own.detect_haisha(rows)))
    assert yc["year"] == 2026 and yc["year_assumed"] is True and yc["candidates"] == [2020, 2026]


def test_gosha_is_the_vehicle_column_only_when_there_is_no_other():
    rows = [["号車", "乗務員", "10/1(木)", "10/2(金)", "10/3(土)"], ["3", "架空 山田", "加古川→江東", "", ""]]
    lay = own.detect_haisha(rows)
    assert lay["kind"] == "wide" and lay["head"][lay["plate_col"]] == "号車"
    r = own.read_haisha(rows, 2026)  # SPEC §47: 4 桁の車番がどこにも無いので、「3」はそのファイルの中の車の名前（3号車）として読む
    assert len(r["rows"]) == 1 and r["rows"][0]["plate"] == "3号車" and r["unreadable"] == []


def test_header_row_is_searched_in_the_first_ten_rows_and_blank_rows_are_skipped():
    rows = [[""] * 5, ["架空 題名"] + [""] * 4, [""] * 5, ["車番", "10/1(木)", "10/2(金)", "10/3(土)", ""], ["1234", "加古川→江東", "江東→加古川（空）", "", ""]]
    r = own.read_haisha(rows, 2026)
    assert r["layout"]["header_line"] == 4 and len(r["rows"]) == 2
    assert own.detect_haisha([[""] * 3] * 12 + [["車番", "10/1(木)", "10/2(金)", "10/3(土)"]]) is None  # 11 行目より下は探さない


def test_vertical_shape_one_row_one_trip():
    rows = rows_of("haisha_vertical_2026-10.csv")
    lay = own.detect_haisha(rows)
    assert lay["kind"] == "vertical" and lay["header_row"] == 0
    r = own.read_haisha(rows, 2026)
    assert len(r["rows"]) == 10 and r["unreadable"] == [] and [p["plate"] for p in r["plates"]] == ["9001", "1234"]
    first = r["rows"][0]
    assert first["date"] == "2026-10-05" and first["parsed"]["legs"] == [{"frm": "加古川", "to": "江東区", "empty": False, "arr": "05:30", "weight_kg": 9200.0, "loaded_mark": True}]  # 区分の「実車」は実車の印
    assert r["rows"][1]["parsed"]["legs"][0]["empty"] is True  # 区分の列が「空車」
    last_out = [x for x in r["rows"] if x["plate"] == "1234"][0]
    assert last_out["parsed"]["legs"][0]["weight_kg"] == 1800.0  # 1800kg


def test_vertical_weight_units_follow_the_header_and_two_weights_need_a_confirm():
    base = [["日付", "車番", "発地", "着地", "重量(t)"], ["2026/10/1", "1234", "加古川", "江東", "9.2"]]
    assert own.read_haisha(base, 2026)["rows"][0]["parsed"]["legs"][0]["weight_kg"] == 9200.0
    kg = [["日付", "車番", "発地", "着地", "重量kg"], ["2026/10/1", "1234", "加古川", "江東", "4,600"]]
    assert own.read_haisha(kg, 2026)["rows"][0]["parsed"]["legs"][0]["weight_kg"] == 4600.0
    bad = [["日付", "車番", "発地", "着地", "重量"], ["2026/10/1", "1234", "加古川", "江東", "-3t"]]
    assert own.read_haisha(bad, 2026)["rows"][0]["parsed"]["errors"] == [own.WEIGHT_SIGN_ERR]


def test_same_last4_with_different_full_plates_are_kept_apart_and_reported():
    r = own.read_haisha(rows_of("haisha_same_last4.csv"), 2026)
    assert [p["plate"] for p in r["plates"]] == ["品川100あ1234", "練馬100い1234"]
    assert r["plate_notes"] == [{"kind": "split", "num": "1234", "forms": ["品川100あ1234", "練馬100い1234"]}]
    assert {x["plate"] for x in r["rows"]} == {"品川100あ1234", "練馬100い1234"}
    # 片方が 4 桁だけなら、同じ車として扱い、確認に出す
    merged = own.read_haisha([["車番", "10/1(木)", "10/2(金)", "10/3(土)"], ["神戸 100 か 1234", "加古川→江東", "", ""], ["1234", "", "江東→加古川", ""]], 2026)
    assert [p["plate"] for p in merged["plates"]] == ["1234"] and [n["kind"] for n in merged["plate_notes"]] == ["merge"]
    # 同じ書き方の繰り返しは何も出さない
    same = own.read_haisha([["車番", "10/1(木)", "10/2(金)", "10/3(土)"], ["神戸 100 か 1234", "加古川→江東", "", ""], ["神戸100か1234", "", "江東→加古川", ""]], 2026)
    assert same["plate_notes"] == [] and len(same["plates"]) == 1
    assert own.plate_identity("品川 100 あ 1234") == ("1234", "品川100あ1234") and own.plate_identity("4t-1234") == ("1234", None)


def test_unknown_columns_with_data_are_reported_as_non_blocking():
    rows = [["車番", "付記", "10/1(木)", "10/2(金)", "10/3(土)"], ["1234", "夜勤", "加古川→江東", "", ""]]
    r = own.read_haisha(rows, 2026)
    assert [(e["header"], e["samples"]) for e in r["extra_cols"]] == [("付記", ["夜勤"])] and r["unreadable"] == []
    r2 = own.read_haisha(rows, 2026, None, {"付記": "skip"})
    assert r2["extra_cols"] == []


def test_user_roles_make_an_unrecognised_vertical_header_readable():
    rows = [["いつ", "車", "積み", "降ろし先"], ["2026/10/1", "1234", "加古川", "江東"]]
    assert own.detect_haisha(rows) is None
    roles = {"いつ": "date", "車": "plate", "積み": "from", "降ろし先": "to"}
    lay = own.detect_haisha(rows, roles)
    assert lay and lay["kind"] == "vertical"
    assert own.read_haisha(rows, 2026, None, roles)["rows"][0]["parsed"]["legs"][0]["to"] == "江東"
    assert own.judged_row(rows) == 0


# ---------------------------------------------------------------- Python: デジタコ

def _dict_rows(name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text_of(name))))


def test_digitaco_other_company_detail_reads_date_plus_time_columns_across_midnight():
    rows = rows_of("digitaco_detail_5678.csv")
    assert own.detect_digitaco(rows)["kind"] == "detail"
    r = own.parse_digitaco(_dict_rows("digitaco_detail_5678.csv"), lambda n: n, "5678")
    assert [(x["start"], x["end"], x["km"], x["jk"]) for x in r["runs"][:2]] == [
        ("2026-10-05T20:10", "2026-10-06T03:00", 551.8, 1), ("2026-10-06T04:10", "2026-10-06T13:20", 560.4, 0)]
    assert [(s["start"], s["end"], s["minutes"], s["name"]) for s in r["stops"][:2]] == [
        ("2026-10-06T03:00", "2026-10-06T04:10", 70, "江東区辰巳"), ("2026-10-06T13:20", "2026-10-06T13:40", 20, "加古川営業所")]
    assert r["errors"] == []


def test_digitaco_daily_summary_makes_loaded_and_empty_runs():
    rows = rows_of("digitaco_summary_9001.csv")
    assert own.detect_digitaco(rows)["kind"] == "summary"
    r = own.parse_digitaco_summary(_dict_rows("digitaco_summary_9001.csv"), "9001")
    assert len(r["runs"]) == 8 and r["errors"] == []
    assert [(x["jk"], x["km"]) for x in r["runs"][:2]] == [(1, 563.2), (0, 568.3)]
    assert r["runs"][0]["start"] == "2026-10-05T19:40" and r["runs"][0]["end"] == "2026-10-06T17:10"
    bad = own.parse_digitaco_summary([{"日付": "x", "車番": "9001", "実車距離": "1", "空車距離": "2"}, {"日付": "2026/10/1", "車番": "9", "実車距離": "1", "空車距離": "2"}])
    assert [e["reason"] for e in bad["errors"]] == ["日付が読めません", "車番が読めません"]


def test_digitaco_duplicates_are_counted_once():
    r = own.parse_digitaco(_dict_rows("digitaco_detail_5678.csv"), lambda n: n, "5678")
    part = {"file": "a.csv", "plate": "5678", "runs": r["runs"], "stops": r["stops"]}
    twin = {"file": "b.csv", "plate": "5678", "runs": [dict(x) for x in r["runs"]], "stops": [dict(x) for x in r["stops"]]}
    d = own.dedupe_digitaco([part, twin])
    assert d["dropped"] == {"runs": 4, "stops": 4, "by_file": {"b.csv": {"runs": 4, "stops": 4}}}
    assert len(d["parts"][0]["runs"]) == 4 and d["parts"][1]["runs"] == [] and "8" in own.dedupe_note(d["dropped"])
    # 車番や距離が違えば別の走行
    other = {"file": "c.csv", "plate": "9999", "runs": [dict(x, plate="9999") for x in r["runs"]], "stops": []}
    assert own.dedupe_digitaco([part, other])["dropped"]["runs"] == 0


def test_unknown_digitaco_columns_need_a_role_and_user_roles_make_them_readable():
    rows = rows_of("digitaco_unknown_cols_9012.csv")
    assert own.detect_digitaco(rows) is None
    need = own.needing_roles("不明", "d.csv", rows[0], rows[1:], lambda h: None, set(), True, 1)
    assert [n["column"] for n in need] == ["日時A", "日時B", "種類", "キロ", "荷", "場所名"] and all(n["blocking"] for n in need)
    assert [n["suggested_role"] for n in need][:2] == ["start_time", "end_time"] and need[0]["samples"][0] == "2026-10-05 20:10"
    assert {o["role"] for o in need[0]["options"]} >= {"date", "plate", "from", "to", "weight", "skip", "start_time", "loaded_km"}
    roles = {"日時A": "start_time", "日時B": "end_time", "種類": "kind", "キロ": "distance_km", "荷": "loaded_flag", "場所名": "place_name"}
    assert own.detect_digitaco(rows, {own.col_key(k): v for k, v in roles.items()})["kind"] == "detail"
    got = own.parse_digitaco(_dict_rows("digitaco_unknown_cols_9012.csv"), lambda n: n, "9012", {own.col_key(k): v for k, v in roles.items()})
    assert len(got["runs"]) == 2 and len(got["stops"]) == 1 and got["errors"] == []


def test_shape_signature_does_not_change_when_the_month_or_the_roles_change():
    a = own.shape_signature("wide", ["車両No.", "車種", "10/1(木)", "10/2(金)"])
    b = own.shape_signature("wide", ["車両No.", "車種", "11/1(日)", "11/2(月)", "11/3(火)"])
    assert a == b and a != own.shape_signature("vertical", ["車両No.", "車種", "10/1(木)", "10/2(金)"])


# ---------------------------------------------------------------- Python: 重さの確認と文言

def _leg(i, plate, kg, kind="loaded", multi=None):
    return {"id": f"L{i:03d}", "plate": plate, "date": f"2026-10-0{i}", "kind": kind, "km": 100.0, "weight_kg": kg, "weights_multi": multi,
            "ref": {"file": "h.csv", "where": f"{i} 行目", "text": "x"}}


def test_zero_weight_and_two_weights_go_to_confirm_items_and_are_left_out_of_the_load_rate():
    vs = [{"plate": "1234", "cls": "大型", "gvw_kg": 0, "max_load_kg": 10000, "tachograph_label": ""}]
    legs = [_leg(1, "1234", 0.0), _leg(2, "1234", None, multi=[3000.0, 4000.0]), _leg(3, "1234", 6000.0), _leg(4, "1234", None)]
    items = own.weight_confirm_items(legs, vs)
    assert [(i["leg"], i["original"]) for i in items] == [
        ("L001", "配車表の重さが 0t の実車の区間です"), ("L002", "1 つのセルに重さが 2 つ書いてあります（3.0t、4.0t）")]
    kp = own.kpis(legs, vs)["load_rate"]
    assert kp["zero_weight"] == 1 and kp["multi_weight"] == 1 and kp["legs"] == 1 and kp["value"] == pytest.approx(0.6)


@pytest.mark.parametrize("w,cap,want", [
    (12000.0, 10000.0, ("12.0t", "10.0t")), (10040.0, 10000.0, ("10.04t", "10.00t")), (10004.0, 10000.0, ("10.004t", "10.000t")),
    (2700.4, 2700.0, ("2,700.40kg", "2,700.00kg")), (2000.01, 2000.0, ("2,000.01kg", "2,000.00kg")),
])
def test_rounded_excess_never_prints_the_same_number_twice(w, cap, want):
    a, b = own.tons_pair(w, cap)
    assert (a, b) == want and a != b


def test_over_max_message_and_insight_use_digits_that_differ():
    vs = [{"plate": "1234", "cls": "大型", "gvw_kg": 0, "max_load_kg": 10000, "tachograph_label": ""}]
    legs = [_leg(1, "1234", 10040.0)]
    assert own.weight_confirm_items(legs, vs)[0]["original"] == "配車表の重さ 10.04t が、最大積載量 10.00t を超えています"
    ins = [i for i in own.insights({"legs": legs, "vehicles": vs, "places": {}, "stops": [], "shifts": []}) if i["rule"] == 7]
    assert ins[0]["evidence"][0]["text"] == "重さ 10.04t > 最大積載量 10.00t"


def test_insight_rules_have_names_by_meaning_and_own_place_names_are_kept():
    places = {"p01": {"muni": "加古川市", "pseudo": "地点 01", "short": "加古川", "label": "加古川営業所"},
              "p02": {"muni": "江東区", "pseudo": "地点 02", "short": "江東", "label": "江東区辰巳"}}
    stops = [{"id": f"S{i}", "plate": "1234", "place": "p02", "name": "x", "start": f"2026-10-0{i}T03:00", "end": f"2026-10-0{i}T04:00", "minutes": 60, "ref": {}} for i in range(1, 6)]
    legs = [{"id": f"L{i}", "plate": "1234", "date": f"2026-10-0{i}", "kind": "empty", "km": 10.0, "dir": "back", "cycle_wd": 0, "cycle_dep": None, "ref": {}} for i in range(1, 6)]
    base = {"legs": legs, "stops": stops, "shifts": [], "vehicles": [], "places": places, "base": "p01", "dock": "p02"}
    own_ins = own.insights({**base, "own_names": True})
    r3 = next(i for i in own_ins if i["rule"] == 3)
    assert "江東区辰巳" in r3["text"] and "地点 02" not in r3["text"]
    sample_r3 = next(i for i in own.insights(base) if i["rule"] == 3)
    assert "江東区の地点 02" in sample_r3["text"]  # サンプルの文は変えない
    assert next(i for i in own_ins if i["rule"] == 1)["rule_label_ja"] == "同じ曜日の帰りがいつも空"
    assert {i["rule"]: i["rule_label_ja"] for i in own_ins}[2] == "空で走った区間"
    assert all("規則" not in i["rule_label_ja"] for i in own_ins)


def test_column_reason_is_plain_japanese():
    for args in [("デジタコ", "ST_TIME", "start_time", True, 1.0, "YYYY-MM-DD HH:MM の日時"), ("デジタコ", "X9", "distance_km", False, 0.88, "小数まじりの数字"),
                 ("デジタコ", "Q", "", False, 0.0, "（当てはまる形がありません）"), ("配車表", "日付の見出し（31 列）", "date", True, 1.0, "x"),
                 ("デジタコ", "区分", "kind", True, 0.5, "1 か 2")]:
        t = own.column_reason_ja(*args)
        assert t and not any(w in t for w in ("辞書", "形", "0.88", "plate", "vehicle_type", "start_time", "distance_km"))


def test_examples_exist_and_are_marked_fictional():
    for n in NEW_EXAMPLES:
        b = (EX / n).read_bytes()
        assert b and b"\r" not in b, n
    assert "架空" in text_of("haisha_excel_2026-10.csv") and "架空" in text_of("haisha_vertical_2026-10.csv")
    g = (ROOT / "docs" / "guide" / "自社の運行を分析する.md").read_text(encoding="utf-8")
    for n in NEW_EXAMPLES:
        assert n in g, n  # 手引きの例のファイルの表に載っている
    assert "架空" in g and "\r" not in g


# ---------------------------------------------------------------- JS（ヘッドレス Edge）と Python の照合

@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        try:
            page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
            assert page.js("typeof OwnCalc === 'object'")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "own_calc.js を読み込めません", e, "own_v41_fail")
        yield page


def J(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def _an(page, files, opts=None):
    return page.js(f"OwnCalc.analyze({J(files)}, {J(opts or {})})")


def _f(name: str, as_name: str | None = None) -> dict:
    return {"name": as_name or name, "text": text_of(name)}


MUNIS = [
    {"code": "28210", "name": "加古川市", "pref": "兵庫県", "lon": 134.841, "lat": 34.757}, {"code": "28110", "name": "神戸市中央区", "pref": "兵庫県", "lon": 135.192, "lat": 34.701}, {"code": "28101", "name": "神戸市東灘区", "pref": "兵庫県", "lon": 135.269, "lat": 34.732},
    {"code": "28201", "name": "姫路市", "pref": "兵庫県", "lon": 134.690, "lat": 34.816}, {"code": "13108", "name": "江東区", "pref": "東京都", "lon": 139.817, "lat": 35.673},
]


def test_js_and_python_read_the_same_real_world_cells(calc):
    for text, *_ in CELL_VECTORS:
        assert calc.js(f"OwnCalc.parseCell({J(text)})") == as_json(own.parse_cell(text)), text
    for text in ["4t車 2.5t 3t", "5トン 6トン", "9.2t", "-2t 3t", "10t-1234", "4,600kg"]:
        assert calc.js(f"OwnCalc.parseWeights({J(text)})") == as_json(own.parse_weights(text)), text


def test_js_and_python_agree_on_header_aliases_and_plates(calc):
    for head, role in HEAD_VECTORS:
        assert calc.js(f"OwnCalc.haishaRole({J(head)})") == role, head
    for raw in ["品川 100 あ 1234", "神戸100か12-34", "4t-1234", "1234", "なにわ 100 あ 12-34", "", "12345", "ａｂ１２３４"]:
        assert calc.js(f"OwnCalc.plateIdentity({J(raw)})") == list(own.plate_identity(raw)), raw


@pytest.mark.parametrize("name", ["haisha_excel_2026-10.csv", "haisha_vertical_2026-10.csv", "haisha_intra_pref.csv", "haisha_same_last4.csv", "haisha_2026-10.csv"])
def test_js_and_python_read_the_same_layout_and_rows(calc, name):
    rows = rows_of(name)
    py = as_json(own.read_haisha(rows, 2026))
    js = calc.js(f"OwnCalc.readHaisha(OwnCalc.parseCsv({J(text_of(name))}), 2026)")
    js["stats"].pop("plate_groups", None)
    assert js == py, [k for k in py if js.get(k) != py[k]]
    lay_py = as_json(own.detect_haisha(rows))
    lay_js = calc.js(f"OwnCalc.detectHaisha(OwnCalc.parseCsv({J(text_of(name))}))")
    assert lay_js == lay_py


def test_js_and_python_agree_on_day_number_headers_and_user_roles(calc):
    rows = [["架空 配車表", "", "", ""], ["車両No.", "車種", "1", "2", "3"], ["", "", "木", "金", "土"], ["1234", "4t", "加古川→江東", "", ""]]
    for month in (None, 10):
        py = as_json(own.read_haisha(rows, 2026, None, None, month))
        js = calc.js(f"OwnCalc.readHaisha({J(rows)}, 2026, null, null, {J(month)})")
        js["stats"].pop("plate_groups", None)
        assert js == py, month
    vr = [["いつ", "車", "積み", "降ろし先"], ["2026/10/1", "1234", "加古川", "江東"]]
    roles = {"いつ": "date", "車": "plate", "積み": "from", "降ろし先": "to"}
    assert calc.js(f"OwnCalc.detectHaisha({J(vr)}, null)") is None
    py = as_json(own.read_haisha(vr, 2026, None, roles))
    js = calc.js(f"OwnCalc.readHaisha({J(vr)}, 2026, null, {J({k.upper(): v for k, v in roles.items()})})")
    js["stats"].pop("plate_groups", None)
    assert js == py


def test_js_and_python_agree_on_digitaco_shapes_summary_duplicates_and_roles(calc):
    for name in ("digitaco_detail_5678.csv", "digitaco_summary_9001.csv", "digitaco_unknown_cols_9012.csv", "digitaco_1234.csv"):
        rows = rows_of(name)
        assert calc.js(f"OwnCalc.detectDigitaco({J(rows)})") == as_json(own.detect_digitaco(rows)), name
    rows = rows_of("digitaco_detail_5678.csv")
    py = as_json(own.parse_digitaco(_dict_rows("digitaco_detail_5678.csv"), lambda n: n, "5678"))
    assert calc.js(f"OwnCalc.parseDigitaco({J(rows)}, (n) => n, '5678')") == py
    rows = rows_of("digitaco_summary_9001.csv")
    py = as_json(own.parse_digitaco_summary(_dict_rows("digitaco_summary_9001.csv"), "9001"))
    assert calc.js(f"OwnCalc.parseDigitacoSummary({J(rows)}, '9001')") == py
    parts = [{"file": "a.csv", "plate": "5678", "runs": as_json(own.parse_digitaco(_dict_rows("digitaco_detail_5678.csv"), lambda n: n, "5678"))["runs"], "stops": []}]
    parts.append({**parts[0], "file": "b.csv"})
    assert calc.js(f"OwnCalc.dedupeDigitaco({J(parts)})") == as_json(own.dedupe_digitaco(parts))
    for cell, want in [("20:10", 1210), ("5:30:15", 330), ("24:00", None), ("x", None)]:
        assert calc.js(f"OwnCalc.parseHm({J(cell)})") == own.parse_hm(cell) == want
    for cell, want in [("2026/10/6", "2026-10-06"), ("2026年10月6日", "2026-10-06"), ("10/6", None), ("2026-02-30", None)]:
        got = own.parse_date_only(cell)
        assert (got.isoformat() if got else None) == want == calc.js(f"OwnCalc.parseDateOnly({J(cell)})")


def test_js_and_python_agree_on_rounded_messages_and_weight_cards(calc):
    for w, cap in [(12000.0, 10000.0), (10040.0, 10000.0), (10004.0, 10000.0), (2700.4, 2700.0), (2000.01, 2000.0)]:
        assert calc.js(f"OwnCalc.tonsPair({w}, {cap})") == list(own.tons_pair(w, cap)), (w, cap)
    vs = [{"plate": "1234", "cls": "大型", "gvw_kg": 0, "max_load_kg": 10000, "tachograph_label": ""}]
    legs = [_leg(1, "1234", 0.0), _leg(2, "1234", None, multi=[3000.0, 4000.0]), _leg(3, "1234", 10040.0), _leg(4, "1234", 6000.0)]
    py = own.weight_confirm_items(legs, vs)
    js = calc.js(f"OwnCalc.weightConfirmItems({J(legs)}, {J(vs)})")
    pick = lambda xs: [(x["leg"], x["title"], x["original"], x["proposal"]) for x in xs]  # noqa: E731
    assert pick(js) == pick(py) and len(py) == 3
    assert calc.js(f"OwnCalc.kpis({J(legs)}, {J(vs)}).load_rate") ["zero_weight"] == own.kpis(legs, vs)["load_rate"]["zero_weight"] == 1
    for args in [("デジタコ", "ST_TIME", "start_time", True, 1.0, "x"), ("デジタコ", "X9", "distance_km", False, 0.88, "小数まじりの数字"), ("デジタコ", "Q", "", False, 0.0, "y"),
                 ("配車表", "日付の見出し（31 列）", "date", True, 1.0, "x"), ("デジタコ", "区分", "kind", True, 0.5, "1 か 2")]:
        assert calc.js(f"OwnCalc.columnReasonJa(...{J(list(args))})") == own.column_reason_ja(*args), args
    assert calc.js(f"OwnCalc.judgedRow({J([['', ''], ['a', 'b', 'c'], ['x']])})") == own.judged_row([["", ""], ["a", "b", "c"], ["x"]]) == 1
    assert calc.js("OwnCalc.shapeSignature('wide', ['車両No.', '10/1(木)', '10/2(金)'])") == own.shape_signature("wide", ["車両No.", "10/1(木)", "10/2(金)"])


# ---------------------------------------------------------------- JS: analyze が実務の形のファイルから結果を返す

def test_analyze_excel_shape_reads_every_cell_without_asking_and_without_unreadable_rows(calc):
    d = _an(calc, [_f("haisha_excel_2026-10.csv")], {"munis": MUNIS})
    assert d["failed"] is None and d["counts"]["legs"] == 18 and d["unreadable"] == [] and d["column_roles_needed"] == []
    legs = {(l["plate"], l["date"]): l for l in d["legs"]}
    big = legs[("9001", "2026-10-05")]
    assert big["arr_hint"] == "05:30" and big["weight_kg"] == 9200.0 and big["kind"] == "loaded"  # 5:30着 9.2t の 9.2t を時刻にしない
    assert legs[("9001", "2026-10-06")]["kind"] == "empty"  # 空車
    assert legs[("1234", "2026-10-05")]["weight_kg"] == 2500.0  # 4t車 2.5t
    assert legs[("1234", "2026-10-28")]["weight_kg"] == 5000.0  # 5トン
    assert legs[("1234", "2026-10-14")]["weight_kg"] is None and legs[("1234", "2026-10-21")]["weight_kg"] == 0.0
    cards = [c for c in d["confirm"] if c["kind"] == "weight"]
    assert [c["original"] for c in cards] == ["1 つのセルに重さが 2 つ書いてあります（3.0t、4.0t）", "配車表の重さが 0t の実車の区間です"]
    lr = d["kpis"]["load_rate"]
    assert lr["zero_weight"] == 1 and lr["multi_weight"] == 1 and lr["legs"] == 4
    assert [p["raw_plate"] for p in d["vehicles"]] == ["神戸 100 か 9001", "4t-1234", "神戸 100 さ 5678"]
    f = d["files"][0]
    assert f["shape"] == "日付が列に並ぶ横の形" and "見出しは 3 行目です" in f["layout_note_ja"] and "合計の行 1 行は読みませんでした" in f["layout_note_ja"]
    assert "「号車」「乗務員」「稼働日数」「備考」の列" in f["layout_note_ja"]
    assert d["year"] == 2026 and d["year_choice"]["year_assumed"] is False and d["layouts"]["haisha_excel_2026-10.csv"]["header_line"] == 3
    assert d["shape_signatures"]["haisha_excel_2026-10.csv"].startswith("wide|")


def test_analyze_weekday_row_decides_the_year_and_assumes_the_latest_year_up_to_2026(calc):
    t = text_of("haisha_excel_2026-10.csv").replace("2026年10月", "10月分")
    d = _an(calc, [{"name": "haisha.csv", "text": t}])
    yc = d["year_choice"]
    assert d["year"] == 2026 and yc["year_assumed"] is True and yc["candidates"] == [2020, 2026] and yc["source"] == "weekday" and not d.get("needs_year")
    d2 = _an(calc, [{"name": "haisha.csv", "text": t}], {"year": 2020})
    assert d2["year"] == 2020 and d2["year_choice"]["year_assumed"] is False and d2["year_choice"]["by_user"] is True
    # 水曜始まりの 10 月（2025・2031・2036）は、先の年にしない
    wed = "車番,車種,10/1(水),10/2(木)\n1234,大型,加古川→江東,江東→加古川\n"
    d3 = _an(calc, [{"name": "h.csv", "text": wed}])
    assert d3["year"] == 2025 and 2036 in d3["year_choice"]["candidates"] and d3["year_choice"]["year_assumed"] is True


def test_analyze_day_headers_without_a_month_ask_for_the_month(calc):
    t = "架空 配車表\n\n車両No.,車種,1,2,3\n,,木,金,土\n1234,4t,加古川→江東 着5:30,江東→加古川 空車,\n"
    d = _an(calc, [{"name": "h.csv", "text": t}])
    assert d["needs_month"] is True and d["month_choice"]["candidates"] == list(range(1, 13)) and d["failed"] and "月が分かりません" in d["failed"]
    d2 = _an(calc, [{"name": "h.csv", "text": t}], {"month": 10})
    assert d2["needs_month"] is False and d2["counts"]["legs"] == 2 and d2["legs"][0]["date"] == "2026-10-01"
    dg = "KBN,ST_TIME,ED_TIME,ODO_KM,JK_FLG,POS_NAME,DRV_CD\n1,2026-10-01 00:00,2026-10-01 05:00,500,1,,D01\n2,2026-10-01 05:00,2026-10-01 06:00,,,江東,D01\n"
    d3 = _an(calc, [{"name": "h.csv", "text": t}, {"name": "digitaco_1234.csv", "text": dg}])
    assert d3["needs_month"] is False and d3["legs"][0]["date"] == "2026-10-01"  # デジタコの日付の月を使う


def test_analyze_vertical_shape(calc):
    d = _an(calc, [_f("haisha_vertical_2026-10.csv")], {"munis": MUNIS})
    assert d["failed"] is None and d["counts"]["legs"] == 10 and d["counts"]["empty"] == 5 and d["counts"]["unreadable"] == 0
    assert d["files"][0]["shape"] == "1 行 = 1 便の縦の形" and d["kpis"]["actual_rate"]["of"] == 10
    first = d["legs"][0]
    assert first["weight_kg"] == 9200.0 and first["ref"]["where"] == "2 行目" and first["frm"] and first["to"]


def test_analyze_same_last4_keeps_two_vehicles_and_adds_a_confirm_item(calc):
    d = _an(calc, [_f("haisha_same_last4.csv")], {"munis": MUNIS})
    assert [v["plate"] for v in d["vehicles"]] == ["品川100あ1234", "練馬100い1234"]
    cards = [c for c in d["confirm"] if c["kind"] == "plate"]
    assert len(cards) == 1 and "別の車として数えました" in cards[0]["proposal"] and cards[0]["title"] == "車番の末尾 4 桁 1234 が重なっています"
    # デジタコの 4 桁は、先に出る車に当てて、確認に出す
    dg = text_of("digitaco_detail_5678.csv").replace("2026/10/05", "2026/10/01").replace("2026/10/12", "2026/10/02")
    d2 = _an(calc, [_f("haisha_same_last4.csv"), {"name": "digitaco_1234.csv", "text": dg}], {"munis": MUNIS})
    assert {c["key"] for c in d2["confirm"] if c["kind"] == "plate"} == {"plate:1234", "plate-digitaco:digitaco_1234.csv"}
    assert all(r["plate"] == "品川100あ1234" for r in d2["legs"] if r["dref"])


def test_analyze_intra_prefecture_round_trip_returns_valid_data(calc):
    d = _an(calc, [_f("haisha_intra_pref.csv")], {"munis": MUNIS})
    assert d["failed"] is None and d["counts"]["legs"] == 13 and d["counts"]["unknown"] == 0
    assert d["places"][d["base"]]["short"] == "加古川" and d["places"][d["dock"]]["short"] == "神戸"
    assert d["geo"] == {"base_pref": "兵庫県", "dock_pref": "兵庫県", "same_pref": True}
    assert all(l["km"] and 10 < l["km"] < 80 for l in d["legs"])  # 直線距離 × 1.25 の推定。県内の短い距離
    assert d["kpis"]["actual_rate"]["value"] is not None and 0 < d["kpis"]["actual_rate"]["value"] < 1
    assert d["table"] and all(isinstance(r["trips"], int) for r in d["table"])
    assert json.dumps(d)  # JSON にできる（NaN・Infinity を含まない）
    assert d["route"]["path"] is None


def test_analyze_digitaco_loaded_twice_is_counted_once_and_reported(calc):
    one = _an(calc, [_f("digitaco_detail_5678.csv")])
    twice = _an(calc, [_f("digitaco_detail_5678.csv"), _f("digitaco_detail_5678.csv", "digitaco_5678_copy.csv")])
    assert one["counts"]["legs"] == twice["counts"]["legs"] == 4 and one["counts"]["stops"] == twice["counts"]["stops"] == 4
    assert twice["digitaco_duplicates"] == {"runs": 4, "stops": 4, "by_file": {"digitaco_detail_5678.csv": {"runs": 4, "stops": 4}}}  # 名前の順で後のほうを捨てる
    assert twice["files_unread"] == [] and twice["files_read"] == 2
    assert any(n["kind"] == "duplicate" and "8 件" in n["text_ja"] for n in twice["notices"])
    assert [f.get("duplicates") for f in twice["files"]] == [None, {"runs": 4, "stops": 4}]
    assert one["digitaco_duplicates"]["runs"] == 0 and one["notices"] == []
    # 距離が違えば別の走行
    changed = text_of("digitaco_detail_5678.csv").replace("551.8", "552.0")
    mixed = _an(calc, [_f("digitaco_detail_5678.csv"), {"name": "digitaco_5678_b.csv", "text": changed}])
    assert mixed["counts"]["legs"] == 6 and mixed["digitaco_duplicates"]["runs"] == 2


def test_analyze_daily_summary_digitaco_gives_a_real_actual_rate_and_is_not_double_counted_with_a_haisha(calc):
    d = _an(calc, [_f("digitaco_summary_9001.csv")])
    ar = d["kpis"]["actual_rate"]
    assert d["failed"] is None and d["counts"]["legs"] == 8 and ar["km_loaded"] == pytest.approx(4 * 563.2) and ar["km_empty"] == pytest.approx(4 * 568.3)
    assert ar["value"] == pytest.approx(2252.8 / (2252.8 + 2273.2)) and d["files"][0]["shape"] == "1 行 = 1 日の集計の形"
    both = _an(calc, [_f("haisha_excel_2026-10.csv"), _f("digitaco_summary_9001.csv")], {"munis": MUNIS})
    assert both["counts"]["legs"] == 18 and any(n["kind"] == "summary" for n in both["notices"])


def test_analyze_other_company_digitaco_with_a_haisha_links_by_plate_and_date(calc):
    d = _an(calc, [_f("haisha_excel_2026-10.csv"), _f("digitaco_detail_5678.csv")], {"munis": MUNIS})
    assert d["failed"] is None and d["counts"]["stops"] == 4 and d["files_unread"] == []
    assert [f["shape"] for f in d["files"]][1] == "1 行 = 1 回の走行か停車の縦の形"


def test_analyze_unknown_digitaco_columns_ask_for_roles_then_read_with_them(calc):
    f = _f("digitaco_unknown_cols_9012.csv")
    d = _an(calc, [f])
    assert d["failed"] and d["counts"]["legs"] == 0
    need = d["column_roles_needed"]
    assert [n["column"] for n in need] == ["日時A", "日時B", "種類", "キロ", "荷", "場所名"] and all(n["blocking"] for n in need)
    assert need[0]["suggested_role"] == "start_time" and need[1]["suggested_role"] == "end_time" and need[0]["files"] == [f["name"]]
    u = d["unreadable"][0]
    assert u["judged_line"] == 1 and u["judged_cells"][:2] == ["日時A", "日時B"] and "例: 区分,開始日時,終了日時,走行距離" in u["howto_ja"] and "車番,車種,10/1(木)" in u["howto_ja"]
    sig = d["shape_signatures"][f["name"]]
    roles = {"日時A": "start_time", "日時B": "end_time", "種類": "kind", "キロ": "distance_km", "荷": "loaded_flag", "場所名": "place_name"}
    ok = _an(calc, [f], {"column_roles": {"digitaco": roles}})
    assert ok["failed"] is None and ok["counts"]["legs"] == 2 and ok["counts"]["stops"] == 1 and ok["column_roles_needed"] == []
    assert ok["shape_signatures"][f["name"]].startswith("digitaco-detail|") and sig.startswith("unknown|")
    flat = _an(calc, [f], {"column_roles": roles})  # 平らな形でも同じ
    assert flat["counts"]["legs"] == 2
    cols = {c["column"]: c for c in ok["columns"]}
    assert cols["日時A"]["user_role"] == "start_time" and cols["日時A"]["role_label_ja"] == "開始の日時" and "あなたが選んだ" in cols["日時A"]["reason_ja"]
    assert not [c for c in ok["confirm"] if c["kind"] == "column"]


def test_analyze_user_roles_make_an_unrecognised_haisha_readable_and_skip_removes_a_column(calc):
    t = "いつ,車,積み,降ろし先\n2026/10/1,1234,加古川,江東\n2026/10/2,1234,江東,加古川\n"
    f = {"name": "h.csv", "text": t}
    d = _an(calc, [f])
    assert d["failed"] and [n["column"] for n in d["column_roles_needed"]] == ["いつ", "車", "積み"]  # 見出しが辞書にある「降ろし先」は聞かない
    ok = _an(calc, [f], {"column_roles": {"haisha": {"いつ": "date", "車": "plate", "積み": "from", "降ろし先": "to"}}})
    assert ok["failed"] is None and ok["counts"]["legs"] == 2
    memo = "車番,付記,10/1(木),10/2(金),10/3(土)\n1234,夜勤,加古川→江東,,\n"
    d2 = _an(calc, [{"name": "h.csv", "text": memo}])
    assert [(n["source"], n["column"], n["blocking"]) for n in d2["column_roles_needed"]] == [("配車表", "付記", False)]
    d3 = _an(calc, [{"name": "h.csv", "text": memo}], {"column_roles": {"haisha": {"付記": "skip"}}})
    assert d3["column_roles_needed"] == []


def test_analyze_unreadable_files_report_the_judged_row_and_how_to_fix(calc):
    d = _an(calc, [_f("haisha_excel_2026-10.csv"), {"name": "memo.csv", "text": "名前,年齢\n山田,30\n"}])
    u = [x for x in d["unreadable"] if x["file"] == "memo.csv"][0]
    assert "配車表にもデジタコにも見えません" in u["reason"] and u["judged_line"] == 1 and u["judged_cells"] == ["名前", "年齢"] and "例:" in u["howto_ja"]
    assert d["files_unread"] == [{"file": "memo.csv", "reason": "配車表にもデジタコにも見えません"}]


def test_analyze_insights_keep_the_original_place_names_and_have_names_by_meaning(calc):
    lines = ["KBN,ST_TIME,ED_TIME,ODO_KM,JK_FLG,POS_NAME,DRV_CD"]
    for d in range(1, 7):
        lines += [f"1,2026-10-0{d} 00:00,2026-10-0{d} 05:00,500,1,,D01", f"2,2026-10-0{d} 05:00,2026-10-0{d} 06:00,,,江東区辰巳,D01"]
    d = _an(calc, [{"name": "digitaco_1234.csv", "text": "\n".join(lines) + "\n"}], {"munis": MUNIS})
    r3 = [i for i in d["insights"] if i["rule"] == 3]
    assert r3 and "江東区辰巳" in r3[0]["text"] and "地点 0" not in r3[0]["text"]
    assert all(i["rule_label_ja"] and "規則" not in i["rule_label_ja"] for i in d["insights"])
    assert [i["rule_label_ja"] for i in r3] == ["同じ場所での長い待ち"]


def test_analyze_mapping_cards_are_in_plain_japanese(calc):
    d = _an(calc, [_f("haisha_2026-10.csv"), _f("digitaco_1234.csv")], {"munis": MUNIS})
    assert d["columns"]
    for c in d["columns"]:
        t = c["reason_ja"] + (c["role_label_ja"] or "")
        assert c["reason_ja"] and not any(w in t for w in ("辞書", "形", "0.88", "plate", "vehicle_type", "start_time"))
    cards = [c for c in d["confirm"] if c["kind"] == "column"]
    assert cards and all(c["reason_ja"] and "role_label_ja" in c for c in cards)


def test_analyze_over_max_message_uses_digits_that_differ(calc):
    t = "車番,車種,2026/10/1\n1234,大型,加古川→江東 10.04t\n"
    d = _an(calc, [{"name": "h.csv", "text": t}], {"largeMaxLoadKg": 10000})
    card = [c for c in d["confirm"] if c["kind"] == "weight"][0]
    assert card["original"] == "配車表の重さ 10.04t が、最大積載量 10.00t を超えています"
    assert [i for i in d["insights"] if i["rule"] == 7][0]["evidence"][0]["text"] == "重さ 10.04t > 最大積載量 10.00t"


def test_analyze_zero_and_multi_weight_decisions_recompute_the_rate(calc):
    t = "車番,車種,2026/10/1,2026/10/2,2026/10/3\n1234,大型,加古川→江東 6t,加古川→江東 3t 4t,加古川→江東 0t\n"
    files = [{"name": "h.csv", "text": t}]
    d = _an(calc, files)
    keys = {c["leg"]: c["key"] for c in d["confirm"] if c["kind"] == "weight"}
    assert len(keys) == 2 and d["kpis"]["load_rate"]["legs"] == 1
    multi_key = next(c["key"] for c in d["confirm"] if "2 つ" in c["original"])
    d2 = _an(calc, files, {"decisions": {multi_key: {"act": "fix", "text": "4t"}}})
    assert d2["kpis"]["load_rate"]["legs"] == 2 and d2["kpis"]["load_rate"]["multi_weight"] == 0
    c = next(c for c in d2["confirm"] if c["key"] == multi_key)
    assert c["decision"]["applied"] is True and "4.0t" in c["decision"]["note"]


def test_the_sample_is_unchanged_by_the_new_reader(calc):
    # 元のサンプルの配車表と同じ結果（区間 107 件）になること。既存の一致の試験（test_v35）と同じ前提で、件数だけ確かめる
    raw = ROOT / "data" / "own_sample" / "raw"
    files = [{"name": n, "text": (raw / n).read_text(encoding="utf-8")} for n in ("haisha_2026-10.csv", "digitaco_1234.csv", "digitaco_5678.csv")]
    d = _an(calc, files, {"munis": MUNIS})
    assert d["counts"]["legs"] >= 100 and d["year"] == 2026 and d["layouts"]["haisha_2026-10.csv"]["header_line"] == 1
    assert [x["reason"] for x in d["unreadable"] if x["file"] == "haisha_2026-10.csv"].count("月が分かりません") == 1


def test_analyze_excel_shape_saved_as_shift_jis_reads_the_same(calc):
    sj = list(text_of("haisha_excel_2026-10.csv").encode("cp932"))
    r = calc.js(
        "(function(){const r = OwnCalc.decode(new Uint8Array(%s)); const d = OwnCalc.analyze([{ name: 'h.csv', text: r.text, encoding: r.encoding }], {});"
        "return { enc: r.encoding, legs: d.counts.legs, unreadable: d.unreadable.length, w: d.legs.find(l => l.plate === '9001' && l.date === '2026-10-05').weight_kg, year: d.year };})()" % J(sj))
    assert r == {"enc": "Shift_JIS", "legs": 18, "unreadable": 0, "w": 9200.0, "year": 2026}


def test_designated_city_names_without_a_ward_are_placed_at_the_city_hall_ward():
    points = json.loads((ROOT / "data" / "geo" / "municipality_points.json").read_text(encoding="utf-8"))["points"]
    for city, ward in own.CITY_HALL_WARD.items():
        code = own.hall_ward_code(city, points)
        assert code and points[code]["name"] == f"{city}市{ward}", city
        assert own.hall_ward_code(city + "市", points) == code and own.hall_ward_code(city + " 倉庫", points) == code
    assert own.hall_ward_code("神戸", points) == "28110" and own.hall_ward_code("加古川", points) is None
    assert len(own.CITY_HALL_WARD) == 20 and "市役所のある区に置きました" in own.HALL_WARD_NOTE


def test_js_hall_ward_matches_python_and_the_place_is_marked_for_fixing(calc):
    points = json.loads((ROOT / "data" / "geo" / "municipality_points.json").read_text(encoding="utf-8"))["points"]
    munis = [{"code": c, "name": p["name"], "pref": p["pref"], "lon": p["lon"], "lat": p["lat"]} for c, p in points.items()]
    assert calc.js("OwnCalc.CITY_HALL_WARD") == own.CITY_HALL_WARD and calc.js("OwnCalc.HALL_WARD_NOTE") == own.HALL_WARD_NOTE
    for city in own.CITY_HALL_WARD:
        r = calc.js(f"OwnCalc.matchMuni({J(city)}, {J(munis)})")
        assert r["m"]["code"] == own.hall_ward_code(city, points) and r["m"]["hall_ward"] is True, city
    assert calc.js(f"OwnCalc.matchMuni('大阪狭山市', {J(munis)})")["m"].get("hall_ward") is None  # 市区町村の名前そのものは、区に置き換えない
    assert calc.js(f"OwnCalc.matchMuni('神戸市東灘区', {J(munis)})")["m"]["code"] == "28101"
    d = _an(calc, [_f("haisha_intra_pref.csv")], {"munis": munis})
    kobe = next(p for p in d["places"].values() if p["label"] == "神戸")
    assert kobe["placed"] is True and kobe["code"] == "28110" and kobe["note"] == own.HALL_WARD_NOTE
    assert [(u["name"], u["reason"]) for u in d["unplaced"]] == [("神戸", own.HALL_WARD_NOTE)] and d["unplaced"][0]["hall_ward"] is True
    assert d["geo"]["same_pref"] is True and d["places"][d["dock"]]["label"] == "神戸"
    # 区が分かれば直せる: 別名で東灘区に置き直すと、注記は消える
    d2 = _an(calc, [_f("haisha_intra_pref.csv")], {"munis": munis, "userAliases": {"神戸": "神戸市東灘区"}})
    assert next(p for p in d2["places"].values() if p["label"] == "神戸")["code"] == "28101" and d2["unplaced"] == []


def test_user_visible_text_has_no_dictionary_or_claude_score():
    d = json.loads((ROOT / "web" / "data" / "own_sample.json").read_text(encoding="utf-8"))
    texts = [c["formula"] for c in d["columns"]] + [f["verdict"] for f in d["files"]] + [c["formula"] for c in d["confirm"]]
    assert texts and not [t for t in texts if "辞書" in t or "Claude" in t or "max(" in t]
    assert any("列名で分かれば 1" in c["formula"] for c in d["columns"])
