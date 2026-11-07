"""受入条件 S: 場面 S1〜S4 の期待値（SPEC §9）と G06（場面は想定の荷主）。"""

from __future__ import annotations

import pytest

from shipcompare.estimate import ST_CHECK, ST_NOCALC, ST_OK, ST_UNUSABLE, summarize
from shipcompare.estimate import estimate_all as _estimate_all
from shipcompare.storage import list_scenarios, load_scenario
from shipcompare.tariffs import load_sample_tariffs

from conftest import row_of


SAMPLE = load_sample_tariffs()  # 金額は架空の見本から決まる値。実在の表の有無に左右されない


def estimate_all(shipment, manual=None):
    return _estimate_all(shipment, manual, tariffs=SAMPLE)


def amounts(sid):
    sc = load_scenario(sid)
    return sc, {r.mode: r for r in estimate_all(sc["shipment"])}


def test_scenarios_are_assumed_shippers():
    scs = list_scenarios()
    assert [s["id"] for s in scs] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    for s in scs:
        assert s["assumed_shipper"] is True
        assert s["label"] == "想定の荷主"


def test_s1():
    sc, r = amounts("S1")
    assert r["takuhai"].amount == 11455
    assert r["takuhai"].detail["sizes"][0]["size"] == 220
    assert r["rosen"].amount == 10500
    assert str(r["rosen"].detail["chargeable_kg"]) == "252.00"
    assert r["rosen"].detail["weight_band_kg"] == 300
    assert r["jitbox"].amount == 20000
    assert r["jitbox"].status == ST_OK  # 120×50×50cm は向きを変えれば入る
    assert r["jitbox"].detail["boxes"] == 1
    assert r["kashikiri_2t"].amount == 109750
    assert r["kashikiri_4t"].amount == 126060
    assert r["kashikiri_10t"].amount == 167860


def test_s2():
    sc, r = amounts("S2")
    assert r["takuhai"].status == ST_UNUSABLE
    # 仕様 §9 は「330cm」と書くが 110+110+120=340cm。実際の合計を出す
    assert any("340" in x and "260cm" in x and "を超える" in x for x in r["takuhai"].reasons)  # 会社名なしの文（SPEC §30.1）
    assert r["takuhai"].amount is None
    assert r["rosen"].amount == 12450
    assert str(r["rosen"].detail["chargeable_kg"]) == "406.56"
    assert r["rosen"].detail["weight_band_kg"] == 450
    # 110×110×120cm は内寸 104×104×170cm に入らない（どの向きでも）。1個は分けられないので使えない（S08）
    assert r["jitbox"].amount is None
    assert r["jitbox"].status == ST_UNUSABLE
    assert any("1個がボックスの内寸を超えます" in x for x in r["jitbox"].reasons)
    assert r["kashikiri_2t"].amount == 82940
    assert r["kashikiri_4t"].amount == 95400
    assert r["kashikiri_10t"].amount == 126910


def test_s3():
    sc, r = amounts("S3")
    assert r["takuhai"].amount == 40909
    assert r["takuhai"].detail["sizes"][0]["size"] == 100
    assert r["rosen"].amount == 9000
    assert str(r["rosen"].detail["chargeable_kg"]) == "250.00"
    assert r["rosen"].detail["weight_band_kg"] == 250
    assert r["jitbox"].amount == 20000
    assert r["jitbox"].detail["boxes"] == 1


def test_s4():
    sc, r = amounts("S4")
    assert r["takuhai"].status == ST_UNUSABLE
    assert r["takuhai"].amount is None
    assert r["rosen"].amount == 42000
    assert str(r["rosen"].detail["chargeable_kg"]) == "813.12"
    assert r["rosen"].detail["weight_band_kg"] == 850
    assert r["jitbox"].amount is None   # 内寸を超える 1 個は分けられないので使えない（S08）
    assert r["jitbox"].status == ST_UNUSABLE
    assert any("1個がボックスの内寸を超えます" in x for x in r["jitbox"].reasons)
    assert r["jitbox"].detail["boxes"] == 2
    kashikiri = [x for x in r.values() if x.mode.startswith("kashikiri")]
    assert len(kashikiri) == 1
    assert kashikiri[0].status == ST_UNUSABLE
    assert "範囲外" in kashikiri[0].reasons[0]
    assert kashikiri[0].amount is None


def test_sorted_cheapest_first_unusable_last():
    sc = load_scenario("S2")
    rows = estimate_all(sc["shipment"])
    priced = [r.amount for r in rows if r.amount is not None]
    assert priced == sorted(priced)
    first_unpriced = next(i for i, r in enumerate(rows) if r.amount is None)
    assert all(r.amount is None for r in rows[first_unpriced:])


def test_every_row_has_source():
    for sid in ("S1", "S2", "S3", "S4"):
        for r in estimate_all(load_scenario(sid)["shipment"]):
            assert r.source and r.source.checked_on
            # 公表運賃の試算（宅配・路線便・カゴ台車）は会社名と各社の表を出さないので URL を持たない（SPEC §30.1）
            if r.mode in ("takuhai", "rosen", "jitbox"):
                assert not r.source.url and "会社名" in r.source.name
            else:
                assert r.source.url
