"""SPEC §43 自社の運行（own）の読み方の直し（実務の効き目 r3・道具の採点 r5 の指摘のうち、読み取りの分）。

矢印の書き方（～ 〜 ⇒ => ->）、1 つのセルの 2 区間（AM/PM・/・セルの中の改行）、半角カナの「空ｺﾝ回送」、降ろし地が欠けたセル（加古川 → 4.6t）、
「1e3kg」、「東京 江東区」「東京都江東区」、2 か月分の月ごとの集計、直前の行きが無い帰りの曜日、使ったファイルの数え方、
落とし直したファイルの順位、不明を空車とした場合の率を、Python（src/shipcompare/own.py）と JS（web/own_calc.js）の両方で確かめる。
Edge が無い環境だけ、JS の試験を飛ばす。
"""

from __future__ import annotations

import json
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

@pytest.mark.parametrize("cell", ["加古川～堺", "加古川〜堺", "加古川⇒堺", "加古川=>堺", "加古川->堺", "加古川→堺", "加古川 ～ 堺", "加古川>堺"])
def test_every_arrow_form_reads_the_same_leg(cell):
    r = own.parse_cell(cell + " 3t")
    assert r["errors"] == [] and legs_of(cell + " 3t") == [("加古川", "堺", False, 3000.0)]


@pytest.mark.parametrize("cell", [
    "AM 姫路→明石 3t / PM 明石→姫路 空",
    "AM 姫路→明石 3t PM 明石→姫路 空",
    "午前 姫路→明石 3t 午後 明石→姫路 空",
    "姫路→明石 3t / 明石→姫路 空",
    "姫路→明石 3t\n明石→姫路 空",
    "姫路→明石 3t\r\n明石→姫路（空）",
    "ＡＭ姫路⇒明石 3t／ＰＭ明石⇒姫路 空",
])
def test_one_cell_with_two_legs_reads_as_two_legs(cell):
    r = own.parse_cell(cell)
    assert r["errors"] == [] and [(l["frm"], l["to"], l["empty"], l["weight_kg"]) for l in r["legs"]] == [("姫路", "明石", False, 3000.0), ("明石", "姫路", True, None)]


def test_am_pm_as_a_time_suffix_does_not_split_the_cell():
    r = own.parse_cell("加古川→江東 着5:30 AM 3t")
    assert r["errors"] == [] and len(r["legs"]) == 1 and r["legs"][0]["to"] == "江東" and r["legs"][0]["weight_kg"] == 3000.0
    r2 = own.parse_cell("姫路～明石 AM 3t / PM 明石⇒姫路 空")
    assert r2["errors"] == [] and [(l["frm"], l["empty"]) for l in r2["legs"]] == [("姫路", False), ("明石", True)]


@pytest.mark.parametrize("cell", ["三田→神戸港 空ｺﾝ回送", "三田→神戸港 空コン回送", "三田→神戸港 回送", "三田→神戸港 空コン"])
def test_empty_container_and_dispatch_marks_make_an_empty_leg(cell):
    assert legs_of(cell) == [("三田", "神戸港", True, None)]
    assert own.is_empty_word("空ｺﾝ回送") and own.is_empty_word("回送") and not own.is_empty_word("空港前")


def test_empty_mark_inside_words_is_still_not_a_mark():
    assert legs_of("空港前→加古川 4t") == [("空港前", "加古川", False, 4000.0)]


@pytest.mark.parametrize("cell,weight,arr", [
    ("加古川 → 4.6t", 4600.0, None), ("加古川 → 12t", 12000.0, None), ("加古川→4,600kg", 4600.0, None), ("加古川 → 5トン積み", 5000.0, None),
    ("加古川 → 着5:30", None, "05:30"), ("加古川→5:30着 9.2t", 9200.0, "05:30"),
])
def test_missing_destination_keeps_the_weight_as_a_weight_and_leaves_the_destination_empty(cell, weight, arr):
    r = own.parse_cell(cell)
    leg = r["legs"][0]
    assert r["errors"] == [] and leg["frm"] == "加古川" and leg["to"] is None and leg["weight_kg"] == weight and leg["arr"] == arr and leg["empty"] is False


def test_missing_both_places_is_a_leg_with_a_weight_only():
    assert legs_of("→ 5t") == [(None, None, False, 5000.0)]
    assert own.is_non_place("4.6t") and own.is_non_place("5:30") and own.is_non_place("８時") and not own.is_non_place("江東") and not own.is_non_place("3丁目倉庫")


def test_a_laden_leg_without_destination_is_unknown_not_loaded():
    legs = [{"plate": "9001", "date": "2026-10-05", "hid": "H1", "did": None, "frm": "p1", "to": None, "empty_mark": False, "has_ends": False, "weight_kg": 4600.0, "jk": None, "flags": [], "sources": {}}]
    assert own.classify_legs(legs, "p1")["legs"][0]["kind"] == "unknown"


@pytest.mark.parametrize("cell", ["加古川→東京 1e3kg", "加古川→東京 1e3t", "加古川→東京 2.5E2 kg"])
def test_scientific_notation_weight_is_unreadable_with_a_reason(cell):
    r = own.parse_cell(cell)
    assert r["errors"] == [own.WEIGHT_SCI_ERR] and "指数" in r["errors"][0]


def test_plain_weights_still_read():
    assert legs_of("加古川→東京 4600kg") == [("加古川", "東京", False, 4600.0)] and legs_of("加古川→東京 4t") == [("加古川", "東京", False, 4000.0)]
    assert own.parse_weights("1e3kg") == [{"error": own.WEIGHT_SCI_ERR}] and own.parse_weights("10t 3kg") == [{"kg": 10000.0}, {"kg": 3.0}]


@pytest.mark.parametrize("cell,to", [
    ("姫路→東京 江東区 9t", "江東区"), ("姫路→東京都江東区 9t", "江東区"), ("姫路→東京都 江東区 9t", "江東区"), ("姫路→大阪 大阪市中央区 9t", "大阪市中央区"),
    ("姫路→兵庫県加古川市 9t", "加古川市"), ("姫路→東京 9t", "東京"), ("姫路→東京都 9t", "東京都"),
])
def test_a_prefecture_followed_by_a_ward_or_city_picks_that_place(cell, to):
    r = own.parse_cell(cell)
    assert r["errors"] == [] and r["legs"][0]["to"] == to and r["legs"][0]["weight_kg"] == 9000.0


def test_pref_split_does_not_cut_city_names_that_start_like_a_prefecture():
    assert own.pref_split("大阪狭山市") == (None, "大阪狭山市") and own.pref_split("京都市") == (None, "京都市") and own.pref_split("東京") == (None, "東京")
    assert own.pref_split("東京 江東区") == ("東京都", "江東区") and own.pref_split("東京都江東区") == ("東京都", "江東区") and own.pref_split("北海道札幌市") == ("北海道", "札幌市")
    assert own.pref_name("東京") == "東京都" and own.pref_name("東京都") == "東京都" and own.pref_name("北海道") == "北海道" and own.pref_name("江東区") is None


def test_vertical_form_reads_the_same_marks_and_prefix():
    rows = [["日付", "車番", "発地", "着地", "区分"], ["2026/10/6", "1234", "江東", "加古川", "空ｺﾝ回送"], ["2026/10/7", "1234", "姫路", "東京都江東区", ""]]
    got = own.read_haisha(rows, 2026)["rows"]
    assert got[0]["parsed"]["legs"][0]["empty"] is True and got[1]["parsed"]["legs"][0]["to"] == "江東区"


# ---------------------------------------------------------------- Python: 不明を空車とした場合の率

def _lg(kind, km, plate="9001"):
    return {"plate": plate, "kind": kind, "km": km, "weight_kg": None}


def test_rates_if_unknown_were_empty():
    legs = [_lg("loaded", 100.0), _lg("empty", 50.0), _lg("unknown", 50.0), _lg("unknown", None), _lg("loaded", None)]
    k = own.kpis(legs, [{"plate": "9001", "cls": "大型"}])
    assert k["actual_rate"]["value"] == pytest.approx(100 / 150)
    assert k["loaded_rate_if_unknown_empty"] == pytest.approx(0.5) and k["empty_rate_if_unknown_empty"] == pytest.approx(0.5)
    assert k["unknown_as_empty"] == {"legs": 3, "unknown_legs": 1, "unknown_legs_without_km": 1, "formula": "不明の区間を空車として数えた場合の率（km が分かる区間だけ）"}
    none = own.kpis([], [])
    assert none["loaded_rate_if_unknown_empty"] is None and none["empty_rate_if_unknown_empty"] is None


# ---------------------------------------------------------------- JS（ヘッドレス Edge）と Python の照合

MUNIS = [
    {"code": "28210", "name": "加古川市", "pref": "兵庫県", "lon": 134.841, "lat": 34.757}, {"code": "28110", "name": "神戸市中央区", "pref": "兵庫県", "lon": 135.192, "lat": 34.701},
    {"code": "28201", "name": "姫路市", "pref": "兵庫県", "lon": 134.690, "lat": 34.816}, {"code": "13108", "name": "江東区", "pref": "東京都", "lon": 139.817, "lat": 35.673},
    {"code": "28208", "name": "明石市", "pref": "兵庫県", "lon": 134.993, "lat": 34.655}, {"code": "27140", "name": "堺市堺区", "pref": "大阪府", "lon": 135.480, "lat": 34.573},
    {"code": "13206", "name": "府中市", "pref": "東京都", "lon": 139.478, "lat": 35.669}, {"code": "34208", "name": "府中市", "pref": "広島県", "lon": 133.236, "lat": 34.568},
]


@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        try:
            page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
            assert page.js("typeof OwnCalc === 'object'")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "own_calc.js を読み込めません", e, "own_v43_fail")
        yield page


def _an(page, files, opts=None):
    return page.js(f"OwnCalc.analyze({J(files)}, {J(opts or {})})")


def _h(text: str, name: str = "h.csv") -> dict:
    return {"name": name, "text": text}


CELLS_43 = [
    "加古川～堺", "加古川〜堺 3t", "姫路⇒岡山", "姫路=>岡山 空", "姫路->岡山", "ＡＭ 姫路→明石 3t ／ ＰＭ 明石→姫路 空", "AM 姫路→明石 3t PM 明石→姫路 空", "午前 姫路→明石 午後 明石→姫路 空",
    "姫路→明石 3t\n明石→姫路 空", "姫路→明石 3t\r\n明石→姫路（空）", "三田→神戸港 空ｺﾝ回送", "三田→神戸港 回送", "空ｺﾝ回送", "回送", "空港前→加古川 4t", "加古川→江東 着5:30 AM 3t",
    "加古川 → 4.6t", "加古川 → 12t", "加古川 → 着5:30", "加古川→5:30着 9.2t", "→ 5t", "江東 → 5t", "加古川→東京 1e3kg", "加古川→東京 1e3t", "加古川→東京 4600kg",
    "姫路→東京 江東区 9t", "姫路→東京都江東区 9t", "姫路→東京都 江東区 9t", "姫路→東京 9t", "姫路→大阪 大阪市中央区 9t", "姫路→兵庫県加古川市 9t", "休", "車検", "加古川市内 ルート配送",
    "加古川→江東 5:30着 12回", "姫路市→神戸市中央区 2回転", "高砂→名古屋市港区 中部鋼材 6時着 13t",
]


def test_js_and_python_read_the_same_new_cells(calc):
    for text in CELLS_43:
        assert calc.js(f"OwnCalc.parseCell({J(text)})") == as_json(own.parse_cell(text)), text


def test_js_and_python_agree_on_prefix_and_non_place_helpers(calc):
    for t in ("東京 江東区", "東京都江東区", "東京", "東京都", "大阪狭山市", "京都市", "京都 伏見区", "北海道札幌市", "兵庫県加古川市", "", "神奈川 横浜市"):
        assert calc.js(f"OwnCalc.prefSplit({J(t)})") == as_json(list(own.pref_split(t))), t
        assert calc.js(f"OwnCalc.prefName({J(t)})") == own.pref_name(t), t
    for t in ("4.6t", "5:30", "８時", "12トン", "-5t", "1e3kg", "江東", "3丁目倉庫", "4", "空港前", ""):
        assert calc.js(f"OwnCalc.isNonPlace({J(t)})") == own.is_non_place(t), t


def test_js_kpis_with_unknown_as_empty_match_python(calc):
    legs = [_lg("loaded", 100.0), _lg("empty", 50.0), _lg("unknown", 50.0), _lg("unknown", None), _lg("loaded", None)]
    vs = [{"plate": "9001", "cls": "大型"}]
    assert calc.js(f"OwnCalc.kpis({J(legs)}, {J(vs)})") == as_json(own.kpis(legs, vs))
    assert calc.js("OwnCalc.kpis([], [])") == as_json(own.kpis([], []))


def test_js_match_muni_ignores_the_prefecture_and_uses_it_to_choose_among_same_names(calc):
    assert calc.js(f"OwnCalc.matchMuni('東京 江東区', {J(MUNIS)})")["m"]["code"] == "13108"
    assert calc.js(f"OwnCalc.matchMuni('東京都江東区', {J(MUNIS)})")["m"]["code"] == "13108"
    assert calc.js(f"OwnCalc.matchMuni('広島 府中市', {J(MUNIS)})")["m"]["code"] == "34208"
    assert calc.js(f"OwnCalc.matchMuni('東京都府中市', {J(MUNIS)})")["m"]["code"] == "13206"
    assert "複数" in calc.js(f"OwnCalc.matchMuni('府中市', {J(MUNIS)})")["reason"]
    assert "m" not in calc.js(f"OwnCalc.matchMuni('東京', {J(MUNIS)})")


HEAD = "車番,車種,10/30(金),10/31(土),11/1(日),11/2(月)\n"


def test_two_months_in_one_drop_are_counted_month_by_month(calc):
    t = HEAD + "1201,大型,加古川→江東 9t,江東→加古川 空,加古川→江東 8t,江東→加古川 空\n"
    d = _an(calc, [_h(t)], {"munis": MUNIS})
    assert d["failed"] is None and d["multi_month"] is True and d["month_label"] == "2026 年 10 月〜11 月（2 か月分）"
    md = d["months_detail"]
    assert [(m["month"], m["counts"]["legs"], m["counts"]["loaded"], m["counts"]["empty"]) for m in md] == [("2026-10", 2, 1, 1), ("2026-11", 2, 1, 1)]
    assert all(m["full"] and m["kpis"]["actual_rate"]["of"] == 2 and m["table"] and m["pairs"] is not None for m in md)
    assert sum(len(m["leg_ids"]) for m in md) == d["counts"]["legs"] == 4 and d["period"] == {"from": "2026-10-30", "to": "2026-11-02"}
    one = _an(calc, [_h(HEAD + "1201,大型,加古川→江東 9t,江東→加古川 空,,\n")], {"munis": MUNIS})
    assert one["multi_month"] is False and one["month_label"] == "2026 年 10 月" and [m["month"] for m in one["months_detail"]] == ["2026-10"] and one["months_detail"][0]["full"] is False
    assert "table" not in one["months_detail"][0]


def test_a_return_without_an_outbound_counts_on_its_own_weekday(calc):
    t = "車番,車種,10/5(月),10/6(火),10/7(水),10/8(木)\n1201,大型,加古川→江東 9t,,加古川→江東 9t,\n1202,大型,,江東→加古川 空,,\n"
    d = _an(calc, [_h(t)], {"munis": MUNIS})
    back = [l for l in d["legs"] if l["plate"] == "1202"]
    assert len(back) == 1 and back[0]["dir"] == "back" and back[0]["cycle_wd"] == 1 and back[0]["date"] == "2026-10-06"  # 火曜（前の日の月曜にはしない）
    assert [(r["dir"], r["wd"], r["trips"], r["empty"]) for r in d["table"] if r["dir"] == "back"] == [("back", 1, 1, 1)]
    # 直前の行きがあるときは、これまでどおり行きの曜日
    t2 = "車番,車種,10/5(月),10/6(火)\n1201,大型,加古川→江東 9t,江東→加古川 空\n"
    d2 = _an(calc, [_h(t2)], {"munis": MUNIS})
    assert [l["cycle_wd"] for l in d2["legs"]] == [0, 0]


def test_a_header_only_haisha_is_not_counted_as_read_together(calc):
    real = _h(HEAD + "1201,大型,加古川→江東 9t,江東→加古川 空,,\n", "real.csv")
    empty = _h(HEAD, "header_only.csv")
    d = _an(calc, [real, empty], {"munis": MUNIS})
    assert [n["kind"] for n in d["notices"] if n["kind"] == "haisha_merged"] == []
    assert [f["file"] for f in d["files_used"]] == ["real.csv"] and d["files_used"][0]["legs"] == 2
    assert "header_only.csv" in [f["file"] for f in d["files_unread"]]
    both = _an(calc, [real, _h("車番,車種,11/3(火),11/4(水)\n1202,大型,加古川→江東 9t,\n", "second.csv")], {"munis": MUNIS})
    assert [f["file"] for f in both["files_used"]] == ["real.csv", "second.csv"] and [n for n in both["notices"] if n["kind"] == "haisha_merged"]


def test_redropping_files_keeps_their_priority_order_and_says_so(calc):
    a = _h(HEAD + "1201,大型,加古川→江東 9t,,,\n", "a.csv")
    b = _h(HEAD + "1201,大型,加古川→江東 1t,,,\n", "b.csv")
    first = _an(calc, [b, a])
    assert first["haisha_choice"]["used"] == ["b.csv"] and first["file_order"] == ["b.csv", "a.csv"] and not [n for n in first["notices"] if n["kind"] == "file_order"]
    # a.csv を先に読んでいた前の順位を渡すと、落とした順（b, a）に関わらず a が先
    kept = _an(calc, [b, a], {"file_order": ["a.csv", "b.csv"]})
    assert kept["haisha_choice"]["used"] == ["a.csv"] and kept["file_order"] == ["a.csv", "b.csv"] and [n["kind"] for n in kept["notices"] if n["kind"] == "file_order"] == ["file_order"]
    assert kept["legs"][0]["ref"]["text"] == "加古川→江東 9t"
    same = _an(calc, [a, b], {"file_order": ["a.csv", "b.csv"]})
    assert not [n for n in same["notices"] if n["kind"] == "file_order"]
    # 使う配車表が前と変わったときは知らせる
    chg = _an(calc, [a, b], {"prev_haisha_used": ["b.csv"]})
    assert [n["kind"] for n in chg["notices"] if n["kind"] == "haisha_used_changed"] == ["haisha_used_changed"]


def test_missing_destination_is_unknown_with_a_confirm_card_and_the_weight_is_not_a_place(calc):
    t = "車番,車種,10/5(月),10/6(火),10/7(水)\n1201,大型,加古川 → 4.6t,加古川→江東 9t,江東→加古川 空\n"
    d = _an(calc, [_h(t)], {"munis": MUNIS})
    assert not [p for p in d["places"].values() if "4.6" in p["label"] or "t" == p["label"][-1:]]
    miss = [l for l in d["legs"] if l["ref"]["text"] == "加古川 → 4.6t"]
    assert len(miss) == 1 and miss[0]["kind"] == "unknown" and miss[0]["to"] is None and miss[0]["weight_kg"] == 4600.0
    cards = [c for c in d["confirm"] if c["kind"] == "dest"]
    assert len(cards) == 1 and cards[0]["key"] == "dest-missing" and cards[0]["count"] == 1 and cards[0]["legs"] == [miss[0]["id"]] and d["counts"]["dest_missing"] == 1
    assert d["unplaced"] == [] and d["counts"]["unknown"] == 1
    # 決定を当てても落ちない
    dd = _an(calc, [_h(t)], {"munis": MUNIS, "decisions": {"dest-missing": {"act": "ok"}}})
    assert [c["decision"]["act"] for c in dd["confirm"] if c["kind"] == "dest"] == ["ok"]


def test_unplaceable_distant_places_go_to_a_confirm_card_and_a_ward_after_the_prefecture_is_placed(calc):
    t = "車番,車種,10/5(月),10/6(火),10/7(水)\n1201,大型,姫路→東京 9t,姫路→東京 江東区 9t,姫路→東京都江東区 9t\n"
    d = _an(calc, [_h(t)], {"munis": MUNIS})
    by = {l["ref"]["text"]: l for l in d["legs"]}
    ward, whole = by["姫路→東京 江東区 9t"], by["姫路→東京都江東区 9t"]
    assert ward["km"] and ward["km"] > 400 and whole["km"] == ward["km"] and ward["to"] == whole["to"]
    assert d["places"][ward["to"]]["code"] == "13108" and d["places"][ward["to"]]["label"] == "江東区"
    lone = by["姫路→東京 9t"]
    assert lone["km"] is None and d["places"][lone["to"]]["placed"] is False
    cards = [c for c in d["confirm"] if c["kind"] == "place"]
    assert len(cards) == 1 and cards[0]["key"] == "place-unplaced" and cards[0]["legs"] == [lone["id"]] and "「東京」" in cards[0]["original"] and d["counts"]["unplaced_legs"] == 1


def test_scientific_weight_row_is_listed_as_unreadable_with_its_reason(calc):
    t = "車番,車種,10/5(月),10/6(火)\n1201,大型,加古川→江東 1e3kg,加古川→江東 9t\n"
    d = _an(calc, [_h(t)], {"munis": MUNIS})
    assert d["counts"]["legs"] == 1 and d["counts"]["unreadable_rows"] == 1 and "指数" in d["unreadable"][0]["reason"] and d["unreadable"][0]["original"] == "加古川→江東 1e3kg"


def test_empty_container_dispatch_is_an_empty_leg_in_a_whole_drop(calc):
    t = "車番,車種,10/5(月),10/6(火)\n1201,大型,三田→神戸港 空ｺﾝ回送,姫路～明石 AM 3t / PM 明石⇒姫路 空\n"
    d = _an(calc, [_h(t)])
    kinds = [(l["ref"]["text"], l["kind"]) for l in d["legs"]]
    assert kinds[0] == ("三田→神戸港 空ｺﾝ回送", "empty") and len(d["legs"]) == 3 and d["counts"]["unreadable"] == 0


def test_unknown_as_empty_rates_in_a_whole_drop_match_python(calc):
    t = "車番,車種,10/5(月),10/6(火),10/7(水),10/8(木)\n1201,大型,加古川→江東 9t,江東→加古川,加古川→江東 9t,江東→加古川 空\n"
    d = _an(calc, [_h(t)], {"munis": MUNIS})
    k = d["kpis"]
    assert d["counts"]["unknown"] == 1 and k["loaded_rate_if_unknown_empty"] < k["actual_rate"]["value"] and k["loaded_rate_if_unknown_empty"] + k["empty_rate_if_unknown_empty"] == pytest.approx(1.0)
    assert k["unknown_as_empty"]["unknown_legs"] == 1 and d["counts"]["unreadable_rows"] == 0
    assert as_json(own.kpis(d["legs"], d["vehicles"])) == k


def test_sample_json_has_the_new_kpi_fields():
    d = json.loads((ROOT / "web" / "data" / "own_sample.json").read_text(encoding="utf-8"))
    k = d["kpis"]
    u = k["unknown_as_empty"]
    assert k["loaded_rate_if_unknown_empty"] <= k["actual_rate"]["value"] and k["empty_rate_if_unknown_empty"] >= 1 - k["actual_rate"]["value"] and u["unknown_legs"] + u["unknown_legs_without_km"] == d["counts"]["unknown"]


def test_example_banshu_haisha_reads_in_python_and_js(calc):
    text = (ROOT / "examples" / "own" / "haisha_banshu_2026-10.csv").read_text(encoding="utf-8")
    import csv
    import io
    py = own.read_haisha(list(csv.reader(io.StringIO(text), strict=False)), 2026)
    n_py = sum(len(own.parse_cell(r["text"])["legs"]) for r in py["rows"] if not own.parse_cell(r["text"])["errors"])
    d = _an(calc, [_h(text, "haisha_banshu_2026-10.csv")], {"munis": MUNIS})
    assert n_py == 19 and d["counts"]["legs"] == 19 and d["counts"]["unreadable"] == 1 and d["counts"]["unreadable_rows"] == 1 and "指数" in d["unreadable"][0]["reason"]
    assert d["counts"]["nonleg_cells"] == 4 and d["counts"]["dest_missing"] == 1 and [c["kind"] for c in d["confirm"] if c["kind"] == "dest"] == ["dest"]
    assert not [p for p in d["places"].values() if p["label"] in ("4.6t", "東京都", "東京 江東区")]
