"""SPEC 34.6 と洗い出し r1 の確認済み指摘（F2/TQ-1・TQ-2・S1・N3・S2・F3・F04・F4・F7）。時計は demo_now か固定。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shipcompare import spaceb, tariffs  # noqa: E402

from test_v21_backhaul import s6_body, s6_ship  # noqa: E402,F401
from test_v3_web import get, post, srv  # noqa: E402,F401  (srv は fixture)

DEMO = datetime(2026, 11, 10, 9, 0)


def pieces(count, l, w, h, kg, dest="28110", origin=None):
    return spaceb.parse_shipment(dict(
        shipment=dict(items=[dict(count=count, length_cm=l, width_cm=w, height_cm=h, weight_kg=kg)]),
        dest_code=dest, origin_code=origin))


class Frozen2027(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2027, 1, 1, 12, 0, 0)

    @classmethod
    def today(cls):
        return cls(2027, 1, 1, 12, 0, 0)


# ---------------------------------------------------------------- F2 / TQ-1 / TQ-2

def test_demo_now_is_in_carriers_json_and_label():
    assert spaceb.demo_now() == DEMO
    assert spaceb.demo_now_label() == "デモの時刻: 11/10 09:00（架空の便に合わせています）"


def test_default_board_clock_starts_at_demo_now_and_reset_returns():
    b = spaceb.Board()
    assert 0 <= (b.now() - DEMO).total_seconds() < 60
    b.reset()
    assert 0 <= (b.now() - DEMO).total_seconds() < 60


def _s6_flow():
    """S6 の荷が T7 に載り、仮予約 → 承諾 → 書面まで通る。結果を比べるために返す。"""
    b = spaceb.Board()
    res = {r["trip_id"]: (r["ok"], r["reasons"]) for r in b.match_all(s6_ship())}
    h = b.hold("T7", s6_ship(), hold_minutes=60)
    b.decide(h["hold_id"], "accept")
    c = b.contract(h["hold_id"], "なし", "前払い")
    return res, c["items"]


def test_find_flow_is_same_when_real_clock_is_2027(monkeypatch):
    before = _s6_flow()
    assert before[0]["T7"][0] is True
    monkeypatch.setattr(spaceb, "datetime", Frozen2027)
    after = _s6_flow()
    assert after == before  # 実際の時計が 2027 年でも同じ結果（SPEC §34.6）


def test_register_trip_is_checked_against_board_clock(monkeypatch):
    monkeypatch.setattr(spaceb, "datetime", Frozen2027)
    body = dict(origin_code="13104", dest_code="28110", vehicle="4t", depart_at="2026-11-13T17:00",
                arrive_at="2026-11-14T10:00", weight_slack_kg=2000, unit_footprint_mm=[6000, 2000],
                clear_height_mm=2100, max_piece_kg=300, drop_points=["28110"])
    b = spaceb.Board()
    t = spaceb.build_registered_trip(body, now=b.now())  # 盤の時計（11/10）より後なので通る
    assert t["depart_at"] == "2026-11-13T17:00"
    with pytest.raises(ValueError, match="出発日時"):
        spaceb.build_registered_trip(dict(body, depart_at="2026-11-09T17:00", arrive_at="2026-11-09T20:00"), now=b.now())


def test_api_trips_returns_demo_clock_and_find_flow_in_2027(srv, monkeypatch):  # noqa: F811
    mod, port = srv
    monkeypatch.setattr(spaceb, "datetime", Frozen2027)
    post(port, "/api/b/reset", {})
    _, r = get(port, "/api/b/trips")
    assert r["demo_now"] == "2026-11-10T09:00" and r["board_now"].startswith("2026-11-10T09:")
    assert r["demo_now_label"] == "デモの時刻: 11/10 09:00（架空の便に合わせています）"
    st, m = post(port, "/api/b/match", s6_body())
    assert st == 200 and {x["trip_id"]: x["ok"] for x in m["results"]}["T7"] is True
    st, h = post(port, "/api/b/hold", dict(s6_body(), trip_id="T7", hold_minutes=30))
    assert st == 200 and h["hold"]["created_at"].startswith("2026-11-10T09:")
    # 盤の時計より前に出発する便は登録できない
    reg = dict(origin_code="13104", dest_code="28110", vehicle="4t", depart_at="2026-11-09T17:00",
               arrive_at="2026-11-09T20:00", weight_slack_kg=2000, unit_footprint_mm=[6000, 2000],
               clear_height_mm=2100, max_piece_kg=300, drop_points=["28110"], price_ex_tax_yen=10000)
    st, e = post(port, "/api/b/register_trip", reg)
    assert st == 400 and "出発日時" in e["error"]
    st, ok = post(port, "/api/b/register_trip", dict(reg, depart_at="2026-11-11T17:00", arrive_at="2026-11-12T08:00"))
    assert st == 200
    post(port, "/api/b/reset", {})


# ---------------------------------------------------------------- S1

def test_s1_shelf_trip_floor_is_unit_footprint_not_truck_floor():
    t1 = next(t for t in spaceb.load_trips() if t["id"] == "T1")
    assert "floor_free_m2" not in t1 and t1["floor_total_m2"] == 28.0
    boxes20 = pieces(20, 100, 70, 10, 0.5)  # 14m²（高さ・重さは通る小ささ）
    ok, reasons = spaceb.match(boxes20, t1)
    assert not ok and any("床の空き 0.88m²" in r for r in reasons)
    b = spaceb.Board()
    with pytest.raises(spaceb.BoardError, match="床の空き"):
        b.hold("T1", pieces(20, 100, 70, 10, 0.5))
    one = pieces(1, 100, 70, 10, 0.5)  # 0.70m² は 1 口 0.88m² に入る
    assert spaceb.match(one, t1)[0] is True


def test_s1_inside_rb_upper_uses_unit_footprint_and_other_types_use_truck_floor():
    t2 = next(t for t in spaceb.load_trips() if t["id"] == "T2")
    assert float(spaceb._trip_floor(t2)) == pytest.approx(0.94 * 0.745)
    t4 = next(t for t in spaceb.load_trips() if t["id"] == "T4")  # free_bed は入れた床の空き（車の床 6.0m² ではない。r1b #4）
    assert spaceb._trip_floor(t4) == 3.0


def test_s1_hold_on_shelf_reduces_unit_floor():
    b = spaceb.Board()
    b.hold("T1", pieces(1, 100, 70, 10, 0.5))
    t1 = next(t for t in b.trips() if t["id"] == "T1")
    assert t1["remaining_floor_m2"] == pytest.approx(0.88 - 0.70) and t1["floor_free_m2"] == pytest.approx(0.88)


# ---------------------------------------------------------------- F3

def test_f3_every_trip_has_a_numeric_floor_for_the_card():
    trips = spaceb.Board().trips()
    for t in trips:
        assert isinstance(t["floor_free_m2"], float) and isinstance(t["remaining_floor_m2"], float), t["id"]
    assert next(t for t in trips if t["id"] == "T1")["floor_is_unit"] is True
    assert next(t for t in trips if t["id"] == "T4")["floor_is_unit"] is False


# ---------------------------------------------------------------- N3

def test_n3_register_vehicle_floor_equals_footprint_product():
    for k, v in spaceb.REGISTER_VEHICLES.items():
        assert v["floor_total_m2"] == pytest.approx(v["footprint_mm"][0] * v["footprint_mm"][1] / 1e6, abs=0.01), k
    assert spaceb.REGISTER_VEHICLES["10t"]["floor_total_m2"] == pytest.approx(23.0, abs=0.1)
    # 2t は data/vehicle_decks.json の 2t ショートの公表例（3000×1700mm、5.1m²）にそろえた（洗い出し r1 N7）
    assert spaceb.REGISTER_VEHICLES["2t"]["floor_total_m2"] == pytest.approx(tariffs.deck_2t()["floor_m2"], abs=0.01)


def test_n3_cargo_floor_over_deck_is_refused_on_registered_10t():
    body = dict(origin_code="13104", dest_code="28110", vehicle="10t", depart_at="2026-11-13T17:00",
                arrive_at="2026-11-14T10:00", weight_slack_kg=4500, unit_footprint_mm=[9600, 2400],
                clear_height_mm=2300, max_piece_kg=500, drop_points=["28110"])
    b = spaceb.Board([], clock=lambda: DEMO)
    t = spaceb.build_registered_trip(body)
    t["price_ex_tax_yen"] = 1
    b.register(t)
    ok = pieces(1, 900, 200, 100, 100)  # 18m² は載る
    assert b.match_all(ok)[0]["ok"] is True
    over = pieces(24, 100, 100, 100, 10)  # 24m² > 23.04m²
    r = b.match_all(over)[0]
    assert not r["ok"] and any("床の空き 23.04m²" in x for x in r["reasons"])


# ---------------------------------------------------------------- S2

def _reg(**kw):
    body = dict(origin_code="13104", dest_code="28110", vehicle="2t", depart_at="2026-11-13T17:00",
                arrive_at="2026-11-14T10:00", weight_slack_kg=1000, unit_footprint_mm=[3000, 1700],
                clear_height_mm=1700, max_piece_kg=150, drop_points=["28110"])
    body.update(kw)
    return spaceb.build_registered_trip(body)


def test_s2_registration_is_bounded_by_vehicle_deck():
    _reg()  # 上限ちょうどは通る
    _reg(unit_footprint_mm=[1700, 3000])  # 縦横の入れ替えは許す
    for kw, word in ((dict(unit_footprint_mm=[15000, 1500]), "荷台"), (dict(unit_footprint_mm=[3100, 1700]), "荷台"),
                     (dict(clear_height_mm=3500), "高さ 1700mm"), (dict(max_piece_kg=800), "1個の重さの上限"),
                     (dict(weight_slack_kg=2500), "重さの余り")):
        with pytest.raises(ValueError, match=word):
            _reg(**kw)
    with pytest.raises(ValueError, match="3000×1700mm・高さ 1700mm です"):
        _reg(unit_footprint_mm=[15000, 1500], clear_height_mm=3500)


# ---------------------------------------------------------------- F04 / F4 / F7 / F6 / 時計の表示（画面の文字列）

def _src(name):
    return (ROOT / "web" / name).read_text(encoding="utf-8")


def test_screen_texts_in_find_js_and_css():
    js, css = _src("find.js"), _src("find_extra.css")
    assert "「仮予約と空きを最初の状態に戻す」で消えます" in js
    assert "。「最初の状態に戻す」で消えます" not in js.replace("「仮予約と空きを最初の状態に戻す」", "")
    for kind in ("寸法が合いません", "重さが合いません", "床が足りません", "日が合いません", "集荷の範囲外", "出発済み", "受けない品目"):
        assert kind in js
    assert "の料金（円・税抜）" in js and "price_unit" in js
    assert "デモの時刻: 11/10 09:00（架空の便に合わせています）" in js  # サーバーの文が無いときの決まった文
    assert "white-space: nowrap" in css and ".f-hbtn" in css and "flex-wrap: wrap" in css
