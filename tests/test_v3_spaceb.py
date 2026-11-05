"""版3の B 空きを探す・つなぐ（SPEC §16.2、受入条件 G36・G37）。

照合は境界の値（ちょうど / 少しだけ超える）で合否を確かめる。盤は同時の仮予約と期限切れを確かめる。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from shipcompare import spaceb
from shipcompare.spaceb import Board, BoardError, BShipment, Piece

from test_v3_web import JAPANESE, get, post, srv  # noqa: F401  (srv は fixture)

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 3, 9, 0, 0)


def trip(**kw):
    base = {
        "id": "X1", "carrier_id": "C1", "carrier_name": "架空試験運送", "space_type": "above_rb_rear",
        "clear_height_mm": 700, "unit_footprint_mm": [1100, 800], "max_piece_kg": 14, "weight_slack_kg": 300,
        "drop_points": ["28110"], "container_required": False, "rb_owner_consent": False,
        "max_upper_per_rb_kg": None, "shelf_fixed": False, "shelf_rated_kg": 250, "price_ex_tax_yen": 1000,
        "refused_categories": ["危険物"],
        "floor_free_m2": 100.0,  # 重さの試験が床で落ちないように広くしておく（棚の 1 口の床の試験は test_v38_booking_fixes）
    }
    base.update(kw)
    return base


def ship(count=1, l_mm=300, w_mm=300, h_mm=300, kg=10, dest="28110", **kw):
    d = Decimal
    return BShipment((Piece(count, d(str(l_mm)), d(str(w_mm)), d(str(h_mm)), d(str(kg))),), dest, **kw)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


# ---------------------------------------------------------------- データ

def test_spec16_2_carriers_json_is_fictional_and_well_formed():
    data = json.loads((ROOT / "data" / "demo" / "carriers.json").read_text(encoding="utf-8"))
    assert "架空" in data["note"] and data["fictional"] is True
    assert len(data["carriers"]) == 4  # C4 は SPEC §21.1 で足した
    assert all("架空" in c["name"] for c in data["carriers"])
    ids = {c["id"] for c in data["carriers"]}
    trips = data["trips"]
    assert 5 <= len(trips) <= 8
    points = json.loads((ROOT / "data" / "geo" / "municipality_points.json").read_text(encoding="utf-8"))["points"]
    for t in trips:
        assert t["carrier_id"] in ids
        assert t["space_type"] in spaceb.SPACE_TYPES
        assert t["origin_code"] in points and t["dest_code"] in points
        assert all(c in points for c in t["drop_points"])
        assert t["depart_at"].startswith("2026-11") and t["arrive_at"].startswith("2026-11")
        assert t["depart_at"] < t["arrive_at"]
        assert t["price_ex_tax_yen"] > 0
        if t["space_type"] != "free_bed":
            assert t["max_piece_kg"] <= 14
        if t["space_type"] == "inside_rb_upper":
            assert t["rb_owner_consent"] and t["max_upper_per_rb_kg"] <= 60
        if t["space_type"] in spaceb.SHELF_TYPES:
            assert t["shelf_rated_kg"] > 0
    assert {t["space_type"] for t in trips} == set(spaceb.SPACE_TYPES)
    assert len(spaceb.load_trips()) == len(trips)


# ---------------------------------------------------------------- G36: 照合の境界

def test_spec16_2_piece_weight_boundary():
    t = trip()
    assert spaceb.match(ship(kg=14), t)[0] is True
    ok, reasons = spaceb.match(ship(kg="14.1"), t)
    assert ok is False and any("1個の重さ" in r for r in reasons)


def test_spec16_2_height_boundary_clear_minus_50():
    t = trip(clear_height_mm=700)
    assert spaceb.match(ship(h_mm=650), t)[0] is True
    ok, reasons = spaceb.match(ship(h_mm=651), t)
    assert ok is False and any("高さ" in r for r in reasons)


def test_spec16_2_total_weight_boundary_uses_slack():
    t = trip(weight_slack_kg=100, shelf_rated_kg=500, max_piece_kg=14)
    assert spaceb.match(ship(count=10, kg=10), t)[0] is True  # 合計 100 ちょうど
    ok, reasons = spaceb.match(ship(count=11, kg=10), t)
    assert ok is False and any("重さの余り" in r for r in reasons)


def test_spec16_2_shelf_rated_is_used_when_smaller():
    t = trip(weight_slack_kg=400, shelf_rated_kg=100)
    assert spaceb.match(ship(count=10, kg=10), t)[0] is True
    ok, reasons = spaceb.match(ship(count=11, kg=10), t)
    assert ok is False and any("棚の耐荷重" in r for r in reasons)
    # 自由な荷台の空き（形 0）には棚の上限を使わない
    t2 = trip(space_type="free_bed", weight_slack_kg=400, shelf_rated_kg=None, max_piece_kg=30)
    assert spaceb.match(ship(count=11, kg=10), t2)[0] is True


def test_spec16_2_footprint_fits_in_either_rotation():
    t = trip(unit_footprint_mm=[1100, 800])
    assert spaceb.match(ship(l_mm=1100, w_mm=800), t)[0] is True
    assert spaceb.match(ship(l_mm=800, w_mm=1100), t)[0] is True  # 回すと収まる
    ok, reasons = spaceb.match(ship(l_mm=1101, w_mm=800), t)
    assert ok is False and any("底面" in r for r in reasons)
    assert spaceb.match(ship(l_mm=900, w_mm=900), t)[0] is False  # どちら向きでも片方が超える


def test_spec16_2_drop_point_and_container():
    t = trip(container_required=True)
    assert spaceb.match(ship(in_container=True), t)[0] is True
    ok, reasons = spaceb.match(ship(in_container=False), t)
    assert ok is False and any("容器" in r for r in reasons)
    ok, reasons = spaceb.match(ship(in_container=True, dest="13101"), t)
    assert ok is False and any("降ろせる地点" in r for r in reasons)


def test_spec16_2_inside_rb_upper_needs_consent_and_per_rb_limit():
    t = trip(space_type="inside_rb_upper", rb_owner_consent=True, max_upper_per_rb_kg=60, shelf_rated_kg=None,
             weight_slack_kg=300)
    assert spaceb.match(ship(count=6, kg=10), t)[0] is True  # 60kg ちょうど
    ok, reasons = spaceb.match(ship(count=7, kg=10), t)
    assert ok is False and any("上段の上限" in r for r in reasons)
    no_consent = dict(t, rb_owner_consent=False)
    ok, reasons = spaceb.match(ship(), no_consent)
    assert ok is False and any("同意" in r for r in reasons)


def test_spec16_2_refused_categories_fragile_and_shape():
    t = trip()
    assert spaceb.match(ship(category="衣類"), t)[0] is True
    ok, reasons = spaceb.match(ship(category="危険物"), t)
    assert ok is False and any("危険物" in r for r in reasons)
    assert spaceb.match(ship(fragile=True), t)[0] is False
    assert spaceb.match(ship(is_box=False), t)[0] is False


def test_spec16_2_reasons_are_all_listed_and_japanese():
    ok, reasons = spaceb.match(ship(kg=20, h_mm=900, dest="13101"), trip())
    assert ok is False and len(reasons) >= 3
    assert all(JAPANESE.search(r) for r in reasons)


# ---------------------------------------------------------------- G37: 仮予約

def test_spec16_2_hold_decrements_slack_and_rejects_over():
    b = Board([trip(weight_slack_kg=100, shelf_rated_kg=500)], clock=Clock())
    b.hold("X1", ship(count=6, kg=10))
    assert b.trips()[0]["remaining_slack_kg"] == 40
    with pytest.raises(BoardError) as e:
        b.hold("X1", ship(count=5, kg=10))
    assert "重さの余り" in str(e.value)
    b.hold("X1", ship(count=4, kg=10))  # 残りちょうどは通る
    assert b.trips()[0]["remaining_slack_kg"] == 0
    assert [r["ok"] for r in b.match_all(ship(kg=1))] == [False]


def test_spec16_2_two_concurrent_holds_on_last_capacity_only_one_wins():
    for _ in range(20):
        b = Board([trip(weight_slack_kg=100, shelf_rated_kg=500)], clock=Clock())
        barrier = threading.Barrier(2)
        results = []

        def worker():
            barrier.wait()
            try:
                results.append(("ok", b.hold("X1", ship(count=8, kg=10))["hold_id"]))
            except BoardError:
                results.append(("ng", None))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert sorted(r[0] for r in results) == ["ng", "ok"]
        assert b.trips()[0]["remaining_slack_kg"] == 20


def test_spec16_2_decline_restores_slack_and_accept_keeps_it():
    b = Board([trip(weight_slack_kg=100, shelf_rated_kg=100)], clock=Clock())
    h1 = b.hold("X1", ship(count=6, kg=10))["hold_id"]
    assert b.decide(h1, "decline")["state"] == "declined"
    t = b.trips()[0]
    assert (t["remaining_slack_kg"], t["remaining_shelf_kg"]) == (100, 100)
    h2 = b.hold("X1", ship(count=6, kg=10))["hold_id"]
    assert b.decide(h2, "accept")["state"] == "accepted"
    assert b.trips()[0]["remaining_slack_kg"] == 40
    with pytest.raises(BoardError):
        b.decide(h2, "decline")  # 決まった後は変えられない
    with pytest.raises(BoardError):
        b.decide("H99", "accept")
    with pytest.raises(BoardError):
        b.decide(h2, "maybe")


def test_spec16_2_hold_expires_and_restores_slack():
    clock = Clock()
    b = Board([trip(weight_slack_kg=100, shelf_rated_kg=100)], clock=clock)
    h = b.hold("X1", ship(count=6, kg=10), hold_minutes=15)
    assert h["expires_at"] == (T0 + timedelta(minutes=15)).isoformat(timespec="seconds")
    clock.advance(14)
    assert b.trips()[0]["remaining_slack_kg"] == 40
    clock.advance(1)  # ちょうど期限
    assert b.trips()[0]["remaining_slack_kg"] == 100
    assert b.holds()[0]["state"] == "expired"
    with pytest.raises(BoardError) as e:
        b.decide(h["hold_id"], "accept")
    assert "期限" in str(e.value)
    assert b.trips()[0]["remaining_slack_kg"] == 100  # 二重に戻さない
    # 期限切れの後は同じ量をまた仮予約できる
    b.hold("X1", ship(count=10, kg=10))


def test_spec16_2_accepted_hold_does_not_expire():
    clock = Clock()
    b = Board([trip(weight_slack_kg=100, shelf_rated_kg=100)], clock=clock)
    h = b.hold("X1", ship(count=6, kg=10), hold_minutes=5)["hold_id"]
    b.decide(h, "accept")
    clock.advance(600)
    assert b.trips()[0]["remaining_slack_kg"] == 40


def test_spec16_2_hold_input_errors():
    b = Board([trip()], clock=Clock())
    for bad in (0, -1, True, "30", spaceb.MAX_HOLD_MINUTES + 1):
        with pytest.raises(BoardError):
            b.hold("X1", ship(), hold_minutes=bad)
    with pytest.raises(BoardError):
        b.hold("Z9", ship())


def test_spec16_2_reset_restores_initial_state():
    b = Board([trip(weight_slack_kg=100, shelf_rated_kg=100)], clock=Clock())
    b.hold("X1", ship(count=6, kg=10))
    b.reset()
    assert b.holds() == [] and b.trips()[0]["remaining_slack_kg"] == 100
    assert b.hold("X1", ship())["hold_id"] == "H1"


# ---------------------------------------------------------------- API が関数の結果と一致する

def body_of(items, dest="28110", **kw):
    return {"shipment": {"items": items}, "dest_code": dest, **kw}


def small_items(count=2, kg=5):
    return [{"count": count, "length_cm": 30, "width_cm": 30, "height_cm": 30, "weight_kg": kg}]


def test_spec16_2_api_trips_match_direct(srv):  # noqa: F811
    mod, port = srv
    post(port, "/api/b/reset", {})
    status, r = get(port, "/api/b/trips")
    assert status == 200
    direct = spaceb.load_trips()
    assert [t["id"] for t in r["trips"]] == [t["id"] for t in direct]
    assert "架空" in r["note"] and "架空" in r["fiction_notice"] and "運送会社が決めます" in r["booking_notice"]
    assert all("架空" in c["name"] for c in r["carriers"])


def test_spec16_2_api_match_parity_with_functions(srv):  # noqa: F811
    mod, port = srv
    post(port, "/api/b/reset", {})
    cases = [
        body_of(small_items(), "28110"),
        body_of(small_items(1, 14), "28110"),
        body_of(small_items(1, 14.1), "28110"),
        body_of([{"count": 1, "length_cm": 30, "width_cm": 30, "height_cm": 80, "weight_kg": 5}], "13103"),
        body_of(small_items(), "04101", category="危険物"),
    ]
    for body in cases:
        status, r = post(port, "/api/b/match", body)
        assert status == 200, r
        s = spaceb.parse_shipment(body)
        direct = [(t["id"],) + spaceb.match(s, t) for t in spaceb.load_trips()]
        assert [(x["trip_id"], x["ok"], x["reasons"]) for x in r["results"]] == direct
    # 少なくとも1つは合い、1つは合わない
    status, r = post(port, "/api/b/match", cases[0])
    assert any(x["ok"] for x in r["results"]) and any(not x["ok"] for x in r["results"])
    assert all(x["reasons"] for x in r["results"] if not x["ok"])


def test_spec16_2_api_hold_decide_flow_and_reset(srv):  # noqa: F811
    mod, port = srv
    post(port, "/api/b/reset", {})
    trip_t1 = next(t for t in spaceb.load_trips() if t["id"] == "T1")
    # T1: 余り 300kg、棚 250kg。合計 250kg ちょうどは通り、1kg 足すと断る
    full = body_of([{"count": 25, "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_kg": 10}], "28110")  # T1 の 1 口の床 0.88m² に入る小ささ
    status, r = post(port, "/api/b/hold", dict(full, trip_id="T1", hold_minutes=30))
    assert status == 200, r
    hold = r["hold"]
    assert hold["state"] == "held" and hold["weight_kg"] == 250
    status, r = post(port, "/api/b/hold", dict(body_of(small_items(1, 1), "28110"), trip_id="T1"))
    assert status == 400 and JAPANESE.search(r["error"]) and "棚の耐荷重" in r["error"]
    _, trips = get(port, "/api/b/trips")
    t1 = next(t for t in trips["trips"] if t["id"] == "T1")
    assert t1["remaining_slack_kg"] == trip_t1["weight_slack_kg"] - 250 and t1["remaining_shelf_kg"] == 0
    assert [h["hold_id"] for h in trips["holds"]] == [hold["hold_id"]]
    status, r = post(port, "/api/b/decide", {"hold_id": hold["hold_id"], "decision": "decline"})
    assert status == 200 and r["hold"]["state"] == "declined"
    _, trips = get(port, "/api/b/trips")
    t1 = next(t for t in trips["trips"] if t["id"] == "T1")
    assert t1["remaining_slack_kg"] == trip_t1["weight_slack_kg"] and t1["remaining_shelf_kg"] == trip_t1["shelf_rated_kg"]
    status, r = post(port, "/api/b/hold", dict(full, trip_id="T1"))
    assert status == 200
    status, r2 = post(port, "/api/b/decide", {"hold_id": r["hold"]["hold_id"], "decision": "accept"})
    assert status == 200 and r2["hold"]["state"] == "accepted"
    assert post(port, "/api/b/reset", {})[0] == 200
    _, trips = get(port, "/api/b/trips")
    assert trips["holds"] == []
    assert next(t for t in trips["trips"] if t["id"] == "T1")["remaining_slack_kg"] == trip_t1["weight_slack_kg"]


def test_spec16_2_api_errors_are_400_japanese(srv):  # noqa: F811
    mod, port = srv
    post(port, "/api/b/reset", {})
    bad = [
        ("/api/b/match", {}),
        ("/api/b/match", {"shipment": {"items": []}, "dest_code": "28110"}),
        ("/api/b/match", {"shipment": {"items": [{"count": 0, "length_cm": 1, "width_cm": 1, "height_cm": 1,
                                                  "weight_kg": 1}]}, "dest_code": "28110"}),
        ("/api/b/match", body_of(small_items())  | {"dest_code": None}),
        ("/api/b/match", body_of(small_items(), fragile="yes")),
        ("/api/b/hold", dict(body_of(small_items()), trip_id="Z9")),
        ("/api/b/hold", dict(body_of(small_items()), trip_id="T1", hold_minutes=0)),
        ("/api/b/decide", {"hold_id": "H99", "decision": "accept"}),
        ("/api/b/decide", {"hold_id": "H99", "decision": "x"}),
        ("/api/b/reset", []),
    ]
    for path, body in bad:
        status, r = post(port, path, body)
        assert status == 400, (path, body, r)
        assert JAPANESE.search(r["error"]), r
    assert get(port, "/api/b/match")[0] == 405
    assert post(port, "/api/b/trips", {})[0] == 405
