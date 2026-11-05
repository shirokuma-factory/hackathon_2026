"""版3の2段目: 地図の画面の配信サーバーと API（SPEC §15、受入条件 G29〜G31）。

サーバーは空いているポートで別スレッドに立て、API の結果を同じ入力の Python の関数の結果と突き合わせる。
画面の見た目は tools/capture_web.py の画面写真で確かめる。
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import re
import sys
import threading
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from shipcompare import geo
from shipcompare.estimate import estimate_all, summarize
from shipcompare.guide import contacts_for_rows
from shipcompare.models import COST_KEYS
from shipcompare.quotes import compare_quotes, line_amounts_ex_tax
from shipcompare.request import build_request
from shipcompare.storage import (
    ORDER_NOTICE, case_from_dict, case_to_dict, demo_quotes_for, list_scenarios, load_scenario, quote_from_dict,
    quote_to_dict, shipment_from_dict, shipment_to_dict,
)
from shipcompare.tariffs import load_tariff

ROOT = Path(__file__).resolve().parents[1]
JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龥]")
TODAY = date(2026, 11, 3)


def load_server_module():
    spec = importlib.util.spec_from_file_location("serve_web", ROOT / "tools" / "serve_web.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["serve_web"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def srv():
    mod = load_server_module()
    httpd = mod.make_server(0)  # 空いているポート
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield mod, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, headers=None, raw=None):
    """(状態コード, JSON か本文) を返す。body は JSON にして送る。raw があればそのまま送る。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    hdr = {} if headers is None else dict(headers)
    data = None
    if raw is not None:
        data = raw
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdr.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=data, headers=hdr)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    if resp.getheader("Content-Type", "").startswith("application/json"):
        return resp.status, json.loads(payload.decode("utf-8"))
    return resp.status, payload


def post(port, path, body):
    return call(port, "POST", path, body)


def get(port, path):
    return call(port, "GET", path)


def scenario(sid):
    return load_scenario(sid)


def empty_case():
    return {"schema_version": 2, "versions": [], "quotes": [], "chosen_quote_id": None, "reason": "", "open_issues": []}


def row_key(r):
    return (r["mode"], r["status"], r["amount"], r["low"], r["high"], tuple(r["reasons"]))


def direct_key(r):
    return (r.mode, r.status, r.amount if r.has_amount() else None, r.low, r.high, tuple(r.reasons))


# ---------------------------------------------------------------- G30: API の結果が Python の関数の結果と一致する
def test_spec15_1_estimate_matches_direct_for_every_scenario(srv):
    _, port = srv
    for sc in list_scenarios():
        s = sc["shipment"]
        for dest in ("27128", "04101"):  # 大阪市中央区（kanto_kansai）、仙台市青葉区（kanto_tohoku）
            status, r = post(port, "/api/estimate", {"shipment": shipment_to_dict(s), "origin_code": "13104", "dest_code": dest})
            assert status == 200, r
            direct_s = replace(s, route=geo.route_for("13104", dest), distance_km=None)
            direct = estimate_all(direct_s)
            assert [row_key(x) for x in r["rows"]] == [direct_key(x) for x in direct], (sc["id"], dest)
            assert r["route"] == direct_s.route
            t = summarize(direct_s)
            assert r["totals"]["weight_kg"] == (float(t.weight_kg) if t.weight_kg is not None else None) or \
                r["totals"]["weight_kg"] == t.weight_kg
            assert r["totals"]["problems"] == t.problems


def test_spec15_2_estimate_other_route_uses_osrm_distance(srv):
    _, port = srv
    dest = "23106"  # 愛知県名古屋市（大阪府・宮城県ではないので区間は other）
    info = geo.drive_info("13104", dest)
    assert info and not info["ferry"] and not info["no_route"]
    s = scenario("S1")["shipment"]
    status, r = post(port, "/api/estimate", {"shipment": shipment_to_dict(s), "origin_code": "13104", "dest_code": dest})
    assert status == 200 and r["route"] == "other" and r["distance_km"] == info["km"]
    direct = estimate_all(replace(s, route="other", distance_km=info["km"], origin_in_kanto=True))
    assert [row_key(x) for x in r["rows"]] == [direct_key(x) for x in direct]
    assert r["shipment"]["route"] == "other" and r["shipment"]["distance_km"] == info["km"]
    # 宅配・路線便・カゴ台車は運賃表がないので金額を出さない（0 や推定で埋めない）
    for g in ("takuhai", "rosen", "jitbox"):
        row = next(x for x in r["rows"] if x["group"] == g)
        assert row["amount"] is None and row["status"] == "計算できない"


def test_spec15_3_estimate_rows_carry_source_and_published_days(srv):
    _, port = srv
    s = scenario("S1")["shipment"]
    _, r = post(port, "/api/estimate", {"shipment": shipment_to_dict(s), "origin_code": "13104", "dest_code": "27128"})
    tk = next(x for x in r["rows"] if x["group"] == "takuhai")
    assert tk["source"]["url"].startswith("https://") and tk["source"]["checked_on"]
    assert tk["transit"]["published"] is True and "1日" in tk["transit"]["text"]
    rosen = next(x for x in r["rows"] if x["group"] == "rosen")
    assert rosen["transit"]["published"] is False and "公表なし" in rosen["transit"]["text"]


def test_spec15_4_request_matches_direct(srv):
    _, port = srv
    for sid in ("S1", "S5"):
        sc = scenario(sid)
        s = sc["shipment"]
        info = {"id": sid, "name": sc["name"], "label": sc["label"], "modified": False}
        status, r = post(port, "/api/request", {"shipment": shipment_to_dict(s), "scenario": info})
        assert status == 200
        d = build_request(s, info)
        assert r["text"] == d.text and r["status"] == d.status
        assert r["missing_required"] == d.missing_required and r["missing_optional"] == d.missing_optional
        want = contacts_for_rows(estimate_all(s))
        assert [(c["mode"], c["name"], c["url"], c["condition"], c["checked_on"]) for c in r["contacts"]] == \
            [(c.mode, c.name, c.url, c.condition, c.checked_on) for c in want]
        assert f"{sc['name']}（想定の荷主）の例です" in r["text"]
    # 貸切は窓口がないので「窓口の確認が必要」の行になる（連絡先を足さない）
    kashikiri = [c for c in r["contacts"] if c["mode"] == "kashikiri"]
    assert kashikiri and all(c["url"] is None and c["has_window"] is False for c in kashikiri)


def test_spec15_5_compare_matches_direct_and_breakdown_sums(srv):
    _, port = srv
    sc = scenario("S5")
    quotes = demo_quotes_for(sc, 1, TODAY)
    for i, q in enumerate(quotes, start=1):
        q.id = f"q{i}"
    tax_in = replace(quotes[1], id="q3", carrier="税込C社", tax="税込",
                     lines={k: replace(v, amount=(v.amount * 11 // 10 + 7 if v.amount else None)) for k, v in quotes[1].lines.items()})
    quotes.append(tax_in)
    status, r = post(port, "/api/compare", {"quotes": [quote_to_dict(q) for q in quotes], "version": 1, "today": TODAY.isoformat()})
    assert status == 200
    d = compare_quotes(quotes, 1, TODAY)
    assert r["message"] == d.message and r["ranking_shown"] == d.ranking_shown and r["notice"] == ORDER_NOTICE
    assert [e["quote"]["id"] for e in r["evaluations"]] == [e.quote.id for e in d.evaluations]
    for got, want in zip(r["evaluations"], d.evaluations):
        assert (got["state"], got["total_ex_tax"], got["partial_ex_tax"], got["rank"], got["diff_from_lowest"],
                got["unknown_keys"], got["summary"]) == (want.state, want.total_ex_tax, want.partial_ex_tax, want.rank,
                                                         want.diff_from_lowest, want.unknown_keys, want.summary)
        assert got["line_amounts_ex_tax"] == line_amounts_ex_tax(want.quote)
        # 内訳の合計は回答の合計（税抜）と一致する（G12）
        if want.state == "complete":
            assert sum(got["line_amounts_ex_tax"].values()) == want.total_ex_tax
    assert [x["key"] for x in r["cost_keys"]] == list(COST_KEYS)


def test_spec15_6_save_and_load_equal_direct(srv):
    _, port = srv
    sc = scenario("S5")
    case = case_from_dict(empty_case())
    case.confirm(sc["shipment"], "2026-11-03T10:00:00", {"id": "S5", "name": sc["name"], "label": sc["label"], "modified": False})
    for i, q in enumerate(demo_quotes_for(sc, 1, TODAY), start=1):
        q.id = f"q{i}"
        case.quotes.append(q)
    case.chosen_quote_id, case.reason, case.open_issues = "q2", "総額が最も低い", ["受託の可否"]
    sent = case_to_dict(case)
    status, saved = post(port, "/api/save", sent)
    assert status == 200 and saved["saved_at"]
    want = dict(case_to_dict(case_from_dict(sent)), saved_at=saved["saved_at"])
    assert saved == want and saved["notice"] == ORDER_NOTICE and saved["schema_version"] == 2
    status, loaded = post(port, "/api/load", saved)
    assert status == 200 and loaded == saved
    assert case_to_dict(case_from_dict(loaded)) == loaded
    # 版1の JSON も読めて、保存し直すと版2になる
    old = {k: v for k, v in sent.items() if k not in ("notice", "estimates", "scenario")}
    old["schema_version"] = 1
    for v in old["versions"]:
        v.pop("scenario", None)
    status, back = post(port, "/api/load", old)
    assert status == 200 and back["schema_version"] == 2 and back["chosen_quote_id"] == "q2"


def test_spec15_7_scenarios_and_demo_quotes(srv):
    _, port = srv
    status, r = get(port, "/api/scenarios")
    assert status == 200
    ids = [s["id"] for s in r["scenarios"]]
    assert ids == [s["id"] for s in list_scenarios()] and ids == ["S1", "S2", "S3", "S4", "S5", "S6"]
    s5 = next(s for s in r["scenarios"] if s["id"] == "S5")
    assert s5["has_demo_quotes"] and s5["label"] == "想定の荷主" and s5["shipment"]["items"][0]["count"] == 8
    assert not next(s for s in r["scenarios"] if s["id"] == "S1")["has_demo_quotes"]
    status, d = get(port, "/api/demo_quotes?id=S5&version=3&today=2026-11-03")
    assert status == 200
    want = [quote_to_dict(q) for q in demo_quotes_for(scenario("S5"), 3, TODAY)]
    assert d["quotes"] == want
    assert [q["carrier"] for q in d["quotes"]] == ["架空A社", "架空B社"]
    assert all(q["is_demo"] and q["version"] == 3 and q["valid_until"] == (TODAY + timedelta(days=14)).isoformat() for q in d["quotes"])


# ---------------------------------------------------------------- 入力の誤りは 400 と日本語の理由
def test_spec15_8_bad_input_returns_400_with_japanese_message(srv):
    _, port = srv
    ship = shipment_to_dict(scenario("S1")["shipment"])
    good_quote = quote_to_dict(replace(demo_quotes_for(scenario("S5"), 1, TODAY)[1], id="q1"))
    cases = [
        ("/api/estimate", {"origin_code": "13104", "dest_code": "27128"}),  # shipment なし
        ("/api/estimate", {"shipment": "x"}),
        ("/api/estimate", {"shipment": {"items": "x"}}),
        ("/api/estimate", {"shipment": dict(ship, route="no_such")}),
        ("/api/estimate", {"shipment": dict(ship, sender_type="宇宙人")}),
        ("/api/estimate", {"shipment": ship, "origin_code": "99999", "dest_code": "27128"}),
        ("/api/estimate", {"shipment": ship, "origin_code": "13104", "dest_code": "abc"}),
        ("/api/estimate", {"shipment": ship, "origin_code": "13104", "dest_code": 27128}),
        ("/api/layer", {"shipment": ship, "origin_code": "01101"}),
        ("/api/request", {"shipment": ship, "scenario": "S5"}),
        ("/api/request", [1, 2]),
        ("/api/compare", {"quotes": "x", "version": 1}),
        ("/api/compare", {"quotes": [good_quote], "version": "1"}),
        ("/api/compare", {"quotes": [good_quote], "version": 1, "today": "2026/11/03"}),
        ("/api/compare", {"quotes": [dict(good_quote, lines={"trunk": {"kind": "free", "amount": 1}})], "version": 1}),
        ("/api/compare", {"quotes": [dict(good_quote, lines={"trunk": {"kind": "amount", "amount": 1.5}})], "version": 1}),
        ("/api/compare", {"quotes": [{"carrier": "x"}], "version": 1}),
        ("/api/save", {"schema_version": 9}),
        ("/api/load", {"schema_version": 3, "versions": []}),
        ("/api/load", ["x"]),
        ("/api/confirm", {"case": empty_case(), "shipment": ship, "scenario_id": "S99"}),
        ("/api/confirm", {"case": empty_case(), "shipment": ship, "scenario_id": "../etc"}),
        ("/api/add_quote", {"case": empty_case(), "quote": good_quote}),  # 条件を確定していない
        ("/api/add_demo_quotes", {"case": empty_case()}),
    ]
    for path, body in cases:
        status, r = post(port, path, body)
        assert status == 400, (path, body, status, r)
        assert JAPANESE.search(r["error"]), (path, r)
    status, r = get(port, "/api/demo_quotes?id=S5")  # version なし
    assert status == 400 and JAPANESE.search(r["error"])
    status, r = get(port, "/api/demo_quotes?id=..%2Fsecret&version=1")
    assert status == 400 and JAPANESE.search(r["error"])


def test_spec15_9_protocol_errors(srv):
    _, port = srv
    status, r = call(port, "POST", "/api/estimate", raw=b"{not json", headers={"Content-Type": "application/json"})
    assert status == 400 and JAPANESE.search(r["error"])
    status, r = call(port, "POST", "/api/estimate", raw=b"{}", headers={"Content-Type": "text/plain"})
    assert status == 400 and "application/json" in r["error"]
    status, r = call(port, "POST", "/api/nothing", body={})
    assert status == 404 and JAPANESE.search(r["error"])
    status, r = call(port, "GET", "/api/estimate")
    assert status == 405
    status, r = call(port, "POST", "/api/scenarios", body={})
    assert status == 405
    big = b'{"x":"' + b"a" * 2_100_000 + b'"}'
    status, r = call(port, "POST", "/api/estimate", raw=big, headers={"Content-Type": "application/json"})
    assert status == 413 and JAPANESE.search(r["error"])
    # Host が 127.0.0.1 / localhost 以外（DNS の書き換えなど）は断る
    status, r = call(port, "GET", "/api/scenarios", headers={"Host": "evil.example"})
    assert status == 400 and JAPANESE.search(r["error"])


def test_spec15_10_static_files_and_traversal(srv):
    mod, port = srv
    status, html = get(port, "/")
    assert status == 200 and "スポット出荷マップ".encode() in html and b"work.js" in html
    for name in ("app.js", "work.js", "style.css", "data/meta.json"):
        assert get(port, "/" + name)[0] == 200
    assert not (ROOT / "web" / "data" / "estimates.json").exists()  # 目安額は API で計算する
    for evil in ("/../CLAUDE.md", "/..%2fCLAUDE.md", "/%2e%2e/CLAUDE.md", "/data/../../CLAUDE.md"):
        status, body = get(port, evil)
        assert not (status == 200 and "ハッカソン".encode() in body), evil
    httpd_host = "127.0.0.1"
    assert mod.DEFAULT_PORT == 8600 and httpd_host in mod.ALLOWED_HOSTS


def test_spec15_11_server_binds_loopback_only(srv):
    mod, _ = srv
    httpd = mod.make_server(0)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()


# ---------------------------------------------------------------- G31: 離島・フェリー（SPEC §15.3）
def test_spec15_12_ferry_boundary_25_kmh():
    assert geo.is_ferry_route(25, 60) is False  # ちょうど 25km/h は判定しない
    assert geo.is_ferry_route(12.5, 30) is False
    assert geo.is_ferry_route(100, 240) is False  # 25km/h
    assert geo.is_ferry_route(24.99, 60) is True
    assert geo.is_ferry_route(99.9, 240) is True
    assert geo.is_ferry_route(25.01, 60) is False
    assert geo.is_ferry_route(50, 119) is False and geo.is_ferry_route(50, 121) is True
    assert geo.is_ferry_route(None, 60) is False and geo.is_ferry_route(10, None) is False  # 経路なしは別扱い
    assert geo.is_ferry_route(0, 0) is False and geo.is_ferry_route(5, 0) is False  # 出発地と同じ市区町村
    assert geo.FERRY_MAX_AVG_KMH == 25


def test_spec15_13_ferry_and_no_route_have_no_days_or_fare(srv):
    _, port = srv
    ferry_code = "13421"
    assert "小笠原" in geo.load_points()[ferry_code]["name"]
    info = geo.drive_info("13104", ferry_code)
    assert info["ferry"] is True and info["days"] is None and info["km"] is not None  # 距離だけは残す
    assert geo.drive_days(info["minutes"]) is not None  # 純粋な日数の関数は変えない
    none_code = next(c for c, d in geo.load_osrm("13104").items() if d["km"] is None)
    ninfo = geo.drive_info("13104", none_code)
    assert ninfo["no_route"] is True and ninfo["ferry"] is False and ninfo["days"] is None
    s = shipment_to_dict(scenario("S1")["shipment"])
    status, r = post(port, "/api/estimate", {"shipment": s, "origin_code": "13104", "dest_code": ferry_code})
    assert status == 200 and "海を渡る経路" in r["blocked_reason"]
    kas = [x for x in r["rows"] if x["group"] == "kashikiri"]
    assert len(kas) == 1 and kas[0]["status"] == "計算できない" and kas[0]["amount"] is None
    assert "海を渡る経路" in kas[0]["reasons"][0] and kas[0]["transit"] is None
    status, r2 = post(port, "/api/estimate", {"shipment": s, "origin_code": "13104", "dest_code": none_code})
    assert status == 200 and "経路がありません" in r2["blocked_reason"]
    assert all(x["amount"] is None for x in r2["rows"] if x["group"] == "kashikiri")
    # 貸切の層: 該当の着地は None、地図用の JSON も日数と額が null で距離は残る
    codes = sorted(geo.load_points())
    fare = geo.fare_layer("13104", None, load_tariff("kashikiri"), codes)
    assert fare[codes.index(ferry_code)] is None and fare[codes.index(none_code)] is None
    od = json.loads((ROOT / "web" / "data" / "origin_13104.json").read_text(encoding="utf-8"))
    i = codes.index(ferry_code)
    assert od["ferry"][i] is True and od["days"][i] is None and od["fare"][i] is None and od["km"][i] is not None
    assert od["cls"]["days"][i] is None and od["cls"]["fare"][i] is None
    assert sum(od["ferry"]) == sum(1 for c in codes if geo.is_ferry_route(*(geo.load_osrm("13104")[c][k] for k in ("km", "minutes"))))


def test_spec15_14_layer_api_matches_direct_and_follows_cargo(srv):
    _, port = srv
    codes = sorted(geo.load_points())
    tariff = load_tariff("kashikiri")
    s = scenario("S5")["shipment"]
    status, r = post(port, "/api/layer", {"origin_code": "13104", "shipment": shipment_to_dict(s)})
    assert status == 200 and r["total_kg"] == 160
    assert r["fare"] == geo.fare_layer("13104", 160, tariff, codes)
    present = [v for v in r["fare"] if v is not None]
    assert r["edges"] == geo.quantile_edges(present)
    assert r["cls"] == [geo.class_of(v, r["edges"]) if v is not None else None for v in r["fare"]]
    heavy = replace(s, items=[replace(s.items[0], count=100, weight_kg=40)])  # 4,000kg は 2t に載らない
    _, h = post(port, "/api/layer", {"origin_code": "13104", "shipment": shipment_to_dict(heavy)})
    assert h["total_kg"] == 4000 and h["fare"] != r["fare"]
    assert h["fare"] == geo.fare_layer("13104", 4000, tariff, codes)


# ---------------------------------------------------------------- 版の規則（API が Case.confirm を呼ぶ）
def test_spec15_15_confirm_rules(srv):
    _, port = srv
    sc = scenario("S5")
    ship = shipment_to_dict(sc["shipment"])
    status, r = post(port, "/api/confirm", {"case": empty_case(), "shipment": ship, "scenario_id": "S5"})
    assert status == 200 and r["changed"] and r["case"]["versions"][0]["number"] == 1
    assert r["case"]["versions"][0]["scenario"] == {"id": "S5", "name": sc["name"], "label": sc["label"], "modified": False}
    # 同じ内容で確定しても版は増えず、場面の情報も変わらない（選択欄が「新しく入力」でも）
    status, same = post(port, "/api/confirm", {"case": r["case"], "shipment": ship, "scenario_id": None})
    assert status == 200 and same["changed"] is False and same["case"]["versions"] == r["case"]["versions"]
    # 内容を変えると版2、場面の情報は modified: true
    changed = dict(ship, pickup="持込")
    status, v2 = post(port, "/api/confirm", {"case": r["case"], "shipment": changed, "scenario_id": "S5"})
    assert v2["changed"] and v2["case"]["versions"][1]["number"] == 2
    assert v2["case"]["versions"][1]["scenario"]["modified"] is True
    # 場面を変えた版では架空の回答例を入れられない
    status, e = post(port, "/api/add_demo_quotes", {"case": v2["case"]})
    assert status == 400 and "場面を変えずに" in e["error"]


# ---------------------------------------------------------------- G29: S5 の筋書きを API だけでたどる
def test_spec15_16_s5_story_end_to_end_via_api(srv):
    _, port = srv
    sc = scenario("S5")
    ship = shipment_to_dict(sc["shipment"])

    # 荷物の入力 → 着地の選択（目安額）→ 確定
    _, est = post(port, "/api/estimate", {"shipment": ship, "origin_code": "13104", "dest_code": "27128"})
    assert est["route"] == "kanto_kansai" and est["shipment"]["route"] == "kanto_kansai"
    _, c = post(port, "/api/confirm", {"case": empty_case(), "shipment": est["shipment"], "scenario_id": "S5"})
    case = c["case"]
    assert case["versions"][0]["scenario"]["modified"] is False  # 区間は場面と同じなので「変えた」にならない

    # 依頼文
    _, req = post(port, "/api/request", {"shipment": case["versions"][0]["shipment"], "scenario": case["versions"][0]["scenario"]})
    assert "合計重量: 160 kg" in req["text"] and req["contacts"]

    # 架空の回答例（A社 18,000 は幹線だけ、B社 26,000 は完全）。二重には入らない
    _, d1 = post(port, "/api/add_demo_quotes", {"case": case, "today": TODAY.isoformat()})
    assert d1["added"] == 2
    _, d2 = post(port, "/api/add_demo_quotes", {"case": d1["case"], "today": TODAY.isoformat()})
    assert d2["added"] == 0 and len(d2["case"]["quotes"]) == 2
    _, cmp1 = post(port, "/api/compare", {"quotes": d2["case"]["quotes"], "version": 1, "today": TODAY.isoformat()})
    assert cmp1["ranking_shown"] is False and cmp1["message"] == "比べる相手がいません（完全な回答が1件だけです）"
    by = {e["quote"]["carrier"]: e for e in cmp1["evaluations"]}
    a, b = by["架空A社"], by["架空B社"]
    assert a["state"] == "incomplete" and a["rank"] is None and a["total_ex_tax"] is None
    assert a["partial_ex_tax"] == 18000 and a["line_amounts_ex_tax"] == {"trunk": 18000}
    assert a["unknown_keys"] == ["pickup", "delivery", "handling", "fuel", "highway", "other"]
    assert a["summary"].startswith("部分合計 18,000円（税抜） + 不明: 集荷・配達")
    assert b["state"] == "complete" and b["total_ex_tax"] == 26000 and b["rank"] is None  # 相手がいないので順位なし

    # 利用者が A社の追加回答（集荷 6,000・幹線 18,000・配達 5,000・ほかは含む = 29,000）を手で入れる
    lines = {"pickup": {"kind": "amount", "amount": 6000}, "trunk": {"kind": "amount", "amount": 18000},
             "delivery": {"kind": "amount", "amount": 5000}}
    lines.update({k: {"kind": "included", "amount": None} for k in ("handling", "fuel", "highway", "other")})
    quote = {"carrier": "架空A社", "product": "路線便", "tax": "税抜", "received_on": TODAY.isoformat(),
             "valid_until": (TODAY + timedelta(days=14)).isoformat(), "is_demo": True, "lines": lines}
    _, added = post(port, "/api/add_quote", {"case": d2["case"], "quote": quote})
    assert added["quote_id"] == "q3"
    _, cmp2 = post(port, "/api/compare", {"quotes": added["case"]["quotes"], "version": 1, "today": TODAY.isoformat()})
    assert cmp2["ranking_shown"] is True
    ranked = [e for e in cmp2["evaluations"] if e["rank"] is not None]
    assert [(e["quote"]["carrier"], e["total_ex_tax"], e["rank"], e["diff_from_lowest"]) for e in ranked] == \
        [("架空B社", 26000, 1, 0), ("架空A社", 29000, 2, 3000)]
    old_a = next(e for e in cmp2["evaluations"] if e["quote"]["id"] == "q1")
    assert old_a["rank"] is None and old_a["state"] == "incomplete"  # 18,000 の回答は順位に入らないまま
    assert sum(ranked[1]["line_amounts_ex_tax"].values()) == 29000

    # 判断 → 保存 → 読み込み（保存はサーバーにファイルを書かない）
    final = dict(added["case"], chosen_quote_id="q2", reason="集荷から配達まで含み総額が最も低い", open_issues=["受託の可否"])
    files_before = {p for p in ROOT.rglob("shipment_case_*.json")}
    _, saved = post(port, "/api/save", final)
    assert {p for p in ROOT.rglob("shipment_case_*.json")} == files_before
    assert saved["notice"] == ORDER_NOTICE and saved["chosen_quote_id"] == "q2" and saved["scenario"]["id"] == "S5"
    assert [e["version"] for e in saved["estimates"]] == [1]
    _, loaded = post(port, "/api/load", json.loads(json.dumps(saved)))
    assert loaded["quotes"] == saved["quotes"] and loaded["reason"] == final["reason"]
    _, cmp3 = post(port, "/api/compare", {"quotes": loaded["quotes"], "version": 1, "today": TODAY.isoformat()})
    assert [(e["quote"]["id"], e["rank"], e["diff_from_lowest"]) for e in cmp3["evaluations"]] == \
        [(e["quote"]["id"], e["rank"], e["diff_from_lowest"]) for e in cmp2["evaluations"]]

    # 条件を変えて確定すると版2になり、古い回答は履歴になる（順位に入らない）
    _, v2 = post(port, "/api/confirm", {"case": loaded, "shipment": dict(ship, delivery="引取"), "scenario_id": "S5"})
    assert v2["case"]["versions"][-1]["number"] == 2
    _, cmp4 = post(port, "/api/compare", {"quotes": v2["case"]["quotes"], "version": 2, "today": TODAY.isoformat()})
    assert {e["state"] for e in cmp4["evaluations"]} == {"history"} and cmp4["ranking_shown"] is False


def test_spec15_17_settings_and_files():
    launch = json.loads((ROOT / ".claude" / "launch.json").read_text(encoding="utf-8"))
    web = next(c for c in launch["configurations"] if c["name"] == "web-map")
    assert web["runtimeArgs"][-1] == "tools/serve_web.py" and web["port"] == 8600
    assert any(c["name"] == "shipcompare" for c in launch["configurations"])
    src = (ROOT / "tools" / "serve_web.py").read_text(encoding="utf-8")
    assert "\r" not in src and "import fastapi" not in src and "import flask" not in src
    for name in ("serve_web.py", "capture_web.py"):
        assert b"\r\n" not in (ROOT / "tools" / name).read_bytes()
    for name in ("index.html", "app.js", "work.js", "style.css", "check.js"):
        assert b"\r\n" not in (ROOT / "web" / name).read_bytes()
    # ?check=1 のときだけ読み込む（通常の利用者には追加の読み込みがない）
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "qs.get('check')" in app and "check.js" not in (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    # 画面側に運賃や順位の計算を持ち込んでいない（API の結果を表示するだけ）
    js = (ROOT / "web" / "work.js").read_text(encoding="utf-8") + (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "diff_from_lowest" in js and "/api/compare" in js and "/api/estimate" in js
    assert not re.search(r"\.sort\(\(a, b\) => a\.(total|amount)", js)
