"""自社の運行: 2026-11-08 の不具合探し（docs/reviews/2026-11-08_不具合探し_自社の運行.md）B1〜B7 の回帰試験。

CSV は各試験に書いてある。JS（OwnCalc）はヘッドレス Edge で、Python の twin（src/shipcompare/own.py）は直接呼ぶ。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import edge_page, edge_path_or_skip

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from shipcompare import own  # noqa: E402

PAGE = ROOT / "web" / "own_lite.html"
HEAD = "日付,車番,発地,着地,重量,実空"
PLATES = ["相模 100 あ 1111", "相模 100 あ 2222"]


def J(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def b1_csv() -> str:
    """10 月の日曜以外、車 2 台が毎日 厚木市 → 江東区 → 厚木市 を同じ日に走る。帰りが空なのは月・水・金だけ。"""
    rows = [HEAD]
    for d in range(1, 32):
        wd = date(2026, 10, d).weekday()
        if wd == 6:
            continue
        for p in PLATES:
            ds = f"2026/10/{d:02d}"
            rows.append(f"{ds},{p},厚木市,江東区,3.5t,実")
            rows.append(f"{ds},{p},江東区,厚木市,,空" if wd in (0, 2, 4) else f"{ds},{p},江東区,厚木市,2.0t,実")
    return "\n".join(rows) + "\n"


# B2・B3: 辞書にない地名（距離が分からない）。10/1〜10/6 の 6 日分、計 12 行
B2_CSV = HEAD + "\n" + "".join(
    f"2026/10/0{d},1111,本社車庫,第二センター,3t,実\n2026/10/0{d},1111,第二センター,本社車庫,,空\n" for d in range(1, 7)
)

# B4: 見出しのない CSV（6 行）
B4_CSV = """2026/10/01,1111,厚木市,江東区,3t,実
2026/10/01,1111,江東区,厚木市,,空
2026/10/02,2222,厚木市,大田区,2t,実
2026/10/02,2222,大田区,厚木市,,空
2026/10/05,1111,厚木市,江東区,3t,実
2026/10/05,1111,江東区,厚木市,,空
"""

# B5: 見出しは辞書にない（日付だけ辞書にある）。車番は 4 桁の番号、荷姿・フラグは配車表の役割に当たらない
B5_CSV = """年月日,号機,出発場所X,到着場所X,荷姿,フラグ
2026/10/01,1111,厚木市,江東区,パレット,1
2026/10/01,1111,江東区,厚木市,パレット,0
2026/10/02,2222,厚木市,大田区,パレット,1
"""


@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
        assert page.js("typeof OwnCalc === 'object'")
        yield page


def an(page, text: str, opts: dict | None = None, name: str = "a.csv") -> dict:
    return page.js(f"OwnCalc.analyze([{{ name: {J(name)}, text: {J(text)} }}], {J(opts or {})})")


def _set_files(page, paths):
    root = page.send("DOM.getDocument", depth=1)["root"]["nodeId"]
    node = page.send("DOM.querySelector", nodeId=root, selector="#file-in")["nodeId"]
    page.send("DOM.setFileInputFiles", nodeId=node, files=[str(Path(p).resolve()) for p in paths])


def _open_lite(page):
    page.goto(PAGE.as_uri())
    page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")


# ---------------------------------------------------------------- B1: 同じ日に行って帰る便の曜日

def test_b1_same_day_round_trips_count_on_their_own_weekday(calc):
    d = an(calc, b1_csv())
    assert d["failed"] is None and d["counts"]["legs"] == 108
    back = [l for l in d["legs"] if l["dir"] == "back"]
    assert all(l["cycle_wd"] == date.fromisoformat(l["date"]).weekday() for l in back)   # 前の日の行きの曜日にしない
    r1 = [i for i in d["insights"] if i["rule"] == 1]
    assert {i["next"]["payload"]["wd"] for i in r1} == {0, 2, 4}                          # 月・水・金。火・木・土は出ない
    joined = "".join(i["text"] for i in r1)
    assert "火曜" not in joined and "木曜" not in joined and "土曜" not in joined
    assert all("すべて空" in i["text"] for i in r1)
    # 表（帰りの行）も同じ曜日
    tb = {t["wd"]: (t["trips"], t["empty"]) for t in d["table"] if t["dir"] == "back"}
    assert tb[0] == (8, 8) and tb[2] == (8, 8) and tb[4] == (10, 10) and tb[1][1] == tb[3][1] == tb[5][1] == 0
    assert [(t["wd"], t["empty"]) for t in d["pair_table"] if t["role"] == "back" and t["empty"]] == [(0, 8), (2, 8), (4, 10)]


def test_b1_python_twin_weekday_table_uses_cycle_wd_of_the_leg_itself():
    legs = []
    for i, (ds, kind) in enumerate([("2026-10-06", "empty"), ("2026-10-07", "loaded")]):
        wd = date.fromisoformat(ds).weekday()
        legs.append({"id": f"L{i}", "plate": "1", "date": ds, "dir": "back", "cycle_wd": wd, "kind": kind, "km": 10.0, "frm": "p2", "to": "p1", "sources": {}})
    rows = own.weekday_table(legs, [], None)
    assert [(r["wd"], r["trips"], r["empty"]) for r in rows if r["dir"] == "back"] == [(1, 1, 1), (2, 1, 0)]


def test_b1_sample_json_insight_and_return_weekday_agree():
    d = json.loads((ROOT / "web" / "data" / "own_sample.json").read_text(encoding="utf-8"))
    back = [l for l in d["legs"] if l["dir"] == "back"]
    for l in back:
        day = (l["dep"] or "")[:10] or l["date"]
        assert l["cycle_wd"] == date.fromisoformat(day).weekday(), l["id"]                # 帰りの便が出た日
    ins = [i for i in d["insights"] if i["rule"] == 1][0]
    assert "土曜の夜に出た 5 便の帰りがすべて空" in ins["text"] and "木曜" not in ins["text"]
    assert ins["next"]["payload"]["wd"] == 5


def test_b1_own_lite_screen_shows_the_same_weekdays(tmp_path):
    edge_path_or_skip()
    p = tmp_path / "b1.csv"
    p.write_bytes(b1_csv().encode("utf-8"))
    with edge_page(1920, 1080, webgl=False) as page:
        _open_lite(page)
        _set_files(page, [p])
        page.wait(lambda: page.js("!!window.OwnLite.state.data"), 60, "読み込み")
        r = page.js("""(function () {
          const d = window.OwnLite.state.data;
          return { ins: d.insights.filter((i) => i.rule === 1).map((i) => i.text), text: document.body.textContent,
                   back: weekdayRows(d).filter((t) => t.role === 'back').map((t) => [t.wd, t.trips, t.empty]) };
        })()""")
        assert len(r["ins"]) == 3 and all(("月曜" in t or "水曜" in t or "金曜" in t) for t in r["ins"])
        assert not any(w in "".join(r["ins"]) for w in ("火曜", "木曜", "土曜"))
        assert sorted(w for w, _, e in r["back"] if e) == [0, 2, 4]
        assert "。。" not in r["text"]


def test_b1_own_lite_sample_matches_the_map_insight():
    edge_path_or_skip()
    with edge_page(1920, 1080, webgl=False) as page:
        _open_lite(page)
        page.js("document.querySelector('[data-act=sample]') && document.querySelector('[data-act=sample]').click()")
        page.wait(lambda: page.js("!!window.OwnLite.state.data"), 60, "サンプル")
        r = page.js("""(function () {
          const d = window.OwnLite.state.data;
          const sat = weekdayRows(d).filter((t) => t.role === 'back' && t.wd === 5).map((t) => [t.trips, t.empty]);
          return { sat, ins: d.insights.find((i) => i.rule === 1).text };
        })()""")
        assert r["sat"] == [[5, 5]]
        assert "土曜の夜に出た 5 便の帰りがすべて空" in r["ins"]


# ---------------------------------------------------------------- B2・B3: 距離が分からないとき

def test_b2_judged_does_not_depend_on_km(calc):
    d = an(calc, B2_CSV)
    ar = d["kpis"]["actual_rate"]
    assert d["failed"] is None and d["counts"]["unknown"] == 0
    assert (ar["legs"], ar["of"], ar["share"], ar["value"], ar["legs_with_km"]) == (12, 12, 1, None, 0)


def test_b2_python_twin_counts_judged_legs_without_km():
    legs = [{"id": f"L{i}", "plate": "1", "kind": "empty" if i % 2 else "loaded", "km": None, "weight_kg": None} for i in range(12)]
    ar = own.kpis(legs, [])["actual_rate"]
    assert (ar["legs"], ar["of"], ar["value"], ar["legs_with_km"]) == (12, 12, None, 0)
    legs[0]["km"] = 100.0
    ar = own.kpis(legs, [])["actual_rate"]
    assert ar["legs"] == 12 and ar["legs_with_km"] == 1 and ar["value"] == 1.0


def test_b3_unknown_distance_is_not_written_as_zero_km(calc):
    d = an(calc, B2_CSV)
    r2 = [i for i in d["insights"] if i["rule"] == 2][0]
    assert "約 0 km" not in r2["text"] and "距離が分からない" in r2["text"] and "km は出していません" in r2["text"]
    assert {n["k"]: n["v"] for n in r2["numbers"]}["空で走った距離"] == "距離が分からない"
    # 一部だけ分からないときは、分かる便だけを足し、分からない便の数を書く
    t = (HEAD + ",走行km\n2026/10/01,1111,厚木市,江東区,3t,実,60\n2026/10/01,1111,江東区,厚木市,,空,60\n"
         "2026/10/02,1111,本社車庫,第二センター,3t,実,\n2026/10/02,1111,第二センター,本社車庫,,空,\n")
    r = [i for i in an(calc, t)["insights"] if i["rule"] == 2][0]["text"]
    assert "距離の分かる 1 便" in r and "距離が分からない 1 便は km に入れていません" in r


def test_b3_python_twin_insight_says_distance_unknown():
    legs = [{"id": f"L{i}", "plate": "1", "date": f"2026-10-0{i + 1}", "dir": "back", "cycle_wd": i, "cycle_dep": None, "kind": "empty", "km": None, "frm": "p2", "to": "p1",
             "hid": None, "did": None, "weight_kg": None, "sources": {}} for i in range(3)]
    data = {"legs": legs, "stops": [], "shifts": [], "vehicles": [], "places": {"p1": {"short": "本社"}, "p2": {"short": "先"}}, "base": "p1", "dock": "p2", "month": "2026-10"}
    ins = [i for i in own.insights(data) if i["rule"] == 2][0]
    assert "約 0 km" not in ins["text"] and "km は出していません" in ins["text"]


def test_b2_b3_own_lite_screen_text(tmp_path):
    edge_path_or_skip()
    p = tmp_path / "b2.csv"
    p.write_bytes(B2_CSV.encode("utf-8"))
    with edge_page(1920, 1080, webgl=False) as page:
        _open_lite(page)
        _set_files(page, [p])
        page.wait(lambda: page.js("!!window.OwnLite.state.data"), 60, "読み込み")
        text = page.js("document.body.textContent")
        assert "約 0 km" not in text and "0.0%（0 / 12）" not in text
        assert "距離が分からないので実車率は出せません" in text
        assert "本社車庫" in text and "本社車 " not in text.replace("本社車庫", "")


# ---------------------------------------------------------------- B4: 見出しのない CSV

def test_b4_headerless_csv_is_read_after_choosing_roles(calc):
    d = an(calc, B4_CSV, name="h.csv")
    assert d["failed"] and [n["column"] for n in d["column_roles_needed"]] == [f"列 {i}" for i in range(1, 7)]
    roles = {"haisha": {"列 1": "date", "列 2": "plate", "列 3": "from", "列 4": "to", "列 5": "weight", "列 6": "mark"}}
    ok = an(calc, B4_CSV, {"column_roles": roles}, name="h.csv")
    assert ok["failed"] is None and ok["counts"]["legs"] == 6 and ok["counts"]["empty"] == 3          # 1 行目も値として読む
    assert any(n["kind"] == "headerless" for n in ok["notices"])
    # 見出しのある CSV は、これまでどおり 1 行目を見出しにする
    ok2 = an(calc, HEAD + "\n" + B4_CSV)
    assert ok2["failed"] is None and ok2["counts"]["legs"] == 6 and not any(n["kind"] == "headerless" for n in ok2["notices"])


def test_b4_own_lite_headerless_after_choosing_six_roles(tmp_path):
    edge_path_or_skip()
    p = tmp_path / "h.csv"
    p.write_bytes(B4_CSV.encode("utf-8"))
    with edge_page(1920, 1080, webgl=False) as page:
        _open_lite(page)
        _set_files(page, [p])
        page.wait(lambda: page.js("document.querySelectorAll('select[data-role-col]').length >= 6"), 60, "列の役割の選択")
        for col, role in [("列 1", "date"), ("列 2", "plate"), ("列 3", "from"), ("列 4", "to"), ("列 5", "weight"), ("列 6", "mark")]:
            page.js(f"""(function () {{
              const s = [...document.querySelectorAll('select[data-role-col]')].find((x) => x.dataset.roleCol === {J(col)});
              if (s) {{ s.value = {J(role)}; s.dispatchEvent(new Event('change', {{ bubbles: true }})); }}
            }})()""")
        page.wait(lambda: page.js("!!window.OwnLite.state.data && !window.OwnLite.state.data.failed"), 60, "読み込み")
        assert page.js("window.OwnLite.state.data.counts.legs") == 6
        assert "区間が読めません" not in page.js("document.body.innerText")


# ---------------------------------------------------------------- B5: 列の中身の候補

def test_b5_suggested_roles_follow_a_vertical_haisha(calc):
    d = an(calc, B5_CSV, name="x.csv")
    sug = {n["column"]: n["suggested_role"] for n in d["column_roles_needed"]}
    assert sug["号機"] == "plate" and sug["出発場所X"] == "from" and sug["到着場所X"] == "to"
    assert sug["荷姿"] is None and sug["フラグ"] is None                                   # 配車表の役割に当たらない列には候補を出さない
    h = {n["column"]: n["suggested_role"] for n in an(calc, B4_CSV, name="h.csv")["column_roles_needed"]}
    assert h == {"列 1": "date", "列 2": "plate", "列 3": "from", "列 4": "to", "列 5": "weight", "列 6": "mark"}


# ---------------------------------------------------------------- B6: 本社車庫

def test_b6_depot_name_is_not_cut(calc):
    d = an(calc, B2_CSV)
    assert sorted(p["short"] for p in d["places"].values()) == ["本社車庫", "第二センター"]
    assert any(p["label"] == "第二センター → 本社車庫" for p in d["pairs"])
    # 施設名の語尾を外す従来の動きは変えない
    t = HEAD + "\n2026/10/01,1111,辰巳倉庫,鹿屋,3t,実\n2026/10/02,1111,鹿屋,辰巳DC3,3t,実\n"
    assert [x["short"] for x in an(calc, t)["merged_places"]] == ["辰巳"]


# ---------------------------------------------------------------- B7: 見本の CSV

def _sample_rows() -> list[str]:
    src = (ROOT / "web" / "own.js").read_text(encoding="utf-8")
    seg = src[src.index("const SAMPLE_CSV = ["):src.index("function saveSampleCsv")]
    return re.findall(r"'(20[^']*|日付[^']*)'", seg)


def test_b7_sample_csv_has_only_placeable_names():
    rows = _sample_rows()
    assert len(rows) == 7 and not any("辰巳" in r for r in rows)


def test_b7_sample_csv_reads_without_unplaced_places_in_own_lite(tmp_path):
    edge_path_or_skip()
    p = tmp_path / "見本_配車表.csv"
    p.write_bytes(("﻿" + "\r\n".join(_sample_rows()) + "\r\n").encode("utf-8"))
    with edge_page(1920, 1080, webgl=False) as page:
        _open_lite(page)
        _set_files(page, [p])
        page.wait(lambda: page.js("!!window.OwnLite.state.data"), 60, "読み込み")
        r = page.js("(function () { const d = window.OwnLite.state.data; return { legs: d.counts.legs, unplaced: d.counts.unplaced_legs, un: d.unplaced.length }; })()")
        assert r["legs"] == 6 and r["unplaced"] == 0 and r["un"] == 0
