"""SPEC 34.1〜34.3: 床の超過・出発済み・途中降ろしの書面（外部監査の再現 1〜3）。時計は固定。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shipcompare import spaceb  # noqa: E402

NOW = datetime(2026, 11, 5)


def clock():
    return NOW


def reg_body():
    return dict(origin_code="13104", dest_code="28110", vehicle="4t",
                depart_at="2026-11-13T17:00", arrive_at="2026-11-14T10:00",
                weight_slack_kg=2000, unit_footprint_mm=[6000, 2000],
                clear_height_mm=2100, max_piece_kg=300, drop_points=["28110"])


def ship(count=1, l=30, w=30, h=30, kg=1, dest="28110", origin="13104"):
    return spaceb.parse_shipment(dict(
        shipment=dict(items=[dict(count=count, length_cm=l, width_cm=w, height_cm=h, weight_kg=kg)]),
        dest_code=dest, origin_code=origin))


def board_with_registered():
    base = spaceb.build_registered_trip(reg_body())
    base["price_ex_tax_yen"] = 10000
    b = spaceb.Board([], clock=clock)
    return b, b.register(base)


def test_g144_repro1_floor_over_is_rejected():
    b, t = board_with_registered()
    big = ship(count=20, l=600, w=200, h=200, kg=1)  # 240m2、便は 12m2
    r = b.match_all(big)[0]
    assert not r["ok"] and any("床の空き 12.00m² に対して荷の床 240.00m²" in x for x in r["reasons"])
    with pytest.raises(spaceb.BoardError, match="床の空き"):
        b.hold(t["id"], big)


def test_g144_floor_restored_after_decline_expire_and_kept_on_accept():
    b, t = board_with_registered()
    s = ship(count=10, l=100, w=100, h=100, kg=5)  # 10m2, 50kg
    h = b.hold(t["id"], s)
    cur = b.trips()[0]
    assert cur["remaining_floor_m2"] == 2.0 and cur["remaining_slack_kg"] == 1950
    # 残り 2m2 に 10m2 は載らない
    with pytest.raises(spaceb.BoardError):
        b.hold(t["id"], s)
    b.decide(h["hold_id"], "decline")
    cur = b.trips()[0]
    assert cur["remaining_floor_m2"] == 12.0 and cur["remaining_slack_kg"] == 2000
    h2 = b.hold(t["id"], s)
    b.decide(h2["hold_id"], "accept")
    cur = b.trips()[0]
    assert cur["remaining_floor_m2"] == 2.0 and cur["remaining_slack_kg"] == 1950


def test_g144_floor_restored_on_expire():
    now = [NOW]
    base = spaceb.build_registered_trip(reg_body())
    base["price_ex_tax_yen"] = 10000
    b = spaceb.Board([], clock=lambda: now[0])
    t = b.register(base)
    b.hold(t["id"], ship(count=10, l=100, w=100, h=100, kg=5))
    assert b.trips()[0]["remaining_floor_m2"] == 2.0
    now[0] = datetime(2026, 11, 5, 1, 0)
    cur = b.trips()[0]
    assert cur["remaining_floor_m2"] == 12.0 and cur["remaining_slack_kg"] == 2000


def test_g144_floor_checked_for_demo_trips_without_free_value():
    trip = next(t for t in spaceb.load_trips() if t.get("floor_free_m2") is None)
    big = ship(count=1, l=100, w=100, h=100, kg=1, dest=trip["drop_points"][0], origin=None)
    big = spaceb.BShipment(big.pieces and tuple(spaceb.Piece(2000, p.length_mm, p.width_mm, p.height_mm, p.weight_kg)
                                                for p in big.pieces), big.dest_code)
    ok, reasons = spaceb.match(big, trip)
    assert any("床の空き" in r for r in reasons)


def test_g144_repro2_departed_trip_rejected():
    base = spaceb.build_registered_trip(reg_body())
    base["price_ex_tax_yen"] = 10000
    old = dict(base, id="PAST", depart_at="2020-01-01T17:00", arrive_at="2020-01-02T10:00")
    b = spaceb.Board([old], clock=clock)
    s = ship()
    assert any("この便は出発済みです" in x for x in b.match_all(s)[0]["reasons"])
    with pytest.raises(spaceb.BoardError, match="出発済み"):
        b.hold("PAST", s)


def test_g144_hold_deadline_capped_at_departure():
    base = spaceb.build_registered_trip(reg_body())
    base["price_ex_tax_yen"] = 10000
    near = dict(base, id="NEAR", depart_at="2026-11-05T00:10", arrive_at="2026-11-05T10:00")
    b = spaceb.Board([near], clock=clock)
    h = b.hold("NEAR", ship())  # 既定 30 分だが出発は 10 分後
    assert h["expires_at"] == "2026-11-05T00:10:00"
    h2 = b.hold("NEAR", ship(), hold_until="2026-11-07T00:00")
    assert h2["expires_at"] == "2026-11-05T00:10:00"
    # 出発の時刻ちょうどは出発済み
    b2 = spaceb.Board([near], clock=lambda: datetime(2026, 11, 5, 0, 10))
    with pytest.raises(spaceb.BoardError, match="出発済み"):
        b2.hold("NEAR", ship())


def test_g144_repro3_contract_destination_follows_shipment():
    t = next(t for t in spaceb.load_trips() if t["id"] == "T7")
    s = spaceb.parse_shipment(dict(
        shipment=dict(items=[dict(count=1, length_cm=30, width_cm=30, height_cm=30, weight_kg=1)]),
        dest_code="27128", origin_code="13108"))
    b = spaceb.Board([t], clock=clock)
    h = b.hold("T7", s)
    b.decide(h["hold_id"], "accept")
    by = {i["key"]: i["value"] for i in b.contract(h["hold_id"])["items"]}
    assert spaceb.muni_name("27128") in by["transport"]
    assert spaceb.muni_name("28210") not in by["transport"]
    assert spaceb.muni_name("28210") in by["trip"] and spaceb.muni_name(t["origin_code"]) in by["trip"]
