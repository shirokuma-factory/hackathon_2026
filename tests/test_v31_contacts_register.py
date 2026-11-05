"""SPEC §31.2（試算額と会社を結び付けない）、§31.4（運送会社が帰り便を登録する）、§31.5 #9・#13・#16・#17（受入条件 G138・G140・G141 の一部）。

画面の動き（JS）は撮影で確かめ、ここではサーバーの結果と、画面の部品が入っていることを確かめる。
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from shipcompare import spaceb
from shipcompare.guide import CONTACT_POINTER, request_contacts
from shipcompare.request import build_request
from shipcompare.storage import load_scenario, shipment_to_dict

from test_v3_web import call, post, srv  # noqa: F401  (srv は fixture)

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
JP = re.compile(r"[ぁ-んァ-ヶ一-龥]")


@pytest.fixture(scope="module")
def pub(srv):  # noqa: F811
    mod, _ = srv
    httpd = mod.make_server(0, public=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def sess(n: str) -> dict:
    return {"X-Demo-Session": f"test-{n}-{'a' * 20}"}


def s1_body() -> dict:
    return {"shipment": shipment_to_dict(load_scenario("S1")["shipment"])}


# ---------------------------------------------------------------- G138 見積を頼む先に手段が無い

def test_g138_public_request_contacts_have_no_mode_and_hide_single_company_windows(pub):
    status, r = post(pub, "/api/request", s1_body())
    assert status == 200
    assert r["contacts"] and all(set(c) == {"name", "url", "condition", "checked_on", "has_window"} for c in r["contacts"])
    assert "mode" not in json.dumps(r["contacts"])
    names = " ".join(c["name"] for c in r["contacts"])
    assert "佐川" not in names and "ヤマト" not in names  # 宅配・カゴ台車は 1 手段 1 社で、並べると結び付く
    assert "日本通運" in names and "名鉄NX運輸" in names
    assert r["contacts_note"] == CONTACT_POINTER == "窓口は比べるページの「見積を頼む先」にあります"
    assert "佐川" not in r["text"] and "ヤマト" not in r["text"]


def test_g138_local_request_contacts_are_sorted_by_reading_without_mode(srv):
    _, port = srv
    status, r = post(port, "/api/request", s1_body())
    assert status == 200 and r["contacts_note"] is None
    assert [c["name"][:4] for c in r["contacts"]] == ["佐川急便", "日本通運", "名鉄NX", "ヤマト運"]
    assert all("mode" not in c for c in r["contacts"])


def test_g138_contacts_do_not_depend_on_which_modes_are_usable(pub):
    # 大きな荷で宅配が使えなくなっても、一覧は同じ（使える手段と会社を結び付けない）
    small = post(pub, "/api/request", s1_body())[1]["contacts"]
    items = [{"count": 1, "length_cm": 200, "width_cm": 100, "height_cm": 100, "weight_kg": 300}]
    big = {"shipment": {**s1_body()["shipment"], "items": items}}
    status, r = post(pub, "/api/request", big)
    assert status == 200 and r["contacts"] == small


def test_g138_request_contacts_function_rules():
    pub_rows, note = request_contacts(public=True)
    loc_rows, note2 = request_contacts(public=False)
    assert note == CONTACT_POINTER and note2 is None
    assert len(loc_rows) == 4 and len(pub_rows) == 2
    assert all(c["has_window"] and c["url"] for c in loc_rows)  # 窓口のない貸切の行は並べない


def test_g138_ui_has_no_mode_column():
    js = (WEB / "work.js").read_text(encoding="utf-8")
    assert "mode_label" not in js and "<th>手段</th>" not in js
    assert "見積を頼む先" in js and "contacts_note" in js


def test_g138_compare_lite_lists_companies_without_mode_headings():
    for path in (WEB / "compare_lite.html", WEB / "publish" / "compare_lite_artifact.html"):
        html = path.read_text(encoding="utf-8")
        assert "data-ask=" not in html and "ASK_KEY" not in html
        assert "ask-co" in html and "String(x.reading || x.name)" in html


# ---------------------------------------------------------------- G140 帰り便の登録

REG = {"origin_code": "13104", "dest_code": "28110", "vehicle": "4t", "depart_at": "2026-11-13T17:00",
       "arrive_at": "2026-11-14T10:00", "weight_slack_kg": 2000, "unit_footprint_mm": [6000, 2000],
       "clear_height_mm": 2100, "max_piece_kg": 300, "drop_points": ["28110"]}


def trips_of(port, n):
    return call(port, "GET", "/api/b/trips", headers=sess(n))[1]


def test_g140_register_shows_in_list_matching_and_resets(pub):
    n = "reg1"
    base = trips_of(pub, n)
    assert len(base["trips"]) == 7 and [c["id"] for c in base["carriers"]] == ["C1", "C2", "C3", "C4"]
    status, r = call(pub, "POST", "/api/b/register_trip", {**REG, **s1_body()}, headers=sess(n))
    assert status == 200, r
    t = r["trip"]
    assert t["id"] == "R1" and t["carrier_id"] == "C9" and t["carrier_name"] == "架空 自社"
    assert t["route_label"].startswith("東京都新宿区→兵庫県神戸市中央区") and t["registered"] is True
    assert t["price_ex_tax_yen"] % 100 == 0 and t["price_ex_tax_yen"] > 0  # 値段の式の既定値（百円の単位）
    after = trips_of(pub, n)
    assert len(after["trips"]) == 8 and after["carriers"][-1]["name"] == "架空 自社"
    # 照合に出る（S1 の荷。結果は ○ か、理由つきの ×）
    status, m = call(pub, "POST", "/api/b/match", {**s1_body(), "dest_code": "28110", "origin_code": "13104"}, headers=sess(n))
    row = next(x for x in m["results"] if x["trip_id"] == "R1")
    assert row["ok"] is True or (row["ok"] is False and row["reasons"])
    # 別の来訪者には見えない
    assert len(trips_of(pub, "other")["trips"]) == 7
    # 戻すと消える
    assert call(pub, "POST", "/api/b/reset", {}, headers=sess(n))[0] == 200
    again = trips_of(pub, n)
    assert len(again["trips"]) == 7 and [c["id"] for c in again["carriers"]] == ["C1", "C2", "C3", "C4"]


def test_g140_registered_trip_can_be_held_and_formula_uses_it(pub):
    n = "reg2"
    r = call(pub, "POST", "/api/b/register_trip", {**REG, **s1_body()}, headers=sess(n))[1]["trip"]
    body = {**s1_body(), "dest_code": "28110", "origin_code": "13104", "trip_id": r["id"], "hold_minutes": 30}
    status, h = call(pub, "POST", "/api/b/hold", body, headers=sess(n))
    assert status == 200 and h["hold"]["trip_id"] == "R1"
    fbody = {**s1_body(), "trip_id": "R1", "dest_code": "28110", "origin_code": "13104"}
    status, f = call(pub, "POST", "/api/b/price_formula", fbody, headers=sess(n))
    assert status == 200 and f["reference"]["reference_yen"] > 0


def test_g140_default_price_is_the_21_6_formula(pub):
    body = {"origin_code": "13104", "dest_code": "28110", "vehicle": "4t", **s1_body()}
    status, d = call(pub, "POST", "/api/b/register_defaults", body, headers=sess("reg3"))
    assert status == 200
    p = d["price"]
    ref = p["reference"]
    assert ref["base_load_rate"] == 0.7 and ref["lead_discount"] == 0 and ref["region"] == "関東"
    assert p["price_ex_tax_yen"] == round(ref["reference_yen"] / 100) * 100
    assert set(d["vehicles"]) == {"2t", "4t", "10t"}


def test_g140_register_validation_is_400_japanese(pub):
    bads = ({**REG, "arrive_at": "2026-11-13T09:00"}, {**REG, "vehicle": "x"}, {**REG, "dest_code": "13104"},
            {**REG, "weight_slack_kg": 99999}, {**REG, "origin_code": "99999"}, {**REG, "drop_points": []},
            {**REG, "depart_at": "あした"})
    for bad in bads:
        status, r = call(pub, "POST", "/api/b/register_trip", {**bad, **s1_body()}, headers=sess("reg4"))
        assert status == 400 and JP.search(r["error"]), (bad, r)
    status, r = call(pub, "POST", "/api/b/register_trip", REG, headers=sess("reg4"))  # 荷が無く、値段も無い
    assert status == 400 and "値段" in r["error"]


def test_g140_registration_is_limited_per_visitor(pub):
    n = "reg5"
    for _ in range(spaceb.MAX_REGISTERED):
        assert call(pub, "POST", "/api/b/register_trip", {**REG, "price_ex_tax_yen": 50000}, headers=sess(n))[0] == 200
    status, r = call(pub, "POST", "/api/b/register_trip", {**REG, "price_ex_tax_yen": 50000}, headers=sess(n))
    assert status == 400 and "まで" in r["error"]


def test_g140_board_register_and_reset_unit():
    b = spaceb.Board()
    trip = spaceb.build_registered_trip({**REG})
    trip["price_ex_tax_yen"] = 50000
    got = b.register(trip)
    assert got["id"] == "R1" and got["remaining_slack_kg"] == 2000.0
    assert [c["id"] for c in b.extra_carriers()] == ["C9"]
    b.reset()
    assert len(b.trips()) == 7 and b.extra_carriers() == []


def test_g140_ui_wiring():
    js = (WEB / "find.js").read_text(encoding="utf-8")
    assert "/api/b/register_trip" in js and "/api/b/register_defaults" in js
    assert "function openCarrier(a, b)" in js and "openCarrier" in js.split("return {")[-1]
    assert "新しい便を足す機能は、この版にはありません" not in js
    assert "架空 自社" in js and "登録した便は直線で表示しています" in js
    assert "Find.openCarrier(" in (WEB / "market.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------- G141 の #9・#13・#16・#17

def test_g141_9_arc_trips_get_the_note():
    js = (WEB / "find.js").read_text(encoding="utf-8")
    assert "経路のデータが無いので直線で表示しています" in js and "f-arcnote" in js
    assert js.count("arcNote(t)") >= 2  # 荷主のカードと運送会社のカード


def test_g141_13_no_duplicated_fiction_mark():
    js = (WEB / "find.js").read_text(encoding="utf-8")
    assert "${esc(sc.name)}（架空）" not in js
    assert "const fict = " in js and "esc(fict(sc.name))" in js


def test_g141_16_request_uses_chosen_place_and_road_distance():
    s = load_scenario("S1")["shipment"]
    place = {"label": "新宿区 → 大阪市中央区", "km": 491.8, "note": None}
    text = build_request(s, None, place).text
    assert "発着: 新宿区 → 大阪市中央区（道路距離 491.8km）" in text
    assert "想定の道路距離 500km" not in text


def test_g141_16_api_passes_place(srv):
    _, port = srv
    note = "距離は最寄りの出発地（新宿区）の値で代用"
    body = {**s1_body(), "place": {"label": "江東区 → 大阪市中央区", "km": 491.8, "note": note}}
    status, r = post(port, "/api/request", body)
    assert status == 200 and f"発着: 江東区 → 大阪市中央区（道路距離 491.8km。{note}）" in r["text"]
    for bad in ({"label": "", "km": 1}, {"label": "a", "km": -1}, {"label": "a", "km": True}, "x"):
        assert post(port, "/api/request", {**s1_body(), "place": bad})[0] == 400


def test_g141_16_work_js_sends_place():
    js = (WEB / "work.js").read_text(encoding="utf-8")
    assert "place: W.place" in js or "{ place: W.place }" in js
    assert "W.place = " in js and "W.place]" in js  # 版の署名にも入れる


def test_g141_17_published_sources_have_no_repo_file_names():
    pat = re.compile(r"docs/|調査\.md|\.md\b|§[A-Z]\b|data/contacts\.json|tests/data|src/shipcompare")
    for path in (WEB / "compare_lite.html", WEB / "publish" / "compare_lite_artifact.html"):
        html = path.read_text(encoding="utf-8")
        i = html.index("const SOURCES = [")
        sources = html[i:html.index("];", i)]
        assert not pat.search(sources), (path.name, pat.search(sources))
        assert "国土交通省 標準的運賃（2024 年 3 月 22 日告示）" in sources
        assert not pat.search(html), (path.name, pat.search(html).group(0))
