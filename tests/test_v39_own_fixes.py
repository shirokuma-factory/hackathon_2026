"""SPEC §39 自社の運行（own）の道具の採点 r1a・r1b の修正。

- 年: 見出しの曜日（10/1(木)）から決め、時計（今日の日付）では決めない。合う年が複数あるときは選ばせる。年をまたぐ配車表は 1 月を翌年にする。
- 区間が 0 件の取り込みは失敗として前の表示を残す。0 月・0 円を出さない。
- 確認のカード（修正・読まない・確定）の決定が OwnCalc.analyze に渡り、数字が計算し直される。
- 終了が開始以前の行は読めない行。距離が数でない行は Python も止まらず「距離が読めません」（Python と JS が同じ）。
- 費用の式は、表示した km と時間で掛けた値と一致する。単価の負の数は理由を出し、全角は NFKC で読む。
- OwnCalc.toKitLegs の出力が web/kit/schema/leg.json に合う。README・手引きの文言。
Edge が無い環境だけ、画面の試験を飛ばす（起動の時間切れは失敗）。
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import own  # noqa: E402

WEB = ROOT / "web"
RAW = ROOT / "data" / "own_sample" / "raw"
EXAMPLES = ROOT / "examples" / "own"
RAW_NAMES = ["haisha_2026-10.csv", "digitaco_1234.csv", "digitaco_5678.csv"]
SCHEMA = WEB / "kit" / "schema" / "leg.json"

# 実際の時計を 2027 年に固定する（新しい文書ごとに先に実行）
CLOCK_2027 = """
(function () {
  const RD = Date, fixed = RD.UTC(2027, 5, 15, 3, 0, 0);
  function FD() { if (!(this instanceof FD)) return new RD(fixed).toString(); return arguments.length === 0 ? new RD(fixed) : new (Function.prototype.bind.apply(RD, [null].concat([].slice.call(arguments))))(); }
  FD.prototype = RD.prototype; FD.now = function () { return fixed; }; FD.UTC = RD.UTC; FD.parse = RD.parse;
  window.Date = FD;
})();
"""

# ---------------------------------------------------------------- 年の表（Python と JS で同じ結果になる）
# (見出し, year, candidates, ambiguous, source)
YEAR_VECTORS = [
    (["車番", "車種", "2/27(金)", "2/28(土)", "3/1(日)"], 2026, [2026], False, "weekday"),  # 2 月の曜日: 2026 年だけ（2020 年は閏年で木曜）
    (["車番", "10/1(木)", "10/2(金)"], 2026, [2020, 2026], True, "weekday"),  # 10 月の曜日: 2020 年と 2026 年が同じ。2026 年以下でいちばん新しい年にする（SPEC §41）
    (["車番", "12/30(水)", "12/31(木)", "1/1(金)", "1/2(土)"], 2026, [2020, 2026], True, "weekday"),  # 年をまたぐ
    (["車番", "10/1", "10/2"], 2026, [2024, 2025, 2026, 2027, 2028, 2029, 2030], True, "none"),  # 曜日が無い
    (["車番", "10/1(木)", "10/2(木)"], 2026, [2024, 2025, 2026, 2027, 2028, 2029, 2030], True, "mismatch"),  # どの年にも合わない（続く 2 日が同じ曜日）
    (["車番", "２/２７(金)", "２/２８(土)", "３/１(日)"], 2026, [2026], False, "weekday"),  # 全角
    (["車番", "2025/10/1", "2025/10/2"], 2025, [2025], False, "explicit"),
    (["車番", "10月1日(木)", "10月2日(金)"], 2026, [2020, 2026], True, "weekday"),
    (["車番", "2/29(土)", "3/1(日)"], 2020, [2020], False, "weekday"),  # 2/29 があるのは閏年だけ
]


@pytest.mark.parametrize("head,year,cands,amb,src", YEAR_VECTORS)
def test_python_infer_year_does_not_use_the_clock(head, year, cands, amb, src):
    r = own.infer_year(head)
    assert (r["year"], r["candidates"], r["ambiguous"], r["source"]) == (year, cands, amb, src)


def test_python_wide_to_long_rolls_january_into_the_next_year():
    rows = [["車番", "12/30(水)", "12/31(木)", "1/1(金)", "1/2(土)"], ["1234", "加古川→江東", "", "加古川→江東", "江東→加古川"]]
    got = own.wide_to_long(rows, 2026)
    assert [r["date"] for r in got["rows"]] == ["2026-12-30", "2027-01-01", "2027-01-02"]
    assert own.assign_head_years(["", "12/30", "1/1", "12/5"], 2026) == {1: (2026, 12, 30), 2: (2027, 1, 1), 3: (2027, 12, 5)}
    # 年つきの見出しは、その年に合わせる
    assert own.assign_head_years(["12/30", "2030/1/1", "1/2"], 2026) == {0: (2026, 12, 30), 1: (2030, 1, 1), 2: (2030, 1, 2)}


# ---------------------------------------------------------------- 距離・終了が開始以前の行（Python と JS の一致）

BROKEN_CSV = """KBN,ST_TIME,ED_TIME,ODO_KM,JK_FLG,POS_NAME,DRV_CD
1,2026-10-06 20:10,2026-10-07 03:00,551.8,1,,A01
2,2026-10-07 03:00,2026-10-07 04:10,,,加古川,A01
1,2026-10-07 04:10,2026-10-07 05:00,abc,0,,A01
2,2026-10-07 05:00,2026-10-07 05:00,,,江東,A01
1,2026-10-07 06:00,2026-10-07 05:30,12,1,,A01
2,2026-10-07 07:00,2026-10-07 06:00,,,加古川,A01
1,2026-10-07 08:00,2026-10-07 09:00,"1,234.5km",0,,A01
1,2026-10-07 10:00,2026-10-07 11:00,1e3,1,,A01
1,2026-10-07 12:00,2026-10-07 13:00,-5,1,,A01
9,2026-10-07 14:00,2026-10-07 15:00,1,1,,A01
1,not-a-time,2026-10-07 15:00,1,1,,A01
2,2026-10-07 16:00,2026-10-07 16:45,,,江東,A01
"""


def _py_broken():
    rows = list(csv.DictReader(io.StringIO(BROKEN_CSV)))
    return own.parse_digitaco(rows, lambda n: n, "1234")


def test_python_parse_digitaco_does_not_raise_and_reports_unreadable_rows():
    r = _py_broken()
    reasons = {e["line"]: e["reason"] for e in r["errors"]}
    assert reasons[4] == "距離が読めません"  # abc
    assert reasons[5] == own.END_BEFORE_START and reasons[6] == own.END_BEFORE_START and reasons[7] == own.END_BEFORE_START  # 停車の終了=開始、走行・停車の終了<開始
    assert reasons[9] == "距離が読めません"  # 1e3 は数として読まない
    assert reasons[10] == "距離が読めません"  # -5
    assert reasons[11] == "区分が読めません" and reasons[12] == "時刻が読めません"
    # 距離が読めない走行は、距離を空にして残る
    assert [x["km"] for x in r["runs"]] == [551.8, None, 1234.5, None, None]
    assert all(s["minutes"] > 0 for s in r["stops"]) and len(r["stops"]) == 2


# ---------------------------------------------------------------- 静的な確認

def test_own_calc_has_no_clock_year_and_no_network():
    calc = (WEB / "own_calc.js").read_text(encoding="utf-8")
    assert "new Date()" not in calc and "Date.now" not in calc and "getFullYear()" not in calc.replace("getUTCFullYear()", "")
    own_py = (ROOT / "src" / "shipcompare" / "own.py").read_text(encoding="utf-8")
    assert "date.today" not in own_py and "datetime.now" not in own_py


def test_readme_matches_the_own_import_and_the_kit():
    r = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "次の版。今はサンプルだけ" not in r and "架空の会社のサンプルで、空車の型" not in r
    for t in ("自社の配車表・デジタコの CSV を落とすと", "docs/guide/自社の運行を分析する.md", "examples/own/", "web/kit/", "examples/kit/", "docs/guide/部品の使い方.md", "§35", "§36"):
        assert t in r, t
    assert "\r" not in r


def test_guide_states_the_alias_assumption_and_to_kit_legs_example():
    g = (ROOT / "docs" / "guide" / "自社の運行を分析する.md").read_text(encoding="utf-8")
    assert "辰巳" in g and "江東区" in g and "77.6%" in g and "76.9%" in g
    assert "を落とすと、同じ画面になります" not in g and "76.9%" in g[g.index("### 例のファイルとサンプルの数字の違い"):]
    k = (ROOT / "docs" / "guide" / "部品の使い方.md").read_text(encoding="utf-8")
    assert "OwnCalc.toKitLegs" in k and "\r" not in k and "\r" not in g


# ---------------------------------------------------------------- JS の計算（ヘッドレス Edge。時計は 2027 年に固定）

def _py_ref_options():
    import build_own_sample as bos
    ref = bos.build(write=False, use_claude=False)
    opts = {
        "year": bos.YEAR, "mon": bos.MON, "places": ref["places"], "base": ref["base"], "dock": ref["dock"],
        "aliases": {"known": [[a, p] for a, p in bos.KNOWN], "proposed": [[a, p] for a, p in bos.PROPOSED]},
        "estKm": {"|".join(sorted((ref["base"], ref["dock"]))): bos.OSRM_KM},
        "vehicles": ref["vehicles"], "driverLabels": bos.DRIVER_LABEL,
    }
    return ref, opts


def _haisha(head_cells, cell="加古川→江東 着5:30"):
    row = ["1234", "大型"] + [cell] * (len(head_cells) - 2)
    return "\n".join([",".join(head_cells), ",".join(row)]) + "\n"


@pytest.fixture(scope="module")
def calc(local_server):
    """own_calc.js だけを読み込んだ文書を、時計を 2027 年に固定した Edge で開く。"""
    with edge_page(1280, 800, webgl=False, hooks=(CLOCK_2027,)) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")  # 同じ配信元の何もない文書
        try:
            page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
            assert page.js("typeof OwnCalc === 'object'")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "own_calc.js を読み込めません", e, "own_fixes_fail")
        yield page


def _an(page, files, opts=None):
    return page.js(f"OwnCalc.analyze({json.dumps(files, ensure_ascii=False)}, {json.dumps(opts or {}, ensure_ascii=False)})")


def test_clock_is_really_fixed_to_2027_in_the_browser(calc):
    assert calc.js("new Date().getFullYear()") == 2027 and calc.js("Date.now() === Date.UTC(2027, 5, 15, 3, 0, 0)")


def test_js_infer_year_matches_python_vectors(calc):
    for head, year, cands, amb, src in YEAR_VECTORS:
        r = calc.js(f"OwnCalc.inferYear({json.dumps(head, ensure_ascii=False)})")
        assert (r["year"], r["candidates"], r["ambiguous"], r["source"]) == (year, cands, amb, src), head
        p = own.infer_year(head)
        assert r == p, head


def test_year_comes_from_weekday_headings_not_from_the_2027_clock(calc):
    head = ["車番", "車種", "2/27(金)", "2/28(土)", "3/1(日)"]
    d = _an(calc, [{"name": "haisha.csv", "text": _haisha(head)}])
    assert d["year"] == 2026 and d["year_choice"]["ambiguous"] is False and d["year_choice"]["source"] == "weekday"
    assert sorted({lg["date"] for lg in d["legs"]}) == ["2026-02-27", "2026-02-28", "2026-03-01"]
    assert d["failed"] is None and d["mon"] in (2, 3)
    # 曜日が分からない見出しでは、2027 年の時計を使わずに年を選ばせる
    d2 = _an(calc, [{"name": "haisha.csv", "text": _haisha(["車番", "車種", "10/1", "10/2"])}])
    assert d2["year"] == 2026 and d2["year_choice"]["ambiguous"] is True and d2["year_choice"]["source"] == "none"
    assert 2027 in d2["year_choice"]["candidates"] and all(lg["date"].startswith("2026-") for lg in d2["legs"])
    # 合う年が複数: 2026 年以下でいちばん新しい年にして、year_assumed を真にする（SPEC §41。先の年は選ばない。年は利用者が変えられる）
    head3 = ["車番", "車種", "10/1(木)", "10/2(金)"]
    d3 = _an(calc, [{"name": "haisha.csv", "text": _haisha(head3)}])
    assert not d3.get("needs_year") and d3["year"] == 2026 and d3["year_choice"]["year_assumed"] is True and d3["year_choice"]["candidates"] == [2020, 2026] and d3["legs"]
    d3b = _an(calc, [{"name": "haisha.csv", "text": _haisha(head3)}], {"year": 2026})
    assert d3b["year"] == 2026 and d3b["year_choice"]["ambiguous"] is True and d3b["year_choice"]["by_user"] is True
    d4 = _an(calc, [{"name": "haisha.csv", "text": _haisha(head3)}], {"year": 2020})
    assert d4["year"] == 2020 and d4["year_choice"]["by_user"] is True and sorted({lg["date"] for lg in d4["legs"]}) == ["2020-10-01", "2020-10-02"]


def test_sheet_spanning_years_rolls_january_into_the_next_year(calc):
    head = ["車番", "車種", "12/30(水)", "12/31(木)", "1/1(金)", "1/2(土)"]
    d = _an(calc, [{"name": "haisha.csv", "text": _haisha(head)}], {"year": 2026})
    assert d["year"] == 2026
    assert sorted({lg["date"] for lg in d["legs"]}) == ["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"]
    assert d["period"] == {"from": "2026-12-30", "to": "2027-01-02"}
    d2 = _an(calc, [{"name": "haisha.csv", "text": _haisha(head)}], {"year": 2020})
    assert sorted({lg["date"] for lg in d2["legs"]}) == ["2020-12-30", "2020-12-31", "2021-01-01", "2021-01-02"]
    # 同じ表を Python で読んだ日付と一致
    rows = [head, ["1234", "大型"] + ["加古川→江東"] * 4]
    assert [r["date"] for r in own.wide_to_long(rows, 2026)["rows"]] == sorted({lg["date"] for lg in d["legs"]})


def test_unreadable_files_only_is_an_import_failure_with_no_month_and_no_cost(calc):
    d = _an(calc, [{"name": "memo.csv", "text": "名前,年齢\n山田,30\n"}])
    assert d["counts"]["legs"] == 0 and d["failed"] and "1 件も読めません" in d["failed"] and "memo.csv" in d["failed"]
    assert d["mon"] is None and d["month"] == "" and d["period"] is None
    c = calc.js(f"OwnCalc.costs({json.dumps(d, ensure_ascii=False)}, '30', '3000')")
    assert c["empty"] is None and c["wait"] is None and c["emptyKm"] is None
    ok = _an(calc, [{"name": "haisha.csv", "text": _haisha(["車番", "車種", "2/27(金)"])}], {"year": 2026})  # 曜日 1 つでは年が決まらない（2015・2026・2032）
    assert ok["failed"] is None and ok["mon"] == 2


def test_js_and_python_agree_on_broken_digitaco_rows(calc):
    rows = list(csv.reader(io.StringIO(BROKEN_CSV)))
    js = calc.js(f"OwnCalc.parseDigitaco({json.dumps(rows, ensure_ascii=False)}, (n) => n, '1234')")
    py = _py_broken()
    assert js == json.loads(json.dumps(py)), (js, py)
    assert {e["reason"] for e in js["errors"]} == {"距離が読めません", own.END_BEFORE_START, "区分が読めません", "時刻が読めません"}
    for s in ("551.8", "１,234.5km", "abc", "1e3", "-5", "", ".5", "5.", "0x10"):
        assert calc.js(f"OwnCalc.parseKm({json.dumps(s)})") == own.parse_km(s), s


def test_cost_formula_multiplies_the_displayed_numbers_and_unit_inputs_are_checked(calc):
    ref, opts = _py_ref_options()
    files = [{"name": n, "text": (RAW / n).read_text(encoding="utf-8")} for n in RAW_NAMES]
    d = _an(calc, files, opts)
    empty_km = round(sum((lg["km"] or 0) for lg in d["legs"] if lg["kind"] == "empty"), 1)
    c = calc.js(f"OwnCalc.costs({json.dumps(d, ensure_ascii=False)}, '３０', '3,000.5')")  # 全角の数字
    assert c["perKm"] == 30 and c["perHour"] == 3000.5 and c["errors"] == {"perKm": None, "perHour": None}
    km_txt = f"{empty_km:,.1f}"
    assert c["empty"]["formula"] == f"空車 {km_txt} km × 30 円/km"
    assert c["empty"]["yen"] == round(empty_km * 30) and float(km_txt.replace(",", "")) * 30 - c["empty"]["yen"] < 1
    hours = round(sum(s["minutes"] for s in d["stops"] if s["minutes"] >= 30) / 60, 1)
    assert c["wait"]["formula"] == f"30 分以上の停車 {hours:.1f} 時間 × 3,000.5 円/時" and c["wait"]["yen"] == round(hours * 3000.5)
    # 小数の単価を整数に丸めて見せない
    c2 = calc.js(f"OwnCalc.costs({json.dumps(d, ensure_ascii=False)}, '30.5', '')")
    assert "× 30.5 円/km" in c2["empty"]["formula"] and c2["wait"] is None
    # 負の数・数でないもの: 理由を出して費用は出さない（0 円にしない）
    for bad, word in (("-5", "0 以上"), ("abc", "数で入れてください"), ("-0.1", "0 以上"), ("１e3", "数で入れてください")):
        c3 = calc.js(f"OwnCalc.costs({json.dumps(d, ensure_ascii=False)}, {json.dumps(bad)}, '')")
        assert c3["empty"] is None and word in c3["errors"]["perKm"], bad
    c4 = calc.js(f"OwnCalc.costs({json.dumps(d, ensure_ascii=False)}, '', ' ')")
    assert c4["empty"] is None and c4["errors"] == {"perKm": None, "perHour": None}
    # 例: 11,368.7 km × 30 円/km = 341,061 円（表示どおりに掛けて合う）
    fake = {"legs": [{"kind": "empty", "km": 11368.7}], "stops": []}
    f = calc.js(f"OwnCalc.costs({json.dumps(fake)}, '30', '')")
    assert f["empty"]["formula"] == "空車 11,368.7 km × 30 円/km" and f["empty"]["yen"] == 341061


def test_decisions_in_the_confirm_tab_change_the_numbers(calc):
    ref, opts = _py_ref_options()
    files = [{"name": n, "text": (RAW / n).read_text(encoding="utf-8")} for n in RAW_NAMES]
    base = _an(calc, files, opts)
    links = [c for c in base["confirm"] if c["kind"] == "link" and c["key"].endswith(":distance")]
    assert links, "距離のずれのカードがありません"
    leg_of = {lg["id"]: lg for lg in base["legs"]}
    changed = None
    for c in links:
        lg = leg_of[c["leg"]]
        if lg["kind"] in ("loaded", "empty"):
            changed = (c, lg)
            break
    assert changed, "判定できた区間の距離のずれがありません"
    c, lg = changed
    k0 = base["kpis"]["actual_rate"]
    # 読まない: デジタコの距離を使わず、推定距離に替える
    d1 = _an(calc, files, {**opts, "decisions": {c["key"]: {"act": "skip", "text": ""}}})
    lg1 = next(x for x in d1["legs"] if x["id"] == lg["id"])
    assert lg1["km"] != lg["km"] and lg1["km_src"] != "デジタコ"
    k1 = d1["kpis"]["actual_rate"]
    assert (k1["km_loaded"], k1["km_empty"]) != (k0["km_loaded"], k0["km_empty"]) and k1["value"] != k0["value"]
    card1 = next(x for x in d1["confirm"] if x["key"] == c["key"])
    assert card1["decision"]["applied"] is True and card1["decision"]["act"] == "skip" and "推定" in card1["decision"]["note"]
    # 修正: 入れた距離にする
    d2 = _an(calc, files, {**opts, "decisions": {c["key"]: {"act": "fix", "text": "10"}}})
    lg2 = next(x for x in d2["legs"] if x["id"] == lg["id"])
    assert lg2["km"] == 10 and lg2["km_src"] == "利用者の修正" and d2["kpis"]["actual_rate"]["value"] != k0["value"]
    # 修正で数でない文字: 値は変えない
    d3 = _an(calc, files, {**opts, "decisions": {c["key"]: {"act": "fix", "text": "たくさん"}}})
    assert d3["kpis"] == base["kpis"] and next(x for x in d3["confirm"] if x["key"] == c["key"])["decision"]["applied"] is False
    # 確定: 数字は変わらず、区間の色は黄色でなくなる
    d4 = _an(calc, files, {**opts, "decisions": {c["key"]: {"act": "ok", "text": ""}}})
    assert d4["kpis"] == base["kpis"] and next(x for x in d4["legs"] if x["id"] == lg["id"])["color"] == "green" and lg["color"] == "yellow"
    # 取り消す（決定なし）と元に戻る
    assert _an(calc, files, {**opts, "decisions": {}})["kpis"] == base["kpis"]
    # カードは決定のあとも残り、件数は変わらない
    assert len(d1["confirm"]) == len(d2["confirm"]) == len(base["confirm"])


def test_column_decisions_skip_or_reassign_a_digitaco_column(calc):
    ref, opts = _py_ref_options()
    files = [{"name": n, "text": (RAW / n).read_text(encoding="utf-8")} for n in RAW_NAMES]
    base = _an(calc, files, opts)
    cols = [c for c in base["confirm"] if c["kind"] == "column"]
    assert cols and all(c["key"].startswith("column:") for c in cols)
    # 走行距離の列を読まないと、距離はデジタコではなく推定になり、実車率が変わる
    d1 = _an(calc, files, {**opts, "decisions": {"column:デジタコ:ODO_KM": {"act": "skip", "text": ""}}})
    assert d1["kpis"]["actual_rate"]["value"] != base["kpis"]["actual_rate"]["value"]
    # 地点名の列（確認のカードがある列）を読まないと、停車の地点が無くなり、待ちの気づき（規則 3）が消える
    pos = "column:デジタコ:POS_NAME"
    assert any(c["key"] == pos for c in cols)
    d1b = _an(calc, files, {**opts, "decisions": {pos: {"act": "skip", "text": ""}}})
    assert 3 in [i["rule"] for i in base["insights"]] and 3 not in [i["rule"] for i in d1b["insights"]]
    card = next(c for c in d1b["confirm"] if c["key"] == pos)
    assert card["decision"]["applied"] is True and "読まずに" in card["decision"]["note"] and len(d1b["confirm"]) == len(base["confirm"])
    # 読まない列に別の役割を当てる: 例の X9 を「走行距離」として読む（もとは読まない列）
    x9 = "column:デジタコ:X9"
    d2 = _an(calc, files, {**opts, "decisions": {x9: {"act": "fix", "text": "走行距離"}}})
    assert d2["kpis"] != base["kpis"] or d2["legs"] != base["legs"]
    assert calc.js("OwnCalc.resolveRole('走行距離')") == "distance_km" and calc.js("OwnCalc.resolveRole('ふしぎ')") is None
    assert calc.js("OwnCalc.resolveRole('開始の日時')") == "start_time" and calc.js("OwnCalc.resolveRole('ODO_KM')") == "distance_km"


def test_to_kit_legs_output_validates_against_the_leg_schema(calc):
    jsonschema = pytest.importorskip("jsonschema")
    ref, opts = _py_ref_options()
    files = [{"name": n, "text": (RAW / n).read_text(encoding="utf-8")} for n in RAW_NAMES]
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    d = _an(calc, files, opts)
    kit = calc.js(f"OwnCalc.toKitLegs({json.dumps(d, ensure_ascii=False)})")
    jsonschema.Draft202012Validator(schema).validate(kit)
    assert len(kit["legs"]) > 50 and {lg["status"] for lg in kit["legs"]} <= {"loaded", "empty", "unknown"}
    assert {lg["basis"] for lg in kit["legs"]} <= {"実測", "推定", "仮定"}
    assert kit["meta"]["fiction"] is False and kit["legs"][0]["id"] == d["legs"][0]["id"]
    # 地図に置けない地点を通る区間は含めず、件数を書く
    d_un = _an(calc, files, {"year": 2026})  # 地点の表が無い: どの地点も置けない
    kit2 = calc.js(f"OwnCalc.toKitLegs({json.dumps(d_un, ensure_ascii=False)})")
    jsonschema.Draft202012Validator(schema).validate(kit2)
    assert len(kit2["legs"]) == 0 and "含めていません" in kit2["meta"]["note"]


# ---------------------------------------------------------------- 画面（地図の画面の「自社の運行」）

NET_HOOK = """
window.__reqs = [];
(function(){
  const f = window.fetch;
  window.fetch = function(u){ try { window.__reqs.push(String(u && u.url ? u.url : u)); } catch (e) {} return f.apply(this, arguments); };
})();
"""


def _open_app(page, url):
    """地図の画面を開く。地図の部品の CDN が遅いと、読み込みの完了（Page.goto の 30 秒）を待てないので、移動だけして、起動を 240 秒まで待つ。時間切れは失敗。"""
    from conftest import RESOURCE_HOOK
    page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
    page.send("Page.navigate", url=url)
    try:
        page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
        page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
    except Exception as e:  # noqa: BLE001
        fail_with_evidence(page, "地図の画面が起動しません", e, "own_fixes_fail")


@pytest.fixture(scope="module")
def app(local_server):
    import capture_web as cw
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK, NET_HOOK, CLOCK_2027)) as page:
        _open_app(page, f"http://localhost:{local_server}/index.html#v=own")
        try:
            page.wait(lambda: page.js("!!document.querySelector('#own-file')"), 60, "ファイルを落とす欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "ファイルを落とす欄が出ません", e, "own_fixes_fail")
        time.sleep(2.0)
        yield page


def _set_files(page, paths):
    doc = page.send("DOM.getDocument", depth=1)
    nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#own-file")["nodeId"]
    page.send("DOM.setFileInputFiles", files=[str(p) for p in paths], nodeId=nid)


def _src(page) -> str:
    return page.js("(document.querySelector('#own-src') || {textContent: ''}).textContent")


def _to_sample(page):
    if page.js("Own.state.mode") != "sample":
        page.js("document.querySelector('#own-src [data-act=reset]').click()")
    page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")


def _import_examples(page):
    _to_sample(page)
    page.js("Own.setTab('import')")
    _set_files(page, [EXAMPLES / n for n in RAW_NAMES])
    page.wait(lambda: "自社のファイル: 3 件" in _src(page), 120, "取り込み")
    time.sleep(0.4)


# 距離のずれが必ず出る自作の入力（SPEC §40）。行き 1 本のデジタコのメーターが 900 km で、直線距離 × 1.25 の推定（約 575 km）より 15% 以上長い。
# 例のデータの有無にかかわらず、距離のずれの確認カードが 1 枚以上できる
GAP_HAISHA = "車番,車種,2026/10/1,2026/10/2\n1234,大型,加古川→江東 着5:30,江東→加古川 （空）\n"
GAP_DIGITACO = ("KBN,ST_TIME,ED_TIME,ODO_KM,JK_FLG,POS_NAME,DRV_CD\n"
                "1,2026-10-01 00:00,2026-10-01 10:00,900,1,,D01\n"
                "2,2026-10-01 10:00,2026-10-01 11:00,,,江東,D01\n")


def _import_gap_files(page, tmp_path):
    h = tmp_path / "haisha_gap.csv"
    h.write_text(GAP_HAISHA, encoding="utf-8")
    d = tmp_path / "digitaco_1234.csv"
    d.write_text(GAP_DIGITACO, encoding="utf-8")
    _to_sample(page)
    page.js("Own.setTab('import')")
    _set_files(page, [h, d])
    page.wait(lambda: "自社のファイル: 2 件" in _src(page), 120, "取り込み")
    time.sleep(0.4)


def test_ui_confirm_decisions_recompute_the_numbers_and_can_be_undone(app, tmp_path):
    page = app
    _import_gap_files(page, tmp_path)
    kp0 = page.js("Own.state.data.kpis.actual_rate")
    page.js("Own.setTab('confirm')")
    key = page.js("(Own.state.data.confirm.find(c => c.kind === 'link' && c.key.endsWith(':distance') && "
                  "Own.state.data.legs.find(l => l.id === c.leg && l.kind !== 'unknown')) || {}).key")
    assert key, "距離のずれの確認カードができていません（自作の入力で必ずできるはず）"
    sel = f"#own-body .o-card[data-key=\"{key}\"]"
    page.js(f"document.querySelector('{sel} [data-act=skip]').click()")
    page.wait(lambda: page.js(f"(Own.state.data.confirm.find(c => c.key === {json.dumps(key)}) || {{}}).decision != null"), 20, "決定の反映")
    kp1 = page.js("Own.state.data.kpis.actual_rate")
    assert kp1["value"] != kp0["value"]
    assert "読まないことにしました" in page.js(f"document.querySelector('{sel}').innerText")
    assert "推定" in page.js(f"document.querySelector('{sel} .o-eff').innerText")
    right = page.js("document.querySelector('#own-right').innerText")
    assert f"{kp1['value'] * 100:.1f}" in right
    # 修正: 数でない文字は断る。数なら反映
    page.js(f"document.querySelector('{sel} [data-act=undo]').click()")
    page.wait(lambda: page.js("Own.state.data.kpis.actual_rate.value") == kp0["value"], 20, "取り消し")
    page.js(f"document.querySelector('{sel} [data-act=fix]').click()")
    page.js(f"(function(){{const i = document.querySelector('{sel} .o-fix input'); i.value = 'abc'; document.querySelector('{sel} [data-act=fixok]').click();}})()")
    assert page.js("Own.state.dec") == {}  # 断られて、決定は入らない
    page.js(f"(function(){{const i = document.querySelector('{sel} .o-fix input'); i.value = '１０'; i.dispatchEvent(new Event('input')); document.querySelector('{sel} [data-act=fixok]').click();}})()")
    assert page.js(f"Own.state.dec[{json.dumps(key)}]") == "fix"
    page.wait(lambda: page.js("Own.state.data.kpis.actual_rate.value") != kp0["value"], 20, "修正の反映")
    _to_sample(page)
    # サンプルでは数字は固定で、そのことをカードに書く
    page.js("Own.setTab('confirm')")
    assert "サンプルの数字は元の記録から作った固定の値です" in page.js("document.querySelector('#own-body').innerText")


def test_ui_unreadable_file_only_keeps_the_previous_display(app, tmp_path):
    page = app
    bad = tmp_path / "memo.csv"
    bad.write_text("名前,年齢\n山田,30\n", encoding="utf-8")
    # サンプルを見ているとき: サンプルのまま
    _to_sample(page)
    page.js("Own.setTab('import')")
    _set_files(page, [bad])
    page.wait(lambda: "1 件も読めません" in page.js("document.querySelector('#own-drop-msg').textContent"), 20, "失敗の表示")
    assert page.js("Own.state.mode") == "sample" and "サンプル（架空の会社）" in _src(page)
    # 自社のファイルを見ているとき、読めないファイルを足しても、前の区間は残る（足したファイルは読めない行に出る）
    _import_examples(page)
    page.js("Own.setTab('import')")
    _set_files(page, [bad])
    page.wait(lambda: "memo.csv" in page.js("document.querySelector('#own-body').innerText"), 20, "足したファイルの表示")
    assert page.js("Own.state.mode") == "import" and page.js("Own.state.data.counts.legs") == 107
    text = page.js("document.querySelector('#own-right').innerText")
    assert not re.search(r"(?<!\d)0 月", text) and "空車 0 km" not in text and "配車表にもデジタコにも見えません" in page.js("document.querySelector('#own-body').innerText")
    _to_sample(page)


def test_ui_year_selector_for_sheets_without_weekdays_and_clock_is_not_used(app, tmp_path):
    page = app
    assert page.js("new Date().getFullYear()") == 2027
    p = tmp_path / "haisha_nowd.csv"
    p.write_text(_haisha(["車番", "車種", "10/1", "10/2", "10/3"]), encoding="utf-8")
    _to_sample(page)
    page.js("Own.setTab('import')")
    _set_files(page, [p])
    page.wait(lambda: "自社のファイル: 1 件" in _src(page), 120, "取り込み")
    assert page.js("Own.state.data.year") == 2026 and page.js("!!document.querySelector('#own-year')")
    assert "今日の日付では決めていません" in page.js("document.querySelector('.o-year').innerText")
    page.js("(function(){const s = document.querySelector('#own-year'); s.value = '2025'; s.dispatchEvent(new Event('change', {bubbles: true}));})()")
    page.wait(lambda: page.js("Own.state.data.year") == 2025, 20, "年の選び直し")
    assert page.js("Own.state.data.legs.every(l => l.date.startsWith('2025-'))") and page.js("document.querySelector('#own-year').value") == "2025"
    # 曜日から年が決まる表では、選ぶ欄を出さない（別の配車表なので、サンプルに戻してから）
    _to_sample(page)
    q = tmp_path / "haisha_wd.csv"
    q.write_text(_haisha(["車番", "車種", "2/27(金)", "2/28(土)", "3/1(日)"]), encoding="utf-8")
    _set_files(page, [q])
    page.wait(lambda: page.js("Own.state.mode") == "import" and page.js("Own.state.data.year") == 2026 and page.js("Own.state.data.legs.length") > 0
              and page.js("Own.state.data.legs.some(l => l.date === '2026-02-27')"), 30, "曜日から年が決まる")
    assert not page.js("!!document.querySelector('#own-year')")
    _to_sample(page)


def test_ui_cost_inputs_show_reasons_and_read_full_width(app):
    page = app
    _import_examples(page)
    page.js("Own.state.cost.perKm = ''; Own.state.cost.perHour = ''")
    page.js("document.querySelector('#own-perkm').dispatchEvent(new Event('input', {bubbles: true}))")
    page.js("(function(){const e = document.querySelector('#own-perkm'); e.focus(); e.value = '-5'; e.dispatchEvent(new Event('input', {bubbles: true}));})()")
    out = page.js("document.querySelector('#own-cost-out').innerText")
    assert "0 以上の数で入れてください" in out and page.js("document.querySelectorAll('#own-cost-out .o-crow').length") == 0
    assert page.js("document.querySelector('#own-perkm').getAttribute('aria-invalid')") == "true"
    page.js("(function(){const e = document.querySelector('#own-perkm'); e.value = '３０'; e.dispatchEvent(new Event('input', {bubbles: true}));})()")
    txt = page.js("document.querySelector('#own-cost-out').innerText")
    assert "× 30 円/km" in txt and "0 以上" not in txt and page.js("document.querySelector('#own-perkm').getAttribute('aria-invalid')") is None
    # 画面に出た式の km × 単価 が、出た円と合う
    import re
    m = re.search(r"空車 ([\d,.]+) km × 30 円/km = ([\d,]+) 円", txt)
    assert m, txt
    assert round(float(m.group(1).replace(",", "")) * 30) == int(m.group(2).replace(",", ""))
    page.js("(function(){const e = document.querySelector('#own-perkm'); e.value = ''; e.dispatchEvent(new Event('input', {bubbles: true}));})()")
    _to_sample(page)


def test_ui_to_kit_legs_from_an_imported_result_validates(app):
    jsonschema = pytest.importorskip("jsonschema")
    page = app
    _import_examples(page)
    kit = page.js("OwnCalc.toKitLegs(Own.state.data)")
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(kit)
    d = page.js("Own.state.data")
    placed = sum(1 for lg in d["legs"] if all(d["places"].get(x, {}).get("lon") is not None for x in (lg["frm"], lg["to"]) if x) and lg["frm"] and lg["to"])
    assert len(kit["legs"]) == placed > 0
    _to_sample(page)


def test_ui_console_has_no_errors_after_the_flow(app):
    # 外部の Web フォント（Google Fonts）の取得失敗は、この修正と関係がない（ネットワーク次第）ので数えない
    errs = [e for e in app.js("window.__errs") if "fonts.googleapis.com" not in e]
    assert errs == []


def test_python_and_js_date_helpers_agree_on_weekdays():
    assert own.WEEKDAYS[date(2026, 10, 1).weekday()] == "木" and own.WEEKDAYS[date(2020, 10, 1).weekday()] == "木"
