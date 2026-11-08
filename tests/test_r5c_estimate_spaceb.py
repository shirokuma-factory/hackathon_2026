"""r5c/r5d の指摘: 地図の画面の比べる欄がフォークリフトを見る、書面の荷役の人手、OSM 由来の配布データの表示。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from conftest import make_shipment, row_of
from shipcompare import spaceb
from shipcompare.estimate import ST_CHECK, ST_OK, estimate_all
from shipcompare.models import Item

from test_v21_backhaul import SHIPPER, s6_ship, t7_board

ROOT = Path(__file__).resolve().parents[1]
HEAVY = [Item(1, 110, 110, 150, 400)]


def _handling_rows(**kw):
    rows = estimate_all(make_shipment(HEAVY, **kw))
    return [r for r in rows if r.mode == "rosen" or r.mode.startswith("kashikiri")]


def test_no_forklift_makes_rosen_and_charter_check_for_400kg_piece():
    rows = _handling_rows(forklift=False, manual_load=True)
    assert rows
    for r in rows:
        if r.status == ST_CHECK:
            assert "荷役（フォークリフト）を確かめてください" in r.reasons, r.mode
            assert "handling_check" in r.codes()
    assert any(r.mode == "rosen" and r.status == ST_CHECK for r in rows)
    assert any(r.mode.startswith("kashikiri") and r.status == ST_CHECK for r in rows)
    assert not [r for r in rows if r.status == ST_OK]


def test_forklift_yes_or_unknown_or_light_piece_unchanged():
    for fk in (True, None):
        assert "handling_check" not in {c for r in _handling_rows(forklift=fk) for c in r.codes()}
    light = estimate_all(make_shipment([Item(1, 30, 30, 30, 50)], forklift=False))
    assert "handling_check" not in {c for r in light for c in r.codes()}
    assert row_of(light, "rosen").status == ST_OK


def test_forklift_field_reaches_the_estimate_api():
    from shipcompare.storage import shipment_to_dict
    assert shipment_to_dict(make_shipment(HEAVY, forklift=False))["forklift"] is False
    js = (ROOT / "web" / "work.js").read_text(encoding="utf-8")
    assert "data-f=\"forklift\"" in js


def _contract(**kw):
    b = t7_board()
    h = b.hold("T7", s6_ship(), hold_minutes=60, shipper=SHIPPER)["hold_id"]
    b.decide(h, "accept")
    return b.contract(h, **kw)


def test_contract_heavy_piece_uses_lift_and_asks_for_availability():
    d = _contract()
    by = {i["key"]: i for i in d["items"]}
    assert "フォークリフトかパワーゲートで積み降ろす" in by["handling"]["value"]
    assert "2 人" not in by["handling"]["value"]
    assert by["lift"]["value"] is None and by["lift"]["label"] in d["missing"] and d["ready"] is False
    d2 = _contract(fuel_surcharge="なし", payment_method=next(c for c in spaceb.load_demo()["carriers"] if c["id"] == "C4")["payment_options"][0],
                   lift_equipment="荷主側にフォークリフトあり（架空）")
    assert d2["ready"] is True


def test_contract_light_piece_has_no_lift_item():
    b = t7_board()
    sh = s6_ship()
    light = spaceb.BShipment((spaceb.Piece(1, Decimal(300), Decimal(300), Decimal(300), Decimal(40)),), sh.dest_code,
                             window_from=sh.window_from, window_to=sh.window_to)
    h = b.hold("T7", light, hold_minutes=60, shipper=SHIPPER)["hold_id"]
    b.decide(h, "accept")
    d = b.contract(h)
    assert "lift" not in {i["key"] for i in d["items"]}


def test_osm_derived_web_data_has_license_and_attribution():
    names = ["flow_lines", "trucks_roads", "hubs", "routes_rail", "routes_meta", "osm_points"]
    for n in names:
        d = json.loads((ROOT / "web" / "data" / f"{n}.json").read_text(encoding="utf-8"))
        assert d.get("license") and d.get("attribution"), n
        assert "ODbL" in d["license"] or n == "routes_meta", n
        assert "OpenStreetMap" in d["attribution"], n
