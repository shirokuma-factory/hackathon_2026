"""SPEC §47 自社の運行（own）の直し（実務の効き目 r5・道具の採点 r7・画面の出来 r2 のうち、読み取りと計算の分）。

実際の配車表によくある崩れ方（縦の一覧の日付の空欄・「1日」「R8.10.1」・号車の番号・備考と荷主の列の空車の印、横の形の「1日(木)」見出し・
先頭の「帰り」・「点検」、経由地の矢印）を、Python（src/shipcompare/own.py）と JS（web/own_calc.js）の両方で確かめる。
使うファイルは examples/own/ の haisha_messy_*.csv（どちらも架空のデータ）。Edge が無い環境だけ、JS の試験を飛ばす。
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
VERT = "haisha_messy_vertical_2026-10.csv"
WIDE = "haisha_messy_wide_day_2026-10.csv"


def J(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def as_json(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


def text_of(name: str) -> str:
    return (EX / name).read_text(encoding="utf-8")


def rows_of(name: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text_of(name)), strict=False))


def _legs(res):
    out = []
    for r in res["rows"]:
        p = r.get("parsed") or own.parse_cell(r["text"])
        out += [(r["plate"], r["date"], l["frm"], l["to"], l["empty"]) for l in p["legs"]]
    return out


# ---------------------------------------------------------------- Python

def test_examples_are_marked_fictional():
    assert "架空" in text_of(VERT) and "架空" in text_of(WIDE)


def test_vertical_messy_reads_every_row_with_numbered_vehicles_and_blank_dates():
    r = own.read_haisha(rows_of(VERT), 2026)
    assert r["layout"]["kind"] == "vertical" and r["unreadable"] == [] and len(r["rows"]) == 42
    assert {p["plate"] for p in r["plates"]} == {"1号車", "2号車", "11号車"}  # 全角の「１１号車」も同じ名前に
    assert r["stats"]["filled_dates"] == 21  # 帰りの行の日付の空欄は上の行の日付で読む
    dates = sorted({x["date"] for x in r["rows"]})
    assert dates[0] == "2026-10-01" and "2026-10-02" in dates and "2026-10-05" in dates and "2026-10-06" in dates  # 10/1・1日・R8.10.5・2026/10/06


def test_vertical_messy_empty_marks_in_remark_and_shipper_columns():
    r = own.read_haisha(rows_of(VERT), 2026)
    empties = [l for l in _legs(r) if l[4]]
    assert len(empties) == 14  # 備考「空車回送」7 + 荷主「帰荷なし」7。「帰荷 ﾄﾗｯｸﾈｯﾄ」（重さあり）は実車
    assert all(l[2] in ("江東区", "辰巳倉庫") for l in empties)
    assert r["stats"]["marked_empty"] == 14


@pytest.mark.parametrize("s,ok", [("空車回送", True), ("空", True), ("回送", True), ("帰荷なし", True), ("帰り荷なし", True), ("空港運輸", False), ("帰荷 ﾄﾗｯｸﾈｯﾄ", False), ("", False)])
def test_has_empty_mark(s, ok):
    assert own.has_empty_mark(s) is ok


def test_a_user_chosen_skip_stops_the_remark_column_from_being_read():
    r = own.read_haisha(rows_of(VERT), 2026, roles={own.col_key("備考"): None, own.col_key("荷主"): None})
    assert not [x for x in _legs(r) if x[4]]


def test_title_year_month_reads_day_only_cells():
    rows = [["令和8年10月分 配車表", "", "", ""], ["日付", "号車", "積地", "卸地"], ["1日", "1", "加古川", "江東区"], ["", "1", "江東区", "加古川"], ["2日", "1", "加古川", "江東区"], ["3", "2", "加古川", "江東区"]]
    r = own.read_haisha(rows, 2026)
    assert r["unreadable"] == [] and [x["date"] for x in r["rows"]] == ["2026-10-01", "2026-10-01", "2026-10-02", "2026-10-03"]
    assert {p["plate"] for p in r["plates"]} == {"1号車", "2号車"}


def test_day_only_cells_without_a_title_ask_for_the_month():
    rows = [["日付", "号車", "積地", "卸地"], ["1日", "1", "加古川", "江東区"], ["2日", "1", "加古川", "江東区"], ["3日", "1", "加古川", "江東区"]]
    assert own.read_haisha(rows, 2026)["needs_month"] is True
    r = own.read_haisha(rows, 2026, month=10)
    assert r["needs_month"] is False and len(r["rows"]) == 3


def test_a_file_with_four_digit_plates_keeps_numbers_as_not_plates():
    r = own.read_haisha(rows_of("haisha_excel_2026-10.csv"), 2026)
    assert not any(p["plate"].endswith("号車") for p in r["plates"])


def test_wide_with_day_and_weekday_headers_reads_all_days():
    r = own.read_haisha(rows_of(WIDE), 2026)
    assert r["layout"]["kind"] == "wide" and r["unreadable"] == []
    assert r["stats"]["date_cols"] == 14
    assert r["layout"]["title_month"] == [2026, 10]  # 令和8年10月
    legs = _legs(r)
    assert sum(1 for l in legs if l[4]) == sum(1 for l in legs if not l[4]) == 28  # 帰りの「荷なし」は空車、行きは実車。「点検」は区間に数えない


@pytest.mark.parametrize("head", ["1日(木)", "1日（木）", "１日(木)"])
def test_day_with_weekday_header_is_a_date(head):
    rows = [["2026年10月 配車表", "", "", "", ""], ["車番", "車種", head, "2日(金)", "3日(土)"], ["1234", "大型", "加古川→江東 5t", "", ""]]
    r = own.read_haisha(rows, 2026)
    assert r["stats"]["date_cols"] == 3 and r["rows"][0]["date"] == "2026-10-01"


@pytest.mark.parametrize("cell", ["帰り 江東→加古川 荷なし", "戻り 江東→加古川 荷なし", "帰り：江東→加古川 荷なし", "帰り江東→加古川 荷なし"])
def test_leading_return_word_and_no_load(cell):
    r = own.parse_cell(cell)
    assert r["errors"] == [] and [(l["frm"], l["to"], l["empty"]) for l in r["legs"]] == [("江東", "加古川", True)]


@pytest.mark.parametrize("cell", ["点検", "定期点検", "修理", "休", "整備"])
def test_inspection_cells_are_not_legs(cell):
    r = own.parse_cell(cell)
    assert r["nonleg"] is True and r["errors"] == [] and r["legs"] == []


def test_multi_hop_arrows_become_legs():
    r = own.parse_cell("江東→加古川→大阪 着5:30 3t")
    assert [(l["frm"], l["to"], l["arr"], l["weight_kg"]) for l in r["legs"]] == [("江東", "加古川", None, 3000.0), ("加古川", "大阪", "05:30", 3000.0)]
    assert all(l["via"] for l in r["legs"])
    assert len(own.parse_cell("加古川→江東 着5:30 3t")["legs"]) == 1


@pytest.mark.parametrize("cell,ymd", [("R8.10.1", (2026, 10, 1)), ("令和8年10月1日", (2026, 10, 1)), ("H31.4.1", (2019, 4, 1)), ("2026.10.01", (2026, 10, 1))])
def test_era_and_dotted_dates(cell, ymd):
    assert own._date_head(cell) == ymd


# ---------------------------------------------------------------- JS（ヘッドレス Edge）と Python の照合

@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        try:
            for src in ("/own_calc.js", "/own_month.js"):
                page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = %s; s.onload = ok; s.onerror = () => ng(new Error('load')); document.head.appendChild(s); })" % json.dumps(src))
            assert page.js("typeof OwnCalc === 'object' && typeof OwnMonth === 'object'")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "own_calc.js を読み込めません", e, "own_v47_fail")
        yield page


@pytest.mark.parametrize("name", [VERT, WIDE])
def test_js_read_haisha_matches_python(calc, name):
    js = calc.js(f"OwnCalc.readHaisha(OwnCalc.parseCsv({J(text_of(name))}), 2026)")
    py = as_json(own.read_haisha(rows_of(name), 2026))
    for k in ("rows", "unreadable", "plates", "needs_month"):
        assert js[k] == py[k], k
    assert {k: v for k, v in js["stats"].items() if k != "plate_groups"} == py["stats"]  # plate_groups は JS だけの項目
    assert js["layout"]["kind"] == py["layout"]["kind"] and js["layout"]["title_month"] == py["layout"]["title_month"]


@pytest.mark.parametrize("cell", [
    "帰り 江東→加古川 荷なし", "戻り 江東→加古川 荷なし", "帰り：江東→加古川 荷なし", "点検", "定期点検", "修理", "江東→加古川→大阪 着5:30 3t", "加古川→江東 着5:30 3t",
    "加古川→江東 5t / 帰り 江東→加古川 荷なし", "江東→加古川 帰荷なし",
])
def test_js_parse_cell_matches_python(calc, cell):
    assert calc.js(f"OwnCalc.parseCell({J(cell)})") == as_json(own.parse_cell(cell)), cell


@pytest.mark.parametrize("s", ["空車回送", "空", "回送", "帰荷なし", "空港運輸", "帰荷 ﾄﾗｯｸﾈｯﾄ", ""])
def test_js_has_empty_mark_matches_python(calc, s):
    assert calc.js(f"OwnCalc.hasEmptyMark({J(s)})") is own.has_empty_mark(s)


@pytest.mark.parametrize("s", ["1号車", "１１号車", "3号", "No.2", "7", "1234", "神戸 100 か 1234", "A号車"])
def test_js_gou_name_matches_python(calc, s):
    assert calc.js(f"OwnCalc.gouName({J(s)})") == own.gou_name(s), s


def test_js_vertical_with_title_and_day_only_dates(calc):
    rows = [["令和8年10月分 配車表", "", "", ""], ["日付", "号車", "積地", "卸地"], ["1日", "1", "加古川", "江東区"], ["", "1", "江東区", "加古川"], ["2日", "1", "加古川", "江東区"], ["3", "2", "加古川", "江東区"]]
    js = calc.js(f"OwnCalc.readHaisha({J(rows)}, 2026)")
    py = as_json(own.read_haisha(rows, 2026))
    assert js["rows"] == py["rows"] and js["unreadable"] == [] and {k: v for k, v in js["stats"].items() if k != "plate_groups"} == py["stats"]


# ---------------------------------------------------------------- 気づき（全部の曜日が空の区間は 1 枚）

def _ins_data(all_empty_other: bool):
    places = {"A": {"id": "A", "short": "江東", "pseudo": "地点 01", "muni": "江東区", "label": "江東"}, "B": {"id": "B", "short": "加古川", "pseudo": "地点 02", "muni": "加古川市", "label": "加古川"}}
    legs = []
    n = 0
    for wd in (0, 1, 2):
        for k in range(4):
            n += 1
            kind = "empty" if (all_empty_other or wd == 0) else ("loaded" if k % 2 else "empty")
            legs.append({"id": f"L{n}", "plate": "1234", "date": f"2026-10-{5 + wd + 7 * (k % 2):02d}", "dir": "back", "cycle_wd": wd, "cycle_dep": f"2026-10-{5 + wd:02d}T08:00:00",
                         "frm": "A", "to": "B", "kind": kind, "km": 100.0, "ref": {"file": "x.csv", "where": "1 行目", "text": "t"}})
    return {"legs": legs, "stops": [], "shifts": [], "vehicles": [], "places": places, "base": "B", "dock": "A", "month": "2026-10"}


def test_insights_merge_pair_when_every_weekday_is_empty():
    ins = [i for i in own.insights(_ins_data(True)) if i["rule"] == 1]
    assert len(ins) == 1 and ins[0]["next"]["payload"]["all_weekdays"] is True and ins[0]["next"]["payload"]["trips"] == 12
    assert "曜日による違いはありません" in ins[0]["text"] and len(ins[0]["evidence"]) == 12
    # 1 つの曜日だけが空なら、これまでの曜日ごとのカード
    one = [i for i in own.insights(_ins_data(False)) if i["rule"] == 1]
    assert len(one) == 1 and "all_weekdays" not in one[0]["next"]["payload"] and one[0]["next"]["payload"]["weekday"] == "月"


@pytest.mark.parametrize("all_empty", [True, False])
def test_js_insights_match_python(calc, all_empty):
    data = _ins_data(all_empty)
    js = calc.js(f"OwnCalc.insights({J(data)})")
    py = as_json(own.insights(data))
    assert [i for i in js if i["rule"] == 1] == [i for i in py if i["rule"] == 1]  # 規則 2 の距離の元の言い方だけは JS と Python で元から違う（est_label）


# ---------------------------------------------------------------- 画面に出す割合・地点のまとめ（JS）

def test_js_read_quality_flags_low_share(calc):
    d = {"counts": {"legs": 300, "unreadable_rows": 0, "unknown": 108}, "kpis": {"actual_rate": {"value": 1.0}, "loaded_rate_if_unknown_empty": 0.637}}
    q = calc.js(f"OwnMonth.readQuality({J(d)})")
    assert q["low"] is True and abs(q["share"] - 0.64) < 1e-9 and q["lo"] == 0.637 and q["hi"] == 1.0
    ok = calc.js(f"OwnMonth.readQuality({J({'counts': {'legs': 100, 'unreadable_rows': 5, 'unknown': 10}, 'kpis': {'actual_rate': {'value': 0.7}}})})")
    assert ok["low"] is False


MUNIS47 = [
    {"code": "28210", "name": "加古川市", "pref": "兵庫県", "lon": 134.841, "lat": 34.757}, {"code": "13108", "name": "江東区", "pref": "東京都", "lon": 139.817, "lat": 35.673},
]


def test_js_places_with_same_core_name_are_merged_and_can_be_split(calc):
    rows = [["日付", "号車", "積地", "卸地", "備考"], ["10/1", "1", "加古川", "江東区", ""], ["", "1", "辰巳倉庫", "加古川", "空車回送"], ["10/2", "1", "加古川", "江東区", ""], ["", "1", "辰巳DC3", "加古川", "空車回送"]]
    text = "\n".join(",".join(r) for r in rows)
    on = calc.js(f"OwnCalc.analyze([{{name: 'm.csv', text: {J(text)}}}], {{munis: {J(MUNIS47)}}})")
    assert [m["names"] for m in on["merged_places"]] == [["辰巳倉庫", "辰巳DC3"]] and on["merged_places"][0]["short"] == "辰巳"
    off = calc.js(f"OwnCalc.analyze([{{name: 'm.csv', text: {J(text)}}}], {{munis: {J(MUNIS47)}, merge_places: false}})")
    assert off["merged_places"] == [] and len(off["places"]) == len(on["places"]) + 1


def test_js_analyze_messy_vertical_counts_marked_empty_legs(calc):
    d = calc.js(f"OwnCalc.analyze([{{name: {J(VERT)}, text: {J(text_of(VERT))}}}], {{munis: {J(MUNIS47)}}})")
    assert d["counts"]["legs"] == 42 and d["counts"]["empty"] == 14 and d["counts"]["unreadable_rows"] == 0
    assert {v["plate"] for v in d["vehicles"]} == {"1号車", "2号車", "11号車"}
    d2 = calc.js(f"OwnCalc.analyze([{{name: {J(WIDE)}, text: {J(text_of(WIDE))}}}], {{munis: {J(MUNIS47)}}})")
    assert d2["counts"]["empty"] == 28 and d2["counts"]["nonleg_cells"] == 2
    cards = [i for i in d2["insights"] if i["rule"] == 1]
    assert [c["next"]["payload"].get("all_weekdays") for c in cards] == [True, None]  # 江東 → 加古川 は毎回空で 1 枚、辰巳倉庫 → 加古川 は金曜だけ


def test_mark_role_is_offered_for_haisha_and_reads_the_chosen_column():
    opts = {o["role"]: o["label_ja"] for o in own.role_options("配車表")}
    assert opts.get("mark") == "実空の印がある列"
    rows = [["日付", "号車", "積地", "卸地", "コメント"], ["10/1", "1", "加古川", "江東区", ""], ["10/1", "1", "江東区", "加古川", "回送"], ["10/2", "1", "加古川", "江東区", ""]]
    assert not [x for x in _legs(own.read_haisha(rows, 2026)) if x[4]]  # 見出し「コメント」は役割が決まらない列なので、読まない
    r = own.read_haisha(rows, 2026, roles={own.col_key("コメント"): "mark"})
    assert [x[2:] for x in _legs(r) if x[4]] == [("江東区", "加古川", True)]


def test_js_mark_role_options(calc):
    assert {o["role"]: o["label_ja"] for o in calc.js("OwnCalc.roleOptions('配車表')")}["mark"] == "実空の印がある列"
