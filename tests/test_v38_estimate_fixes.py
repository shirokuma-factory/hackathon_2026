"""洗い出し r1 の確認済みの指摘（N1・N2・E1・E2・F01・F03・F09・N6・N7）の試験。"""

from __future__ import annotations

import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from shipcompare import geo, loadplan
from shipcompare.estimate import estimate_all, kashikiri_candidates
from shipcompare.models import Item
from shipcompare.storage import shipment_to_dict
from shipcompare.tariffs import deck_2t, load_ferry_only, load_kashikiri, load_tariff, load_vehicle_decks

from conftest import make_shipment

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def web():
    spec = importlib.util.spec_from_file_location("serve_web", ROOT / "tools" / "serve_web.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["serve_web"] = mod
    spec.loader.exec_module(mod)
    return mod


def _est(web, items, origin, dest):
    s = make_shipment(items)
    return web.api_estimate({"shipment": shipment_to_dict(s), "origin_code": origin, "dest_code": dest})


def _layer_amount(items, origin, dest):
    s = make_shipment(items)
    kg = float(sum(i.count * i.weight_kg for i in s.items))
    fare = geo.fare_layer(origin, kg, load_tariff("kashikiri"), [dest], items=loadplan.items_from_shipment(s))
    return fare[0]


def _first_kashikiri(est):
    return next(r for r in est["rows"] if r["group"] == "kashikiri")


# ---------------------------------------------------------------- N1
@pytest.mark.parametrize("origin,dest", [("08201", "04101"), ("10201", "27128"), ("13104", "27128"), ("13104", "04101"),
                                         ("12101", "23106")])
def test_n1_card_amount_equals_map_fill_amount(web, origin, dest):
    items = [Item(3, 120, 50, 50, 40)]
    est = _est(web, items, origin, dest)
    row = _first_kashikiri(est)
    assert row["amount"] is not None
    assert row["amount"] == _layer_amount(items, origin, dest)
    info = geo.drive_info(origin, dest)
    assert est["distance_km"] == info["km"]
    assert est["osrm_kashikiri"]["amount"] == row["amount"]
    assert est["kashikiri_basis"] == "road"  # 額の距離の基準は道路距離（デモの区間の組でも同じ）


def test_n1_pair_not_in_demo_is_other_route_and_demo_pair_keeps_table_route(web):
    mito = _est(web, [Item(3, 120, 50, 50, 40)], "08201", "04101")
    assert mito["demo_route"] is None and mito["shipment"]["route"] == "other"
    tokyo = _est(web, [Item(3, 120, 50, 50, 40)], "13104", "04101")
    assert tokyo["demo_route"] == "kanto_tohoku" and tokyo["shipment"]["route"] == "kanto_tohoku"
    # 同じ着地でも発地が違えば額が違う（想定距離 350km 固定ではない）
    assert _first_kashikiri(mito)["amount"] != _first_kashikiri(tokyo)["amount"]


def test_n1_demo_pair_puts_assumed_distance_amount_in_note(web):
    est = _est(web, [Item(3, 120, 50, 50, 40)], "13104", "04101")  # 想定 350km と道路距離は違う
    row = _first_kashikiri(est)
    assert any("想定距離 350km" in n for n in row["notes"])
    assumed = estimate_all(make_shipment([Item(3, 120, 50, 50, 40)], route="kanto_tohoku"))
    a = next(r for r in assumed if r.mode == "kashikiri_2t")
    assert f"{a.amount:,}円" in " ".join(row["notes"])
    assert a.amount != row["amount"]


# ---------------------------------------------------------------- N2
def test_n2_hokkaido_and_islands_are_ferry_routes():
    for code in ("01101", "01202", "15224", "32528", "37324", "42210", "42209", "47201", "13421"):
        assert geo.is_ferry_only_destination(code), code
        info = geo.drive_info("13104", code)
        if info is not None:
            assert (info["ferry"] or info["no_route"]) and info["days"] is None, code
    for code in ("13101", "28226", "35305", "34215", "38356", "23106"):  # 橋でつながる島・本州は対象外
        assert not geo.is_ferry_only_destination(code), code


def test_n2_speed_rule_is_kept():
    assert geo.is_ferry_route(24.99, 60) is True and geo.is_ferry_route(25, 60) is False
    assert geo.is_ferry_destination("13101", 24.99, 60) is True
    assert geo.is_ferry_destination("01101", 500, 300) is True  # 速度が速くても一覧の地域は True


def test_n2_ferry_list_has_basis_and_valid_codes():
    data = load_ferry_only()
    assert data["basis"] and data["checked_on"]
    pts = geo.load_points()
    for code, name in data["municipalities"].items():
        assert pts[code]["name"] == name, code
    assert set(data["prefectures"]) == {"01", "47"}


def test_n2_api_estimate_gives_no_kashikiri_amount_for_hokkaido_and_sado(web):
    for dest in ("01101", "15224", "42210"):
        est = _est(web, [Item(3, 120, 50, 50, 40)], "13104", dest)
        assert est["blocked_reason"] and "海を渡る" in est["blocked_reason"]
        k = [r for r in est["rows"] if r["group"] == "kashikiri"]
        assert len(k) == 1 and k[0]["amount"] is None and k[0]["status"] == "計算できない"
        assert est["geo"]["days"] is None


def test_n2_fill_layer_has_no_fare_for_ferry_destinations():
    tariff = load_tariff("kashikiri")
    codes = ["01101", "15224", "27128"]
    fare = geo.fare_layer("13104", 100, tariff, codes)
    assert fare[0] is None and fare[1] is None and fare[2] is not None
    od = json.loads((ROOT / "web" / "data" / "origin_13104.json").read_text(encoding="utf-8"))
    idx = json.loads((ROOT / "web" / "data" / "index.json").read_text(encoding="utf-8"))["codes"]
    for c in ("01101", "15224", "42210"):
        i = idx.index(c)
        assert od["ferry"][i] is True and od["fare"][i] is None and od["days"][i] is None


# ---------------------------------------------------------------- E1・F03
def test_e1_item_heavier_than_box_payload_makes_jitbox_unusable():
    rows = estimate_all(make_shipment([Item(1, 60, 60, 60, 600)]))
    j = next(r for r in rows if r.mode == "jitbox")
    assert j.status == "使えない" and j.amount is None
    assert "600" in j.reasons[0] and "500kg" in j.reasons[0]
    ok = next(r for r in estimate_all(make_shipment([Item(1, 60, 60, 60, 500)])) if r.mode == "jitbox")
    assert ok.status != "使えない" and ok.amount is not None


# ---------------------------------------------------------------- E2
def test_e2_box_count_respects_inner_dimensions():
    j = next(r for r in estimate_all(make_shipment([Item(3, 100, 100, 100, 10)])) if r.mode == "jitbox")
    assert j.detail["boxes_by_weight"] == 1 and j.detail["boxes_by_volume"] == 2
    assert j.detail["boxes"] == 3  # 高さの都合で 1 本に 1 個ずつ
    assert j.amount == 3 * j.detail["fare_per_box"]
    assert any("3 本" in n for n in j.notes)


# ---------------------------------------------------------------- F01
def test_f01_bulky_light_load_is_not_a_2t_cheapest():
    items = [Item(20, 100, 100, 100, 30)]  # 600kg、20m³。2t ショートの荷室は約 8.7m³
    rows = estimate_all(make_shipment(items, route="other", distance_km=300.0, origin_in_kanto=True))
    k = [r for r in rows if r.mode.startswith("kashikiri")]
    two = next(r for r in k if r.detail.get("vehicle") == "2t")
    assert two.status == "使えない" and two.amount is None and "容積" in two.reasons[0]
    best = [r for r in k if r.has_amount()][0]
    assert best.detail["vehicle"] in ("4t", "10t")
    # 重さだけなら 2t が最安だったはずの荷
    assert load_kashikiri()["vehicles"][0]["max_kg"] >= 600


def test_f01_map_layer_follows_the_same_choice(web):
    items = [Item(20, 100, 100, 100, 30)]
    est = _est(web, items, "13104", "27205")
    row = _first_kashikiri(est)
    assert row["detail"]["vehicle"] != "2t"
    assert row["amount"] == _layer_amount(items, "13104", "27205")
    assert _layer_amount(items, "13104", "27205") > geo.fare_layer("13104", 600, load_tariff("kashikiri"), ["27205"])[0]


def test_f01_two_t_short_fails_but_long_fits_is_check():
    items = [Item(1, 400, 100, 100, 100)]  # 長さ 4m: ショート（3.0m）に載らず、標準（4.2m）には載る
    rows = estimate_all(make_shipment(items, route="other", distance_km=300.0, origin_in_kanto=True))
    two = next(r for r in rows if r.mode == "kashikiri_2t")
    assert two.status == "要確認" and two.amount is not None
    assert "ロング" in two.reasons[0]


# ---------------------------------------------------------------- F09
def test_f09_over_10t_shows_vehicle_count():
    items = [Item(2, 100, 100, 100, 6000)]  # 12t
    rows = estimate_all(make_shipment(items, route="other", distance_km=300.0, origin_in_kanto=True))
    k = [r for r in rows if r.mode.startswith("kashikiri")]
    assert len(k) == 1 and k[0].detail["vehicles"] == 2 and k[0].detail["vehicle"] == "10t"
    assert k[0].amount == 2 * k[0].detail["unit_amount"] and k[0].status == "要確認"
    assert "2台" in k[0].label and "2" in " ".join(k[0].notes)
    t = load_kashikiri()
    unit = [f for f in __import__("shipcompare.estimate", fromlist=["x"]).kashikiri_fares(Decimal(300), Decimal(0), t) if f["class"] == "10t"][0]
    assert k[0].detail["unit_amount"] == unit["amount"]


def test_f09_piece_larger_than_any_vehicle_is_unusable():
    rows = estimate_all(make_shipment([Item(1, 100, 100, 100, 20000)], route="other", distance_km=300.0, origin_in_kanto=True))
    k = [r for r in rows if r.mode.startswith("kashikiri")]
    assert len(k) == 1 and k[0].status == "使えない" and k[0].amount is None


def test_f09_candidates_amount_multiplies_unit_fare():
    t = load_kashikiri()
    cands = kashikiri_candidates(Decimal(300), loadplan.items_from_shipment(make_shipment([Item(3, 100, 100, 100, 5000)])),
                                 Decimal(15000), t)
    assert len(cands) == 1 and cands[0]["vehicles"] == 2 or cands[0]["vehicles"] >= 2
    assert cands[0]["amount"] == cands[0]["unit_amount"] * cands[0]["vehicles"]


# ---------------------------------------------------------------- N6・N7
def test_n6_compare_lite_4t_has_tariff_note_and_check_over_2700():
    html = (ROOT / "web" / "compare_lite.html").read_text(encoding="utf-8")
    assert loadplan.TARIFF_4T_NOTE in html
    assert "DECKS.tariff_4t_note" in html and "RULES.t4.payloadKg" in html
    tpl = (ROOT / "tools" / "compare_lite_template.html").read_text(encoding="utf-8")
    assert "cls === '4t' && overKg(t, RULES.t4.payloadKg)" in tpl  # 重さは整数で比べる（SPEC §43）


def test_n7_one_source_for_2t_deck():
    d = deck_2t()
    assert d["footprint_mm"] == [3000, 1700] and d["floor_m2"] == 5.1
    short = loadplan.container_by_id("box_2t_short")
    assert (short.length_mm, short.width_mm, short.height_mm, short.payload_kg) == \
        (d["length_mm"], d["width_mm"], d["height_mm"], d["payload_kg"])
    decks = load_vehicle_decks()
    for dk in decks["decks"]:
        c = loadplan.container_by_id(dk["id"])
        assert (c.length_mm, c.width_mm, c.height_mm, c.payload_kg) == (dk["length_mm"], dk["width_mm"], dk["height_mm"], dk["payload_kg"])
    for name in ("compare_lite.html", "publish/compare_lite_artifact.html"):
        html = (ROOT / "web" / name).read_text(encoding="utf-8")
        assert json.dumps(decks["decks"][0], ensure_ascii=False, separators=(",", ":")) in html
        assert "5.5m²" not in html and "310cm" not in html and "約 5.5" not in html
