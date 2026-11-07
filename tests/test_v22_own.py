"""自社の運行（SPEC §22、G66〜G71）。

純粋な関数（src/shipcompare/own.py）と、サンプルを作る tools/build_own_sample.py の結果を確かめる。通信はしない。
サンプルの数字は元の CSV から計算した値で、SPEC §22.1 の数字と一致することも見る。
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from shipcompare import own

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_own_sample as bos  # noqa: E402

WEB = ROOT / "web"
RAW = ROOT / "data" / "own_sample" / "raw"


@pytest.fixture(scope="module")
def sample() -> dict:
    return json.loads((WEB / "data" / "own_sample.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mapping() -> dict:
    return json.loads((ROOT / "data" / "own_sample" / "mapping.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- G66 車番と横の形

@pytest.mark.parametrize("raw, want", [
    ("神戸 100 か 1234", "1234"), ("1234", "1234"), ("4t-1234", "1234"), ("２ｔ-７７８８", "7788"),
    ("神戸 400 あ 7799", "7799"), ("神戸100か12-34", "1234"), (" 1234 ", "1234"),
    ("", None), (None, None), ("神戸 100 か", None), ("12345", None), ("4t", None),
])
def test_g66_normalize_plate(raw, want):
    assert own.normalize_plate(raw) == want


HAND_TABLE = [  # 手で作った小さな配車表（結合セルの代わりの空欄行、空行、月の無い列）
    ["車番", "車種", "10/1(木)", "10/2(金)", "10/3(土)", "13日"],
    ["神戸 100 か 9001", "大型", "加古川 → 江東 着530", "江東 → 加古川（空）", "", ""],
    ["", "", "架空 A商事", "", "", ""],
    ["4t-1234", "4t", "", "整備", "加古川 → 江東", "加古川 → 江東"],
    ["", "", "", "", "架空 B商事", ""],
    ["", "", "", "", "", ""],
    ["", "", "江東 → 加古川", "", "", ""],
]


def test_g66_wide_to_long_by_hand():
    r = own.wide_to_long(HAND_TABLE, 2026)
    got = [(x["plate"], x["date"], x["text"], tuple(x["notes"])) for x in r["rows"]]
    assert got == [
        ("9001", "2026-10-01", "加古川 → 江東 着530", ("架空 A商事",)),
        ("9001", "2026-10-02", "江東 → 加古川（空）", ()),
        ("1234", "2026-10-02", "整備", ()),
        ("1234", "2026-10-03", "加古川 → 江東", ("架空 B商事",)),
    ]
    reasons = sorted((u["reason"], u["original"]) for u in r["unreadable"])
    assert reasons == [("月が分かりません", "加古川 → 江東"), ("車番がありません", "江東 → 加古川")]
    assert r["stats"]["filled_rows"] == 2 and r["stats"]["groups"] == 3 and r["stats"]["date_cols"] == 3


def test_g66_wide_to_long_sample_matches_independent_reading():
    """サンプルの元の表を、CSV の各セルを直接読んだ正解（plate × 日付 × 文字）と突き合わせる。"""
    rows = list(csv.reader(open(RAW / "haisha_2026-10.csv", encoding="utf-8", newline="")))
    r = own.wide_to_long(rows, 2026)
    want = {}
    for i in range(1, 17, 2):  # 車両ごとに 2 行（運行の行、荷主の行）
        plate = own.normalize_plate(rows[i][0])
        for c in range(2, 33):
            if rows[i][c]:
                want[(plate, f"2026-10-{c - 1:02d}")] = rows[i][c]
    got = {(x["plate"], x["date"]): x["text"] for x in r["rows"]}
    assert got == want
    assert r["stats"] == {"filled_rows": 8, "groups": 9, "date_cols": 31, "bad_cols": 1, "data_rows": 17}
    assert sorted(u["reason"] for u in r["unreadable"]) == ["月が分かりません", "車番がありません"]
    assert len({x["plate"] for x in r["rows"]}) == 8
    # 車番の書き方が 3 通り混ざっている
    raws = {x["plate_raw"] for x in r["rows"]}
    assert "4t-1234" in raws and "9002" in raws and "鹿児島 100 か 9001" in raws


def test_g66_parse_cell():
    p = own.parse_cell("加古川 → 辰巳 着5時30分 6.5t")
    assert p["legs"] == [{"frm": "加古川", "to": "辰巳", "empty": False, "arr": "05:30", "weight_kg": 6500.0}]
    assert own.parse_cell("江東 → 加古川（空）")["legs"][0]["empty"] is True
    assert own.parse_cell("加古川 →")["legs"][0]["to"] is None
    # SPEC §42: 「8時半ごろ着」は時刻だけ捨てて区間は残す（errors ではなく warnings）
    p8 = own.parse_cell("江東 → 加古川 着8時半ごろ")
    assert p8["errors"] == [] and p8["warnings"] == ["時刻が読めません"] and p8["legs"][0]["arr"] is None and p8["legs"][0]["to"] == "加古川"
    assert own.parse_cell("整備")["nonleg"] is True
    two = own.parse_cell("加古川 → 大阪／大阪 → 加古川（空）")
    assert [x["empty"] for x in two["legs"]] == [False, True]
    assert [own.parse_time(t) for t in ("530", "5:30", "05:30", "0530", "5時30分", "5時", "8時半ごろ")] == ["05:30", "05:30", "05:30", "05:30", "05:30", "05:00", None]


# ---------------------------------------------------------------- G67 結び付けと実車・空車

def _h(i, plate, date, **kw):
    base = dict(id=f"H{i}", plate=plate, date=date, idx=0, frm="p01", to="p07", empty=False, arr=None, weight_kg=None, ref=None)
    base.update(kw)
    return base


def _d(i, plate, start, end, km=556.0, jk=1, **kw):
    base = dict(id=f"D{i}", plate=plate, start=start, end=end, km=km, jk=jk, frm="p01", to="p07", driver="D01", ref=None)
    base.update(kw)
    return base


EST = {frozenset(("p01", "p07")): 556.3}


def test_g67_link_night_departure_is_one_job_and_two_days_are_not_split():
    h = [_h(1, "1234", "2026-10-06", arr="05:30"), _h(2, "1234", "2026-10-07", frm="p07", to="p01", empty=True, arr="04:30")]
    d = [_d(1, "1234", "2026-10-06T20:10", "2026-10-07T05:30"), _d(2, "1234", "2026-10-07T19:00", "2026-10-08T04:30", jk=0, frm="p07", to="p01")]
    r = own.link(h, d, EST)
    assert len(r["legs"]) == 2 and r["digitaco_only"] == 0 and r["haisha_only"] == 0
    out, back = r["legs"]
    assert (out["hid"], out["did"]) == ("H1", "D1") and (back["hid"], back["did"]) == ("H2", "D2")
    assert out["dep"] == "2026-10-06T20:10" and out["arr"] == "2026-10-07T05:30"  # 日付をまたいでも 1 つの区間
    assert out["flags"] == [] and back["flags"] == []
    assert out["sources"]["km"] == "デジタコ" and out["sources"]["time"] == "デジタコ"


def test_g67_link_flags_at_boundaries():
    # 時刻: 59 分は確認待ちにならず、60 分で確認待ち。夜をまたぐ差は短い側で数える
    for diff, flagged in ((59, False), (60, True), (-60, True)):
        hint = f"{(5 * 60 + 30 + diff) // 60:02d}:{(5 * 60 + 30 + diff) % 60:02d}"
        r = own.link([_h(1, "1234", "2026-10-06", arr=hint)], [_d(1, "1234", "2026-10-06T20:00", "2026-10-07T05:30")], EST)
        assert bool(r["legs"][0]["flags"]) is flagged, diff
    r = own.link([_h(1, "1234", "2026-10-06", arr="23:50")], [_d(1, "1234", "2026-10-06T20:00", "2026-10-07T00:10")], EST)
    assert r["legs"][0]["flags"] == []  # 23:50 と 00:10 は 20 分差
    # 距離: 14.9% は確認待ちにならず、15% で確認待ち
    for km, flagged in ((556.3 * 1.149, False), (556.3 * 1.15, True), (556.3 * 0.85, True), (556.3 * 0.86, False)):
        r = own.link([_h(1, "1234", "2026-10-06")], [_d(1, "1234", "2026-10-06T20:00", "2026-10-07T05:30", km=km)], EST)
        assert [f["type"] for f in r["legs"][0]["flags"]] == (["distance"] if flagged else []), km


def test_g67_link_haisha_only_digitaco_only_and_km_source():
    h = [_h(1, "9001", "2026-10-02"), _h(2, "1234", "2026-10-06")]
    d = [_d(1, "1234", "2026-10-06T20:00", "2026-10-07T05:30"), _d(2, "1234", "2026-10-20T20:00", "2026-10-21T05:30")]
    r = own.link(h, d, EST)
    by = {l["plate"] + l["date"]: l for l in r["legs"]}
    assert by["90012026-10-02"]["did"] is None and by["90012026-10-02"]["km"] == 556.3 and by["90012026-10-02"]["sources"]["km"] == "OSRM 推定"
    assert r["haisha_only"] == 1 and r["digitaco_only"] == 1


def test_g67_classify_order_and_unknown_ratio():
    base = dict(jk=None, empty_mark=False, has_ends=False)
    legs = [
        dict(base, id="a", jk=0, has_ends=True),  # デジタコが最優先（積地・降ろし地があっても空車）
        dict(base, id="b", jk=1, empty_mark=True),  # デジタコ > 配車表の（空）
        dict(base, id="c", empty_mark=True, has_ends=True),  # 配車表: （空）の印が積地・降ろし地より先
        dict(base, id="d", has_ends=True),  # 配車表: 積地・降ろし地 → 実車
        dict(base, id="e", invoiced=True),  # 請求書
        dict(base, id="f", log_loaded=False),  # 業務記録
        dict(base, id="g", invoiced=True, log_loaded=False),  # 請求書 > 業務記録
        dict(base, id="h"),  # 不明
        dict(base, id="i"),  # 不明
        dict(base, id="j", has_ends=False, empty_mark=False, log_loaded=True),
    ]
    r = own.classify_legs(legs)
    got = {l["id"]: (l["kind"], l["kind_src"]) for l in r["legs"]}
    assert got["a"] == ("empty", "デジタコの実空区分") and got["b"] == ("loaded", "デジタコの実空区分")
    assert got["c"] == ("empty", "配車表の（空）の印") and got["d"] == ("loaded", "配車表の積地・降ろし地")
    assert got["e"] == ("loaded", "請求書の区間") and got["f"] == ("empty", "業務記録の荷役") and got["g"][0] == "loaded"
    assert got["h"] == ("unknown", None) and got["j"][0] == "loaded"
    assert r["counts"] == {"loaded": 5, "empty": 3, "unknown": 2} and r["unknown_ratio"] == pytest.approx(0.2)
    assert own.classify_legs([])["unknown_ratio"] == 0.0


def test_g67_sample_classification(sample):
    c = sample["counts"]
    assert c["legs"] == c["loaded"] + c["empty"] + c["unknown"]
    assert c["unknown_ratio"] >= 0.15 and c["unknown"] / c["legs"] == pytest.approx(c["unknown_ratio"], abs=1e-4)
    legs = sample["legs"]
    # デジタコの 2 台の区間は、すべてデジタコの実空区分で決まっている
    for l in legs:
        if l["plate"] in ("1234", "5678"):
            assert l["kind_src"] == "デジタコの実空区分", l["id"]
        else:
            assert l["kind_src"] in ("配車表の（空）の印", "配車表の積地・降ろし地", None)
            assert (l["kind"] == "unknown") == (l["kind_src"] is None)
    # 日をまたぐ運行（夜発・翌朝着）が 1 区間にまとまっている
    night = [l for l in legs if l["dep"] and l["arr"] and l["dep"][:10] != l["arr"][:10]]
    assert night
    assert any(l["dep"][11:13] >= "19" and l["arr"][11:13] <= "07" for l in night)


# ---------------------------------------------------------------- G68 確かさ

def test_g68_confidence_boundaries():
    assert own.confidence(True, 0.2, False) == pytest.approx(0.6)
    assert own.confidence_color(own.confidence(True, 0.2, False)) == "yellow"  # ちょうど 0.6 は黄
    assert own.confidence_color(own.confidence(True, 0.19, False)) == "red"
    assert own.confidence(True, 0.8, False) == pytest.approx(0.9)
    assert own.confidence_color(own.confidence(True, 0.8, False)) == "green"  # ちょうど 0.9 は緑
    assert own.confidence_color(own.confidence(True, 0.79, False)) == "yellow"
    assert own.confidence(True, 1.0, True) == pytest.approx(1.0)  # 辞書と Claude が両方一致しても 1 を超えない
    assert own.confidence(False, 1.0, False) == pytest.approx(0.5) and own.confidence_color(0.5) == "red"
    assert own.confidence(False, 0.0, False) == 0.0


def test_g68_unknown_column_turns_green_with_claude_and_shape():
    """辞書に無い列でも、Claude の一致と値の形の一致で緑になる。Claude が無ければ赤のまま。"""
    assert own.confidence_color(own.confidence(False, 0.9, True)) == "green"  # 0.5 + 0.45 = 0.95
    assert own.confidence_color(own.confidence(False, 0.8, True)) == "green"
    assert own.confidence_color(own.confidence(False, 0.7, True)) == "yellow"
    assert own.confidence_color(own.confidence(False, 0.9, False)) == "red"


def test_g68_sample_columns_and_claude_not_made_yet(sample, mapping):
    cols = {(c["source"], c["column"]): c for c in sample["columns"]}
    assert cols[("デジタコ", "X9")]["color"] == "red" and cols[("デジタコ", "X9")]["dict_hit"] is False
    assert cols[("デジタコ", "POS_NAME")]["color"] == "yellow" and cols[("配車表", "車種")]["color"] == "yellow"
    assert cols[("デジタコ", "JK_FLG")]["color"] == "green"
    for c in mapping["columns"]:
        if c["claude_proposal"] is None:
            assert c["claude_note"] == "未作成（本線が作る）"
            assert c["claude_agrees"] is False
            # Claude が無いときは、辞書の一致だけで計算している
            assert c["confidence"] == pytest.approx(own.confidence(c["dict_hit"], c["shape_ratio"], False), abs=1e-3)
    assert sample["claude"]["present"] == any(c["claude_proposal"] for c in mapping["columns"])
    assert (sample["claude"]["label"] == "Claude の提案: 未作成") == (not sample["claude"]["present"])


def test_g68_mapping_keeps_claude_proposal_when_rebuilt(tmp_path, monkeypatch):
    """本線が claude_proposal を入れたあとに作り直しても消えず、確かさが Claude の一致を使って変わる。"""
    p = tmp_path / "mapping.json"
    monkeypatch.setattr(bos, "MAPPING", p)
    hrows = [["車番", "車種", "10/1(木)"], ["1234", "4t", ""]]
    dig = {"X9": [f"{v}.5" for v in range(10)]}
    m1, _ = bos.build_mapping(hrows, dig, {})
    x9 = next(c for c in m1["columns"] if c["column"] == "X9")
    assert x9["color"] == "red" and x9["claude_proposal"] is None
    data = json.loads(p.read_text(encoding="utf-8"))
    for c in data["columns"]:
        if c["column"] == "X9":
            c["claude_proposal"] = {"role": x9["shape_best_role"], "reason": "試験"}
            c["claude_note"] = "試験"
    data["claude_model"] = "試験用"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    m2, _ = bos.build_mapping(hrows, dig, {})
    x9b = next(c for c in m2["columns"] if c["column"] == "X9")
    assert x9b["claude_proposal"]["role"] == x9["shape_best_role"] and x9b["claude_agrees"] is True
    assert x9b["color"] == "green" and m2["claude_model"] == "試験用"


# ---------------------------------------------------------------- G69 拘束時間

def _day(s, e):
    return {"start": datetime.fromisoformat(s), "end": datetime.fromisoformat(e)}


def test_g69_kousoku_plan_4_2_example():
    """筋書き §4.2 の表: 11/12 の始業 13 時間、11/13 の始業 10.5 時間。"""
    days = [_day("2026-11-12T19:30", "2026-11-13T06:00"), _day("2026-11-13T17:00", "2026-11-14T01:00"), _day("2026-11-14T12:00", "2026-11-14T14:30")]
    h = own.kousoku_hours(days)
    assert [x["hours"] for x in h] == [13.0, 10.5, 2.5]
    # 行きの到着が 1 時間遅れ（降ろし終わり 07:00）で、積む時刻は 17:00 のまま → 14 時間
    late = [_day("2026-11-12T19:30", "2026-11-13T07:00"), _day("2026-11-13T17:00", "2026-11-14T01:00"), _day("2026-11-14T12:00", "2026-11-14T14:30")]
    assert own.kousoku_hours(late)[0]["hours"] == 14.0
    # 文字列で渡しても同じ。24 時間の枠の外の業務は足さない
    s = own.kousoku_hours([{"start": "2026-11-12T19:30", "end": "2026-11-13T06:00"}, {"start": "2026-11-13T19:30", "end": "2026-11-13T22:30"}])
    assert [x["hours"] for x in s] == [10.5, 3.0]


def test_g69_build_shifts_splits_on_rest():
    ev = [{"start": "2026-10-06T20:00", "end": "2026-10-07T05:30", "line": 2}, {"start": "2026-10-07T05:30", "end": "2026-10-07T06:20", "line": 3},
          {"start": "2026-10-07T17:00", "end": "2026-10-08T02:00", "line": 4}]
    sh = own.build_shifts(ev)
    assert [(s["start"], s["end"]) for s in sh] == [("2026-10-06T20:00", "2026-10-07T06:20"), ("2026-10-07T17:00", "2026-10-08T02:00")]


def test_g69_sample_numbers(sample):
    sh = sample["shifts"]
    over13 = [s for s in sh if s["hours"] > 13]
    over14 = [s for s in sh if s["hours"] > 14]
    assert len(over13) == 6 and len(over14) == 3 and max(s["hours"] for s in sh) < 15
    assert len({s["driver"] for s in over14}) == 1  # 14 時間超の 3 日は同じ運転者
    weeks = {datetime.fromisoformat(s["start"]).isocalendar()[:2] for s in over14}
    assert len(weeks) == 1  # 同じ週


# ---------------------------------------------------------------- G70 気づき 6 規則

def _leg(i, wd, kind, d="back", plate="1234", dep="2026-10-01T20:00", **kw):
    base = dict(id=f"L{i}", plate=plate, date=f"2026-10-{(i % 28) + 1:02d}", dir=d, cycle_wd=wd, cycle_dep=dep, kind=kind, km=556.3,
                ref={"file": "haisha_2026-10.csv", "where": "x", "text": "t"})
    base.update(kw)
    return base


PLACES = {"p01": {"pseudo": "地点 01", "muni": "加古川市", "short": "加古川"}, "p07": {"pseudo": "地点 07", "muni": "江東区", "short": "江東"}}


def _ins(legs=(), stops=(), shifts=(), vehicles=()):
    return own.insights({"legs": list(legs), "stops": list(stops), "shifts": list(shifts), "vehicles": list(vehicles), "places": PLACES, "base": "p01", "dock": "p07", "month": 10})


def _rules(**kw):
    return [i["rule"] for i in _ins(**kw)]


def test_g70_rule1_boundaries():
    def thu(empty, loaded, wd=3):
        return [_leg(i, wd, "empty") for i in range(empty)] + [_leg(100 + i, wd, "loaded") for i in range(loaded)]
    assert 1 in _rules(legs=thu(3, 1))  # 4 便中 3 便
    assert 1 not in _rules(legs=thu(2, 2))  # 4 便中 2 便
    assert 1 not in _rules(legs=thu(3, 0))  # 3 便しかない
    assert 1 in _rules(legs=thu(4, 1))  # 5 便中 4 便
    assert 1 not in _rules(legs=thu(3, 2))  # 5 便中 3 便は 0.6
    assert 1 in _rules(legs=thu(5, 0))
    # 不明の便は数えない（3 空 + 1 不明 は 3 便しかない）
    assert 1 not in _rules(legs=thu(3, 0) + [_leg(9, 3, "unknown")])
    # 文: 夜に出た便と、ほかの曜日の数
    t = next(i for i in _ins(legs=thu(5, 0) + [_leg(50 + i, 0, "loaded") for i in range(4)]) if i["rule"] == 1)
    assert "木曜の夜" in t["text"] and "5 便の帰りがすべて空" in t["text"] and "4 便中 0 便が空" in t["text"]
    assert t["next"]["label"] == "帰り便の下書きにする" and len(t["evidence"]) == 5
    day = next(i for i in _ins(legs=[_leg(i, 3, "empty", dep="2026-10-01T09:00") for i in range(4)]) if i["rule"] == 1)
    assert "夜" not in day["text"]


def test_g70_rule2_empty_legs():
    assert 2 not in _rules(legs=[_leg(1, 0, "loaded")])
    r = next(i for i in _ins(legs=[_leg(1, 0, "empty"), _leg(2, 1, "empty", d="out")]) if i["rule"] == 2)
    assert "2 便" in r["text"] and "1,113 km" in r["text"] and len(r["evidence"]) == 2


def _stops(n, minutes=30, place="p07"):
    return [dict(id=f"S{i}", plate="1234", place=place, start=f"2026-10-{i + 1:02d}T05:30", end="2026-10-01T06:30", minutes=minutes, ref={"file": "d.csv", "where": "w"}) for i in range(n)]


def test_g70_rule3_boundaries():
    assert 3 in _rules(stops=_stops(5, 30))  # 5 回・30 分ちょうど
    assert 3 not in _rules(stops=_stops(4, 30))
    assert 3 not in _rules(stops=_stops(5, 29))
    assert 3 not in _rules(stops=_stops(3, 30, "p07") + _stops(3, 30, "p01"))  # 地点が別なら合算しない
    t = next(i for i in _ins(stops=_stops(11, 52)) if i["rule"] == 3)
    assert "11 回" in t["text"] and "9.5 時間" in t["text"] and "江東区の地点 07 で" in t["text"]


def _shifts(hours, driver="D01", start="2026-10-12T20:00"):
    d = datetime.fromisoformat(start)
    return [dict(id=f"K{i}", driver=driver, plate="1234", start=(d.replace(day=d.day + 2 * i)).isoformat(timespec="minutes"), end="x", hours=h, ref={"file": "d.csv", "where": "w"}) for i, h in enumerate(hours)]


def test_g70_rule4_boundaries():
    assert 4 not in _rules(shifts=_shifts([13.0, 12.0]))  # ちょうど 13 時間は超えていない
    assert 4 in _rules(shifts=_shifts([13.01]))
    t = next(i for i in _ins(shifts=_shifts([14.1, 14.2, 14.3, 13.5])) if i["rule"] == 4)
    assert "原則の 13 時間を超えた日が 4 日" in t["text"]
    assert "週 3 回の週が 1 つ" in t["text"] and "15 時間を超えた日はありません" in t["text"]
    two = next(i for i in _ins(shifts=_shifts([14.1, 14.2])) if i["rule"] == 4)
    assert "つあります" not in two["text"]  # 同じ週に 2 回までは目安の範囲
    assert "15 時間を超えた日が 1 日" in next(i for i in _ins(shifts=_shifts([15.2])) if i["rule"] == 4)["text"]
    assert "15 時間を超えた日が" not in next(i for i in _ins(shifts=_shifts([15.0])) if i["rule"] == 4)["text"]  # ちょうど 15 時間は超えていない
    # 別の運転者の 14 時間超は合算しない
    mixed = _shifts([14.1, 14.2], "D01") + _shifts([14.3], "D02")
    assert "つあります" not in next(i for i in _ins(shifts=mixed) if i["rule"] == 4)["text"]


def _veh(gvw, load, plate="1234", cls="4t"):
    return dict(plate=plate, cls=cls, gvw_kg=gvw, max_load_kg=load, tachograph_label="デジタコ")


def test_g70_rule5_boundaries():
    loaded_nw = [_leg(1, 0, "loaded", d="out")]
    assert 5 in _rules(legs=loaded_nw, vehicles=[_veh(8000, 3500)])  # 車両総重量 8t ちょうど
    assert 5 not in _rules(legs=loaded_nw, vehicles=[_veh(7999, 3500)])
    assert 5 in _rules(legs=loaded_nw, vehicles=[_veh(6900, 5000)])  # 最大積載量 5t ちょうど
    assert 5 not in _rules(legs=loaded_nw, vehicles=[_veh(6900, 4999)])
    assert 5 not in _rules(legs=[_leg(1, 0, "loaded", d="out", weight_kg=3000.0)], vehicles=[_veh(8000, 3500)])  # 重さが入っていれば出ない
    t = next(i for i in _ins(legs=loaded_nw, vehicles=[_veh(8000, 3500), _veh(8000, 3500, "5678")]) if i["rule"] == 5)
    assert "4t の 2 台" in t["text"] and "車検証" in t["text"]


def test_g70_rule6_boundaries():
    def legs(unknown, total=20):
        return [_leg(i, 0, "unknown" if i < unknown else "loaded", d="out") for i in range(total)]
    assert 6 in _rules(legs=legs(3))  # 15% ちょうど
    assert 6 not in _rules(legs=legs(2))  # 10%
    t = next(i for i in _ins(legs=legs(4)) if i["rule"] == 6)
    assert "区間の 20% は実車か空車かが分かりません" in t["text"] and t["next"]["label"] == "配車表を取り込む"


def test_g70_sample_fires_all_six_rules(sample):
    ins = sample["insights"]
    assert sorted({i["rule"] for i in ins}) == [1, 2, 3, 4, 5, 6]  # 確認待ちの別名「辰巳」を当てないので、規則 3 は地点 07 と地点 08 の 2 件に分かれる
    by = {i["rule"]: i for i in ins}
    for i in ins:
        assert i["evidence"] and i["next"]["label"] and i["text"]
        assert all(e["file"] and e["text"] for e in i["evidence"])
    t = by[1]
    assert "土曜の夜に出た 5 便の帰りがすべて空" in t["text"] and len(t["evidence"]) == 5  # 帰りの便が出た日（行きを出た木曜ではない）
    assert t["next"]["action"] == "draft_backhaul" and t["next"]["payload"]["trips"] == 5 and t["next"]["payload"]["empty"] == 5
    r3 = [i["text"] for i in ins if i["rule"] == 3]  # 確認待ちの別名「辰巳」を当てないので、地点 07（江東区辰巳）と地点 08（辰巳）の 2 件に分かれる（合わせて 10 回・8.0 時間）
    assert len(r3) == 2 and any("5 回" in t and "3.8 時間" in t for t in r3) and any("5 回" in t and "4.2 時間" in t for t in r3)
    assert "6 日" in by[4]["text"] and "週 3 回の週が 1 つ" in by[4]["text"] and "15 時間を超えた日はありません" in by[4]["text"]
    assert "4t の 2 台" in by[5]["text"]
    assert by[6]["text"].startswith("区間の 16%")


def test_g70_sample_spec_numbers_from_raw(sample):
    """SPEC §22.1: 土曜に出た帰り（木曜の夜に出た行きの帰り。曜日は帰りの便が出た日）が 5 回とも空、30 分以上の停車 10 回 = 8.0 時間、確認待ち 7 件、読めない 3 行。"""
    thu = [l for l in sample["legs"] if l["dir"] == "back" and l["cycle_wd"] == 5]
    assert len(thu) == 5 and all(l["kind"] == "empty" for l in thu)
    assert sorted(l["cycle_dep"][:10] for l in thu) == ["2026-10-03", "2026-10-10", "2026-10-17", "2026-10-24", "2026-10-31"]
    assert all(l["cycle_dep"][11:13] >= "18" for l in thu)  # 夜に出た
    long = [s for s in sample["stops"] if s["place"] in ("p07", bos.UNCONFIRMED_PID) and s["minutes"] >= 30]  # 「辰巳」は確定するまで別の地点（p08）
    assert len(long) == 10 and sum(s["minutes"] for s in long) == 480
    assert {s["plate"] for s in long} == {"1234", "5678"}
    assert len(sample["confirm"]) == 7 and len(sample["unreadable"]) == 3
    assert {u["reason"] for u in sample["unreadable"]} == {"月が分かりません", "区間が読めません", "車番がありません"}  # SPEC §42: 時刻だけ読めないセルは区間を残すので、3 つ目は矢印の無いセル
    assert sample["paper"]["text"] == "紙の日報 3 枚（この版では読みません）"
    # 土曜以外に、規則 1 に当たる曜日・向きが無い
    from collections import Counter
    g: dict = {}
    for l in sample["legs"]:
        if l["kind"] != "unknown":
            g.setdefault((l["dir"], l["cycle_wd"]), []).append(l["kind"] == "empty")
    hit = [k for k, v in g.items() if len(v) >= 4 and sum(v) * 4 >= len(v) * 3]
    assert hit == [("back", 5)]
    assert Counter(c["kind"] for c in sample["confirm"]) == {"alias": 1, "column": 2, "link": 4}
    vs = {v["plate"]: v for v in sample["vehicles"]}
    assert len(vs) == 8 and sorted(v["cls"] for v in vs.values()).count("大型") == 2 and sorted(v["cls"] for v in vs.values()).count("4t") == 4
    assert [vs[p]["tachograph"] for p in ("1234", "5678")] == ["digital", "digital"] and vs["1234"]["gvw_kg"] >= 8000


def test_g70_sample_kpis(sample):
    k = sample["kpis"]
    # 判定できた区間は、実車か空車かが分かり、距離もある区間（距離の無い区間は、不明でなくても入らない）
    assert 0 < k["actual_rate"]["value"] < 1 and k["actual_rate"]["share"] <= 1 - sample["counts"]["unknown_ratio"] + 1e-3
    assert k["load_efficiency"]["value"] == pytest.approx(k["load_rate"]["value"] * k["large_actual_rate"]["value"])
    assert k["load_rate"]["legs"] < k["load_rate"]["of_large"]
    n = sample["national"]
    assert n["actual_rate_pct"] == 62.15 and n["load_efficiency_pct"] == 39.08 and "000040511074" in n["source"]
    assert n["caution"] == "全国の積載効率はトンキロの比で、自社の値と同じ式ではありません。比べられるのは実車率だけです"


# ---------------------------------------------------------------- G71 Claude に送る記録の形

def test_g71_claude_input_has_no_names(mapping):
    """列名と値の形の説明だけ。氏名・荷主名・地点名・車番・会社名を含まない。"""
    text = json.dumps([c["claude_input"] for c in mapping["columns"]], ensure_ascii=False)
    rows = list(csv.reader(open(RAW / "haisha_2026-10.csv", encoding="utf-8", newline="")))
    shippers = {c for r in rows[1:] for c in r[2:] if c.startswith("架空 ")}
    assert shippers
    banned = list(shippers) + [
        "加古川", "江東", "辰巳", "営業所", "播磨", "ライン運輸", "運転者", "神戸", "架空", "D01", "D02",
        "1234", "5678", "9001", "9002", "3344", "3355", "7788", "7799", "2026-10",
    ]
    for w in banned:
        assert w not in text, w
    allowed_keys = {"source", "column", "row_count", "non_blank_ratio", "shape_summary"}
    for c in mapping["columns"]:
        assert set(c["claude_input"]) == allowed_keys
        assert all(isinstance(v, (int, float)) for v in c["claude_input"]["shape_summary"].values())
    # 実際の値が入るのは、元の CSV と、画面用の直した結果だけ
    for c in mapping["columns"]:
        assert "claude_proposal" in c and c["claude_agrees"] in (True, False)


def test_g71_shape_summary_never_contains_values():
    s = own.shape_summary(["加古川営業所", "辰巳A棟", "江東区辰巳", ""])
    assert set(s) == {"漢字・かなだけの名称", "英数字をふくむ文字列"} and s["漢字・かなだけの名称"] == pytest.approx(0.6667, abs=1e-3)
    assert own.shape_summary(["2026-10-06 20:10"]) == {"YYYY-MM-DD HH:MM の日時": 1.0}
    assert own.shape_ratio(["1", "2", "3"], "code12") == pytest.approx(2 / 3)


# ---------------------------------------------------------------- サンプルの作り方

def test_sample_is_deterministic_and_matches_files(sample):
    """作り直しても同じ結果になり、置いてある JSON と一致する（違うときは tools/build_own_sample.py を実行し直す）。"""
    raw_before = {p.name: p.read_bytes() for p in RAW.glob("*.csv")}
    a = bos.build()
    b = bos.build()
    assert a == b
    assert {p.name: p.read_bytes() for p in RAW.glob("*.csv")} == raw_before, "元の CSV が作り直しで変わった"
    assert json.loads(json.dumps(a, ensure_ascii=False)) == sample, "web/data/own_sample.json が古い。uv run python tools/build_own_sample.py を実行してください"


def test_sample_files_use_lf_and_expected_columns():
    for p in RAW.glob("*.csv"):
        assert b"\r" not in p.read_bytes(), p.name
    assert sorted(p.name for p in RAW.glob("*.csv")) == ["digitaco_1234.csv", "digitaco_5678.csv", "haisha_2026-10.csv"]
    head = (RAW / "digitaco_1234.csv").read_text(encoding="utf-8").splitlines()[0]
    assert head == "KBN,ST_TIME,ED_TIME,ODO_KM,JK_FLG,POS_NAME,DRV_CD,X9"
    hh = (RAW / "haisha_2026-10.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert hh[:3] == ["車番", "車種", "10/1(木)"] and hh[-1] == "13日" and len(hh) == 34
    assert b"\r" not in (WEB / "data" / "own_sample.json").read_bytes()


def test_sample_haisha_alias_and_digitaco_parse():
    rows = list(csv.DictReader(open(RAW / "digitaco_5678.csv", encoding="utf-8", newline="")))
    r = own.parse_digitaco(rows, lambda n: bos.resolve_place(n)[0], "5678")
    assert len(r["runs"]) == 28 and len(r["stops"]) == 14 and r["errors"] == []  # 1 運行 = 行きの走行 2 つ（途中で休息）+ 停車 + 帰りの走行 2 つ + 停車
    assert all(x["jk"] in (0, 1) for x in r["runs"])
    assert {s["place"] for s in r["stops"]} == {"p01", "p07", bos.UNCONFIRMED_PID}
    assert bos.resolve_place("辰巳倉庫") == (bos.UNCONFIRMED_PID, "辰巳", True) and bos.resolve_place("江東区辰巳A棟")[2] is False


# ---------------------------------------------------------------- 画面（HTML と JS の文言）

def test_ui_view_button_and_panels():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'data-view="own"' in html and "自社の運行" in html
    for ident in ('id="own-view"', 'id="own-right"', 'id="own-notes"', 'id="own-draft"'):
        assert ident in html
    assert html.index('<script src="own.js">') < html.index('<script src="app.js">')
    assert 'href="own.css"' in html
    # 常に出す文
    for t in ("架空の会社のサンプルです", "配車表やデジタコの CSV をここに置くと、この画面の中だけで読みます。外には送りません。"   # 文言の直し 2026-11-07
                   , "列の対応づけの Claude の提案は、開発のときに作った記録です"):
        assert t in html, t
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "'own'" in app and "Own.enter" in app and "Own.layers" in app


def test_ui_own_js_wording():
    js = (WEB / "own.js").read_text(encoding="utf-8")
    assert "AI の案: まだありません" in js   # 文言の直し 2026-11-07  # 提案が null のときの表示
    assert "この版ではサンプルだけを扱います" not in js and "OwnCalc.analyze" in js  # ファイルを落とすと、JS で計算して表示する（SPEC §35）
    assert "空き探しの画面にある架空の便 T7 は、この下書きの使い方を見せるための例です" in js
    assert "帰り便の下書きにする" in (WEB / "data" / "own_sample.json").read_text(encoding="utf-8")
    assert "Find.setRole('carrier')" in js and "App.setView('find')" in js
    assert "PathLayer" in js and "全国の荷の流れを地図に重ねる" in js and "Market.flowLayers" in (WEB / "app.js").read_text(encoding="utf-8")
    assert "紙の日報" in (WEB / "data" / "own_sample.json").read_text(encoding="utf-8")
