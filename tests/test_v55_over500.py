"""SPEC §5.5 の訂正: 500km を超える貸切は 50km ごとに加算する（受入条件 G80）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from shipcompare.estimate import kashikiri_fares
from shipcompare.tariffs import load_kashikiri, load_tariff


def amount(region, km, cls="4t", kg=3000):
    fares = kashikiri_fares(Decimal(str(km)), Decimal(kg), load_kashikiri(region))
    return next(f["amount"] for f in fares if f["class"] == cls)


def test_g80_kanto_4t():
    assert amount("関東", 500) == 126_060
    assert amount("関東", 556) == 147_960
    assert amount("関東", 501) == 126_060 + 10_950
    assert amount("関東", 550) == 126_060 + 10_950
    assert amount("関東", 551) == 126_060 + 2 * 10_950


def test_g80_kinki_4t():
    assert amount("近畿", 556) == 140_950


@pytest.mark.parametrize("region,cls,exp", [("関東", "2t", 52_300 + 15 * 3_830 + 9_580), ("関東", "10t", 80_110 + 15 * 5_850 + 14_620),
                                             ("近畿", "2t", 50_270 + 15 * 3_680 + 9_210), ("近畿", "10t", 77_370 + 15 * 5_650 + 14_130)])
def test_g80_other_classes_501km(region, cls, exp):
    assert amount(region, 501, cls, 1000) == exp


def test_default_region_is_kanto_and_matches_load_tariff():
    assert load_kashikiri() == load_tariff("kashikiri")
    with pytest.raises(KeyError):
        load_kashikiri("九州")


def test_table_without_over_500_keys_keeps_20km_steps():
    t = {"base_km": 200, "step_km": 20, "vehicles": [{"class": "2t", "max_kg": 2000, "base_fare": 100, "add_per_step": 10}]}
    assert kashikiri_fares(Decimal(600), Decimal(100), t)[0]["amount"] == 100 + 20 * 10


def test_under_500_unchanged():
    assert amount("関東", 200) == 60_360
    assert amount("関東", 230) == 60_360 + 2 * 4_380


# ---------------------------------------------------------------- web/data に焼き込んだ貸切の額（SPEC §5.5 の訂正の続き）
def _baked(origin: str):
    import json
    from pathlib import Path

    web = Path(__file__).resolve().parents[1] / "web" / "data"
    idx = json.loads((web / "index.json").read_text(encoding="utf-8"))
    o = json.loads((web / f"origin_{origin}.json").read_text(encoding="utf-8"))
    return idx["codes"], o


def _old_20km_amount(km: float) -> int:
    """訂正前の式（500km を超えても 20km ごとに加算）。2t 車。"""
    t = load_tariff("kashikiri")
    v = next(v for v in t["vehicles"] if v["class"] == "2t")
    steps = -(-int((Decimal(str(km)) - t["base_km"]) * 1000) // (t["step_km"] * 1000))
    return v["base_fare"] + steps * v["add_per_step"]


def test_baked_web_fares_use_the_corrected_over_500_rule():
    from shipcompare import geo

    tariff = load_tariff("kashikiri")
    changed = 0
    for origin in geo.ORIGINS:
        codes, o = _baked(origin)
        far = 0
        for i, km in enumerate(o["km"]):
            if km is None or o["fare"][i] is None:
                continue
            want = kashikiri_fares(Decimal(str(km)), Decimal(0), tariff)[0]["amount"]
            assert o["fare"][i] == want, (origin, codes[i], km)
            if km > 500:
                far += 1
                if o["fare"][i] != _old_20km_amount(km):
                    changed += 1
        assert far > 0, origin  # 500km を超える着地がある出発地だけを試験に使う（関東発なら全国に届く）
    assert changed > 0


def test_baked_web_fare_for_a_far_destination_changed():
    """新宿区から 500km を超える着地（札幌市中央区など）の額は、訂正前の 20km ごとの加算の額と変わっている。"""
    codes, o = _baked("13104")
    rows = [(c, km, f) for c, km, f in zip(codes, o["km"], o["fare"]) if km and km > 600 and f]
    assert rows
    for c, km, f in rows:
        assert f != _old_20km_amount(km), (c, km)
