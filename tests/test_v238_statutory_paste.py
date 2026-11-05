"""SPEC §23.8 輸送安全規則 第 8 条第 1 項の言葉の見出しで貼り付けた業務記録を読む（受入条件 G107〜G112）。

JS 側は同じ試験表（tests/data/waiting_vectors.json）を web/waiting.html と web/publish/waiting_artifact.html の ?selftest=1 が通す
（test_v237_waiting_page.py、test_v26_artifact.py）。ここでは Python 側の期待値の要点と、見出しの別名表が JS と同じことを確かめる。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from shipcompare import waiting

ROOT = Path(__file__).resolve().parents[1]
VEC = {c["name"]: c for c in json.loads((ROOT / "tests" / "data" / "waiting_vectors.json").read_text(encoding="utf-8"))["cases"]}
TEMPLATE = (ROOT / "tools" / "waiting_page_template.html").read_text(encoding="utf-8")

STATUTORY = ("集荷地点等", "荷待ち地点", "到着日時", "到着の日時", "荷役作業の開始日時", "荷役開始日時", "荷役作業の終了日時", "荷役終了日時",
             "出発日時", "出発の日時", "荷役作業等の内容", "荷主の確認の有無", "乗務員氏名", "運転者氏名", "車両登録番号", "自動車登録番号",
             "荷主都合", "荷主都合か", "荷主都合の有無")


def _js_aliases() -> dict[str, str]:
    # 見出しの別名の表は、地図の画面と共有する web/waiting_calc.js にある（SPEC §31.1）
    src = (ROOT / "web" / "waiting_calc.js").read_text(encoding="utf-8")
    body = src[src.index("const HEADER_ALIASES = {"):src.index("const CLASS_ALIASES")]
    return dict(re.findall(r"'([^']+)': '([^']+)'", body))


def test_g107_alias_tables_are_the_same_in_python_and_js():
    assert _js_aliases() == waiting._HEADER_ALIASES


def test_g107_statutory_wording_is_known():
    for h in STATUTORY:
        assert h in waiting._HEADER_ALIASES, h
    a = waiting._HEADER_ALIASES
    assert a["到着日時"] == "arrive" and a["荷役作業の終了日時"] == "end" and a["出発の日時"] == "depart"
    assert a["車両登録番号"] == a["自動車登録番号"] == "vehicle_no" and a["荷主都合の有無"] == "shipper_fault"
    assert a["乗務員氏名"] == a["運転者氏名"] == "_ignore" and a["荷役作業等の内容"] == "work_content"


def test_g108_statutory_block_parses_with_zero_errors_and_keeps_no_names():
    c = VEC["paste_statutory_headings_zero_errors"]
    rows, defaulted, notes = waiting.parse_paste_ex(c["text"])
    res = waiting.calc(rows, 2026)
    assert res["errors"] == [] and len(res["rows"]) == 3 and defaulted == []
    assert "山田" not in json.dumps(rows, ensure_ascii=False)          # 乗務員氏名は行に残さない
    assert all("荷主の確認の有無" not in k and not k.startswith("_") for r in rows for k in r)
    assert [r["vehicle_no"] for r in rows][0] == "姫路100あ1234"
    r1 = res["rows"][0]
    assert (r1["date"], r1["arrive"], r1["start"], r1["end"], r1["depart"], r1["appointed"]) == ("2026-10-05", "08:10", "10:15", "10:55", "11:05", "08:30")


def test_g108_date_comes_from_the_datetime_cell_when_there_is_no_date_column():
    c = VEC["paste_statutory_headings_date_only_in_datetime_cells"]
    rows, defaulted, notes = waiting.parse_paste_ex(c["text"])
    res = waiting.calc(rows, None)             # 年を選んでいなくても、2026/10/05 のように年があれば読める。和暦も読める
    assert res["errors"] == [] and [r["date"] for r in res["rows"]] == ["2026-10-05", "2026-10-06"]
    assert [r["arrive"] for r in res["rows"]] == ["09:00", "09:00"]
    assert waiting.parse_time("10/5 9:00") == 540 and waiting.parse_time("2026/10/05 9:00") == 540 and waiting.parse_time("R8.10.5 9時00分") == 540
    rows2, _, _ = waiting.parse_paste_ex("車種\t荷主\t到着日時\t荷役開始日時\t荷主都合\n中型\tA運輸\t10/5 9:00\t10/5 10:00\t○\n")
    assert rows2[0]["date"] == "10/5" and waiting.calc(rows2, 2026)["rows"][0]["date"] == "2026-10-05"
    assert waiting.calc(rows2, None)["errors"][0]["message"].startswith("日付「10/5」の年が分かりません")


def test_g109_unknown_headings_are_reported_by_name_and_do_not_switch_to_position_order():
    rows, defaulted, notes = waiting.parse_paste_ex(VEC["paste_unknown_headings_are_reported_by_name"]["text"])
    assert notes == ["見出し『XYZ』は読みませんでした", "見出し『天気』は読みませんでした"]   # 同じ名前は 1 回
    assert waiting.calc(rows, 2026)["errors"] == [] and rows[0]["date"] == "10/5"
    rows2, _, notes2 = waiting.parse_paste_ex(VEC["paste_few_known_headings_still_read_by_name"]["text"])
    assert notes2 == ["見出し『謎の列』は読みませんでした"] and rows2[0]["partner"] == "A運輸"
    # 見出しの行が無いときだけ列の順で読み、そのことを注記する
    rows3, _, notes3 = waiting.parse_paste_ex(VEC["paste_no_header_says_position_order"]["text"])
    assert len(notes3) == 1 and notes3[0].startswith("見出しの行が無いので、列の順") and rows3[0]["partner"] == "A運輸"


def test_g110_missing_handler_column():
    # 荷役作業等の内容がある行は運転者（法定の記録は運転者自身の荷役）。方法は内容から読む（フォークの語があればフォークリフト等、無ければ手積み）
    rows, _, notes = waiting.parse_paste_ex(VEC["paste_no_handler_column_work_content_means_driver"]["text"])
    res = waiting.calc(rows, 2026)
    assert res["errors"] == [] and [(r["handler"], r["method"]) for r in res["rows"]] == [("運転者", "手積み"), ("運転者", "フォークリフト等"), ("運転者", "手積み")]
    assert all(r["handling_min"] == 40 and r["handling_fee"] > 0 for r in res["rows"]) and len(notes) == 1
    # 内容が無い行は残して、荷役の時間は待機にも積込料にも入れない。待機（到着 → 荷役開始）は計算する
    rows2, _, notes2 = waiting.parse_paste_ex(VEC["paste_no_handler_column_no_work_content_keeps_row_with_note"]["text"])
    res2 = waiting.calc(rows2, 2026)
    assert res2["errors"] == [] and len(res2["rows"]) == 2 and notes2 == []
    r = res2["rows"][0]
    assert (r["handler"], r["wait_min"], r["handling_min"], r["handling_fee"], r["wait_fee"]) == ("不明", 75, 0, 0, 3520)
    assert r["notes"] == ["荷役をした人が分からないので、荷役の時間は待機にも積込料にも入れていません"]
    assert "notes" not in res2["rows"][1]                              # 荷役終了が無い行には注記を出さない
    # 荷役をした人の列がある表では、これまでどおり空欄は誤り
    rows3, _, _ = waiting.parse_paste_ex("日付\t車種\t荷主\t到着\t荷役開始\t荷役終了\t荷役をした人\t荷主都合\n10/5\t中型\tA運輸\t09:00\t10:00\t10:30\t\t○\n")
    assert "荷役をした人がありません" in waiting.calc(rows3, 2026)["errors"][0]["message"]


def test_g111_unreadable_shipper_fault_values_are_errors_not_yes():
    rows, defaulted, _ = waiting.parse_paste_ex(VEC["paste_unreadable_shipper_fault_values_are_errors"]["text"])
    assert defaulted == []
    res = waiting.calc(rows, 2026)
    assert [e["row"] for e in res["errors"]] == [2, 3]
    assert "「要確認」" in res["errors"][0]["message"]
    assert [r["shipper_fault"] for r in res["rows"]] == [True, False]   # ○ は はい、× は いいえ
    # 欄がまるごと無いときだけ はい にする（従来どおり）
    rows2, d2, _ = waiting.parse_paste_ex(VEC["paste_header_without_shipper_fault_column_is_defaulted_with_note"]["text"])
    assert d2 == ["shipper_fault"] and rows2[0]["shipper_fault"] == "はい"


def test_g112_server_api_returns_notes():
    import importlib.util
    spec = importlib.util.spec_from_file_location("serve_web_t238", ROOT / "tools" / "serve_web.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = mod.api_waiting_calc({"year": 2026, "text": VEC["paste_unknown_headings_are_reported_by_name"]["text"]})
    assert res["notes"] == ["見出し『XYZ』は読みませんでした", "見出し『天気』は読みませんでした"] and res["errors"] == []
