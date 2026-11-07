"""SPEC §46 自社の運行（own）の直し（実務の効き目 r4・道具の採点 r6・画面の出来 r1 の指摘のうち、自社の運行の分）。

- 配車表のセル: 区間の前に付けた空車の印（「回送 江東 → 加古川」「（回送）…」「回送：…」）、積地の前の都道府県（「東京都 江東区 → 加古川」）、空車の語（戻り空・荷なし・空回送・帰り）
- 実車として数えた区間のセルに付いていた語の一覧（laden_words）
- 日付が縦・車が横の配車表
- 停車の記録が無い（デジタコ無し）ときに停車の費用 0 円を出さない、費用の単価の範囲の確認
- 帰り荷の売上の目安が数えた範囲、次の稼働日（日曜など休みの曜日を初期値にしない）
- 画面: 「データを選び直す」と「最初の画面に戻る」が分かれる、便の図が暗い画面になじむ、1 枚版は暗い配色で地図の凡例を出さない
Python（src/shipcompare/own.py）と JS（web/own_calc.js）を同じ入力で照らす。Edge が無い環境だけ、JS の試験を飛ばす。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import own  # noqa: E402


def J(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def as_json(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


def legs_of(text: str):
    return [(l["frm"], l["to"], l["empty"], l["weight_kg"]) for l in own.parse_cell(text)["legs"]]


# ---------------------------------------------------------------- Python: セルの読み取り

@pytest.mark.parametrize("cell", [
    "回送 江東 → 加古川", "回送　江東→加古川", "（回送）江東→加古川", "(回送)江東→加古川", "回送：江東→加古川", "回送:江東→加古川", "回送江東→加古川",
    "空 江東→加古川", "空車 江東 → 加古川", "戻り空 江東 → 加古川", "荷なし 江東→加古川", "空回送 江東→加古川", "江東→加古川 回送", "江東→加古川 戻り空", "江東→加古川 荷なし", "江東→加古川 空回送",
    "江東→加古川 帰り",
])
def test_every_way_to_write_an_empty_mark_makes_the_same_empty_leg(cell):
    assert legs_of(cell) == [("江東", "加古川", True, None)], cell


def test_the_same_trip_has_the_same_rate_whichever_side_the_mark_is_on():
    a = own.parse_cell("回送 江東 → 加古川")["legs"][0]
    b = own.parse_cell("江東 → 加古川 回送")["legs"][0]
    assert (a["frm"], a["to"], a["empty"]) == (b["frm"], b["to"], b["empty"]) == ("江東", "加古川", True)


def test_a_lone_empty_mark_is_still_an_empty_leg_without_places():
    for c in ("回送", "空", "戻り空", "荷なし", "空回送", "帰り"):
        assert legs_of(c) == [(None, None, True, None)], c


def test_places_that_start_like_an_empty_word_are_not_marks():
    assert legs_of("空港前→加古川 4t") == [("空港前", "加古川", False, 4000.0)]
    assert legs_of("空知→加古川 4t") == [("空知", "加古川", False, 4000.0)]
    assert legs_of("カラマツ台→加古川 4t") == [("カラマツ台", "加古川", False, 4000.0)]


def test_return_word_with_a_weight_is_a_loaded_leg():
    assert legs_of("江東→加古川 帰り 4t") == [("江東", "加古川", False, 4000.0)]


@pytest.mark.parametrize("cell,frm", [
    ("東京都 江東区 → 加古川 4t", "江東区"), ("東京 江東区→加古川 4t", "江東区"), ("東京都江東区→加古川 4t", "江東区"), ("大阪 大阪市中央区→姫路 4t", "大阪市中央区"),
    ("兵庫 姫路市→東京 江東区 9t", "姫路市"), ("回送 東京都 江東区 → 加古川", "江東区"),
])
def test_a_prefecture_before_the_origin_is_skipped(cell, frm):
    r = own.parse_cell(cell)
    assert r["errors"] == [] and r["legs"][0]["frm"] == frm, cell


def test_a_prefecture_alone_before_an_arrow_is_still_the_origin():
    assert legs_of("東京 → 加古川 4t") == [("東京", "加古川", False, 4000.0)]
    assert legs_of("東京都 → 加古川 4t") == [("東京都", "加古川", False, 4000.0)]


def test_words_left_in_a_laden_cell_are_kept_as_a_note():
    leg = own.parse_cell("江東→加古川 冷凍 着5:30 4t")["legs"][0]
    assert leg["note"] == "冷凍" and leg["empty"] is False
    assert "note" not in own.parse_cell("江東→加古川 着5:30 4t")["legs"][0]
    assert "note" not in own.parse_cell("江東→加古川 回送")["legs"][0]
    assert "note" not in own.parse_cell("江東→加古川 実車 4t")["legs"][0]
    assert own.parse_cell("江東→加古川 帰り便 4t")["legs"][0]["note"] == "帰り便"


def test_laden_words_counts_words_on_loaded_cells_only():
    ref = lambda t: {"text": t}  # noqa: E731
    legs = [
        {"kind": "loaded", "kind_src": "配車表の積地・降ろし地", "note": "戻り便", "ref": ref("江東→加古川 戻り便")},
        {"kind": "loaded", "kind_src": "配車表の積地・降ろし地", "note": "戻り便 冷凍", "ref": ref("江東→加古川 戻り便 冷凍")},
        {"kind": "empty", "kind_src": "配車表の（空）の印", "note": "空戻り", "ref": ref("x")},
        {"kind": "loaded", "kind_src": "デジタコの実空区分", "note": "デジ", "ref": ref("y")},
        {"kind": "loaded", "kind_src": "配車表の積地・降ろし地", "ref": ref("z")},
    ]
    assert own.laden_words(legs) == [{"word": "戻り便", "count": 2, "example": "江東→加古川 戻り便"}, {"word": "冷凍", "count": 1, "example": "江東→加古川 戻り便 冷凍"}]


def test_insight_text_does_not_say_meter_when_no_digitaco():
    legs = [{"id": "L1", "plate": "1234", "kind": "empty", "km": 100.0, "dir": "back", "frm": "p2", "to": "p1", "date": "2026-10-05", "sources": {"km": "OSRM 推定"}, "cycle_wd": 0, "flags": [], "ref": {}}]
    data = {"legs": legs, "stops": [], "shifts": [], "vehicles": [], "places": {"p1": {"short": "加古川"}, "p2": {"short": "江東"}}, "base": "p1", "dock": "p2", "month": 10}
    t = [i["text"] for i in own.insights(data) if i["rule"] == 2][0]
    assert "メーター" not in t and "OSRM の推定" in t
    legs[0]["sources"] = {"km": "デジタコ"}
    t2 = [i["text"] for i in own.insights(data) if i["rule"] == 2][0]
    assert "デジタコのメーター" in t2


# ---------------------------------------------------------------- Python: 日付が縦・車が横の配車表

DATE_DOWN = (
    "2026年10月 配車表\n"
    "日付,曜日,1234,5678\n"
    "10/1,木,加古川→江東 着5:30 4.6t,\n"
    "10/2,金,江東→加古川 空,加古川→江東 4t\n"
    "10/3,土,休,江東→加古川 空\n"
    "10/4,日,休,休\n"
)


def _rows(text: str) -> list[list[str]]:
    import csv
    import io
    return list(csv.reader(io.StringIO(text)))


def test_date_down_sheet_is_read_like_the_wide_one():
    rows = _rows(DATE_DOWN)
    assert own.detect_haisha(rows) is None  # そのままでは配車表の形に見えない
    tr = own.transpose_date_down(rows)
    assert tr is not None and tr[1]["cols"] == [2, 3] and tr[0][1][0] == "車番"
    out = own.read_haisha(rows, 2026)
    cells = {(r["plate"], r["date"]): r["text"] for r in out["rows"]}
    assert cells[("1234", "2026-10-01")] == "加古川→江東 着5:30 4.6t"
    assert cells[("1234", "2026-10-02")] == "江東→加古川 空" and cells[("5678", "2026-10-02")] == "加古川→江東 4t"
    assert out["unreadable"] == [] and len(out["plates"]) == 2


def test_a_normal_wide_sheet_is_not_turned():
    rows = _rows("車番,車種,10/1(木),10/2(金),10/3(土)\n1234,大型,加古川→江東 4t,江東→加古川 空,\n5678,大型,,加古川→江東 4t,\n")
    assert own.transpose_date_down(rows) is None
    assert own.detect_haisha(rows) is not None


def test_a_vertical_trip_list_is_not_turned():
    rows = _rows("日付,車番,発地,着地\n10/1,1234,加古川,江東\n10/2,1234,江東,加古川\n10/3,5678,加古川,江東\n")
    assert own.transpose_date_down(rows) is None and own.detect_haisha(rows) is not None


# ---------------------------------------------------------------- JS（ヘッドレス Edge）と Python の照合

CELLS_46 = [
    "回送 江東 → 加古川", "（回送）江東→加古川", "回送：江東→加古川", "回送江東→加古川", "空 江東→加古川", "戻り空 江東 → 加古川", "荷なし 江東→加古川", "空回送 江東→加古川",
    "江東→加古川 戻り空", "江東→加古川 荷なし", "江東→加古川 空回送", "江東→加古川 帰り", "江東→加古川 帰り 4t", "戻り空", "荷なし", "帰り", "空回送",
    "東京都 江東区 → 加古川 4t", "東京 江東区→加古川 4t", "大阪 大阪市中央区→姫路 4t", "回送 東京都 江東区 → 加古川", "東京 → 加古川 4t", "東京都 → 加古川 4t",
    "江東→加古川 冷凍 着5:30 4t", "江東→加古川 帰り便 4t", "江東→加古川 実車 4t", "高砂→名古屋市港区 中部鋼材 6時着 13t", "空港前→加古川 4t", "空知→加古川 4t",
    "AM 回送 江東→加古川 / PM 加古川→江東 4t",
]


@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        try:
            page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
            page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_month.js'; s.onload = ok; s.onerror = () => ng(new Error('own_month.js')); document.head.appendChild(s); })")
            assert page.js("typeof OwnCalc === 'object' && typeof OwnMonth === 'object'")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "own_calc.js / own_month.js を読み込めません", e, "own_v46_fail")
        yield page


def test_js_and_python_read_the_same_cells(calc):
    for text in CELLS_46:
        assert calc.js(f"OwnCalc.parseCell({J(text)})") == as_json(own.parse_cell(text)), text


def test_js_and_python_turn_the_same_date_down_sheet(calc):
    rows = _rows(DATE_DOWN)
    py = own.transpose_date_down(rows)
    js = calc.js(f"OwnCalc.transposeDateDown({J(rows)})")
    assert js["rows"] == py[0] and js["cols"] == py[1]["cols"] and js["header_row"] == py[1]["header_row"] and js["date_col"] == py[1]["date_col"]
    assert calc.js(f"OwnCalc.transposeDateDown({J(_rows('車番,車種,10/1(木),10/2(金),10/3(土)' + chr(10) + '1234,大型,a→b,,'))})") is None


HAISHA_WIDE = ("車番,車種,10/5(月),10/6(火),10/7(水),10/8(木)\n"
               "1234,大型,加古川→江東 着5:30 4.6t,江東→加古川 戻り空,加古川→江東 荷なし,江東→加古川 冷凍 4t\n")
HAISHA_DOWN = "日付,曜日,1234\n10/5,月,加古川→江東 着5:30 4.6t\n10/6,火,江東→加古川 戻り空\n10/7,水,加古川→江東 荷なし\n10/8,木,江東→加古川 冷凍 4t\n"


def _an(page, files, opts=None):
    return page.js(f"OwnCalc.analyze({J(files)}, {J(opts or {})})")


def test_js_reads_a_date_down_sheet_to_the_same_legs_as_the_wide_sheet(calc):
    wide = _an(calc, [{"name": "w.csv", "text": HAISHA_WIDE}])
    down = _an(calc, [{"name": "d.csv", "text": HAISHA_DOWN}])
    key = lambda d: [(l["plate"], l["date"], l["kind"], l["weight_kg"]) for l in d["legs"]]  # noqa: E731
    assert key(wide) == key(down) and len(down["legs"]) == 4, (key(wide), key(down))
    assert down["counts"]["unreadable"] == 0


def test_empty_words_count_as_empty_and_the_other_words_are_listed(calc):
    d = _an(calc, [{"name": "w.csv", "text": HAISHA_WIDE}])
    assert [l["kind"] for l in d["legs"]] == ["loaded", "empty", "empty", "loaded"]
    assert d["laden_words"] == [{"word": "冷凍", "count": 1, "example": "江東→加古川 冷凍 4t"}]


def test_a_sheet_alone_has_no_stop_cost_and_does_not_say_zero_yen(calc):
    d = _an(calc, [{"name": "w.csv", "text": HAISHA_WIDE}])
    c = calc.js(f"OwnCalc.costs({J(d)}, '30', '2800')")
    assert c["waitMissing"] is True and c["wait"] is None and c["empty"] is not None
    txt = calc.js(f"OwnCalc.summaryText({J(d)}, OwnCalc.costs({J(d)}, '30', '2800'))")
    assert "停車の費用" not in txt and "停車 0 回" not in txt and "デジタコを入れていない" in txt


def test_the_cost_row_stays_when_a_digitaco_file_is_loaded(calc):
    d = json.loads((ROOT / "web" / "data" / "own_sample.json").read_text(encoding="utf-8"))
    c = calc.js(f"OwnCalc.costs({J(d)}, '30', '2800')")
    assert c["waitMissing"] is False and c["wait"] is not None


def test_unit_prices_beyond_a_realistic_range_get_a_check_message(calc):
    d = json.loads((ROOT / "web" / "data" / "own_sample.json").read_text(encoding="utf-8"))
    c = calc.js(f"OwnCalc.costs({J(d)}, '99999', '999999')")
    assert "円/km" in c["warns"]["perKm"] and "円/時" in c["warns"]["perHour"] and c["empty"] is not None
    ok = calc.js(f"OwnCalc.costs({J(d)}, '30', '2800')")
    assert ok["warns"] == {"perKm": None, "perHour": None}


def test_revenue_scope_names_the_empty_trips_it_did_not_count(calc):
    d = json.loads((ROOT / "web" / "data" / "own_sample.json").read_text(encoding="utf-8"))
    sc = calc.js(f"OwnMonth.revenueScope({J(d)})")
    emp = [l for l in d["legs"] if l["kind"] == "empty"]
    assert sc["all"] == len(emp) and sc["back"] == sum(1 for l in emp if l["dir"] == "back") and sc["other"] == sc["all"] - sc["back"]


def _wd_data(days):
    """日付ごとに区間が 1 つある最小の結果。"""
    return {"legs": [{"date": d, "dep": d + "T05:00", "kind": "loaded"} for d in days]}


def test_next_workday_skips_the_closed_weekday(calc):
    # 2026-10 の月〜土に動き、日曜（11/8 など）は全車休み。2026-11-07（土）の次の日は日曜なので、月曜の 11/9 になる
    days = [f"2026-10-{d:02d}" for d in range(1, 32) if (__import__("datetime").date(2026, 10, d).weekday() != 6)]
    r = calc.js(f"OwnMonth.nextWorkday({J(_wd_data(days))}, new Date(2026, 10, 7, 12, 0))")
    assert r["iso"] == "2026-11-09" and r["skipped"] == ["2026-11-08"] and r["basis"] is True and 6 not in r["open"]
    # 日曜が稼働日なら飛ばさない
    all_days = [f"2026-10-{d:02d}" for d in range(1, 32)]
    r2 = calc.js(f"OwnMonth.nextWorkday({J(_wd_data(all_days))}, new Date(2026, 10, 7, 12, 0))")
    assert r2["iso"] == "2026-11-08" and r2["skipped"] == []
    # 区間が無いときは、これまでどおり明日
    r3 = calc.js("OwnMonth.nextWorkday({ legs: [] }, new Date(2026, 10, 7, 12, 0))")
    assert r3["iso"] == "2026-11-08" and r3["basis"] is False


def test_board_default_date_is_the_next_workday(calc):
    d = json.loads((ROOT / "web" / "data" / "own_sample.json").read_text(encoding="utf-8"))
    nw = calc.js(f"OwnMonth.nextWorkday({J(d)}, new Date(2026, 10, 7, 12, 0))")
    assert calc.js(f"OwnMonth.boardDefaults({J(d)}, new Date(2026, 10, 7, 12, 0))")["date"] == nw["iso"]


def test_saving_the_same_month_again_keeps_one_entry(calc):
    calc.js("localStorage.removeItem('backfull.own.v41')")
    calc.js("OwnMonth.save({ month: '2026-10', sig: 'a', summary: { label: '2026 年 10 月' } }); OwnMonth.save({ month: '2026-10', sig: 'b', summary: { label: '2026 年 10 月' } }); OwnMonth.save({ month: '2026-11', sig: 'c', summary: { label: '2026 年 11 月' } })")
    assert calc.js("OwnMonth.list().map((e) => e.month + ':' + e.sig)") == ["2026-10:b", "2026-11:c"]
    calc.js("OwnMonth.clearAll()")


def test_two_month_files_dropped_one_by_one_are_counted_month_by_month(calc):
    a = ("車番,車種,10/30(金),10/31(土)\n1234,大型,加古川→江東 4t,江東→加古川 空\n")
    b = ("車番,車種,11/2(月),11/3(火)\n1234,大型,加古川→江東 4t,江東→加古川 4t\n")
    d = _an(calc, [{"name": "10.csv", "text": a}, {"name": "11.csv", "text": b}])
    assert d["multi_month"] is True and [m["month"] for m in d["months_detail"]] == ["2026-10", "2026-11"]
    s = calc.js(f"OwnMonth.summaries({J(d)}, OwnCalc.costs({J(d)}, '', ''))")
    assert [x["month"] for x in s] == ["2026-10", "2026-11"] and s[0]["empty_km"] != s[1]["empty_km"] or s[0]["legs"] == s[1]["legs"] == 2
    assert "〜" in d["month_label"] and "2 か月" in d["month_label"]


# ---------------------------------------------------------------- 画面（静的な確認）

def test_the_table_head_button_no_longer_says_back_to_the_first_screen():
    neg = (ROOT / "web" / "negotiate.js").read_text(encoding="utf-8")
    head = neg[neg.index("function headerHtml"):neg.index("function gHeaderHtml") if "function gHeaderHtml" in neg else None][:900]
    assert 'data-act="to-entry">別のデータを選ぶ</button>' in head and "最初の画面に戻る" not in head


def test_trip_view_uses_the_dark_ground_of_the_map_screen():
    css = (ROOT / "web" / "trip_view.css").read_text(encoding="utf-8")
    m = re.search(r"--tv-ground:\s*(#[0-9a-fA-F]{6})", css)
    assert m and int(m.group(1)[1:3], 16) < 0x30, css[:300]
    assert "#fff;" not in re.sub(r"\.tv-chev[^\n]*", "", css)  # 白い札を残さない（矢印の線だけが白）


def test_lite_page_is_dark_like_the_map_screen_and_has_no_map_legend():
    html = (ROOT / "web" / "own_lite.html").read_text(encoding="utf-8")
    root = re.search(r"^:root \{([^}]*)\}", html, re.M).group(1)
    assert "color-scheme: dark" in root and "--bg: #070b12" in root and "--accent: #5cc8ff" in root
    assert ".neg-lg { display: none; }" in html
    art = (ROOT / "web" / "publish" / "own_lite_artifact.html").read_text(encoding="utf-8")
    assert "--bg: #070b12" in art


def test_guided_labels_do_not_wrap_the_other_weekdays_label():
    css = (ROOT / "web" / "own.css").read_text(encoding="utf-8")
    m = re.search(r"\.neg-guided \.neg-lab \{([^}]*)\}", css)
    assert m and "white-space: nowrap" in m.group(1)


def test_lite_and_app_use_the_same_table_words():
    for p in (ROOT / "web" / "own.js", ROOT / "tools" / "own_page_template.html"):
        t = p.read_text(encoding="utf-8")
        assert "車庫から" in t and "車庫へ" in t and "車庫は " in t, p.name
