"""SPEC §40 自社の運行（own）の道具の採点 r2 の修正。

- 重さ: 最大積載量を超える区間は積載率・積載効率に入れず、確認のカードと気づきに出す。符号つき（-1t）は読めない行。4600kg は kg で読む。
- 日時・列名・区分の値: Python（own.py）と JS（own_calc.js）が同じ入力を同じに読む。共通の入力表で両方を照合する。
- 年: 見出しの曜日に合う年が複数のとき、いちばん新しい年を使わず、年が選ばれるまで計算しない。
- 文言: 読めなかったファイルの数え方、列の対応が同じときだけ「同じです」、停車の範囲、3 つの数字の式、下書きが取り込んだ区間を入れること。
Edge が無い環境だけ、画面の試験を飛ばす（起動の時間切れは失敗）。
"""

from __future__ import annotations

import csv
import io
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from shipcompare import own  # noqa: E402

# ---------------------------------------------------------------- 共通の入力表（Python と JS の両方に通す）

CELL = "加古川→江東 着530 "
# (セルの文字, 重さ kg, 理由)
WEIGHT_VECTORS = [
    (CELL + "6.5t", 6500.0, None),
    (CELL + "6.5ｔ", 6500.0, None),
    (CELL + "4600kg", 4600.0, None),
    (CELL + "4,600kg", 4600.0, None),
    (CELL + "4.6KG", 4.6, None),
    (CELL + "12t", 12000.0, None),
    (CELL + "99t", 99000.0, None),
    (CELL + "-1t", None, own.WEIGHT_SIGN_ERR),
    (CELL + "－1t", None, own.WEIGHT_SIGN_ERR),  # 全角のマイナス
    (CELL + "−1t", None, own.WEIGHT_SIGN_ERR),  # U+2212
    (CELL + "+5t", None, own.WEIGHT_SIGN_ERR),
    (CELL + "-300kg", None, own.WEIGHT_SIGN_ERR),
    (CELL + "4600", None, None),  # 単位が無い数は重さと見なさない
    (CELL + "6.5ton", None, None),
    ("加古川→江東", None, None),
]

# (日時の文字, 読めたときの ISO 'YYYY-MM-DDTHH:MM')。None は読めない
DT_VECTORS = [
    ("2026-10-01 05:00", "2026-10-01T05:00"),
    ("2026/10/01 05:00", "2026-10-01T05:00"),
    ("2026/10/6 20:10", "2026-10-06T20:10"),
    ("２０２６/１０/６ ２０:１０", "2026-10-06T20:10"),
    ("2026-10-01T05:00", "2026-10-01T05:00"),
    ("2026-10-01 05:00:30", "2026-10-01T05:00"),
    ("2026-10-01", "2026-10-01T00:00"),
    ("20261001T0500", None),  # 区切りの無い形は、Python も JS も読まない（決めたこと）
    ("2026-10-01 05:00+09:00", None),
    ("2026-10-01 24:00", None),
    ("2026-02-30 10:00", None),
    ("2026-10-01 5:0", None),
    ("", None),
    ("abc", None),
]

JP_DIGITACO = """区分,開始日時,終了日時,走行距離,実空区分,地点名,乗務員コード
走行,2026/10/6 20:10,2026/10/7 03:00,551.8,実,,D01
停車,2026/10/7 03:00,2026/10/7 04:10,,,加古川,D01
走行,２０２６/１０/７ ０４:１０,2026/10/07 05:00,１２.５km,空車,,D01
停車,2026/10/7 05:00,2026/10/7 06:00,,,江東,D01
走行,2026/10/7 06:00,2026/10/7 07:00,30,,,D01
走行,20261007T0700,2026-10-07 08:00,1,1,,D01
ＫＢＮ,2026/10/7 09:00,2026/10/7 10:00,1,1,,D01
"""

# (形の名前, 値の一覧, 一致率)
SHAPE_VECTORS = [
    ("code12", ["1", "2", "走行", "停車"], 1.0),
    ("code12", ["走行", "x"], 0.5),
    ("flag01", ["実", "空", "実車", "空車", "0", "1"], 1.0),
    ("datetime", ["2026/10/6 20:10", "２０２６/１０/６ ２０:１０", "2026-10-06 20:10:30", "20261006T2010"], 0.75),
    ("decimal", ["１２.５", "3"], 1.0),
    ("vehicle_type", ["２ｔ", "4t", "大型"], 2 / 3),  # 車種は元の文字のまま（サンプルの黄色の例のため）
    ("date_header", ["10/1(木)", "10月1日", "2026/10/1", "x"], 0.75),
]


def _same(a, b, path="$"):
    """入れ子の dict・list・数を比べる。数は相対 1e-9 まで同じとする。違いの場所を返す（同じなら None）。"""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                if a.get(k) is None and b.get(k) is None:
                    continue
                return f"{path}.{k}: キーが片方だけ"
            r = _same(a[k], b[k], f"{path}.{k}")
            if r:
                return r
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: 長さ {len(a)} / {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            r = _same(x, y, f"{path}[{i}]")
            if r:
                return r
        return None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return None if abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b)) else f"{path}: {a!r} / {b!r}"
    return None if a == b else f"{path}: {a!r} / {b!r}"


# ---------------------------------------------------------------- Python（own.py）

@pytest.mark.parametrize("text,kg,err", WEIGHT_VECTORS)
def test_python_parse_cell_reads_kg_and_refuses_signed_weights(text, kg, err):
    r = own.parse_cell(text)
    assert r["legs"][0]["weight_kg"] == kg
    assert r["errors"] == ([err] if err else [])


@pytest.mark.parametrize("text,iso", DT_VECTORS)
def test_python_parse_dt(text, iso):
    got = own.parse_dt(text)
    assert (got.isoformat(timespec="minutes") if got else None) == iso


def test_python_parse_digitaco_reads_japanese_columns_values_and_slashed_dates():
    r = own.parse_digitaco(list(csv.DictReader(io.StringIO(JP_DIGITACO))), lambda n: n, "1234")
    assert [(x["start"], x["end"], x["km"], x["jk"]) for x in r["runs"]] == [
        ("2026-10-06T20:10", "2026-10-07T03:00", 551.8, 1),
        ("2026-10-07T04:10", "2026-10-07T05:00", 12.5, 0),
        ("2026-10-07T06:00", "2026-10-07T07:00", 30.0, None),
    ]
    assert [(s["name"], s["minutes"]) for s in r["stops"]] == [("加古川", 70), ("江東", 60)]
    assert [(e["line"], e["reason"]) for e in r["errors"]] == [(7, "時刻が読めません"), (8, "区分が読めません")]
    assert r["runs"][1]["frm"] == "加古川" and r["runs"][1]["to"] == "江東"


@pytest.mark.parametrize("key,values,ratio", SHAPE_VECTORS)
def test_python_shapes_recognise_japanese_values_and_full_width(key, values, ratio):
    assert own.shape_ratio(values, key) == pytest.approx(ratio)


def test_columns_with_japanese_values_are_not_given_confidence_half():
    # 走行・停車、実・空だけの列は、辞書に当たれば確かさ 1.0（緑）。0.5（黄）にして確認のカードを出さない
    for key, vals in (("code12", ["走行", "停車", "走行"]), ("flag01", ["実", "空", "実車"])):
        c = own.confidence(True, own.shape_ratio(vals, key), False)
        assert c == 1.0 and own.confidence_color(c) == "green"


def test_python_infer_year_picks_the_latest_year_up_to_the_demo_year_for_ambiguous_weekdays():
    r = own.infer_year(["車番", "車種", "10/1(水)", "10/2(木)"])
    cands = [y for y in range(2020, 2037) if date(y, 10, 1).weekday() == 2]
    assert r["ambiguous"] is True and r["year_assumed"] is True and r["source"] == "weekday"
    assert r["candidates"] == cands and 2036 in cands
    assert r["year"] == 2025 and r["year"] != 2036  # 2026 年以下でいちばん新しい年。2036 年のような先の年は選ばない（SPEC §41）
    one = own.infer_year(["車番", "2/27(金)", "2/28(土)", "3/1(日)"])
    assert one["year"] == 2026 and one["ambiguous"] is False and one["year_assumed"] is False


def _vehicles():
    return [{"plate": "1234", "cls": "大型", "gvw_kg": 0, "max_load_kg": 10000, "tachograph_label": ""},
            {"plate": "5678", "cls": "4t", "gvw_kg": 0, "max_load_kg": 3000, "tachograph_label": ""}]


def _legs():
    def lg(i, plate, kg, kind="loaded"):
        return {"id": f"L{i:03d}", "plate": plate, "date": f"2026-10-0{i}", "kind": kind, "km": 100.0, "weight_kg": kg, "ref": {"file": "h.csv", "where": f"{i} 行目", "text": "x"}}
    return [lg(1, "1234", 12000.0), lg(2, "1234", 4600.0), lg(3, "1234", 6000.0), lg(4, "5678", 3500.0), lg(5, "1234", None)]


def test_python_over_max_weights_are_left_out_of_the_load_rate_and_reported():
    legs, vs = _legs(), _vehicles()
    assert [l["id"] for l in own.over_max_legs(legs, vs)] == ["L001", "L004"]
    kp = own.kpis(legs, vs)
    assert kp["load_rate"]["value"] == pytest.approx((0.46 + 0.6) / 2) and kp["load_rate"]["legs"] == 2 and kp["load_rate"]["over_max"] == 2
    ins = [i for i in own.insights({"legs": legs, "vehicles": vs, "places": {}, "stops": [], "shifts": []}) if i["rule"] == 7]
    assert len(ins) == 1 and "2 件" in ins[0]["text"] and ins[0]["numbers"] == [{"k": "最大積載量を超える記録", "v": "2 件"}]
    assert ins[0]["evidence"][0]["text"] == "重さ 12.0t > 最大積載量 10.0t"
    items = own.weight_confirm_items(legs, vs)
    assert [(i["kind"], i["leg"]) for i in items] == [("weight", "L001"), ("weight", "L004")]
    assert items[0]["original"] == "配車表の重さ 12.0t が、最大積載量 10.0t を超えています"
    # 境界: 最大積載量ちょうどは超えていない
    legs[0]["weight_kg"] = 10000.0
    assert own.kpis(legs, vs)["load_rate"]["over_max"] == 1


def test_python_column_signature_is_the_same_only_for_the_same_mapping():
    a = ["区分", "開始日時", "終了日時"]
    assert own.column_signature(a) == own.column_signature(list(reversed(a)))
    assert own.column_signature(["KBN", "ST_TIME", "ED_TIME"]) != own.column_signature(a)


# ---------------------------------------------------------------- JS（ヘッドレス Edge）と Python の照合

@pytest.fixture(scope="module")
def calc(local_server):
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        try:
            page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
            assert page.js("typeof OwnCalc === 'object'")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "own_calc.js を読み込めません", e, "own_v40_fail")
        yield page


def _an(page, files, opts=None):
    return page.js(f"OwnCalc.analyze({json.dumps(files, ensure_ascii=False)}, {json.dumps(opts or {}, ensure_ascii=False)})")


def test_js_and_python_read_the_same_weights(calc):
    for text, kg, err in WEIGHT_VECTORS:
        js = calc.js(f"OwnCalc.parseCell({json.dumps(text, ensure_ascii=False)})")
        py = own.parse_cell(text)
        assert js == json.loads(json.dumps(py)), text
        assert js["legs"][0]["weight_kg"] == kg and js["errors"] == ([err] if err else []), text


def test_js_and_python_read_the_same_datetimes(calc):
    epoch = datetime(1970, 1, 1)
    for text, iso in DT_VECTORS:
        js = calc.js(f"OwnCalc.parseDT({json.dumps(text, ensure_ascii=False)})")
        py = own.parse_dt(text)
        assert (None if py is None else int((py - epoch).total_seconds() // 60)) == js, text
        assert (py.isoformat(timespec="minutes") if py else None) == iso, text


def test_js_and_python_read_the_same_japanese_digitaco(calc):
    rows = list(csv.reader(io.StringIO(JP_DIGITACO)))
    js = calc.js(f"OwnCalc.parseDigitaco({json.dumps(rows, ensure_ascii=False)}, (n) => n, '1234')")
    py = own.parse_digitaco(list(csv.DictReader(io.StringIO(JP_DIGITACO))), lambda n: n, "1234")
    assert js == json.loads(json.dumps(py))
    assert len(js["runs"]) == 3 and len(js["stops"]) == 2 and len(js["errors"]) == 2


def test_js_and_python_agree_on_shapes_and_signature(calc):
    for key, values, ratio in SHAPE_VECTORS:
        js = calc.js(f"OwnCalc.shapeRatio({json.dumps(values, ensure_ascii=False)}, {json.dumps(key)})")
        assert js == pytest.approx(own.shape_ratio(values, key)) == pytest.approx(ratio), key
    # JS の infer_year も Python と同じ（年を決めない）
    head = ["車番", "車種", "10/1(水)", "10/2(木)"]
    assert calc.js(f"OwnCalc.inferYear({json.dumps(head, ensure_ascii=False)})") == json.loads(json.dumps(own.infer_year(head)))


def _haisha(head, cells, plate="1234", typ="大型"):
    return ",".join(head) + "\n" + ",".join([plate, typ] + cells) + "\n"


def test_js_over_max_weights_are_left_out_and_match_python(calc):
    head = ["車番", "車種", "2026/10/1", "2026/10/2", "2026/10/3", "2026/10/4"]
    cells = ["加古川→江東 12t", "加古川→江東 4600kg", "加古川→江東 -1t", "加古川→江東 6t"]
    files = [{"name": "haisha_w.csv", "text": _haisha(head, cells)}]
    d = _an(calc, files, {"largeMaxLoadKg": 10000})  # 最大積載量を決めたときは、決めた値のまま超える重さを数える（決めないときは仮定を上げる。tests/test_v48_own_r6.py）
    # -1t は読めない行（理由つき）。区間は 3 つ
    assert [u["reason"] for u in d["unreadable"]] == [own.WEIGHT_SIGN_ERR] and d["counts"]["legs"] == 3
    lr = d["kpis"]["load_rate"]
    assert lr["over_max"] == 1 and lr["legs"] == 2 and lr["value"] == pytest.approx((0.46 + 0.6) / 2)
    # 確認のカードと気づき（規則 7）
    cards = [c for c in d["confirm"] if c["kind"] == "weight"]
    assert len(cards) == 1 and cards[0]["original"] == "配車表の重さ 12.0t が、最大積載量 10.0t を超えています" and cards[0]["leg"] == "L001"
    r7 = [i for i in d["insights"] if i["rule"] == 7]
    assert len(r7) == 1 and "1 件" in r7[0]["text"]
    # Python に同じ区間・車両を渡した結果と一致
    vs = [{**v, "gvw_kg": v["gvw_kg"] or 0, "max_load_kg": v["max_load_kg"] or 0} for v in d["vehicles"]]
    assert _same(own.kpis(d["legs"], vs), d["kpis"]) is None
    py7 = [i for i in own.insights({"legs": d["legs"], "vehicles": vs, "places": d["places"], "stops": d["stops"], "shifts": d["shifts"], "base": d["base"], "dock": d["dock"], "month": d["mon"]}) if i["rule"] == 7]
    for k in ("id",):
        py7[0].pop(k), r7[0].pop(k)
    assert _same(py7, r7) is None
    py_cards = own.weight_confirm_items(d["legs"], vs)
    strip = lambda c: {k: v for k, v in c.items() if k not in ("id", "key", "effect", "decision")}  # noqa: E731
    assert _same([strip(c) for c in py_cards], [strip(c) for c in cards]) is None
    # 決定: 修正で 4.6t にすると率に入る。読まないでは入らず、気づきも消える
    fixed = _an(calc, files, {"largeMaxLoadKg": 10000, "decisions": {"weight:L001": {"act": "fix", "text": "4.6t"}}})
    assert fixed["kpis"]["load_rate"]["over_max"] == 0 and fixed["kpis"]["load_rate"]["legs"] == 3
    assert not [i for i in fixed["insights"] if i["rule"] == 7]
    assert next(c for c in fixed["confirm"] if c["kind"] == "weight")["decision"]["applied"] is True
    bad = _an(calc, files, {"largeMaxLoadKg": 10000, "decisions": {"weight:L001": {"act": "fix", "text": "-3t"}}})
    assert bad["kpis"]["load_rate"]["over_max"] == 1 and next(c for c in bad["confirm"] if c["kind"] == "weight")["decision"]["applied"] is False
    skipped = _an(calc, files, {"largeMaxLoadKg": 10000, "decisions": {"weight:L001": {"act": "skip", "text": ""}}})
    assert skipped["kpis"]["load_rate"]["over_max"] == 0 and skipped["kpis"]["load_rate"]["legs"] == 2
    # 99t でも 500% にならない
    big = _an(calc, [{"name": "h.csv", "text": _haisha(head[:3], ["加古川→江東 99t"])}])
    assert big["kpis"]["load_rate"]["value"] is None and big["kpis"]["load_efficiency"]["value"] is None and big["kpis"]["load_rate"]["over_max"] == 1


def test_js_reads_japanese_digitaco_without_a_needless_confirm_card(calc):
    head = ["車番", "車種", "2026/10/6", "2026/10/7"]
    files = [{"name": "haisha.csv", "text": _haisha(head, ["加古川→江東 着5:30 6.5t", "江東→加古川 （空）"])},
             {"name": "digitaco_1234.csv", "text": JP_DIGITACO}]
    d = _an(calc, files)
    assert d["counts"]["legs"] >= 2 and not [c for c in d["confirm"] if c["kind"] == "column"], [c["title"] for c in d["confirm"]]
    cols = {c["column"]: c for c in d["columns"]}
    # 区分の列には、わざと読めない値（ＫＢＮ）を 1 行入れてあるので 1.0 にはならないが、緑（0.9 以上）で、辞書にも当たる
    assert all(cols[k]["color"] == "green" and cols[k]["dict_hit"] for k in ("区分", "実空区分", "開始日時", "終了日時", "走行距離", "地点名", "乗務員コード"))
    assert cols["区分"]["shape_ratio"] > 0.85 and cols["実空区分"]["confidence"] == 1.0


def test_js_year_is_assumed_not_asked_when_several_years_match(calc):
    head = ["車番", "車種", "10/1(水)", "10/2(木)"]
    files = [{"name": "haisha.csv", "text": _haisha(head, ["加古川→江東", "江東→加古川"])}]
    d = _an(calc, files)
    cands = [y for y in range(2020, 2037) if date(y, 10, 1).weekday() == 2]
    assert not d.get("needs_year") and d["year"] == 2025 and d["year_choice"]["year_assumed"] is True and d["year_choice"]["candidates"] == cands and d["counts"]["legs"] == 2
    ok = _an(calc, files, {"year": 2025})
    assert ok["year"] == 2025 and ok["counts"]["legs"] == 2 and ok["legs"][0]["date"] == "2025-10-01"
    # デジタコの日付があれば、その年に絞れるので選ばせない
    dg = "KBN,ST_TIME,ED_TIME,ODO_KM,JK_FLG,POS_NAME,DRV_CD\n1,2025-10-01 00:00,2025-10-01 05:00,500,1,,D01\n2,2025-10-01 05:00,2025-10-01 06:00,,,江東,D01\n"
    both = _an(calc, files + [{"name": "digitaco_1234.csv", "text": dg}])
    assert not both.get("needs_year") and both["year"] == 2025


def test_js_counts_files_without_legs_or_stops_as_unread_and_notes_same_columns_only_when_same(calc):
    head = ["車番", "車種", "2026/10/6", "2026/10/7"]
    good = {"name": "haisha.csv", "text": _haisha(head, ["加古川→江東", "江東→加古川"])}
    jp = {"name": "digitaco_1234.csv", "text": JP_DIGITACO}
    kb = {"name": "digitaco_5678.csv", "text": JP_DIGITACO.replace("区分,開始日時,終了日時,走行距離,実空区分,地点名,乗務員コード", "KBN,ST_TIME,ED_TIME,ODO_KM,JK_FLG,POS_NAME,DRV_CD")}
    jp2 = {"name": "digitaco_9012.csv", "text": JP_DIGITACO}
    d = _an(calc, [good, jp, kb, jp2])
    notes = {f["file"]: f.get("note") for f in d["files"] if f["kind"] == "デジタコ"}
    assert notes == {"digitaco_1234.csv": None, "digitaco_5678.csv": None, "digitaco_9012.csv": "列の対応は digitaco_1234.csv と同じです"}
    assert own.column_signature(JP_DIGITACO.splitlines()[0].split(",")) == own.column_signature(JP_DIGITACO.splitlines()[0].split(","))
    assert d["files_read"] == 4 and d["files_unread"] == []
    # 区間も停車も取り出せないファイルは、読めなかった（件数に数えない）
    empty_dg = {"name": "digitaco_3333.csv", "text": "KBN,ST_TIME,ED_TIME,ODO_KM\n1,x,y,1\n9,x,y,1\n"}
    memo = {"name": "memo.csv", "text": "名前,年齢\n山田,30\n"}
    second = {"name": "haisha2.csv", "text": good["text"]}
    d2 = _an(calc, [good, jp, empty_dg, memo, second])
    assert d2["files_read"] == 2
    assert sorted(u["file"] for u in d2["files_unread"]) == ["digitaco_3333.csv", "haisha2.csv", "memo.csv"]
    assert [u for u in d2["unreadable"] if u["file"] == "haisha2.csv"]


# ---------------------------------------------------------------- 画面（ヘッドレス Edge）

# 距離のずれが必ず出る入力: 行きのメーター 900 km が、直線距離 × 1.25 の推定（約 575 km）より 15% 以上長い。
# 停車は 江東（主な行き先）が 60 分、加古川（自社）が 90 分で、表の停車（江東だけ）と費用の合計（全地点）の数が違う
GAP_HAISHA = "車番,車種,2026/10/1,2026/10/2\n1234,大型,加古川→江東 着5:30,江東→加古川 （空）\n"
GAP_DIGITACO = ("KBN,ST_TIME,ED_TIME,ODO_KM,JK_FLG,POS_NAME,DRV_CD\n"
                "1,2026-10-01 00:00,2026-10-01 10:00,900,1,,D01\n"
                "2,2026-10-01 10:00,2026-10-01 11:00,,,江東,D01\n"
                "2,2026-10-01 11:00,2026-10-01 12:30,,,加古川,D01\n")
WEIGHT_HAISHA = "車番,車種,2026/10/1,2026/10/2,2026/10/3,2026/10/4\n1234,大型,加古川→江東 12t,加古川→江東 4600kg,加古川→江東 -1t,加古川→江東 6t\n"


@pytest.fixture(scope="module")
def app(local_server):
    import capture_web as cw
    from conftest import RESOURCE_HOOK
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
        page.send("Page.navigate", url=f"http://localhost:{local_server}/index.html#v=own")
        try:
            page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
            page.wait(lambda: page.js("!!document.querySelector('#own-file')"), 60, "ファイルを落とす欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面が起動しません", e, "own_v40_fail")
        time.sleep(2.0)
        yield page


def _set_files(page, paths):
    doc = page.send("DOM.getDocument", depth=1)
    nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#own-file")["nodeId"]
    page.send("DOM.setFileInputFiles", files=[str(p) for p in paths], nodeId=nid)


def _src(page) -> str:
    return page.js("(document.querySelector('#own-src') || {textContent: ''}).textContent")


def _to_sample(page):
    page.js("App.setView('own')")
    if page.js("Own.state.mode") != "sample":
        page.js("document.querySelector('#own-src [data-act=reset]').click()")
    page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_ui_unreadable_files_formula_text_and_stop_scope(app, tmp_path):
    page = app
    _to_sample(page)
    page.js("Own.setTab('import')")
    files = [_write(tmp_path, "haisha_gap.csv", GAP_HAISHA), _write(tmp_path, "digitaco_1234.csv", GAP_DIGITACO),
             _write(tmp_path, "memo.csv", "名前,年齢\n山田,30\n"), _write(tmp_path, "memo2.csv", "a,b\n1,2\n")]
    _set_files(page, files)
    page.wait(lambda: "自社のファイル: 2 件（読めなかった 2 件）" in _src(page), 120, "取り込み")
    msg = page.js("document.querySelector('#own-drop-msg').textContent")
    assert "ファイル 2 件" in msg and "を読みました" in msg and "読めなかった 2 件" in msg and "memo.csv" in msg
    # 3 つの数字の式は、押して開く説明に出る（title だけではない）
    page.js("Own.setTab('import')")
    assert page.js("document.querySelector('#own-fm summary').textContent") == "3 つの数字の式を見る"
    page.js("document.querySelector('#own-fm').open = true")
    fm = page.js("document.querySelector('#own-fm').innerText")
    for t in ("実車の km ÷ 走った km", "積んだ重さ ÷ 最大積載量", "大型の積載率 × 大型の実車率"):
        assert t in fm, fm
    # 表の見出しに地点を書き、合計との違いを 1 行出す
    heads = page.js("[...document.querySelectorAll('#own-tw th')].map(t => t.textContent)")
    assert "江東での 30 分以上の停車" in heads
    scope = page.js("document.querySelector('#own-tw .o-scope').textContent")  # 2026-11-08 の最終評価に合わせて: 表の説明は閉じた「表の見方」に入れたので textContent で読む
    assert "主な行き先（江東）" in scope and "1 回" in scope and "2 回" in scope and "2.5 時間" in scope, scope
    _to_sample(page)


def test_ui_year_is_assumed_and_can_be_changed_when_several_years_match(app, tmp_path):
    page = app
    _to_sample(page)
    page.js("Own.setTab('import')")
    _set_files(page, [_write(tmp_path, "haisha_wed.csv", "車番,車種,10/1(水),10/2(木)\n1234,大型,加古川→江東,江東→加古川\n")])
    page.wait(lambda: page.js("Own.state.mode") == "import", 60, "取り込み")
    d = page.js("Own.state.data")
    assert d["year"] == 2025 and d["year_choice"]["year_assumed"] is True and d["counts"]["legs"] == 2  # 先の年（2036）にしない。年を聞かずに計算する（SPEC §41）
    page.js("(function(){const s = document.querySelector('#own-year'); s.value = '2031'; s.dispatchEvent(new Event('change', {bubbles: true}));})()")
    page.wait(lambda: page.js("Own.state.data.year") == 2031, 20, "年の選び直し")
    _to_sample(page)


def test_ui_over_max_weight_is_shown_and_left_out_of_the_rates(app, tmp_path):
    page = app
    _to_sample(page)
    page.js("Own.setTab('import')")
    page.js("Own.state.largeT = 10")  # 決めた値のまま超える重さを数える
    _set_files(page, [_write(tmp_path, "haisha_w.csv", WEIGHT_HAISHA)])
    page.wait(lambda: "自社のファイル: 1 件" in _src(page), 120, "取り込み")
    d = page.js("Own.state.data")
    assert d["kpis"]["load_rate"]["over_max"] == 1 and d["kpis"]["load_rate"]["legs"] == 2
    right = page.js("document.querySelector('#own-right').innerText")
    # 注記は各数字の閉じた「詳しく」の中にある（SPEC §45）。閉じた中は innerText に出ないので textContent で読む
    note = page.js("document.querySelector('#own-right').textContent")
    assert "最大積載量を超える重さの記録 1 件は入れていません" in note and "53.0" in right  # 大型の積載率 (46% + 60%) / 2
    assert "記号つきは入れられません" in page.js("document.querySelector('#own-body').innerText")
    page.js("Own.setTab('confirm')")
    sel = '#own-body .o-card[data-key="weight:L001"]'
    assert "12.0t が、最大積載量 10.0t を超えています" in page.js(f"document.querySelector('{sel}').innerText")
    page.js("Own.setTab('insight')")
    assert "最大積載量を超えている記録が 1 件" in page.js("document.querySelector('#own-body').innerText")
    # 修正: 数でない文字は断り、4.6t なら率に入る
    page.js("Own.setTab('confirm')")
    page.js(f"document.querySelector('{sel} [data-act=fix]').click()")
    page.js(f"(function(){{const i = document.querySelector('{sel} .o-fix input'); i.value = '-3t'; document.querySelector('{sel} [data-act=fixok]').click();}})()")
    assert page.js("Own.state.dec") == {}
    page.js(f"(function(){{const i = document.querySelector('{sel} .o-fix input'); i.value = '４.６ｔ'; document.querySelector('{sel} [data-act=fixok]').click();}})()")
    page.wait(lambda: page.js("Own.state.data.kpis.load_rate.over_max") == 0 and page.js("Own.state.data.kpis.load_rate.legs") == 3, 20, "修正の反映")
    _to_sample(page)


def test_ui_draft_fills_the_register_from_the_imported_leg_and_only_the_sample_says_t7(app, tmp_path):
    page = app
    _to_sample(page)
    page.js("Own.setTab('import')")
    _set_files(page, [_write(tmp_path, "haisha_gap.csv", GAP_HAISHA), _write(tmp_path, "digitaco_1234.csv", GAP_DIGITACO)])
    page.wait(lambda: "自社のファイル: 2 件" in _src(page), 120, "取り込み")
    page.js("Own.setTab('insight')")
    page.js("document.querySelector('#own-body .o-ins button[data-a=draft_backhaul]').click()")
    page.wait(lambda: page.js("!!(Find.state.reg && document.querySelector('#fr-o'))"), 60, "登録の欄")
    txt = page.js("document.querySelector('#own-draft .od-text').textContent")
    assert "T7" not in txt and "架空の便" not in txt and "江東" in txt and "加古川" in txt, txt
    reg = page.js("Find.state.reg")
    assert reg["o"] == "13108" and reg["d"] == "28210"  # 空車の帰り: 江東区 → 加古川市
    assert "江東区" in page.js("document.querySelector('#fr-o').value") and "加古川市" in page.js("document.querySelector('#fr-d').value")
    assert page.js("new Date(Find.state.reg.dep).getDay()") == 5 and reg["dep"].endswith("T17:00")  # 10/2 は金曜（時刻が無いので 17:00）
    page.js("document.querySelector('#own-draft .od-close').click()")
    _to_sample(page)
    # 2026-11-08 の直しに合わせて: サンプルでも、地図の市区町村へ置けた帰りの区間があれば取り込みと同じに欄へ入れる（T7 の説明は出ない）
    page.js("Own.setTab('insight')")
    page.js("document.querySelector('#own-body .o-ins button[data-a=draft_backhaul]').click()")
    page.wait(lambda: not page.js("document.querySelector('#own-draft').hidden"), 20, "サンプルの下書き")
    sample_txt = page.js("document.querySelector('#own-draft .od-text').textContent")
    assert "T7" not in sample_txt and "帰り（" in sample_txt and "登録の欄に発地・着地と" in sample_txt, sample_txt
    page.js("document.querySelector('#own-draft .od-close').click()")
    page.js("App.setView('own')")


def test_ui_console_has_no_errors_after_the_flow(app):
    errs = [e for e in app.js("window.__errs") if "fonts.googleapis.com" not in e]
    assert errs == []
