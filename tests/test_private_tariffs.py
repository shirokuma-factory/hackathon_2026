"""実在の会社の運賃表（data/tariffs/private/、git に入れない）があるときだけ流す試験。

期待値もコミットしない data/tariffs/private/expected.json から読む（このファイルに実在の運賃の数字は書かない）。
CI など private/ がない環境では飛ばす。論理の試験は架空の見本の表で別に流している（test_scenarios.py など）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shipcompare.estimate import estimate_all
from shipcompare.models import Item
from shipcompare.storage import load_scenario
from shipcompare.tariffs import is_sample_tariff, load_all_tariffs

from conftest import make_shipment, one, row_of

PRIVATE = Path(__file__).resolve().parents[1] / "data" / "tariffs" / "private"
EXPECTED = PRIVATE / "expected.json"

pytestmark = pytest.mark.skipif(not EXPECTED.is_file() or not (PRIVATE / "sagawa_kanto.json").is_file(),
                                reason="実在の運賃表は手元だけ")


def _expected():
    return json.loads(EXPECTED.read_text(encoding="utf-8"))


def test_private_tables_are_preferred_over_samples():
    for key in ("takuhai", "rosen", "jitbox"):
        assert not is_sample_tariff(key)
    assert not load_all_tariffs()["takuhai"]["is_sample"]


@pytest.mark.parametrize("sid", ["S1", "S2", "S3", "S4"])
def test_scenario_amounts_with_real_tables(sid):
    got = {r.mode: r.amount for r in estimate_all(load_scenario(sid)["shipment"]) if r.mode in ("takuhai", "rosen", "jitbox")}
    assert got == _expected()["scenarios"][sid]


def test_takuhai_dropoff_and_jitbox_with_real_tables():
    e = _expected()
    assert row_of(estimate_all(make_shipment([Item(1, 20, 20, 20, 1)], pickup="持込")), "takuhai").amount == e["takuhai_60_dropoff"]
    assert row_of(estimate_all(make_shipment([Item(1, 20, 20, 20, 1)], pickup="集荷")), "takuhai").amount == e["takuhai_60_pickup"]
    assert row_of(estimate_all(make_shipment(one())), "jitbox").amount == e["jitbox_one"]
