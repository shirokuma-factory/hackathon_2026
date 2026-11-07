"""SPEC §39 道具の採点 r1a・r1b の修正（車の値の一本化、帰り便の判定、床の上限、入力の検査）。

- r1a F2: 4t ウイングの床・最大積載量は data/vehicle_decks.json だけが持ち、デモの便 T7・T2、登録の車、積付けが同じ値を使う。
- r1a F3: 比べるページの帰り便の目安は、地図の照合（/api/b/match）と同じ「便の空き」で判定する。
- r1b #4: 登録した便・自由な荷台の便の床の上限は、入れた空きか 1口の底面 × 口の数。
- r1b #5: plan_load の結果が web/kit/schema/placement.json に合う。
- r1a F11・r1b #11・#12: 全角の数、Infinity/NaN、保存ファイルの負の値。
Edge が無い環境だけ画面の試験を飛ばす。
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import edge_page

from shipcompare import loadplan, spaceb, storage, tariffs
from shipcompare.models import Item, Shipment, to_decimal

from test_v36_kit import _errors
from test_v3_web import call, srv  # noqa: F401  (srv は fixture)

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "web" / "compare_lite.html"


def cargo(count, l, w, h, kg, dest="46203", **kw):
    return spaceb.parse_shipment({"shipment": {"items": [{"count": count, "length_cm": l, "width_cm": w, "height_cm": h, "weight_kg": kg}]},
                                  "dest_code": dest, **kw})


def trips_by_id():
    return {t["id"]: t for t in spaceb.load_trips()}


# ---------------------------------------------------------------- F2
def test_f2_wing_4t_values_come_from_one_deck_file():
    deck = tariffs.deck_view("wing_4t")
    assert deck["floor_m2"] == pytest.approx(deck["length_mm"] * deck["width_mm"] / 1e6, abs=0.005)
    reg = spaceb.REGISTER_VEHICLES["4t"]
    assert reg["floor_total_m2"] == deck["floor_m2"] and reg["max_load_kg"] == deck["payload_kg"]
    assert reg["footprint_mm"] == [deck["length_mm"], deck["width_mm"]]
    for tid in ("T2", "T7"):  # 「架空 4t ウイング」の便
        t = trips_by_id()[tid]
        assert t["vehicle"] == "架空 4t ウイング" and t["deck"] == "wing_4t"
        assert t["floor_total_m2"] == deck["floor_m2"] and t["max_load_kg"] == deck["payload_kg"], tid
    t = trips_by_id()["T7"]
    assert t["unit_footprint_mm"] == [deck["length_mm"], deck["width_mm"]]
    assert t["floor_total_m2"] == pytest.approx(t["unit_footprint_mm"][0] * t["unit_footprint_mm"][1] / 1e6, abs=0.005)


def test_f2_all_register_vehicles_use_their_deck():
    for k, v in spaceb.REGISTER_VEHICLES.items():
        d = tariffs.deck_view(spaceb.DECK_IDS[k])
        assert (v["floor_total_m2"], v["max_load_kg"], v["footprint_mm"]) == (d["floor_m2"], d["payload_kg"], d["footprint_mm"]), k


def test_f2_loadplan_container_and_compare_page_use_the_same_deck():
    c = loadplan.container_by_id("wing_4t")
    deck = tariffs.deck_view("wing_4t")
    assert (c.length_mm, c.width_mm, c.payload_kg) == (deck["length_mm"], deck["width_mm"], deck["payload_kg"])
    html = PAGE.read_text(encoding="utf-8")
    assert '"id":"wing_4t"' in html and f'"payload_kg":{deck["payload_kg"]}' in html


def test_f2_reference_price_is_the_same_on_every_path():
    sh = cargo(10, 100, 60, 50, 20)
    reg = spaceb.build_registered_trip(dict(origin_code="13108", dest_code="28210", vehicle="4t", depart_at="2026-11-13T17:00",
                                            arrive_at="2026-11-14T12:30", weight_slack_kg=2000, unit_footprint_mm=[6200, 2200],
                                            clear_height_mm=2100, max_piece_kg=300, drop_points=["28210"]))
    ref_t7 = spaceb.per_unit_reference(trips_by_id()["T7"], sh, "0.7", "0", "近畿", km=Decimal("491.8"), km_note=None)
    ref_reg = spaceb.per_unit_reference(reg, sh, "0.7", "0", "近畿", km=Decimal("491.8"), km_note=None)
    assert ref_t7["reference_yen"] == ref_reg["reference_yen"]
    assert (ref_t7["floor_total_m2"], ref_t7["max_load_kg"]) == (ref_reg["floor_total_m2"], ref_reg["max_load_kg"])


# ---------------------------------------------------------------- F3
CARGOS = [  # (個数, 縦, 横, 高さ cm, 1個の kg)
    (1, 50, 50, 50, 10), (3, 100, 100, 100, 1000), (30, 100, 100, 50, 100), (10, 100, 60, 50, 200), (10, 100, 60, 50, 200.1),
    (11, 100, 60, 50, 10), (1, 100, 100, 100, 301), (1, 100, 100, 210, 10), (1, 700, 100, 100, 10), (2, 240, 60, 200, 140),
    (20, 100, 100, 50, 150), (1, 620, 220, 100, 100), (1, 621, 100, 100, 100),
]


def test_f3_compare_lite_home_matches_api_match_on_t7_space():
    t = trips_by_id()["T7"]
    rows = [dict(n=c[0], l=c[1], w=c[2], h=c[3], kg=c[4]) for c in CARGOS]
    with edge_page(1200, 800, webgl=False) as page:
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        got = page.js("(" + json.dumps(rows) + ").map((r) => evaluate({rows: [r], from: '東京都', to: '鹿児島県', km: 1346, recv: 'business', flex: 4})"
                      ".options.find((o) => o.id === 'home').status)")
    assert len(got) == len(CARGOS)
    for c, status in zip(CARGOS, got):
        ok, reasons = spaceb.match(cargo(*c), t)  # 受け取り場所は T7 の降ろせる地点。集荷の地点は荷に無いので調べない
        assert (status == "check") == ok, (c, status, reasons)


def test_f3_compare_lite_has_no_truck_total_limit():
    html = PAGE.read_text(encoding="utf-8")
    assert "t4home" not in html and "3,500kg" not in html and "帰り便に合う荷です。" not in html
    t = trips_by_id()["T7"]
    home = json.loads(re.search(r"const HOME_TRIP = (\{.*?\});", html).group(1))
    assert home["weight_slack_kg"] == t["weight_slack_kg"] and home["floor_free_m2"] == t["floor_free_m2"]
    assert home["unit_footprint_mm"] == t["unit_footprint_mm"]


# ---------------------------------------------------------------- r1b #4
def _reg(**kw):
    body = dict(origin_code="13104", dest_code="28110", vehicle="10t", depart_at="2026-11-11T17:00", arrive_at="2026-11-12T08:00",
                weight_slack_kg=1000, unit_footprint_mm=[1000, 1000], clear_height_mm=2000, max_piece_kg=100, drop_points=["28110"])
    body.update(kw)
    return spaceb.build_registered_trip(body)


def test_r1b4_registered_trip_floor_is_unit_footprint_not_truck_floor():
    t = _reg()
    assert t["floor_free_m2"] == pytest.approx(1.0)
    ok, reasons = spaceb.match(cargo(10, 100, 100, 50, 1, dest="28110"), t)
    assert not ok and any("床の空き 1.00m²" in r for r in reasons)
    assert spaceb.match(cargo(1, 100, 100, 50, 1, dest="28110"), t)[0] is True
    b = spaceb.Board()
    t["price_ex_tax_yen"] = 10000
    reg = b.register(t)
    assert reg["floor_free_m2"] == pytest.approx(1.0) and reg["remaining_floor_m2"] == pytest.approx(1.0)
    with pytest.raises(spaceb.BoardError, match="床の空き"):
        b.hold(reg["id"], cargo(10, 100, 100, 50, 1, dest="28110"))


def test_r1b4_entered_free_floor_and_units():
    assert _reg(floor_free_m2=4.5)["floor_free_m2"] == pytest.approx(4.5)
    assert _reg(units=3)["floor_free_m2"] == pytest.approx(3.0)
    with pytest.raises(ValueError, match="床の空き"):
        _reg(floor_free_m2=99)  # 10t の床 23.04m² を超える
    with pytest.raises(ValueError, match="口の数"):
        _reg(units=1.5)


def test_r1b4_every_free_bed_demo_trip_has_a_bounded_floor():
    for t in spaceb.load_trips():
        if t["space_type"] == "free_bed":
            assert t.get("floor_free_m2") is not None and t["floor_free_m2"] <= t["floor_total_m2"], t["id"]
            assert t["floor_free_m2"] >= t["unit_footprint_mm"][0] * t["unit_footprint_mm"][1] / 1e6 or t["id"] == "T7", t["id"]


def test_r1b4_free_bed_without_floor_free_uses_footprint():
    t = dict(trips_by_id()["T4"])
    t.pop("floor_free_m2")
    assert float(spaceb._trip_floor(t)) == pytest.approx(1.7 * 1.3)
    t["units"] = 2
    assert float(spaceb._trip_floor(t)) == pytest.approx(2 * 1.7 * 1.3)


# ---------------------------------------------------------------- r1b #5
def test_r1b5_plan_load_output_matches_placement_schema():
    schema = json.loads((ROOT / "web" / "kit" / "schema" / "placement.json").read_text(encoding="utf-8"))
    items = [loadplan.LoadItem(6, 110, 110, 15, 120, True), loadplan.LoadItem(2, 80, 60, 50, 15, False),
             loadplan.LoadItem(300, 50, 40, 30, 1, False)]
    res = loadplan.plan_load(items, loadplan.container_by_id("wing_4t"))
    assert len(res["items"]) == 3 and res["items"][0]["count"] == 6
    assert res["unplaced"], "未配置のある出力でも形に合うことを確かめる"
    assert _errors(json.loads(json.dumps(res)), schema) == []
    assert all(0 <= p["item"] < len(res["items"]) for p in res["placements"])


# ---------------------------------------------------------------- F11
@pytest.mark.parametrize("v", ["３０", " ３０ ", "30", "３０.５"])
def test_f11_full_width_numeric_strings_are_read(v):
    sh = spaceb.parse_shipment({"shipment": {"items": [{"count": 1, "length_cm": v, "width_cm": 10, "height_cm": 10, "weight_kg": 1}]},
                                "dest_code": "28210"})
    assert sh.pieces[0].length_mm > 0


def test_f11_unreadable_text_and_range_messages_differ():
    def msg(v):
        with pytest.raises(ValueError) as e:
            spaceb.parse_shipment({"shipment": {"items": [{"count": 1, "length_cm": v, "width_cm": 10, "height_cm": 10, "weight_kg": 1}]},
                                   "dest_code": "28210"})
        return str(e.value)
    assert "数で入れてください" in msg("abc") and "0より大きい" not in msg("abc")
    assert "0より大きい数" in msg(0) and "0より大きい数" in msg("-5")


# ---------------------------------------------------------------- r1b #11
@pytest.mark.parametrize("num", ["Infinity", "NaN", "-Infinity", "1e999"])
@pytest.mark.parametrize("path", ["/api/b/match", "/api/loadplan"])
def test_r1b11_api_rejects_non_finite_numbers_in_japanese(srv, path, num):
    _, port = srv
    raw = ('{"shipment":{"items":[{"count":1,"length_cm":' + num + ',"width_cm":1,"height_cm":1,"weight_kg":1}]},"dest_code":"28210"}').encode()
    st, data = call(port, "POST", path, raw=raw, headers={"Content-Type": "application/json"})
    assert st == 400, (path, num, data)
    assert re.search(r"[ぁ-んァ-ン一-龥]", data["error"]) and "m²" not in data["error"] and "Infinitym" not in data["error"]


def test_r1b11_to_decimal_rejects_non_finite():
    assert to_decimal(float("inf")) is None and to_decimal(float("nan")) is None and to_decimal(1.5) is not None


# ---------------------------------------------------------------- r1b #12
def _case(**item):
    return storage.Case(versions=[storage.ConditionVersion(number=1, shipment=Shipment(items=[Item(**item)]))])


@pytest.mark.parametrize("field,value", [("count", -1), ("count", 0), ("count", 1.5), ("length_cm", -5), ("width_cm", 0), ("height_cm", -0.1),
                                         ("weight_kg", -3), ("weight_kg", float("inf")), ("weight_kg", float("nan"))])
def test_r1b12_validate_case_rejects_out_of_range_items(field, value):
    base = dict(count=1, length_cm=10.0, width_cm=10.0, height_cm=10.0, weight_kg=1.0)
    base[field] = value
    with pytest.raises(ValueError):
        storage.validate_case(_case(**base))


def test_r1b12_valid_and_unfilled_items_still_load():
    storage.validate_case(_case(count=2, length_cm=30, width_cm=20, height_cm=10, weight_kg=0.5))
    storage.validate_case(_case())
