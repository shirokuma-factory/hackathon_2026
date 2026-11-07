"""自社の運行 r7a・r9a・r4a・r4b の採点で出た指摘の直しの試験（SPEC §49）。

日ごとの集計のデジタコ（拘束時間の列）、同じ内容は 2 つの画面で同じ距離、荷主の種類の名前、経路の並び、
公休の行と空欄の表示、弱い空車の印の確認、画面の見え方。Python（src/shipcompare/own.py）と JS（web/own_calc.js）を同じ入力で比べる。
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


def munis() -> list[dict]:
    ix = json.loads((ROOT / "web" / "data" / "index.json").read_text(encoding="utf-8"))
    pt = json.loads((ROOT / "web" / "data" / "muni.json").read_text(encoding="utf-8")).get("pt") or []
    return [{"code": c, "name": ix["names"][i], "pref": ix["prefs"][i], "lon": (pt[i] if i < len(pt) and pt[i] else [ix["lon"][i], ix["lat"][i]])[0],
             "lat": (pt[i] if i < len(pt) and pt[i] else [ix["lon"][i], ix["lat"][i]])[1]} for i, c in enumerate(ix["codes"])]


def files_of(*names):
    return [{"name": n, "text": (EX / n).read_text(encoding="utf-8")} for n in names]


@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        try:
            page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
            assert page.js("typeof OwnCalc === 'object'")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "own_calc.js を読み込めません", e, "own_v49_fail")
        yield page


SUM = "digitaco_daily_summary.csv"


def an(calc, files, extra=None):
    opts = {"munis": munis(), **(extra or {})}
    return calc.js(f"OwnCalc.analyze({J(files)}, {J(opts)})")


@pytest.mark.parametrize("text,hours", [("13:10", 13.1667), ("34:10", 34.1667), ("13時間10分", 13.1667), ("13.5時間", 13.5), ("13.5", 13.5), ("790", 13.1667), ("790分", 13.1667), ("abc", None)])
def test_restraint_hours_python_and_js_agree(calc, text, hours):
    assert own.parse_restraint_hours(text) == hours
    assert calc.js(f"OwnCalc.parseRestraintHours({J(text)})") == hours


def test_daily_summary_uses_km_sums_and_restraint_column(calc):
    f = files_of(SUM)
    d = an(calc, f)
    k = d["kpis"]["actual_rate"]
    assert d["failed"] is None and k["basis"] == "距離の合計" and "距離の合計" in k["formula"]
    # 実車率は距離の合計どうしの割り算
    rows = [r.split(",") for r in f[0]["text"].strip().splitlines()[1:]]
    loaded, empty = sum(float(r[6]) for r in rows), sum(float(r[7]) for r in rows)
    assert k["value"] == pytest.approx(loaded / (loaded + empty)) and k["km_loaded"] == pytest.approx(loaded, abs=0.1)
    # 13 時間・15 時間の確認は拘束時間の列から（明細が無くても出る）
    hrs = sorted(s["hours"] for s in d["shifts"])
    assert len(hrs) == len(rows) and any(h > 15 for h in hrs) and any(h > 13 for h in hrs)
    assert any("13 時間" in i["text"] for i in d["insights"])
    # 拘束時間の列は、出庫〜帰庫の幅より優先する（帰庫の列を変えても拘束時間は同じ）
    text = f[0]["text"].replace("2026/10/05 21:10", "2026/10/05 20:00", 1)
    d2 = an(calc, [{"name": SUM, "text": text}])
    h0 = [s["hours"] for s in d2["shifts"] if s["start"] == "2026-10-05T08:00"]
    assert h0 == [13.1667]


def test_daily_summary_python_parse_matches_js_counts(calc):
    import csv
    lines = (EX / SUM).read_text(encoding="utf-8").splitlines()
    head = next(csv.reader([lines[0]]))
    rows = [dict(zip(head, r)) for r in csv.reader(lines[1:])]
    pr = own.parse_digitaco_summary(rows)
    assert pr["errors"] == [] and len(pr["restraints"]) == len(rows) and len(pr["runs"]) == 2 * len(rows)
    d = an(calc, files_of(SUM))
    assert d["counts"]["legs"] == len(pr["runs"]) and d["counts"]["shifts"] == len(pr["restraints"])


def test_same_place_pair_gives_same_distance_in_any_order(calc):
    names = ("haisha_2026-10.csv", "digitaco_1234.csv", "digitaco_5678.csv")
    a = an(calc, files_of(*names))
    b = an(calc, files_of(*reversed(names)))
    renamed = [{"name": f"x{i}.csv", "text": f["text"]} for i, f in enumerate(files_of(*names))]
    c = an(calc, [renamed[0], {"name": "digitaco_1234.csv", "text": renamed[1]["text"]}, {"name": "digitaco_5678.csv", "text": renamed[2]["text"]}])
    for d in (b, c):
        for key in ("value", "km_loaded", "km_empty"):
            assert d["kpis"]["actual_rate"][key] == a["kpis"]["actual_rate"][key], key
    assert a["kpis"]["actual_rate"]["km_loaded"] > 0
    # 鹿屋 - 江東の 2 地点には OSRM の値（1381.7）
    ks = {l["km"] for l in a["legs"] if l.get("sources", {}).get("km") == "OSRM 推定" or l.get("km_src") == "OSRM 推定"}
    assert ks <= {1381.7}


def test_route_picker_orders_by_empty_km(calc):
    def leg(i, fr, to, km, kind):
        return dict(id=f"L{i}", plate="1111", date=f"2026-10-{i + 1:02d}", dir="back", cycle_wd=i % 7, cycle_dep=None, kind=kind, km=km, frm=fr, to=to, hid=f"H{i}", did=None, weight_kg=None, sources={})
    legs = [leg(0, "pA", "base", 100.0, "empty"), leg(1, "pA", "base", 100.0, "empty"), leg(2, "pA", "base", 100.0, "empty"), leg(3, "pB", "base", 1300.0, "empty")]
    places = {k: {"id": k, "short": k, "pseudo": k} for k in ("pA", "pB", "base")}
    ps = calc.js(f"OwnCalc.returnPairs({J(legs)}, {J(places)}, 'base', 'pA').map((p) => p.fr)")
    assert ps[0] == "pB"
    pp = own.return_pairs(legs, places, "base", "pA")
    assert pp[0]["fr"] == "pB"


def test_koukyuu_row_is_not_a_leg_and_blank_is_not_called_empty(calc):
    csv = "日付,車番,積地,卸地,区分" + chr(10) + "2026/10/1,2222,,,公休" + chr(10) + "2026/10/2,2222,加古川,江東," + chr(10)
    d = an(calc, [{"name": "haisha_x.csv", "text": csv}])
    assert d["counts"]["legs"] == 1 and "(空)" not in json.dumps(d["unreadable"], ensure_ascii=False)


def test_weak_empty_mark_goes_to_confirm(calc):
    for cell in ("江東→加古川 帰り", "帰り"):
        assert calc.js(f"OwnCalc.parseCell({J(cell)})") == as_json(own.parse_cell(cell))
        assert own.parse_cell(cell)["legs"][0]["weak_empty"] is True
    legs = [dict(plate="1", frm="a", to="b", empty_mark=True, weak_empty=True, has_ends=True, hid="H1", weight_kg=None)]
    r = own.classify_legs(legs, "b")
    assert r["legs"][0]["kind"] == "unknown" and r["legs"][0]["return_unmarked"] is True
    strong = own.parse_cell("江東→加古川 回送")["legs"][0]
    assert "weak_empty" not in strong


def test_negotiate_type_names_follow_the_route(calc, local_server):
    calc.goto(f"http://localhost:{local_server}/data/meta.json")
    calc.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/negotiate.js'; s.onload = ok; s.onerror = ng; document.head.appendChild(s); })")
    r = calc.js("(() => { const a = { pair: { fr_label: '名古屋', to_label: '春日部' } }; return [Negotiate.labelOf('commodity', Negotiate.TYPES.commodity, a), Negotiate.hintOf('returnable', Negotiate.TYPES.returnable, a), Negotiate.hintOf('commodity', Negotiate.TYPES.commodity, a)]; })()")
    assert r[0] == "春日部方面へ商品を出す荷主" and "名古屋側から春日部側へ" in r[1] and "関西" not in "".join(r)


def test_guided_own_works_without_webgl(local_server):
    """WebGL が使えなくても、自社の運行（案内）は静止画の地図の上で手順 1 を始められる。"""
    no_gl = "(function () { const g = HTMLCanvasElement.prototype.getContext; HTMLCanvasElement.prototype.getContext = function (t) { return /webgl/i.test(t) ? null : g.apply(this, arguments); }; })();"
    with edge_page(1280, 800, webgl=False, hooks=(no_gl,)) as page:
        page.goto(f"http://localhost:{local_server}/index.html#v=own&g=empty")
        try:
            page.wait(lambda: page.js("!!document.querySelector('#own-nomap') && !!document.querySelector('#own-right #neg-entry-sample, #own-right [data-act=entry-sample]')"), 60, "地図なしの画面")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図なしの自社の運行が出ません", e, "own_v49_nomap")
        assert page.js("getComputedStyle(document.querySelector('#own-nomap')).display") == "block"
        page.js("(document.querySelector('#own-right #neg-entry-sample') || document.querySelector('[data-act=entry-sample]')).click()")
        page.wait(lambda: page.js("document.body.dataset.step || document.querySelector('#own-right').dataset.step") in ("2", 2), 30, "手順 2")
        assert page.js("!!document.querySelector('#own-right .neg-bar')")
