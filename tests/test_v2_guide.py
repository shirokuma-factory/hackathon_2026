"""版2の追加（SPEC §13.7〜13.9、受入条件 G14〜G16）。問い合わせ先・お届け日数・早見表。"""

from __future__ import annotations

import json

import pytest

from shipcompare.estimate import ST_CHECK, ST_NOCALC, ST_OK, ST_UNUSABLE, estimate_all
from shipcompare.guide import (
    NO_CONTACT, contacts_for_rows, load_contacts, load_guide, load_transit_days, transit_days, transit_days_text,
)
from shipcompare.tariffs import ROUTES, data_dir

from conftest import make_shipment, one, row_of

CHECKED = "2026-11-03"


# ---------------------------------------------------------------- G14 (13.7)
def test_spec13_7_contacts_only_for_usable_modes():
    rows = estimate_all(make_shipment(one(1, 30, 30, 30, 10)))
    usable = {r.mode.split("_")[0] for r in rows if r.status in (ST_OK, ST_CHECK)}
    cs = contacts_for_rows(rows)
    assert {c.mode for c in cs} == usable
    assert all(c.checked_on == CHECKED for c in cs)


def test_spec13_7_nocalc_mode_has_contact():
    rows = estimate_all(make_shipment(one(1, 30, 30, 30, 10), route="kanto_tohoku"))
    nocalc = {r.mode.split("_")[0] for r in rows if r.status == ST_NOCALC}
    assert nocalc and nocalc <= {c.mode for c in contacts_for_rows(rows)}


def test_spec13_7_unusable_mode_has_no_contact():
    # 3辺の合計が160cm超なので宅配は使えない
    rows = estimate_all(make_shipment(one(1, 100, 100, 100, 40)))
    assert row_of(rows, "takuhai").status == ST_UNUSABLE
    assert "takuhai" not in {c.mode for c in contacts_for_rows(rows)}


def test_spec13_7_kashikiri_says_window_needs_check():
    rows = estimate_all(make_shipment(one(8, 80, 60, 40, 20)))
    cs = [c for c in contacts_for_rows(rows) if c.mode == "kashikiri"]
    assert len(cs) == 1 and cs[0].url is None and not cs[0].has_window
    assert NO_CONTACT == "窓口の確認が必要" and NO_CONTACT in cs[0].condition


def test_spec13_7_contact_values_match_spec():
    data = load_contacts()
    assert [e["url"] for e in data["rosen"]] == [
        "https://www.nipponexpress.com/jp/ja/contact/",
        "https://www.meitetsuunyu.co.jp/meitetsu-nx/inquiry/luggage/",
    ]
    assert data["takuhai"][0]["url"] == "https://www2.sagawa-exp.co.jp/contact/logistics/"
    assert data["jitbox"][0]["url"] == "https://pages.kuronekoyamato.co.jp/jitquotation_input.html"
    assert "法人発→法人着" in data["jitbox"][0]["condition"]
    assert all("こぐまアロー便" not in e["condition"] for e in data["rosen"])  # SPEC §32 #4: 商品名は条件の欄に出さない
    with open(data_dir() / "contacts.json", encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["source"] and raw["checked_on"] == CHECKED


def test_spec13_7_receiver_individual_makes_rosen_unusable():
    rows = estimate_all(make_shipment(receiver_type="個人"))
    r = row_of(rows, "rosen")
    assert r.status == ST_UNUSABLE and "個人あての配送はしていません" in "".join(r.reasons)
    assert "rosen" not in {c.mode for c in contacts_for_rows(rows)}


def test_spec13_7_sender_individual_makes_rosen_check():
    rows = estimate_all(make_shipment(sender_type="個人"))
    r = row_of(rows, "rosen")
    assert r.status == ST_CHECK and "法人のお客様が対象" in "".join(r.reasons)
    assert r.amount is not None and "rosen" in {c.mode for c in contacts_for_rows(rows)}


def test_spec30_1_rosen_label_names_no_carrier():
    r = row_of(estimate_all(make_shipment()), "rosen")
    assert r.label == "路線便（積合せの公表運賃からの試算）"


def test_spec13_7_corporate_scenarios_unchanged():
    for sid in ("S1", "S2", "S3", "S4"):
        from shipcompare.storage import load_scenario
        rows = estimate_all(load_scenario(sid)["shipment"])
        assert row_of(rows, "rosen").status in (ST_OK, ST_CHECK, ST_NOCALC)


# ---------------------------------------------------------------- G15 (13.8)
SAGAWA_NOTE = "代表の郵便番号で照会した値。集荷時間や時期で変わります"


@pytest.mark.parametrize("route,expected", [
    ("kanto_kansai", "発送日+1日（午後以降の配達）"),
    ("kanto_tohoku", "発送日+1日"),
    ("sapporo_tokyo", "発送日+2日"),
])
def test_spec13_8_takuhai_days(route, expected):
    td = transit_days("takuhai", route)
    assert td.text == expected and td.note == SAGAWA_NOTE and td.published
    assert SAGAWA_NOTE in transit_days_text(td)


def test_spec13_8_takuhai_other_links_to_inquiry():
    td = transit_days("takuhai", "other")
    assert td.text == "大手の宅配の照会画面で調べてください" and not td.published
    assert td.url == "https://www2.sagawa-exp.co.jp/send/days/input/"


@pytest.mark.parametrize("route", list(ROUTES))
def test_spec13_8_rosen_and_kashikiri_not_published(route):
    for mode in ("rosen", "kashikiri", "kashikiri_2t"):
        td = transit_days(mode, route)
        assert td.text == "公表なし（見積で確認）" and not td.published


@pytest.mark.parametrize("route", list(ROUTES))
def test_spec13_8_jitbox_shows_rule_never_next_day_alone(route):
    td = transit_days("jitbox", route)
    assert td.text == "900km圏内は預かりの翌日、900km以上は翌々日（距離の定義は記載なし）"
    assert td.text != "翌日" and "900km以上は翌々日" in td.text and not td.published


def test_spec13_8_unknown_route_has_no_days_and_data_has_sources():
    assert transit_days("takuhai", None) is None
    modes = load_transit_days()
    assert all(m["source_url"].startswith("https://") and m["checked_on"] == CHECKED for m in modes.values())


# ---------------------------------------------------------------- G16 (13.9)
def test_spec13_9_every_guide_row_has_url_and_checked_on():
    rows = load_guide()
    assert len(rows) == 5
    assert all(g.source_url.startswith("https://") and g.checked_on == CHECKED for g in rows)


def test_spec13_9_ferry_has_no_price_example():
    ferry = next(g for g in load_guide() if g.name.startswith("フェリー"))
    assert ferry.price is None and "料金の例は出さない" in ferry.price_note
    assert not any(ch.isdigit() for ch in ferry.price_text)


def test_spec13_9_guide_values_match_spec():
    rows = {g.name: g for g in load_guide()}
    pal = rows["パレット1枚単位（ヤマト JITパレットチャーター便）"]
    assert "東京→宮城 18,300円" in pal.price and pal.who == "法人発→法人着"
    assert "500km で 69,100円（公表の距離制から計算）" in rows["軽トラック1台の貸切（赤帽首都圏）"].price
    jr = rows["鉄道 12ft コンテナ（JR貨物、駅間）"]
    assert "札幌→東京 98,900円" in jr.price and "要確認" in jr.who and "札幌→東京 翌日" in jr.days
    assert "運賃表なし" in rows["パレット1枚単位（SST便）"].price_text


