"""積付けの 3D 表示（SPEC §20）の配置計算と API の試験。

配置は、入る・あふれる、回転、積み重ねの有無、重量、同じ入力で同じ結果、重なりなし、荷室の内側、で確かめる。
"""

from __future__ import annotations

import json

import pytest

from shipcompare.estimate import estimate_jitbox  # noqa: F401  (箱の内寸の出所が同じことを下で確かめる)
from shipcompare.loadplan import (
    LoadItem, container_by_id, containers, items_from_shipment, plan_load,
)
from shipcompare.models import Item, Shipment
from shipcompare.tariffs import load_tariff

from test_v3_web import JAPANESE, post, srv  # noqa: F401  (srv は fixture)


def cont(cid: str):
    return container_by_id(cid)


def check_geometry(res: dict) -> None:
    """全部が荷室の内側で、どの2つも重ならない。"""
    c = res["container"]
    ps = res["placements"]
    for p in ps:
        assert p["x"] >= 0 and p["y"] >= 0 and p["z"] >= 0
        assert p["x"] + p["l"] <= c["length_mm"]
        assert p["y"] + p["w"] <= c["width_mm"]
        assert p["z"] + p["h"] <= c["height_mm"]
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            sep = (a["x"] + a["l"] <= b["x"] or b["x"] + b["l"] <= a["x"]
                   or a["y"] + a["w"] <= b["y"] or b["y"] + b["w"] <= a["y"]
                   or a["z"] + a["h"] <= b["z"] or b["z"] + b["h"] <= a["z"])
            assert sep, (a, b)


# ---------------------------------------------------------------- 荷室の表

def test_containers_have_required_kinds():
    ids = {c.id for c in containers()}
    assert {"kei_van", "box_2t", "wing_4t", "wing_10t", "jr_12ft", "jr_31ft", "roll_cage", "jitbox",
            "rb_upper_1700", "rb_upper_2000"} <= ids
    assert len(ids) == len(containers())


def test_jitbox_uses_same_inner_dims_as_tariff():
    box = load_tariff("jitbox")["box"]
    c = cont("jitbox")
    assert [c.length_mm, c.width_mm, c.height_mm] == box["inner_mm"]
    assert c.payload_kg == box["max_kg"]


def test_rb_upper_is_marked_estimate_and_not_sellable():
    for cid in ("rb_upper_1700", "rb_upper_2000"):
        c = cont(cid)
        assert c.basis == "推定" and c.sellable_slot is False
        assert "販売枠ではありません" in c.note
    assert cont("rb_upper_1700").height_mm > cont("rb_upper_2000").height_mm  # 1700 と 2000 を区別する


# ---------------------------------------------------------------- 入る・あふれる

def test_twelve_boxes_fit_in_4t_wing():
    res = plan_load([LoadItem(12, 80, 60, 50, 15)], cont("wing_4t"))
    s = res["summary"]
    assert s["fits"] and s["placed"] == 12 and s["unplaced_count"] == 0 and res["unplaced"] == []
    assert s["total_weight_kg"] == 180 and s["weight_ok"]
    assert s["verdict"] == "○"
    assert 0 < s["floor_area_pct"] < 100 and 0 < s["volume_pct"] < 100
    check_geometry(res)


def test_overflow_reports_unplaced_with_reason():
    res = plan_load([LoadItem(40, 80, 60, 50, 5)], cont("kei_van"))
    s = res["summary"]
    assert not s["fits"] and s["unplaced_count"] > 0 and s["placed"] + s["unplaced_count"] == 40
    assert s["verdict"] == "×"
    assert res["unplaced"][0]["count"] == s["unplaced_count"] and res["unplaced"][0]["reason"]
    check_geometry(res)


def test_too_tall_and_too_wide_have_specific_reasons():
    tall = plan_load([LoadItem(1, 50, 50, 130, 5)], cont("kei_van"))
    assert "高さ" in tall["unplaced"][0]["reason"]
    wide = plan_load([LoadItem(1, 250, 250, 50, 5)], cont("kei_van"))
    assert "床" in wide["unplaced"][0]["reason"]


# ---------------------------------------------------------------- 回転

def test_rotation_on_floor_only():
    # 長さ 130cm は荷室の幅 130cm とちょうど。幅 190cm は長さ 180cm に入らないので、190 を y に回しても入らない
    ok = plan_load([LoadItem(1, 110, 170, 50, 5)], cont("kei_van"))  # 110×170: 回すと x=170, y=110 で入る
    assert ok["summary"]["fits"]
    p = ok["placements"][0]
    assert p["rotated"] is True and (p["l"], p["w"]) == (1700, 1100)
    # 傾けない: 高さは変わらない
    assert p["h"] == 500


def test_rotation_enables_extra_fit():
    # 幅 130 の荷室に 100×60 を並べる。回さないと y に 1 つ(100)しか載らないが、回すと 60 で 2 つ載る
    res = plan_load([LoadItem(4, 100, 60, 50, 1)], cont("kei_van"))
    assert res["summary"]["fits"]
    check_geometry(res)


# ---------------------------------------------------------------- 積み重ね

def test_stacking_off_leaves_unplaced_and_on_places():
    items_off = [LoadItem(6, 80, 60, 50, 5, stackable=False)]
    items_on = [LoadItem(6, 80, 60, 50, 5, stackable=True)]
    c = cont("roll_cage")  # 床に 1 個だけ載る（800×600）、高さ 1700 → 積むと 3 個
    off = plan_load(items_off, c)
    on = plan_load(items_on, c)
    assert off["summary"]["placed"] == 1 and not off["summary"]["stacked"]
    assert "積み重ね不可" in off["unplaced"][0]["reason"]
    assert on["summary"]["placed"] == 3 and on["summary"]["stacked"]
    assert [p["z"] for p in on["placements"]] == [0, 500, 1000]
    assert on["unplaced"][0]["count"] == 3 and "余地" in on["unplaced"][0]["reason"]
    check_geometry(on)


def test_stack_needs_support_footprint():
    # 下が 80×60、上が 100×100 の荷は載せられない
    items = [LoadItem(1, 80, 60, 50, 5), LoadItem(1, 100, 100, 50, 5)]
    res = plan_load(items, cont("roll_cage"))
    assert res["summary"]["placed"] == 1 and res["unplaced"][0]["item"] == 1


# ---------------------------------------------------------------- 重量

def test_weight_limit():
    # 箱は入るが 350kg を超える
    res = plan_load([LoadItem(10, 30, 30, 30, 40)], cont("kei_van"))
    s = res["summary"]
    assert s["all_placed"] and not s["weight_ok"] and not s["fits"]
    assert s["weight_over_kg"] == 50 and s["verdict"] == "×"
    exact = plan_load([LoadItem(7, 30, 30, 30, 50)], cont("kei_van"))
    assert exact["summary"]["weight_ok"] and exact["summary"]["weight_over_kg"] == 0


def test_estimated_container_is_never_circle():
    res = plan_load([LoadItem(1, 30, 30, 10, 1)], cont("rb_upper_1700"))
    assert res["summary"]["fits"] and res["summary"]["verdict"] == "△"


# ---------------------------------------------------------------- 決定性

def test_deterministic():
    items = [LoadItem(5, 70, 40, 45, 8), LoadItem(9, 55, 55, 30, 6), LoadItem(3, 120, 80, 100, 40)]
    a = plan_load(items, cont("box_2t"))
    b = plan_load(items, cont("box_2t"))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    check_geometry(a)


@pytest.mark.parametrize("cid", [c.id for c in containers()])
def test_geometry_in_every_container(cid):
    items = [LoadItem(7, 60, 40, 35, 3), LoadItem(4, 90, 50, 70, 10), LoadItem(5, 30, 30, 30, 2)]
    check_geometry(plan_load(items, cont(cid)))


# ---------------------------------------------------------------- 入力の検証

def test_bad_inputs_raise_japanese():
    with pytest.raises(ValueError, match="荷物の行"):
        plan_load([], cont("kei_van"))
    with pytest.raises(ValueError, match="個数"):
        plan_load([LoadItem(0, 10, 10, 10, 1)], cont("kei_van"))
    with pytest.raises(ValueError, match="寸法"):
        plan_load([LoadItem(1, 0, 10, 10, 1)], cont("kei_van"))
    with pytest.raises(ValueError, match="多すぎ"):
        plan_load([LoadItem(5000, 1, 1, 1, 1)], cont("wing_10t"))
    with pytest.raises(ValueError, match="荷室"):
        container_by_id("nothing")


def test_items_from_shipment():
    s = Shipment(items=[Item(2, 10, 20, 30, 4)])
    its = items_from_shipment(s, stackable=False)
    assert its == [LoadItem(2, 10.0, 20.0, 30.0, 4.0, False)]
    with pytest.raises(ValueError, match="未入力"):
        items_from_shipment(Shipment(items=[Item(2, None, 20, 30, 4)]))
    with pytest.raises(ValueError, match="不正"):
        items_from_shipment(Shipment(items=[Item(2, -1, 20, 30, 4)]))


# ---------------------------------------------------------------- API

SHIP = {"items": [{"count": 12, "length_cm": 80, "width_cm": 60, "height_cm": 50, "weight_kg": 15}]}


def test_api_matches_python(srv):
    _, port = srv
    st, j = post(port, "/api/loadplan", {"shipment": SHIP, "container": "wing_4t"})
    assert st == 200
    direct = plan_load([LoadItem(12, 80, 60, 50, 15)], cont("wing_4t"))
    assert j["placements"] == direct["placements"] and j["summary"] == direct["summary"]
    assert any(c["id"] == "rb_upper_1700" for c in j["containers"])
    assert j["items"][0]["count"] == 12


def test_api_stackable_flag_and_default_container(srv):
    _, port = srv
    ship = {"items": [{"count": 6, "length_cm": 80, "width_cm": 60, "height_cm": 50, "weight_kg": 5}]}
    st, j = post(port, "/api/loadplan", {"shipment": ship, "container": "roll_cage", "stackable": False})
    assert st == 200 and j["summary"]["placed"] == 1
    st, j = post(port, "/api/loadplan", {"shipment": ship, "container": "roll_cage"})
    assert st == 200 and j["summary"]["placed"] == 3
    st, j = post(port, "/api/loadplan", {"shipment": ship})  # container 省略は 2t 箱車（標準）
    assert st == 200 and j["container"]["id"] == "box_2t"


@pytest.mark.parametrize("body", [
    {},
    {"shipment": {"items": []}},
    {"shipment": {"items": [{"count": 1, "length_cm": None, "width_cm": 1, "height_cm": 1, "weight_kg": 1}]}},
    {"shipment": SHIP, "container": "nothing"},
    {"shipment": SHIP, "container": 5},
    {"shipment": SHIP, "stackable": "yes"},
    {"shipment": {"items": [{"count": 99999, "length_cm": 1, "width_cm": 1, "height_cm": 1, "weight_kg": 1}]}},
])
def test_api_bad_input_is_400_japanese(srv, body):
    _, port = srv
    st, j = post(port, "/api/loadplan", body)
    assert st == 400 and JAPANESE.search(j["error"])
