"""SPEC §42 自社の運行（own）の読み方の直し（実務の効き目 r2・道具の採点 r4 の指摘）。

採点者が作った配車表（題名の行・曜日の行・13 台・傭車・「空」「帰り空」・「8時半ごろ着」・「2回転」・「加古川市内 ルート配送」）と、
メーカーのソフトの形の日ごとの集計（見出しに単位がつく）を、この中で作り直して、Python（src/shipcompare/own.py）と JS（web/own_calc.js）の両方に通す。
Edge が無い環境だけ、JS の試験を飛ばす（起動の時間切れは失敗）。
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import random
import sys
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import own  # noqa: E402

EX = ROOT / "examples" / "own"


def as_json(x):
    return json.loads(json.dumps(x, ensure_ascii=False))


def J(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def rows_of_text(t: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(t), strict=False))


# ---------------------------------------------------------------- 採点者の配車表と日報（実務の効き目 r2a の自作ファイルの作り直し。架空）

YOBI = "月火水木金土日"
CARS = [("1", "姫路 100 か 1201", "大型", "田中"), ("2", "姫路 100 か 1202", "大型", "佐藤"), ("3", "姫路 100 か 1203", "大型", "鈴木"),
        ("4", "姫路 100 か 1204", "大型", "高橋"), ("5", "姫路 100 か 1205", "大型", "伊藤"), ("6", "姫路 400 さ 2206", "4t", "渡辺"),
        ("7", "姫路 400 さ 2207", "4t", "山本"), ("8", "姫路 400 さ 2208", "4t", "中村"), ("9", "姫路 400 さ 2209", "4ｔ", "小林"),
        ("10", "姫路 400 さ 2210", "4t", "加藤"), ("11", "姫路 100 か 1211", "大型", "吉田"), ("12", "姫路 100 か 1212", "大型", "山田"),
        ("傭", "傭車(丸山運送)", "大型", "")]
PAT_BIG = ["加古川→大井ふ頭 ﾄｳﾜ電機 5:30着 9.2t", "大井→加古川 空", "高砂→名古屋市港区 中部鋼材 6時着 13t",
           "名古屋→加古川 帰り空車", "加古川→江東区 ﾄｳﾜ 着5時半 8.8t", "江東区→加古川（空）", "南港→加古川 鋼材 6ｔ",
           "加古川→横浜市鶴見区 8時半ごろ着", "横浜→加古川 空", "休", "車検", "加古川→福岡市東区 ﾌｪﾘｰ", "福岡→加古川 空"]
PAT_4T = ["加古川→尼崎市 3.5t / 尼崎市→加古川 空", "姫路市→神戸市中央区 2回転", "加古川→大阪市住之江区 ﾔﾏﾄ持込",
          "明石市→加古川 引取 1.2t", "休", "加古川→京都市南区 9:00着 3.8t / 京都→加古川 空", "加古川市内 ルート配送"]


def grader_haisha() -> str:
    days = [dt.date(2026, 10, d) for d in range(1, 32)]
    rows = [["令和8年10月度　配車表（東播運輸・本社営業所）"] + [""] * 40, [""] * 41]
    rows.append(["No", "車番", "運転手", "車格"] + [f"{d.month}/{d.day}" for d in days] + ["稼働", "備考"])
    rows.append(["", "", "", ""] + [YOBI[d.weekday()] for d in days] + ["", ""])
    for no, ban, kaku, drv in CARS:
        cells = []
        for i, d in enumerate(days):
            if d.weekday() == 6:
                cells.append("休")
                continue
            p = PAT_BIG if kaku == "大型" else PAT_4T
            cells.append(p[(i + int(ban[-1]) if ban[-1].isdigit() else i) % len(p)])
        rows.append([no, ban, drv, kaku] + cells + [str(sum(c not in ("休", "車検") for c in cells)), ""])
        if no == "3":
            rows.append(["", "", "", ""] + ["荷主:中部鋼材 はｱﾙﾐ製ﾊﾟﾚｯﾄ"] + [""] * 32 + ["", ""])
    rows.append(["合計", "", "", ""] + [""] * 31 + ["", ""])
    return "\r\n".join(",".join('"' + c + '"' if "," in c else c for c in r) for r in rows) + "\r\n"


def grader_digitaco() -> str:
    random.seed(7)
    days = [dt.date(2026, 10, d) for d in range(1, 32)]
    dl = ["運行日報一覧,,,,,,,,,", "出力日:2026/11/02,,,,,,,,,",
          "運行日,車輌番号,乗務員名,出庫日時,帰庫日時,総走行距離(km),実車距離(km),空車距離(km),拘束時間,休憩時間"]
    for d in days:
        if d.weekday() == 6:
            continue
        for ban in ["1201", "1202", "1203"]:
            tot = random.choice([1110.2, 1102.5, 680.3, 645.0, 1290.8])
            jk = round(tot * random.uniform(0.48, 0.58), 1)
            dl.append(f"{d:%Y/%m/%d},姫路100か{ban},架空,{d:%Y/%m/%d} 19:{random.randint(0, 59):02d},{(d + dt.timedelta(days=1)):%Y/%m/%d} 06:{random.randint(0, 59):02d},{tot},{jk},{round(tot - jk, 1)},34:10,3:00")
    return "\r\n".join(dl) + "\r\n"


G_HAISHA = "東播運輸_配車表_2026年10月.csv"
G_DIGI = "運行日報_202610.csv"

MUNIS = [
    {"code": "28210", "name": "加古川市", "pref": "兵庫県", "lon": 134.841, "lat": 34.757}, {"code": "28110", "name": "神戸市中央区", "pref": "兵庫県", "lon": 135.192, "lat": 34.701},
    {"code": "28201", "name": "姫路市", "pref": "兵庫県", "lon": 134.690, "lat": 34.816}, {"code": "13108", "name": "江東区", "pref": "東京都", "lon": 139.817, "lat": 35.673},
    {"code": "23111", "name": "名古屋市港区", "pref": "愛知県", "lon": 136.880, "lat": 35.100}, {"code": "23101", "name": "名古屋市中区", "pref": "愛知県", "lon": 136.900, "lat": 35.170},
    {"code": "28202", "name": "尼崎市", "pref": "兵庫県", "lon": 135.407, "lat": 34.733},
]

# ---------------------------------------------------------------- Python: セルの読み取り

EMPTY_MARKS = ["空", "帰り空", "回送", "空荷", "カラ", "空車", "(空)", "（空）", "帰り空車"]


@pytest.mark.parametrize("mark", EMPTY_MARKS)
def test_every_empty_mark_makes_an_empty_leg_in_the_horizontal_cell(mark):
    r = own.parse_cell(f"江東→加古川 {mark}")
    assert [(l["frm"], l["to"], l["empty"]) for l in r["legs"]] == [("江東", "加古川", True)] and r["errors"] == []


def test_empty_mark_words_are_not_read_inside_other_words_and_a_bare_mark_is_an_empty_leg_without_ends():
    r = own.parse_cell("空港前→加古川 4t")
    assert r["legs"][0]["frm"] == "空港前" and r["legs"][0]["empty"] is False
    for t in ("空", "帰り空", "回送", "（空）"):
        lone = own.parse_cell(t)["legs"]
        assert lone == [{"frm": None, "to": None, "empty": True, "arr": None, "weight_kg": None}], t


def test_vertical_form_and_horizontal_form_share_the_same_empty_marks():
    for mark in ("帰り空", "回送", "空荷", "カラ", "空"):
        rows = [["日付", "車番", "発地", "着地", "区分"], ["2026/10/6", "1234", "江東", "加古川", mark]]
        legs = own.read_haisha(rows, 2026)["rows"][0]["parsed"]["legs"]
        assert legs[0]["empty"] is True, mark


def test_approximate_time_drops_only_the_time_and_keeps_the_leg():
    for t in ("加古川→横浜市鶴見区 8時半ごろ着", "加古川→横浜市鶴見区 着8時頃", "加古川→横浜市鶴見区 9時ころ着 5t"):
        r = own.parse_cell(t)
        assert r["errors"] == [] and r["warnings"] == [own.TIME_DROPPED] and r["legs"][0]["arr"] is None and r["legs"][0]["to"] == "横浜市鶴見区", t
    assert own.parse_cell("加古川→横浜市鶴見区 9時ころ着 5t")["legs"][0]["weight_kg"] == 5000.0
    rows = [["日付", "車番", "発地", "着地", "時刻"], ["2026/10/6", "1234", "江東", "加古川", "8時半ごろ"]]
    row = own.read_haisha(rows, 2026)["rows"][0]["parsed"]
    assert row["errors"] == [] and row["warnings"] == [own.TIME_DROPPED] and row["legs"][0]["arr"] is None


def test_local_delivery_is_a_loaded_leg_and_rounds_are_counted():
    r = own.parse_cell("加古川市内 ルート配送")
    assert r["errors"] == [] and r["legs"] == [{"frm": "加古川市", "to": "加古川市", "empty": False, "arr": None, "weight_kg": None, "local": True}]
    r2 = own.parse_cell("姫路市→神戸市中央区 2回転")
    assert r2["legs"][0]["rounds"] == 2 and r2["legs"][0]["to"] == "神戸市中央区" and r2["errors"] == []
    assert own.parse_cell("加古川→江東 5:30着 12回")["legs"][0].get("rounds") is None  # 12 回の 2 は回数ではない
    assert own.parse_cell("加古川→江東 着5:30 2.5t")["legs"][0].get("rounds") is None
    assert own.parse_cell("地場 ルート配送 3回転")["legs"][0]["rounds"] == 3


def test_hired_vehicle_rows_are_counted_apart_and_not_read_as_a_plate():
    r = own.read_haisha(rows_of_text(grader_haisha()), 2026)
    assert [h["name"] for h in r["hired"]] == ["傭車(丸山運送)"] and r["hired"][0]["cells"] == 24 and r["hired"][0]["legs"] == 24
    assert not [u for u in r["unreadable"] if "車番が読めません" in u["reason"]] and "傭車(丸山運送)" not in [p["raw"] for p in r["plates"]]
    assert [p["plate"] for p in r["plates"]][:3] == ["1201", "1202", "1203"] and len(r["plates"]) == 12


# ---------------------------------------------------------------- Python: 車番（車格・運転者が違う）

def test_same_last4_with_different_class_or_driver_is_kept_apart_and_reported():
    rows = [["車番", "車種", "運転手", "10/1(木)", "10/2(金)"], ["春日部130え4521", "4t", "高橋", "春日部→大宮 1t", ""], ["4521", "大型", "中村", "", "大宮→春日部 3t"]]
    r = own.read_haisha(rows, 2026)
    assert [p["plate"] for p in r["plates"]] == ["春日部130え4521", "4521"] and [p["driver"] for p in r["plates"]] == ["高橋", "中村"]
    assert r["plate_notes"] == [{"kind": "attr", "num": "4521", "forms": ["春日部130え4521（4t・高橋）", "4521（大型・中村）"]}]
    # 車格も運転者も同じなら、これまでどおり同じ車（片方が 4 桁だけ）
    same = [["車番", "車種", "運転手", "10/1(木)", "10/2(金)"], ["春日部130え4521", "4t", "高橋", "A→B", ""], ["4521", "4t", "高橋", "", "B→A"]]
    r2 = own.read_haisha(same, 2026)
    assert [p["plate"] for p in r2["plates"]] == ["4521"] and [n["kind"] for n in r2["plate_notes"]] == ["merge"]
    # 同じ書き方 4 桁だけで、車格が違うなら、車格を添えて別の車にする
    bare = [["車番", "車種", "10/1(木)", "10/2(金)"], ["4521", "4t", "A→B", ""], ["4521", "大型", "", "B→A"]]
    assert [p["plate"] for p in own.read_haisha(bare, 2026)["plates"]] == ["4521（4t）", "4521（大型）"]
    assert own.class_of("4ｔ") == "4t" and own.class_of("10トン") == "大型" and own.class_of("") == "不明" and own.class_of("2.5t") == "2.5t"


# ---------------------------------------------------------------- Python: デジタコの見出し

@pytest.mark.parametrize("head,role", [("実車距離(km)", "loaded_km"), ("空車距離（km）", "empty_km"), ("実車距離 (KM)", "loaded_km"), ("空車距離[km]", "empty_km"), ("車輌番号", "plate"),
                                       ("車両番号", "plate"), ("乗務員名", "driver_code"), ("出庫日時", "start_time"), ("帰庫日時", "end_time"), ("総走行距離(km)", None), ("拘束時間", None)])
def test_digitaco_summary_headers_with_units_and_variants(head, role):
    assert own.role_of(head) == role


def test_digitaco_daily_summary_of_the_graders_file_reads_without_a_file_name_plate():
    rows = rows_of_text(grader_digitaco())
    det = own.detect_digitaco(rows)
    assert det["kind"] == "summary" and det["header_row"] == 2
    head = rows[2]
    recs = [dict(zip(head, r)) for r in rows[3:] if any(r)]
    res = own.parse_digitaco_summary(recs, None, None, 2)
    assert res["errors"] == [] and {r["plate"] for r in res["runs"]} == {"1201", "1202", "1203"}
    first = res["runs"][0]
    assert first["start"].endswith("T19:00") or first["start"][11:13] == "19"  # 出庫日時・帰庫日時（日付つき）を読む
    assert first["end"][:10] == "2026-10-02" and first["driver"] == "架空" and first["time_unknown"] is False
    assert sum(r["km"] for r in res["runs"] if r["jk"] == 1) > 0


def test_digitaco_detail_with_a_plate_column_needs_no_plate_from_the_file_name():
    rows = [{"車番": "1234", "区分": "1", "開始日時": "2026-10-01 00:00", "終了日時": "2026-10-01 05:00", "走行距離": "500", "実空区分": "1", "地点名": ""},
            {"車番": "1234", "区分": "2", "開始日時": "2026-10-01 05:00", "終了日時": "2026-10-01 06:00", "走行距離": "", "実空区分": "", "地点名": "江東"},
            {"車番": "5678", "区分": "1", "開始日時": "2026-10-01 00:00", "終了日時": "2026-10-01 04:00", "走行距離": "300", "実空区分": "0", "地点名": ""}]
    res = own.parse_digitaco(rows, lambda n: "p-" + n, None)
    assert res["errors"] == [] and [r["plate"] for r in res["runs"]] == ["1234", "5678"] and res["stops"][0]["plate"] == "1234" and res["runs"][0]["to"] == "p-江東"
    assert res["runs"][1]["frm"] is None  # 車番が変わったら、前の停車の地点を引き継がない


# ---------------------------------------------------------------- Python: 年

def test_year_uses_hints_first_and_then_the_candidate_nearest_to_2026():
    fri = ["10/1(金)", "10/2(土)"]  # 2021・2027・2032
    yc = own.infer_year(fri)
    assert yc["year"] == 2027 and yc["candidates"] == [2021, 2027, 2032] and yc["year_assumed"] is True and yc["hint"] is None  # 2021 にしない
    assert own.infer_year(["10/1(水)", "10/2(木)"])["year"] == 2025  # 2025・2031・2036 のうち 2026 に近い 2025
    assert own.infer_year(["10/1(木)", "10/2(金)"])["year"] == 2026  # 2020・2026
    hint = own.infer_year(fri, [{"year": 2032, "source": "file_name"}, {"year": 2027, "source": "saved"}])
    assert hint["year"] == 2032 and hint["year_assumed"] is False and hint["hint"] == "file_name" and hint["candidates"] == [2021, 2027, 2032]
    assert own.infer_year(fri, [{"year": 2030, "source": "title"}])["year"] == 2027  # 候補に無い手掛かりは使わない
    assert own.nearest_year([2020, 2032]) == 2020 and own.nearest_year([2025, 2027]) == 2025
    assert own.years_in_text("東播運輸_配車表_2026年10月.csv") == [2026] and own.years_in_text("運行日報_202610.csv") == [2026]
    assert own.years_in_text("令和8年10月度 配車表") == [2026] and own.years_in_text("1234.csv") == []


# ---------------------------------------------------------------- Python: 区間の組・最大積載量・不明

def _lg(i, plate, frm, to, kind, km=100.0, date="2026-10-05", wd=None):
    return {"id": f"L{i:03d}", "plate": plate, "date": date, "frm": frm, "to": to, "kind": kind, "km": km, "dir": None, "cycle_wd": wd if wd is not None else 0}


PLACES = {"p1": {"short": "加古川"}, "p2": {"short": "名古屋"}, "p3": {"short": "大井"}, "p4": {"short": "神戸市中央"}}


def test_pair_tables_count_by_the_actual_origin_and_destination():
    legs = [_lg(1, "9001", "p2", "p1", "empty", 400.0), _lg(2, "9001", "p2", "p1", "empty", 400.0, "2026-10-12"), _lg(3, "5678", "p3", "p1", "empty", 600.0, "2026-10-06"),
            _lg(4, "5678", "p4", "p1", "loaded", 40.0, "2026-10-06"), _lg(5, "9001", "p1", "p2", "loaded", 400.0, "2026-10-04"), _lg(6, "9001", "p1", "p1", "loaded", None, "2026-10-04")]
    vs = [{"plate": "9001", "cls": "大型"}, {"plate": "5678", "type_raw": "4t"}]
    out = own.pair_tables(legs, PLACES, vs, "p1", "p4")
    labels = [(p["label"], p["trips"], p["empty"], p["empty_km"], p["role"]) for p in out["pairs"]]
    assert labels[:2] == [("名古屋 → 加古川", 2, 2, 800.0, "back"), ("大井 → 加古川", 1, 1, 600.0, "back")]  # 「神戸市中央 → 加古川」に集めない
    assert ("加古川 → 加古川", 1, 0, 0.0, None) not in labels and len(labels) == 4  # 発地と着地が同じ区間（地場）は数えない
    p0 = out["pairs"][0]
    assert p0["classes"] == {"大型": 2} and p0["empty_classes"] == {"大型": 2} and p0["plates"] == ["9001"] and p0["id"] == "P1"
    assert [(w["wd"], w["trips"]) for w in p0["by_weekday"]] == [(0, 2)]  # 10/5 と 10/12 は月曜
    row = [r for r in out["pair_table"] if r["label"] == "大井 → 加古川"][0]
    assert row["weekday"] == "火" and row["classes"] == {"4t": 1} and row["empty_km"] == 600.0 and row["leg_ids"] == ["L003"]


def test_insights_name_the_real_pair_for_rule_1_and_rule_2():
    legs = [dict(_lg(i, f"90{i % 3}", "p2", "p1", "empty", 400.0, f"2026-10-{5 + 7 * i:02d}" if i < 4 else "2026-10-26", wd=2), ref={"file": "f", "where": "w", "text": "t"}) for i in range(5)]
    legs.append(dict(_lg(9, "5678", "p3", "p1", "empty", 600.0, wd=3), ref=None))
    legs[4]["date"] = "2026-10-26"
    ins = own.insights({"legs": legs, "vehicles": [], "places": PLACES, "base": "p1", "dock": "p4", "month": 10, "stops": [], "shifts": []})
    r1 = [i for i in ins if i["rule"] == 1]
    assert r1 and r1[0]["text"].startswith("名古屋 → 加古川の帰りは、加古川を水曜に出た 5 便の帰りがすべて空でした")
    p = r1[0]["next"]["payload"]
    assert p["dir"] == "back" and p["frm"] == "p2" and p["to"] == "p1" and p["label"] == "名古屋 → 加古川"
    r2 = [i for i in ins if i["rule"] == 2][0]
    assert "向きは 名古屋 → 加古川 が 5 便、大井 → 加古川 が 1 便 です。" in r2["text"] and "神戸市中央" not in r2["text"]


def test_a_return_leg_without_a_mark_or_a_weight_is_unknown_and_marks_make_it_known():
    def h(i, frm, to, **kw):
        return {"plate": "9001", "date": "2026-10-0" + str(i), "hid": f"H{i}", "did": None, "frm": frm, "to": to, "empty_mark": False, "has_ends": True, "weight_kg": None,
                "jk": None, "flags": [], "sources": {}, **kw}
    legs = [h(1, "p2", "p1"), h(2, "p2", "p1", empty_mark=True), h(3, "p2", "p1", weight_kg=3000.0), h(4, "p2", "p1", loaded_mark=True), h(5, "p1", "p2"), h(6, "p1", "p1", local=True), h(7, "p2", "p1", jk=1)]
    out = own.classify_legs(legs, "p1")
    assert [l["kind"] for l in out["legs"]] == ["unknown", "empty", "loaded", "loaded", "loaded", "loaded", "loaded"]
    assert out["legs"][0]["kind_src"] == own.RETURN_UNMARKED_SRC and out["legs"][0]["return_unmarked"] is True
    assert [l["kind"] for l in own.classify_legs(legs)["legs"]][0] == "loaded"  # 車庫が分からないときは、これまでどおり


def test_max_payload_by_plate_comes_before_by_class_and_bad_values_are_ignored():
    vs = [{"plate": "1201", "cls": "大型", "max_load_kg": 10000, "max_load_assumed": True}, {"plate": "1202", "cls": "大型", "max_load_kg": 10000, "max_load_assumed": True},
          {"plate": "2206", "cls": "4t", "max_load_kg": None, "max_load_assumed": False}]
    out = own.apply_max_payload(vs, {"1201": 13000}, {"大型": 12000, "4t": 0})
    assert [(v["max_load_kg"], v["max_load_src"]) for v in out[:2]] == [(13000.0, "plate"), (12000.0, "class")] and out[2]["max_load_kg"] is None and "max_load_src" not in out[2]
    assert out[0]["max_load_assumed"] is False and vs[0]["max_load_kg"] == 10000  # 元の車両は変えない
    assert own.apply_max_payload(vs, {"1201": "13t"}, None)[0]["max_load_kg"] == 10000


# ---------------------------------------------------------------- JS（ヘッドレス Edge）と Python の照合

@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        try:
            page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
            assert page.js("typeof OwnCalc === 'object'")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "own_calc.js を読み込めません", e, "own_v42_fail")
        yield page


def _an(page, files, opts=None):
    return page.js(f"OwnCalc.analyze({J(files)}, {J(opts or {})})")


def _hf(text=None, name=G_HAISHA):
    return {"name": name, "text": text if text is not None else grader_haisha()}


def _df(text=None, name=G_DIGI):
    return {"name": name, "text": text if text is not None else grader_digitaco()}


CELLS_42 = (["江東→加古川 " + m for m in EMPTY_MARKS] + ["空", "帰り空", "回送", "（空）", "空港前→加古川 4t", "加古川→横浜市鶴見区 8時半ごろ着", "加古川→横浜市鶴見区 着8時頃",
            "加古川市内 ルート配送", "姫路市→神戸市中央区 2回転", "地場 ルート配送 3回転", "加古川→尼崎市 3.5t / 尼崎市→加古川 空", "名古屋→加古川 帰り空車", "江東→加古川 実車", "江東→加古川（実車）",
            "加古川→江東 着5:30 12回", "高砂→名古屋市港区 中部鋼材 6時着 13t", "休", "車検", "南港→加古川 鋼材 6ｔ"])


def test_js_and_python_read_the_same_new_cells(calc):
    for text in CELLS_42:
        assert calc.js(f"OwnCalc.parseCell({J(text)})") == as_json(own.parse_cell(text)), text


def test_js_and_python_agree_on_digitaco_headers_years_and_plates(calc):
    for h in ("実車距離(km)", "空車距離（km）", "実車距離 (KM)", "空車距離[km]", "車輌番号", "車両番号", "乗務員名", "出庫日時", "帰庫日時", "総走行距離(km)", "拘束時間", "日付", "実車"):
        assert calc.js(f"OwnCalc.roleOf({J(h)})") == own.role_of(h), h
    for head, kind in ((["運行日", "車輌番号", "実車距離(km)", "空車距離（km）"], "summary"), (["区分", "開始日時", "終了日時", "走行距離"], "detail")):
        det = calc.js(f"OwnCalc.detectDigitaco([{J(head)}])")
        assert det and det["kind"] == kind == own.detect_digitaco([head])["kind"]
    for fri in (["10/1(金)", "10/2(土)"], ["10/1(水)", "10/2(木)"], ["10/1(木)", "10/2(金)"]):
        for hints in (None, [{"year": 2032, "source": "file_name"}], [{"year": 2030, "source": "title"}]):
            py = own.infer_year(fri, hints)
            assert calc.js(f"OwnCalc.inferYear({J(fri)}, {J(hints)})") == as_json(py), (fri, hints)
    for t in ("東播運輸_配車表_2026年10月.csv", "運行日報_202610.csv", "令和8年10月度", "1234.csv", "2027-10_配車表 2026"):
        assert calc.js(f"OwnCalc.yearsInText({J(t)})") == own.years_in_text(t), t
    ent = [["春日部130え4521", "4t", "高橋"], ["4521", "大型", "中村"], ["品川100あ1234", "大型", ""], ["練馬100い1234", "大型", ""]]
    assert calc.js(f"OwnCalc.plateAttrConflicts({J(ent)})") == own.plate_attr_conflicts([tuple(e) for e in ent]) == {"4521": ["春日部130え4521（4t・高橋）", "4521（大型・中村）"]}


def test_js_pair_tables_and_max_payload_match_python(calc):
    legs = [_lg(1, "9001", "p2", "p1", "empty", 400.0), _lg(2, "9001", "p2", "p1", "empty", 400.0, "2026-10-12"), _lg(3, "5678", "p3", "p1", "empty", 600.0, "2026-10-06"),
            _lg(4, "5678", "p4", "p1", "loaded", 40.0, "2026-10-06"), _lg(5, "9001", "p1", "p2", "loaded", 400.0, "2026-10-04"), _lg(6, "9001", "p1", "p1", "loaded", None, "2026-10-04")]
    vs = [{"plate": "9001", "cls": "大型"}, {"plate": "5678", "type_raw": "4t"}]
    assert calc.js(f"OwnCalc.pairTables({J(legs)}, {J(PLACES)}, {J(vs)}, 'p1', 'p4')") == as_json(own.pair_tables(legs, PLACES, vs, "p1", "p4"))
    mv = [{"plate": "1201", "cls": "大型", "max_load_kg": 10000, "max_load_assumed": True}, {"plate": "1202", "cls": "大型", "max_load_kg": 10000, "max_load_assumed": True}]
    assert calc.js(f"OwnCalc.applyMaxPayload({J(mv)}, {{'1201': 13000}}, {{'大型': 12000, '4t': 0}})") == as_json(own.apply_max_payload(mv, {"1201": 13000}, {"大型": 12000, "4t": 0}))


# ---------------------------------------------------------------- JS: analyze（採点者の配車表と日報）

@pytest.fixture(scope="module")
def graded(calc):
    return _an(calc, [_hf(), _df()], {"munis": MUNIS})


def test_analyze_graders_files_read_every_cell_and_count_empty_returns_as_empty(graded):
    d = graded
    c = d["counts"]
    assert d["failed"] is None and d["files_unread"] == [] and c["unreadable"] == 0 and c["unknown"] == 0
    assert c["empty"] == 112 and c["loaded"] == 230 and c["legs"] == 342 and c["nonleg_cells"] == 93
    rate = d["kpis"]["actual_rate"]
    assert rate["value"] is None or rate["value"] < 0.92  # 「空」を実車にしていた r2b の 92.0% にはならない
    assert d["year"] == 2026 and d["year_choice"]["hint"] == "title" and d["year_choice"]["year_assumed"] is False  # 題名の「令和8年」
    assert c["time_dropped"] == 15 and c["local_legs"] == 21 and c["hired_cells"] == 24
    td = d["time_dropped"][0]
    assert td["text"] in ("加古川→横浜市鶴見区 8時半ごろ着",) and td["where"]
    assert [n["kind"] for n in d["notices"]] == ["summary", "hired", "time_dropped"]


def test_analyze_graders_empty_returns_appear_under_their_real_origin(graded):
    d = graded
    by = {p["label"]: p for p in d["pairs"]}
    for lb in ("名古屋 → 加古川", "大井 → 加古川", "横浜 → 加古川", "福岡 → 加古川", "江東 → 加古川"):
        assert lb in by and by[lb]["role"] == "back" and by[lb]["empty"] == by[lb]["trips"], lb
    assert "神戸市中央 → 加古川" not in by and not [p for p in d["pairs"] if p["to"] == p["frm"]]
    assert by["名古屋 → 加古川"]["classes"] == {"大型": by["名古屋 → 加古川"]["trips"]} and by["尼崎 → 加古川"]["classes"] == {"4t": by["尼崎 → 加古川"]["trips"]}
    assert sum(p["trips"] for p in d["pairs"]) == len([l for l in d["legs"] if l["frm"] and l["to"] and l["frm"] != l["to"]])
    tbl = [r for r in d["pair_table"] if r["label"] == "名古屋 → 加古川"]
    assert tbl and sum(r["trips"] for r in tbl) == by["名古屋 → 加古川"]["trips"] and all(r["weekday"] in "月火水木金土日" for r in tbl)
    # 名古屋市港区が「名古屋市港」にならない（区を残す）
    assert by["高砂 → 名古屋市港区"]["to_label"] == "名古屋市港区"
    text = " ".join(i["text"] for i in d["insights"])
    assert "名古屋 → 加古川の帰り" in text and "神戸市中央 → 加古川" not in text
    r1 = [i for i in d["insights"] if i["rule"] == 1][0]["next"]["payload"]
    assert {"dir", "wd", "frm", "to", "frm_label", "to_label", "label"} <= set(r1) and r1["to_label"] == "加古川"


def test_analyze_insights_for_rule_1_and_2_match_the_python_function(graded):
    d = graded
    py = own.insights({"legs": d["legs"], "vehicles": [], "places": d["places"], "stops": d["stops"], "shifts": d["shifts"], "base": d["base"], "dock": d["dock"], "month": d["mon"],
                       "own_names": True, "est_label": "推定"})
    js = [i for i in d["insights"] if i["rule"] in (1, 2)]
    assert [(i["rule"], i["text"], i["rule_label_ja"]) for i in js] == [(i["rule"], i["text"].replace("OSRM の推定", "推定"), i["rule_label_ja"]) for i in py if i["rule"] in (1, 2)]


def test_analyze_graders_daily_summary_headers_are_read_with_units_and_the_notice_matches_the_verdict(graded, calc):
    d = graded
    f = [x for x in d["files"] if x["file"] == G_DIGI][0]
    assert f["shape"] == "1 行 = 1 日の集計の形" and f["unreadable"] == 0 and "車番" not in f["verdict"].split("列")[0]
    assert "実車率の計算には入れていません" in f["verdict"] and [n for n in d["notices"] if n["kind"] == "summary"]  # 上の知らせと下の判定が同じことを言う
    assert any(c["column"] == "総走行距離(km)" for c in d["columns"]) or "総走行距離(km)" in f["verdict"]
    # 配車表なしで読むと、日報だけで実車と空車が出る（車番は列から）
    only = _an(calc, [_df()], {})
    assert only["failed"] is None and only["files_unread"] == [] and only["counts"]["legs"] > 0 and {v["plate"] for v in only["vehicles"]} == {"1201", "1202", "1203"}
    assert only["counts"]["empty"] > 0 and only["counts"]["loaded"] > 0


def test_analyze_user_chosen_plate_column_needs_no_file_name_plate_and_reasons_agree(calc):
    t = "いつ,車の番号,実,空\n2026/10/1,姫路100か1201,300,200\n2026/10/2,姫路100か1202,100.5,50\n"
    f = {"name": "日報_自社.csv", "text": t}
    d = _an(calc, [f])
    assert d["failed"] and d["files_unread"] == [{"file": "日報_自社.csv", "reason": "列の役割が決まっていません（画面で各列が何かを選ぶと読めます）"}] or d["files_unread"][0]["reason"] == "配車表にもデジタコにも見えません"
    roles = {"いつ": "date", "車の番号": "plate", "実": "loaded_km", "空": "empty_km"}
    ok = _an(calc, [f], {"column_roles": {"digitaco": roles}})
    assert ok["failed"] is None and ok["files_unread"] == [] and {v["plate"] for v in ok["vehicles"]} == {"1201", "1202"} and ok["counts"]["empty"] == 2
    # 車番の列が無く、ファイル名にも 4 桁が無いと、読めない。上の知らせ・ファイルごとの判定・読めなかった一覧が同じ理由を言う
    t2 = "運行日,実車距離(km),空車距離(km)\n2026/10/1,300,200\n"
    bad = _an(calc, [{"name": "日報.csv", "text": t2}])
    reason = bad["files_unread"][0]["reason"]
    assert reason == "ファイル名から車番が分かりません" and bad["files"][0]["reason"] == reason and bad["unreadable"][0]["reason"].startswith(reason)
    assert "車番の列の役割を「車番」" in bad["unreadable"][0]["reason"]


def test_analyze_same_last4_with_a_different_class_and_driver_raises_a_confirm_card(calc):
    t = "車番,車種,運転手,10/1(木),10/2(金),10/3(土)\n春日部130え4521,4t,高橋,春日部→大宮 1t,大宮→春日部 空,\n4521,大型,中村,,春日部→大宮 3t,大宮→春日部 空\n"
    d = _an(calc, [{"name": "h.csv", "text": t}])
    assert [v["plate"] for v in d["vehicles"]] == ["春日部130え4521", "4521"] and [v["cls"] for v in d["vehicles"]] == ["4t", "大型"]
    cards = [c for c in d["confirm"] if c["kind"] == "plate"]
    assert len(cards) == 1 and cards[0]["key"] == "plate:4521" and "車格か運転者が違う" in cards[0]["proposal"] and "（4t・高橋）" in cards[0]["original"]
    assert d["layouts"]["h.csv"]["plate_notes"][0]["kind"] == "attr"


def test_analyze_year_prefers_file_name_title_digitaco_and_saved_months_then_the_nearest_to_2026(calc):
    fri = "車番,車種,10/1(金),10/2(土),10/3(日)\n1234,大型,加古川→江東,江東→加古川 空,\n"  # 日付の列が 3 つ未満だと、題名の行より下の見出しは横の形と見なされない
    d = _an(calc, [{"name": "h.csv", "text": fri}])
    assert d["year"] == 2027 and d["year_choice"]["year_assumed"] is True and d["year_choice"]["candidates"] == [2021, 2027, 2032]  # 2021 にしない
    d2 = _an(calc, [{"name": "配車表_2032年10月.csv", "text": fri}])
    assert d2["year"] == 2032 and d2["year_choice"]["year_assumed"] is False and d2["year_choice"]["hint"] == "file_name"
    d3 = _an(calc, [{"name": "h.csv", "text": "令和3年10月分\n" + fri}])
    assert d3["year"] == 2021 and d3["year_choice"]["hint"] == "title"
    d4 = _an(calc, [{"name": "h.csv", "text": fri}], {"year_hint": 2032})
    assert d4["year"] == 2032 and d4["year_choice"]["hint"] == "saved" and d4["year_choice"]["year_assumed"] is False
    d5 = _an(calc, [{"name": "h.csv", "text": fri}], {"year": 2021})
    assert d5["year"] == 2021 and d5["year_choice"]["by_user"] is True


def test_analyze_multiple_haisha_files_are_merged_when_they_do_not_overlap(calc):
    a = {"name": "本社_10月.csv", "text": "車番,車種,10/1(木),10/2(金)\n1234,大型,加古川→江東 着5:30 5t,江東→加古川 空\n"}
    b = {"name": "姫路営業所_11月.csv", "text": "車番,車種,11/2(月),11/3(火)\n5678,4t,姫路→神戸 2t,神戸→姫路 空\n"}
    d = _an(calc, [a, b])
    assert d["failed"] is None and d["files_unread"] == [] and d["counts"]["legs"] == 4 and d["months"] == ["2026-10", "2026-11"]
    assert [v["plate"] for v in d["vehicles"]] == ["1234", "5678"] and sorted(d["layouts"]) == ["姫路営業所_11月.csv", "本社_10月.csv"]
    assert [f["file"] for f in d["files"] if f["kind"] == "配車表"] == ["本社_10月.csv", "姫路営業所_11月.csv"]
    assert d["haisha_choice"]["needs_choice"] is False and d["haisha_choice"]["used"] == ["本社_10月.csv", "姫路営業所_11月.csv"] and d["haisha_choice"]["overlaps"] == []
    assert [n["kind"] for n in d["notices"]] == ["haisha_merged"] and [fc["year"] for fc in d["year_choices"]] == [2026, 2026]


def test_analyze_overlapping_haisha_files_are_reported_and_the_ui_can_choose(calc):
    a = {"name": "a.csv", "text": "車番,車種,10/1(木),10/2(金)\n1234,大型,加古川→江東 5t,江東→加古川 空\n"}
    b = {"name": "b.csv", "text": "車番,車種,10/1(木),10/2(金)\n1234,大型,加古川→大阪 5t,大阪→加古川 空\n"}
    same = {"name": "a_copy.csv", "text": a["text"]}
    d = _an(calc, [a, b, same])
    assert d["counts"]["legs"] == 2 and d["haisha_choice"]["needs_choice"] is True and d["haisha_choice"]["used"] == ["a.csv"]
    ov = d["haisha_choice"]["overlaps"]
    assert [o["files"] for o in ov] == [["a.csv", "b.csv"], ["a.csv", "a_copy.csv"], ["b.csv", "a_copy.csv"]] and ov[0]["cells"] == 2 and ov[0]["identical"] is False and ov[1]["identical"] is True
    assert sorted(u["file"] for u in d["files_unread"]) == ["a_copy.csv", "b.csv"] and [x["used"] for x in d["haisha_choice"]["files"]] == [True, False, False]
    assert [x["reason"] for x in d["haisha_choice"]["files"] if not x["used"]] == [d["files_unread"][0]["reason"], d["files_unread"][1]["reason"]]
    assert "選べます" in d["files_unread"][0]["reason"] and "同じ内容なので読んでいません" in d["files_unread"][1]["reason"]
    # 利用者が b を選ぶと、b を読む
    pick = _an(calc, [a, b, same], {"haisha_use": ["b.csv"]})
    assert pick["haisha_choice"]["used"] == ["b.csv"] and pick["haisha_choice"]["needs_choice"] is False and {p["label"] for p in pick["pairs"]} == {"大阪 → 加古川", "加古川 → 大阪"}
    # 重なる 2 つを両方選ぶと、先に読んだほうだけを使い、捨てた件数を報告する
    both = _an(calc, [a, b], {"haisha_use": ["a.csv", "b.csv"]})
    assert both["counts"]["legs"] == 2 and both["counts"]["dropped_overlap"] == 2 and [n["kind"] for n in both["notices"]] == ["haisha_dropped"]  # SPEC §43: 区間を 1 件も出さなかった配車表（重なりで全部捨てた）は「合わせて読みました」に数えない


def test_analyze_reads_every_readable_example_file(calc):
    # r2 の時点の 10 ファイル（r3 で足した書き方の見本 haisha_banshu は tests/test_v43_own_read.py が確かめる）
    files = [{"name": p.name, "text": p.read_text(encoding="utf-8")} for p in sorted(EX.glob("*.csv")) if not p.name.startswith("haisha_banshu")]
    assert len(files) == 10
    d = _an(calc, files, {"munis": MUNIS})
    kinds = {f["file"]: f["kind"] for f in d["files"]}
    assert sorted(f for f, k in kinds.items() if k == "配車表") == ["haisha_2026-10.csv", "haisha_excel_2026-10.csv", "haisha_intra_pref.csv", "haisha_same_last4.csv", "haisha_vertical_2026-10.csv"]
    hc = d["haisha_choice"]
    assert hc["used"] == ["haisha_2026-10.csv", "haisha_same_last4.csv"] and len(hc["files"]) == 5 and hc["needs_choice"] is True
    unread = {u["file"] for u in d["files_unread"]}
    assert unread == {"haisha_excel_2026-10.csv", "haisha_intra_pref.csv", "haisha_vertical_2026-10.csv", "digitaco_unknown_cols_9012.csv"}  # 読めるのに使っていない 3 つは、重なりとして報告される
    assert d["files_read"] == 6 and {p["plates"] == p["plates"] for p in hc["files"]}
    # どれかを選べば、その配車表を読む（選ばなかった配車表は「選ばれていません」）
    one = _an(calc, files, {"munis": MUNIS, "haisha_use": ["haisha_excel_2026-10.csv"]})
    assert one["haisha_choice"]["used"] == ["haisha_excel_2026-10.csv"] and "選ばれていません" in [x["reason"] for x in one["haisha_choice"]["files"] if not x["used"]][0]


def test_analyze_max_payload_by_plate_and_by_class_changes_the_over_max_cards(graded, calc):
    base_cards = [c for c in graded["confirm"] if c["kind"] == "weight"]
    assert len(base_cards) == 16 and all(c["over_max"] and c["cls"] == "大型" and c["cap_kg"] == 10000 and c["cap_assumed"] is True and c["cap_src"] == "assumed" for c in base_cards)
    by_class = _an(calc, [_hf(), _df()], {"munis": MUNIS, "max_payload_by_class": {"大型": 13000}})
    assert [c for c in by_class["confirm"] if c["kind"] == "weight"] == [] and {v["max_load_kg"] for v in by_class["vehicles"] if v["cls"] == "大型"} == {13000.0}
    assert all(v["max_load_src"] == "class" and v["max_load_assumed"] is False for v in by_class["vehicles"] if v["cls"] == "大型")
    by_plate = _an(calc, [_hf(), _df()], {"munis": MUNIS, "max_payload_by_plate": {"1201": 13000}, "max_payload_by_class": {"大型": 10500}})
    caps = {v["plate"]: (v["max_load_kg"], v["max_load_src"]) for v in by_plate["vehicles"] if v["cls"] == "大型"}
    assert caps["1201"] == (13000.0, "plate") and caps["1202"] == (10500.0, "class")
    assert len([c for c in by_plate["confirm"] if c["kind"] == "weight"]) < 16 + 1 and {c["plate"] for c in by_plate["confirm"] if c["kind"] == "weight"} >= {"1203"} and "1201" not in {c["plate"] for c in by_plate["confirm"] if c["kind"] == "weight"}
    assert by_class["kpis"]["load_rate"]["over_max"] == 0 and graded["kpis"]["load_rate"]["over_max"] == 16


def test_analyze_return_legs_without_a_mark_or_a_weight_go_to_confirm_and_a_decision_applies(calc):
    t = "車番,車種,10/1(木),10/2(金),10/5(月),10/6(火)\n1234,大型,加古川→江東 着5:30 5t,江東→加古川,加古川→江東 着5:30 5t,江東→加古川 実車\n"
    d = _an(calc, [{"name": "h.csv", "text": t}], {"munis": MUNIS})
    assert d["counts"]["legs"] == 4 and d["counts"]["unknown"] == 1 and d["counts"]["loaded"] == 3 and d["counts"]["return_unmarked"] == 1
    card = [c for c in d["confirm"] if c["kind"] == "return"][0]
    assert card["key"] == "return-unmarked" and card["count"] == 1 and "空の印" in card["original"] and card["effect"] == "kpi" and len(card["legs"]) == 1
    assert [l for l in d["legs"] if l["return_unmarked"]][0]["kind"] == "unknown" and [l for l in d["legs"] if l["date"] == "2026-10-06"][0]["kind"] == "loaded"
    fixed = _an(calc, [{"name": "h.csv", "text": t}], {"munis": MUNIS, "decisions": {"return-unmarked": {"act": "fix", "text": "空"}}})
    assert fixed["counts"]["empty"] == 1 and fixed["counts"]["unknown"] == 0
    c2 = [c for c in fixed["confirm"] if c["kind"] == "return"][0]
    assert c2["decision"]["applied"] is True and "空車" in c2["decision"]["note"]
    bad = _an(calc, [{"name": "h.csv", "text": t}], {"munis": MUNIS, "decisions": {"return-unmarked": {"act": "fix", "text": "さあ"}}})
    assert [c for c in bad["confirm"] if c["kind"] == "return"][0]["decision"]["applied"] is False and bad["counts"]["unknown"] == 1


def test_analyze_place_display_keeps_the_ward_of_a_designated_city(calc):
    t = "車番,車種,10/1(木),10/2(金)\n1234,大型,加古川→名古屋市港区 5t,名古屋市港区→加古川 空\n"
    d = _an(calc, [{"name": "h.csv", "text": t}], {"munis": MUNIS})
    assert {p["short"] for p in d["places"].values()} == {"加古川", "名古屋市港区"} and d["pairs"][0]["label"] == "名古屋市港区 → 加古川"
    # 区の字を省いた書き方でも、区は残る。東京の特別区と市は、これまでどおり末尾の字を略す
    d2 = _an(calc, [{"name": "h.csv", "text": t.replace("名古屋市港区", "名古屋市港")}], {"munis": MUNIS})
    assert "名古屋市港区" in {p["short"] for p in d2["places"].values()}
    d3 = _an(calc, [{"name": "h.csv", "text": t.replace("名古屋市港区", "江東区")}], {"munis": MUNIS})
    assert "江東" in {p["short"] for p in d3["places"].values()}


def test_analyze_sjis_graders_file_reads_the_same(calc):
    sj = list(grader_haisha().encode("cp932", errors="replace"))
    r = calc.js(
        "(function(){const r = OwnCalc.decode(new Uint8Array(%s)); const d = OwnCalc.analyze([{ name: %s, text: r.text, encoding: r.encoding }], {});"
        "return { enc: r.encoding, legs: d.counts.legs, hired: d.counts.hired_cells, year: d.year };})()" % (J(sj), J(G_HAISHA)))
    assert r["enc"] == "Shift_JIS" and r["legs"] == 342 and r["hired"] == 24 and r["year"] == 2026
