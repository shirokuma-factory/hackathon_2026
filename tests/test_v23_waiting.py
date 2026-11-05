"""SPEC §23 荷待ち・荷役の記録の計算（受入条件 G73〜G78）。

G78 の「守る線と根拠の欄の文言が画面に常に出る」は web/own.js の文言を見る（末尾）。
積込料・取卸料の数え方は告示の文言からの読み取りで、Q&A の例で確かめたのは待機時間料だけ。
"""

from __future__ import annotations

import builtins
import http.client
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

from shipcompare import waiting
from shipcompare.tariffs import load_waiting_tariff

ROOT = Path(__file__).resolve().parents[1]


def row(**kw):
    base = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A運輸", "arrive": "09:00", "start": "10:00",
            "end": "", "handler": "", "method": "", "shipper_fault": "はい"}
    base.update(kw)
    return base


def one(**kw):
    res = waiting.calc([row(**kw)], 2026)
    assert res["errors"] == [], res["errors"]
    return res["rows"][0]


def test_tariff_file_has_source_and_all_values():
    t = load_waiting_tariff()
    assert t["source_url"] == "https://wwwtb.mlit.go.jp/kanto/content/000330329.pdf"
    assert t["applies_from"] == "2024-03-22" and t["checked_on"] == "2026-11-05"
    assert t["waiting"]["normal"] == {"小型": 1680, "中型": 1760, "大型": 1890, "トレーラー": 2220}
    assert t["waiting"]["over_2h"]["トレーラー"] == 2670
    assert t["handling"]["手積み"]["normal"]["中型"] == 2100
    assert t["handling"]["フォークリフト等"]["over_2h"]["トレーラー"] == 3300


# ---------------------------------------------------------------- G73
def test_g73_q29_one_hour_small_is_1680():
    r = one(start="10:00")
    assert (r["wait_min"], r["wait_normal_units"], r["wait_over_units"], r["wait_fee"]) == (60, 1, 0, 1680)


def test_g73_q29_two_hours_fifteen_small_is_7050():
    r = one(start="11:15")
    assert (r["wait_normal_units"], r["wait_over_units"]) == (3, 1)
    assert r["wait_fee"] == 1680 * 3 + 2010 == 7050


def test_g73_within_first_30_min_is_zero():
    assert one(start="09:30")["wait_fee"] == 0


# ---------------------------------------------------------------- G74
def test_g74_medium_eleven_times_52_min_is_19360():
    rows = [row(vehicle_class="中型", date=f"2026-10-{d:02d}", start="09:52") for d in range(1, 12)]
    res = waiting.calc(rows, 2026)
    assert res["errors"] == []
    p = res["by_partner"][0]
    assert p["count"] == 11 and p["amount"] == 19360 and p["wait_minutes"] == 11 * 52
    assert p["months"] == [{"month": "2026-10", **{k: p[k] for k in
            ("count", "wait_minutes", "handling_minutes", "wait_fee", "handling_fee", "amount", "over_1h_count", "over_2h_count")}}]


# ---------------------------------------------------------------- G75
def test_g75_not_shippers_fault_has_no_waiting_fee():
    r = one(start="11:15", shipper_fault="いいえ")
    assert r["wait_min"] == 0 and r["wait_fee"] == 0 and r["amount"] == 0


def test_g75_shipper_handling_counts_as_waiting_and_has_no_handling_fee():
    r = one(start="09:20", end="10:10", handler="荷主側", shipper_fault="はい")
    assert r["wait_min"] == 70 and r["handling_fee"] == 0
    assert r["wait_normal_units"] == 2 and r["wait_fee"] == 3360


def test_g75_driver_handling_fee_changes_at_two_hour_boundary_from_arrival():
    # 到着 09:00、荷役 10:50〜11:20。2 時間の境は 11:00
    r = one(start="10:50", end="11:20", handler="運転者", method="フォークリフト等")
    assert (r["handling_normal_units"], r["handling_over_units"]) == (1, 1)
    assert r["handling_fee"] == 2080 + 2490
    assert r["wait_fee"] == 3 * 1680  # 110 分 − 30 分 = 80 分 → 3 回
    assert r["amount"] == r["wait_fee"] + r["handling_fee"]


def test_g75_driver_handling_by_hand_and_not_fault_still_charges_handling():
    r = one(start="10:30", end="10:50", handler="運転者", method="手積み", shipper_fault="いいえ")
    assert r["wait_fee"] == 0 and r["handling_fee"] == 2000


def test_g75_handling_after_boundary_uses_over_2h_rate_only():
    r = one(vehicle_class="大型", start="11:10", end="12:00", handler="運転者", method="手積み")
    assert (r["handling_normal_units"], r["handling_over_units"]) == (0, 2)
    assert r["handling_fee"] == 2 * 2710


# ---------------------------------------------------------------- G76
def test_g76_midnight_crossing_is_next_day():
    r = one(arrive="23:30", start="00:40", end="01:10", depart="01:30", handler="運転者", method="手積み")
    assert r["crosses_midnight"] is True
    assert r["wait_raw_min"] == 70 and r["handling_min"] == 30
    assert r["wait_fee"] == 2 * 1680 and r["handling_fee"] == 2000


def test_g76_errors_have_japanese_reasons_and_rows_are_excluded():
    bad = [
        row(arrive="25:00"),
        row(start="abc"),
        row(partner=""),
        row(date=""),
        row(vehicle_class="軽"),
        row(shipper_fault=""),
        row(handler="運転者", end="10:30", method=""),
        row(date="13/40"),
        row(),  # 正常
    ]
    res = waiting.calc(bad, 2026)
    assert len(res["rows"]) == 1 and res["rows"][0]["row"] == 9
    by_row = {}
    for e in res["errors"]:
        assert any("぀" <= c <= "鿿" for c in e["message"]), e
        by_row.setdefault(e["row"], []).append(e["message"])
    assert set(by_row) == {1, 2, 3, 4, 5, 6, 7, 8}
    assert "到着" in by_row[1][0] and "荷役開始" in by_row[2][0] and "契約の相手" in by_row[3][0]
    assert "日付" in by_row[4][0] and "車種" in by_row[5][0] and "荷主等の都合" in by_row[6][0]
    assert "荷役の方法" in by_row[7][0]


def test_g76_md_date_needs_year_and_uses_given_year():
    res = waiting.calc([row(date="10/5")], None)
    assert res["rows"] == [] and "年" in res["errors"][0]["message"]
    assert waiting.calc([row(date="10/5")], 2026)["rows"][0]["date"] == "2026-10-05"


def test_g76_invalid_input_shapes():
    with pytest.raises(ValueError):
        waiting.calc("x", 2026)
    with pytest.raises(ValueError):
        waiting.calc([], 1999)
    assert waiting.calc(["x"], 2026)["errors"][0]["row"] == 1


# ---------------------------------------------------------------- G77
HEADER = "\t".join(["日付", "車番", "車種", "契約の相手", "場所", "到着", "荷役開始", "荷役終了", "出発", "荷役をした人",
                    "荷役の方法", "荷主等の都合の待機か"])
LINE1 = "10/5\t品川100あ1\t小型（2t）\tA運輸\t川崎\t09:00\t11:15\t\t\t\t\tはい"
LINE2 = "10/6\t\t中型\tB商事\t\t8:00\t8:40\t9:10\t9:30\t運転者\tフォークリフト等\tはい"


@pytest.mark.parametrize("with_header", [True, False])
def test_g77_tab_separated_paste(with_header):
    text = ("\r\n".join([HEADER, LINE1, LINE2]) if with_header else "\n".join([LINE1, LINE2])) + "\n"
    rows = waiting.parse_paste(text)
    assert len(rows) == 2 and rows[0]["partner"] == "A運輸"
    res = waiting.calc(rows, 2026)
    assert res["errors"] == []
    assert res["rows"][0]["wait_fee"] == 7050
    assert res["rows"][1]["handling_fee"] == 2180 and res["rows"][1]["wait_fee"] == 1760


def test_g77_header_with_different_column_order_is_followed():
    head = "契約の相手\t日付\t車種\t到着\t荷役開始\t荷主等の都合の待機か"
    res = waiting.calc(waiting.parse_paste(head + "\nA運輸\t10/5\t小型\t09:00\t10:00\tはい"), 2026)
    assert res["errors"] == [] and res["rows"][0]["wait_fee"] == 1680


# ---------------------------------------------------------------- 集計
def test_aggregate_by_partner_and_month():
    rows = [row(start="10:00"), row(date="2026-11-02", start="11:15"), row(partner="B商事", start="10:00")]
    res = waiting.calc(rows, 2026)
    a = next(p for p in res["by_partner"] if p["partner"] == "A運輸")
    assert [m["month"] for m in a["months"]] == ["2026-10", "2026-11"]
    assert a["amount"] == 1680 + 7050 and a["over_1h_count"] == 2 and a["over_2h_count"] == 1
    assert res["by_partner"][0]["partner"] == "A運輸"  # 参考額の大きい順


# ---------------------------------------------------------------- G78（API と、サーバーが書かないこと）
def _server():
    spec = importlib.util.spec_from_file_location("serve_web", ROOT / "tools" / "serve_web.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["serve_web"] = mod
    spec.loader.exec_module(mod)
    return mod


def _post(port, body):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request("POST", "/api/waiting/calc", body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return resp.status, data


def test_g78_api_calc_and_does_not_write_files(monkeypatch):
    mod = _server()
    httpd = mod.make_server(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    writes: list[tuple] = []
    real_open = builtins.open

    def spy(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            writes.append((file, mode))
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    try:
        status, data = _post(port, {"year": 2026, "rows": [row(start="11:15", partner="秘密商事")]})
        status_t, data_t = _post(port, {"year": 2026, "text": LINE1})
        status_e, data_e = _post(port, {"year": 2026})
        status_b, _ = _post(port, {"year": "x", "rows": []})
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 200 and data["rows"][0]["wait_fee"] == 7050
    assert data["by_partner"][0]["partner"] == "秘密商事" and data["errors"] == []
    assert status_t == 200 and data_t["rows"][0]["wait_fee"] == 7050
    assert status_e == 400 and "error" in data_e
    assert status_b == 400
    assert writes == []  # 記録を保存しない（ファイルを書き込みで開いていない）


def test_g78_api_text_returns_input_rows_for_the_table():
    mod = _server()
    httpd = mod.make_server(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, data = _post(httpd.server_address[1], {"year": 2026, "text": "\n".join([HEADER, LINE1, LINE2])})
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 200 and len(data["input_rows"]) == 2 and data["input_rows"][0]["partner"] == "A運輸"
    assert data["tariff"]["waiting"]["normal"]["中型"] == 1760 and data["tariff"]["handling"]["手積み"]["normal"]["小型"] == 2000
    assert data["by_partner"][0]["date_from"] == "2026-10-05" and data["by_partner"][0]["date_to"] == "2026-10-05"


# ---------------------------------------------------------------- SPEC 23.6 の訂正
def test_s23_6_over_1h_counts_waiting_only_and_over_2h_counts_waiting_plus_driver_handling():
    # 待機 50 分 + 運転者の荷役 20 分: 1 時間以上ではない。合計 70 分で 2 時間以上でもない
    r = one(start="09:50", end="10:10", handler="運転者", method="手積み")
    assert (r["over_1h"], r["over_2h_total"]) == (False, False)
    # 待機 50 分 + 運転者の荷役 80 分: 1 時間以上ではないが、合計 130 分で 2 時間以上
    r = one(start="09:50", end="11:10", handler="運転者", method="手積み")
    assert (r["wait_min"], r["handling_min"], r["over_1h"], r["over_2h_total"]) == (50, 80, False, True)
    # 待機 60 分ちょうど: 1 時間以上。荷役なしで 2 時間未満
    r = one(start="10:00")
    assert (r["over_1h"], r["over_2h_total"]) == (True, False)
    # 荷主側の荷役は待機に入る: 待機 30 分 + 荷主側の荷役 30 分 = 60 分 → 1 時間以上
    r = one(start="09:30", end="10:00", handler="荷主側")
    assert (r["wait_min"], r["over_1h"]) == (60, True)
    # 荷主等の都合でない待機は 0 分。運転者の荷役が 2 時間以上でも 1 時間以上の荷待ちには入らない
    r = one(start="09:10", end="11:30", handler="運転者", method="手積み", shipper_fault="いいえ")
    assert (r["wait_min"], r["over_1h"], r["over_2h_total"]) == (0, False, True)


def test_s23_6_partner_counts_follow_the_two_rules():
    rows = [row(start="09:50", end="11:10", handler="運転者", method="手積み"), row(date="2026-10-06", start="10:00"),
            row(date="2026-10-07", start="09:20")]
    p = waiting.calc(rows, 2026)["by_partner"][0]
    assert (p["over_1h_count"], p["over_2h_count"]) == (1, 1)


# ---------------------------------------------------------------- G78（画面の文言）
OWN_JS = (ROOT / "web" / "own.js").read_text(encoding="utf-8")


def test_g78_ui_always_shows_protect_lines_and_legal_basis():
    for text in ("参考額は告示の表に当てた計算です。請求するか、いくらにするかは運送会社が決めます。法的な判断は弁護士・運輸支局などに確かめてください",
                 "入れた記録はこの PC の中だけで計算し、外に送りません",
                 "改正法 Q&A 問2-2・2-3", "標準運送約款 第 34 条", "取適法", "Q42・Q132", "トラック・物流Gメンの情報提供の窓口"):
        assert text in OWN_JS, text
    # 守る線と根拠の欄は、計算の結果の有無に関わらず右の欄に出す（waitRightHtml の先頭で作る）
    head = OWN_JS[OWN_JS.index("function waitRightHtml()"):]
    assert head.index("const protect") < head.index("if (!r ||") and head.index("const basis") < head.index("if (!r ||")
    assert 'class="w-protect no-print"' not in OWN_JS  # 画面では隠さない


def test_g78_ui_has_the_four_tab_wait_input_and_examples():
    assert 'data-tab="wait"' in OWN_JS and "荷待ちを入れる" in OWN_JS
    for label in ("日付", "車番", "車種", "契約の相手", "場所", "到着", "荷役開始", "荷役終了", "出発", "荷役をした人", "荷役の方法", "荷主等の都合の待機か"):
        assert f"t: '{label}'" in OWN_JS, label
    assert "例を入れる" in OWN_JS and "架空 元請A物流" in OWN_JS and "江東区 地点07" in OWN_JS and "W_EXAMPLE_DAYS = [1, 2, 5, 6, 7, 8, 9, 13, 14, 15, 16]" in OWN_JS
    assert "/api/waiting/calc" in OWN_JS and "text/csv" in OWN_JS and "window.print()" in OWN_JS


def test_g78_ui_example_rows_give_11_times_52_min_and_19360_yen():
    """画面の「例を入れる」と同じ行を API の計算に通すと、G74 の 19,360 円になる。"""
    ex = [{"date": f"2026-10-{d:02d}", "vehicle_class": "中型", "partner": "架空 元請A物流", "place": "江東区 地点07", "arrive": "09:00",
           "start": "09:30", "end": "09:52", "depart": "10:00", "handler": "荷主側", "shipper_fault": "はい"} for d in (1, 2, 5, 6, 7, 8, 9, 13, 14, 15, 16)]
    res = waiting.calc(ex, 2026)
    assert res["errors"] == []
    p = res["by_partner"][0]
    assert p["count"] == 11 and p["wait_minutes"] == 11 * 52 and p["amount"] == 19360 and p["over_1h_count"] == 0


def test_g78_print_css_prints_only_the_draft():
    css = (ROOT / "web" / "own.css").read_text(encoding="utf-8")
    assert "@media print" in css and "#wait-doc" in css
