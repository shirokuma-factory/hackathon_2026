"""自社の運行 r6a・r8a・r8b の採点で出た指摘の直しの試験。

荷主名や「積」を挟んだ配車表のセル、「帰り荷さがし中」、公休などの読み飛ばす語、作業の明細の区分と荷待ち時間の列、
閉じていない引用符、確認待ちの別名を当てないサンプル（2 つの画面で数字が同じ）、最大積載量の仮定の引き上げ、
区間 × 曜日の表と気づきの曜日の数え方、指数の重さの注記。Python（src/shipcompare/own.py）と JS（web/own_calc.js）を同じ入力で比べる。
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

EX = ROOT / "examples" / "own"


def J(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def as_json(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


# (セルの文字, [(frm, to, empty, weight_kg)], 読めない理由の数, 読み飛ばす語か)
CELLS = [
    ("姫路 ﾄｳﾜ電機 → 春日部 着6:00 9.5t", [("姫路", "春日部", False, 9500.0)], 0, False),
    ("岩槻 積 → 姫路 6.2t", [("岩槻", "姫路", False, 6200.0)], 0, False),
    ("ﾄｳﾜ電機 姫路→春日部 9.5t", [("姫路", "春日部", False, 9500.0)], 0, False),
    ("姫路 荷主A商事 → 春日部 卸 6t", [("姫路", "春日部", False, 6000.0)], 0, False),
    ("東京 江東区 → 加古川 4t", [("江東区", "加古川", False, 4000.0)], 0, False),
    ("春日部→姫路 帰り荷さがし中", [("春日部", "姫路", True, None)], 0, False),
    ("春日部→姫路 帰り荷探し", [("春日部", "姫路", True, None)], 0, False),
    ("春日部→姫路 帰り荷待ち", [("春日部", "姫路", True, None)], 0, False),
    ("春日部→姫路 求荷中", [("春日部", "姫路", True, None)], 0, False),
    ("春日部→姫路 帰り荷あり 5t", [("春日部", "姫路", False, 5000.0)], 0, False),
    ("公休", [], 0, True), ("有休", [], 0, True), ("有給", [], 0, True), ("欠勤", [], 0, True), ("代休", [], 0, True), ("休日", [], 0, True), ("休み", [], 0, True),
]


def _legs(p: dict) -> list[tuple]:
    return [(l["frm"], l["to"], l["empty"], l["weight_kg"]) for l in p["legs"]]


@pytest.mark.parametrize("text,legs,nerr,nonleg", CELLS)
def test_python_reads_real_world_cells(text, legs, nerr, nonleg):
    p = own.parse_cell(text)
    assert _legs(p) == legs and len(p["errors"]) == nerr and p["nonleg"] is nonleg, text


def test_cell_note_drops_exponent_fragment():
    p = own.parse_cell("江東→加古川 1e3kg")
    assert p["errors"] and "note" not in p["legs"][0], p
    assert own.cell_note("1e3kg 冷凍") == "冷凍"
    assert own.cell_note("冷凍 1E+3t") == "冷凍"


def test_digitaco_work_detail_kinds_are_read():
    rows = [
        {"運行日": "2026-10-05", "車番": "1234", "作業": "運転", "開始": "2026-10-05 08:00", "終了": "2026-10-05 10:00", "距離": "80", "場所": ""},
        {"運行日": "2026-10-05", "車番": "1234", "作業": "荷卸", "開始": "2026-10-05 10:00", "終了": "2026-10-05 10:40", "距離": "", "場所": "江東"},
        {"運行日": "2026-10-05", "車番": "1234", "作業": "待機", "開始": "2026-10-05 10:40", "終了": "2026-10-05 11:20", "距離": "", "場所": "江東"},
        {"運行日": "2026-10-05", "車番": "1234", "作業": "休憩", "開始": "2026-10-05 11:20", "終了": "2026-10-05 11:50", "距離": "", "場所": ""},
    ]
    roles = {own.col_key("作業"): "kind"}
    r = own.parse_digitaco(rows, lambda n: n or None, None, roles)
    assert r["errors"] == [], r["errors"]
    assert len(r["runs"]) == 1 and len(r["stops"]) == 3
    assert [s.get("wait", False) for s in r["stops"]] == [False, True, False]


def test_digitaco_summary_wait_minutes_role():
    rows = [{"日付": "2026-10-05", "車番": "1234", "実車距離": "300", "空車距離": "100", "荷待ち時間": "1:30"},
            {"日付": "2026-10-06", "車番": "1234", "実車距離": "200", "空車距離": "0", "荷待ち時間": "45"}]
    assert own.role_of("荷待ち時間") == "wait_min" and "wait_min" in [o["role"] for o in own.role_options("デジタコ")]
    r = own.parse_digitaco_summary(rows)
    assert r["errors"] == [] and [w["minutes"] for w in r["waits"]] == [90, 45]
    assert own.parse_wait_min("1.5時間") == 90 and own.parse_wait_min("abc") is None


def test_load_rate_share_uses_large_legs_as_denominator():
    vs = [{"plate": "1", "cls": "大型", "max_load_kg": 10000.0}, {"plate": "2", "cls": "4t", "max_load_kg": 4000.0}]
    base = {"kind": "loaded", "km": 100.0}
    legs = [dict(base, plate="1", weight_kg=5000.0), dict(base, plate="1", weight_kg=None), dict(base, plate="1", kind="empty", weight_kg=None)] + [dict(base, plate="2", weight_kg=2000.0)] * 7
    lr = own.kpis(legs, vs)["load_rate"]
    assert lr["legs"] == 1 and lr["of_large"] == 3 and abs(lr["share"] - 1 / 3) < 1e-9, lr


def test_assumed_max_is_raised_not_dropped():
    vs = [{"plate": "1", "cls": "大型", "max_load_kg": 10000.0, "max_load_assumed": True}, {"plate": "2", "cls": "大型", "max_load_kg": 15000.0, "max_load_assumed": False},
          {"plate": "3", "cls": "大型", "max_load_kg": 10000.0, "max_load_assumed": True}]
    legs = [{"plate": "1", "weight_kg": 12000.0}, {"plate": "3", "weight_kg": 13000.0}, {"plate": "1", "weight_kg": 9000.0}, {"plate": "2", "weight_kg": 14000.0}]
    r = own.raise_assumed_max(vs, legs)
    assert [v["max_load_kg"] for v in r["vehicles"]] == [13000.0, 15000.0, 13000.0]
    assert r["note"] == {"cls": "大型", "from_kg": 10000.0, "to_kg": 13000.0, "legs": 2, "vehicles": 2}
    assert vs[0]["max_load_kg"] == 10000.0  # 元は変えない
    # 上限（20t）を超える重さは、t と kg の取り違えかもしれないので上げない
    r2 = own.raise_assumed_max(vs, [{"plate": "1", "weight_kg": 120000.0}])
    assert r2["note"] is None and r2["vehicles"][0]["max_load_kg"] == 10000.0
    # 車ごと・車格ごとに利用者が決めた値は変えない
    assert own.raise_assumed_max(own.apply_max_payload(vs, None, {"大型": 11000}), legs)["note"] is None


def _leg(i, d, wd, kind="empty"):
    return {"id": f"L{i}", "plate": "1", "date": d, "frm": "p2", "to": "p1", "dir": "back", "cycle_wd": wd, "kind": kind, "km": 100.0, "sources": {}}


def test_pair_table_and_insight_count_the_same_weekday():
    # 月曜に出て火曜に戻る 6 便。便自身の日付は火曜だが、往復が車庫を出た曜日（月曜）で数える
    legs = [_leg(i, f"2026-10-{6 + 7 * (i % 3):02d}", 0) for i in range(6)]
    places = {"p1": {"short": "加古川", "muni": "加古川市", "pseudo": "地点 01"}, "p2": {"short": "江東", "muni": "江東区", "pseudo": "地点 02"}}
    pt = own.pair_tables(legs, places, [{"plate": "1", "cls": "大型"}], "p1", "p2")
    assert [(r["weekday"], r["trips"], r["empty"]) for r in pt["pair_table"]] == [("月", 6, 6)]
    ins = own.insights({"legs": legs, "places": places, "base": "p1", "dock": "p2", "vehicles": []})
    assert ins[0]["rule"] == 1 and "月曜" in ins[0]["text"] and "6 便" in ins[0]["text"], ins[0]["text"]
    # cycle_wd が無い区間は、便自身の日付の曜日
    assert own.leg_wd({"date": "2026-10-06"}) == 1 and own.leg_wd({"date": "2026-10-06", "cycle_wd": 0}) == 0


# ---------------------------------------------------------------- JS（ヘッドレス Edge）と Python の照合

@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        try:
            page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
            assert page.js("typeof OwnCalc === 'object'")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "own_calc.js を読み込めません", e, "own_v48_fail")
        yield page


def test_js_reads_the_same_cells_as_python(calc):
    for text, legs, nerr, nonleg in CELLS:
        js = calc.js(f"OwnCalc.parseCell({J(text)})")
        assert js == as_json(own.parse_cell(text)), text
    for rest in ["1e3kg 冷凍", "冷凍 1E+3t", "納品 9t"]:
        assert calc.js(f"OwnCalc.cellNote({J(rest)})") == own.cell_note(rest), rest


def test_js_digitaco_kinds_wait_role_and_raise_match_python(calc):
    rows = [["運行日", "車番", "作業", "開始", "終了", "距離", "場所"],
            ["2026-10-05", "1234", "運転", "2026-10-05 08:00", "2026-10-05 10:00", "80", ""],
            ["2026-10-05", "1234", "荷卸", "2026-10-05 10:00", "2026-10-05 10:40", "", "江東"],
            ["2026-10-05", "1234", "待機", "2026-10-05 10:40", "2026-10-05 11:20", "", "江東"],
            ["2026-10-05", "1234", "休憩", "2026-10-05 11:20", "2026-10-05 11:50", "", ""]]
    drows = [dict(zip(rows[0], r)) for r in rows[1:]]
    roles = {own.col_key("作業"): "kind"}
    py = own.parse_digitaco(drows, lambda n: n or None, None, roles)
    js = calc.js(f"OwnCalc.parseDigitaco({J(rows)}, (n) => n || null, null, {J(roles)})")
    assert js == as_json(py) and not js["errors"]
    srows = [["日付", "車番", "実車距離", "空車距離", "荷待ち時間"], ["2026-10-05", "1234", "300", "100", "1:30"], ["2026-10-06", "1234", "200", "0", "45"]]
    pys = own.parse_digitaco_summary([dict(zip(srows[0], r)) for r in srows[1:]])
    jss = calc.js(f"OwnCalc.parseDigitacoSummary({J(srows)}, null, undefined)")
    assert jss == as_json(pys) and [w["minutes"] for w in jss["waits"]] == [90, 45]
    assert calc.js("OwnCalc.roleOf('荷待ち時間')") == "wait_min"
    assert "wait_min" in [o["role"] for o in calc.js("OwnCalc.roleOptions('デジタコ')")]
    vs = [{"plate": "1", "cls": "大型", "max_load_kg": 10000.0, "max_load_assumed": True}, {"plate": "2", "cls": "大型", "max_load_kg": 15000.0, "max_load_assumed": False}]
    legs = [{"plate": "1", "weight_kg": 12000.0}, {"plate": "1", "weight_kg": 120000.0}]
    assert calc.js(f"OwnCalc.raiseAssumedMax({J(vs)}, {J(legs)})") == as_json(own.raise_assumed_max(vs, legs))
    assert calc.js(f"OwnCalc.legWd({J({'date': '2026-10-06'})})") == own.leg_wd({"date": "2026-10-06"}) == 1


def test_csv_unterminated_quote_does_not_swallow_the_file(calc):
    text = '車番,日付,備考\n1234,10/1,"姫路 → 春日部\n5678,10/1,春日部 → 姫路\n9012,10/1,休\n'
    rows = calc.js(f"(() => {{ const r = OwnCalc.parseCsv({J(text)}); return {{ rows: r, warnings: r.warnings }}; }})()")
    assert len(rows["rows"]) == 4 and rows["rows"][2] == ["5678", "10/1", "春日部 → 姫路"], rows
    assert rows["warnings"] and rows["warnings"][0]["line"] == 2
    # 正しい複数行の引用符つきのセルは、これまでどおり 1 つのセル
    ok = calc.js(f"OwnCalc.parseCsv({J(chr(10).join(['a,b,c', '1,\"x', 'y\",3', '4,5,6', '']))})")
    assert ok == [["a", "b", "c"], ["1", "x\ny", "3"], ["4", "5", "6"]]


def test_both_screens_give_the_same_sample_numbers(calc):
    """地図の画面のサンプル（web/data/own_sample.json）と、own_lite.html のサンプル（examples/own/ を JS が読んだ結果）の数字が同じ。"""
    sample = json.loads((ROOT / "web" / "data" / "own_sample.json").read_text(encoding="utf-8"))
    files = [{"name": n, "text": (EX / n).read_text(encoding="utf-8")} for n in ("haisha_2026-10.csv", "digitaco_1234.csv", "digitaco_5678.csv")]
    ix = json.loads((ROOT / "web" / "data" / "index.json").read_text(encoding="utf-8"))
    pt = json.loads((ROOT / "web" / "data" / "muni.json").read_text(encoding="utf-8")).get("pt") or []
    munis = [{"code": c, "name": ix["names"][i], "pref": ix["prefs"][i], "lon": (pt[i] if i < len(pt) and pt[i] else [ix["lon"][i], ix["lat"][i]])[0],
              "lat": (pt[i] if i < len(pt) and pt[i] else [ix["lon"][i], ix["lat"][i]])[1]} for i, c in enumerate(ix["codes"])]
    # own_lite.html のサンプルは、鹿屋市と江東区の間を OSRM の値で数える（tools/own_page_template.html の sampleEst）
    est = "(a, b) => ((a.code === '46203' && b.code === '13108') || (a.code === '13108' && b.code === '46203') ? { km: 1381.7, src: 'OSRM 推定' } : null)"
    d = calc.js(f"OwnCalc.analyze({J(files)}, {{ munis: {J(munis)}, estKmFn: {est} }})")
    import build_own_sample as bos
    assert bos.OSRM_KM == 1381.7 and "'13108|46203': 1381.7" in (ROOT / "web" / "own_calc.js").read_text(encoding="utf-8")
    a, b = sample["kpis"], d["kpis"]
    for k in ("actual_rate", "load_rate", "load_efficiency"):
        for f in ("value", "legs", "km_loaded", "km_empty", "share"):
            if f in a[k]:
                assert a[k][f] == pytest.approx(b[k][f]), (k, f, a[k][f], b[k][f])
    for k in ("legs", "loaded", "empty", "unknown"):
        assert sample["counts"][k] == d["counts"][k], k


def test_js_depot_basis_and_raised_assumption_are_reported(calc):
    files = [{"name": n, "text": (EX / n).read_text(encoding="utf-8")} for n in ("haisha_2026-10.csv", "digitaco_1234.csv", "digitaco_5678.csv")]
    d = calc.js(f"OwnCalc.analyze({J(files)}, {J({'munis': [{'code': '46203', 'name': '鹿屋市', 'pref': '鹿児島県', 'lon': 130.867, 'lat': 31.427}, {'code': '13108', 'name': '江東区', 'pref': '東京都', 'lon': 139.817, 'lat': 35.673}]})})")
    b = d["base_basis"]
    assert d["base"] and b["kind"] == "haisha_from" and b["count"] >= 1 and b["total"] >= b["count"]
    # 帰りの向きは、車庫を基準に決める
    for l in d["legs"]:
        if l["to"] == d["base"] and l["frm"] != d["base"]:
            assert l["dir"] == "back", l
    # 大型に 13t の記録がある配車表: 10t の仮定を 13t に上げ、外さない
    csv_text = "車番,車種,10/1(木),10/2(金)\n1001,大型,加古川 → 江東 13t,江東 → 加古川 空\n"
    d2 = calc.js(f"OwnCalc.analyze({J([{'name': 'h.csv', 'text': csv_text}])}, {J({'year': 2026, 'munis': []})})")
    assert d2["max_load_raised"] and d2["max_load_raised"]["to_kg"] == 13000 and d2["max_load_raised"]["from_kg"] == 10000
    assert d2["kpis"]["load_rate"]["over_max"] == 0 and d2["kpis"]["load_rate"]["legs"] == 1
    # 利用者が最大積載量を決めたときは、その値のまま（超える重さは超える重さとして数える）
    d3 = calc.js(f"OwnCalc.analyze({J([{'name': 'h.csv', 'text': csv_text}])}, {J({'year': 2026, 'munis': [], 'largeMaxLoadKg': 10000})})")
    assert d3["max_load_raised"] is None and d3["kpis"]["load_rate"]["over_max"] == 1
