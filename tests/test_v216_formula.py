"""SPEC §21.6 帰り便の値段の式（受入条件 G81）。

S6 の荷と T7（架空 4t ウイング、近畿の表）で、式どおりの額が出ること、荷主の役の画面の HTML に式の欄が無いことを確かめる。
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from shipcompare import spaceb
from shipcompare.estimate import kashikiri_fares
from shipcompare.tariffs import load_kashikiri

from test_v21_backhaul import s6_body, s6_ship, t7
from test_v3_web import get, post, srv  # noqa: F401  (srv は fixture)

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def hand(rate: str, disc: str) -> int:
    """式を手で書いた値（SPEC §21.7）。荷を積む区間 江東区 → 大阪市中央区は新宿区 → 大阪市中央区の 491.8km で代用。
    近畿 4t 491.8km の距離制運賃 120,050 円 [57,350 + 15 × 4,180]。"""
    fare = next(f["amount"] for f in kashikiri_fares(Decimal("491.8"), Decimal(3500), load_kashikiri("近畿")) if f["class"] == "4t")
    assert fare == 120_050
    ratio = max(Decimal("4.08") / Decimal("13.0"), Decimal(400) / Decimal(3500))
    return int((Decimal(fare) * ratio / Decimal(rate) * (1 - Decimal(disc))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def test_t7_has_load_floor_and_new_price():
    t = t7()
    assert t["max_load_kg"] == 3500 and t["floor_total_m2"] == 13.0 and t["price_ex_tax_yen"] == 43_000


def test_g81_formula_s6_t7_at_20_percent_discount_and_70_percent_base():
    r = spaceb.per_unit_reference(t7(), s6_ship(), "0.7", "0.2", "近畿")
    assert r["reference_yen"] == hand("0.7", "0.2") == 43_060
    assert r["distance_fare_yen"] == 120_050 and r["tariff_class"] == "4t" and r["km"] == 491.8
    assert abs(r["floor_ratio"] - 4.08 / 13.0) < 1e-9 and abs(r["weight_ratio"] - 400 / 3500) < 1e-9 and r["ratio_by"] == "床"
    assert "新宿区" in r["km_note"]  # 江東区は新宿区の表で代用し、その旨を出す


@pytest.mark.parametrize("rate,disc", [("0.7", "0"), ("0.5", "0.3"), ("1", "0.5"), ("0.6", "0.1")])
def test_g81_formula_other_inputs(rate, disc):
    assert spaceb.per_unit_reference(t7(), s6_ship(), rate, disc, "近畿")["reference_yen"] == hand(rate, disc)


def test_formula_kanto_table_gives_a_different_amount():
    kinki = spaceb.per_unit_reference(t7(), s6_ship(), "0.7", "0.2", "近畿")["reference_yen"]
    kanto = spaceb.per_unit_reference(t7(), s6_ship(), "0.7", "0.2", "関東")
    assert kanto["distance_fare_yen"] != 120_050 and kanto["reference_yen"] != kinki


def test_formula_weight_ratio_wins_for_a_heavy_light_floor_cargo():
    s = spaceb.parse_shipment({"shipment": {"items": [{"count": 10, "length_cm": 50, "width_cm": 50, "height_cm": 50, "weight_kg": 300}]},
                               "dest_code": "28210"})
    r = spaceb.per_unit_reference(t7(), s, "0.7", "0", "近畿")
    assert r["ratio_by"] == "重さ" and abs(r["ratio"] - 3000 / 3500) < 1e-9


@pytest.mark.parametrize("rate,disc,region", [("0", "0", "近畿"), ("1.1", "0", "近畿"), ("0.7", "0.15", "近畿"), ("0.7", "0.6", "近畿"),
                                              ("0.7", "0", "九州"), ("x", "0", "近畿")])
def test_formula_rejects_bad_inputs(rate, disc, region):
    with pytest.raises(ValueError):
        spaceb.per_unit_reference(t7(), s6_ship(), rate, disc, region)


def test_formula_needs_registered_load_and_floor():
    t = dict(t7())
    del t["max_load_kg"]
    with pytest.raises(ValueError, match="最大積載量"):
        spaceb.per_unit_reference(t, s6_ship(), "0.7", "0", "近畿")


def test_formula_api(srv):
    _, port = srv
    st, d = post(port, "/api/b/price_formula", {**s6_body(), "trip_id": "T7", "base_load_rate": 0.7, "lead_discount": 0.2, "region": "近畿"})
    assert st == 200 and d["reference"]["reference_yen"] == 43_060
    assert "値段は運送会社が決めます" in d["reference"]["notice"]
    # 地域を省くと運送会社の登録（C4 は近畿）になる
    st, d = post(port, "/api/b/price_formula", {**s6_body(), "trip_id": "T7"})
    assert st == 200 and d["reference"]["region"] == "近畿" and d["reference"]["lead_discount"] == 0.0
    assert post(port, "/api/b/price_formula", {**s6_body(), "trip_id": "T7", "lead_discount": 0.25})[0] == 400
    assert post(port, "/api/b/price_formula", {**s6_body(), "trip_id": "T9"})[0] == 400
    assert post(port, "/api/b/price_formula", {**s6_body(), "trip_id": "T1"})[0] == 400  # 最大積載量が未登録の便


def test_g81_shipper_role_html_has_no_formula_panel():
    """式の欄は find.js が運送会社の役のときだけ作る。index.html（荷主の役の初期画面）には式の文言も入力欄も無い。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for text in ("値段の式", "基準積載率", "リードタイムの割引", "地域の表", "fm-rate", "個建運賃の参考額"):
        assert text not in html, text
    js = (WEB / "find.js").read_text(encoding="utf-8")
    body = js[js.index("function renderFormula()"):]
    assert "if (F.role !== 'carrier') { el.innerHTML = ''" in body.split("if (!F.fm.built)")[0]  # 荷主の役では空にして戻る
    for text in ("値段の式", "基準積載率（%）", "リードタイムの割引", "地域の表", "個建運賃の参考額 = 距離制運賃 × max（重さの割合, 床の割合）÷ 基準積載率 ×（1 − リードタイムの割引）",
                 "標準的運賃（2024 年 3 月 22 日告示）の個建運賃とリードタイムの割引の考え方に当てた参考額です。値段は運送会社が決めます"):
        assert text in js, text
    assert re.search(r'<div id="f-formula"[^>]*></div>', html)  # 中身は空の入れ物だけ


# ---------------------------------------------------------------- 最低保証料（任意）
def test_min_fee_raises_the_reference_when_the_formula_is_lower():
    base = spaceb.per_unit_reference(t7(), s6_ship(), "0.7", "0.2", "近畿")
    assert base["min_fee_yen"] is None and base["min_fee_applied"] is False and base["reference_yen"] == base["formula_yen"] == 43_060
    hi = spaceb.per_unit_reference(t7(), s6_ship(), "0.7", "0.2", "近畿", min_fee_yen=45_000)
    assert hi["formula_yen"] == 43_060 and hi["reference_yen"] == 45_000 and hi["min_fee_applied"] is True
    lo = spaceb.per_unit_reference(t7(), s6_ship(), "0.7", "0.2", "近畿", min_fee_yen="40000")
    assert lo["reference_yen"] == 43_060 and lo["min_fee_applied"] is False
    assert spaceb.per_unit_reference(t7(), s6_ship(), "0.7", "0.2", "近畿", min_fee_yen="")["min_fee_yen"] is None
    assert "最低保証料を下回るときは最低保証料" in hi["min_fee_note"]
    with pytest.raises(ValueError, match="最低保証料"):
        spaceb.per_unit_reference(t7(), s6_ship(), "0.7", "0.2", "近畿", min_fee_yen=-1)


def test_min_fee_api_and_ui_text(srv):
    _, port = srv
    st, d = post(port, "/api/b/price_formula", {**s6_body(), "trip_id": "T7", "lead_discount": 0.2, "min_fee_yen": 45000})
    assert st == 200 and d["reference"]["reference_yen"] == 45_000
    assert post(port, "/api/b/price_formula", {**s6_body(), "trip_id": "T7", "min_fee_yen": "abc"})[0] == 400
    js = (WEB / "find.js").read_text(encoding="utf-8")
    assert "最低保証料（円）" in js and "最低保証料を下回るときは最低保証料" in js
    assert "便の全体" not in js
