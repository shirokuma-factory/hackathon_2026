"""SPEC §53 荷待ち協議の書面の「Claude で説明文を作る」。SDK は全部差し替える（本物の API は呼ばない。キーも使わない）。

- 3 段（Haiku → Sonnet → Opus）の順、モデル名、出力の指定（構造化出力・effort・thinking を送らない）
- 断り（refusal）・確かめの失敗・数字の機械照合で、下書きを出さず型どおりに戻ること
- 上限（1 日・来訪者ごと・大きさ）、状態、同じ入力の再利用、キーが応答にもログにも出ないこと
- 画面: ボタンは使えるときだけ。進み具合の表示（ヘッドレス Edge と、差し替えた応答を返す手元のサーバー）
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from shipcompare import ai_explain as ax

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import serve_web  # noqa: E402

SENTINEL = "sk-" + "ant-TESTSENTINEL-0123456789"


# ---------------------------------------------------------------- 差し替えの部品
def facts_sample(n: int = 2) -> dict:
    rows = [{"row": i + 1, "date": f"2026-10-{i + 1:02d}", "place": "地点1", "arrive": "09:00", "start": "10:30", "end": "11:00",
             "handler": "運転者", "shipper_fault": "はい", "wait_min": 90, "handling_min": 30, "amount": 1680 + i,
             } for i in range(n)]
    return {"partner": "荷主A", "period": "2026-10-01 〜 2026-10-02", "count": n, "rows": rows,
            "totals": {"amount_yen_ex_tax": 3361, "wait_total": "3 時間 0 分", "handling_total": "1 時間 0 分"},
            "basis": {"tariff_applies_from": "2024-05-15", "unit_minutes": 30, "free_minutes": 30, "quote": "告示の別紙の表に当てた額です"}}


def facts_raw(n: int = 2) -> dict:
    """ページが持っている、置き換える前の事実（実名・地点名・車番つき）。"""
    f = facts_sample(n)
    f["partner"] = "架空 元請A物流"
    for r in f["rows"]:
        r["place"] = "江東区 地点07"
        r["handler"] = "山田太郎"
    f["consult"] = ["江東区 地点07は8時台に着いた 2 回の荷待ちは、合計 98 分です。", "架空 元請A物流 さんと 品川 100 あ 1234 の件"]
    return f


DRAFT_OK = "荷主A 様。2026-10-01 から 2026-10-02 の 2 回で、待機は合計 3 時間 0 分、参考額は 3361 円（税抜）です。告示の表に当てた目安で、協議のお願いです。"


class FakeClient:
    """messages.create(**kw) だけを持つ。呼び出しを calls に残す。plan で段ごとの応答を変える。"""

    def __init__(self, plan: dict | None = None):
        self.plan = plan or {}
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _resp(self, text: str, stop: str = "end_turn", tin: int = 100, tout: int = 50):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=tin, output_tokens=tout))

    def _create(self, **kw):
        self.calls.append(kw)
        model = kw["model"]
        step = "check" if kw["system"] == ax.SYS_CHECK else "draft" if kw["system"] == ax.SYS_DRAFT else "verify"
        p = self.plan.get(step)
        if isinstance(p, Exception):
            raise p
        if callable(p):
            return p(kw)
        if p is not None:
            return p
        if step == "check":
            return self._resp(json.dumps({"issues": [{"row": 1, "field": "start", "message": "荷役開始が到着より前に見えます" * 3}]}, ensure_ascii=False), tin=300, tout=40)
        if step == "draft":
            return self._resp(DRAFT_OK, tin=900, tout=200)
        return self._resp(json.dumps({"ok": True, "problems": []}), tin=1200, tout=120)

    def resp(self, *a, **k):
        return self._resp(*a, **k)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(ax, "LIMITER", ax.Limiter())
    ax.CACHE.clear()
    monkeypatch.setattr(ax, "_RUN_ASYNC", False)
    for k in ("AI_EXPLAIN_ENABLED", "AI_EXPLAIN_PUBLIC", "AI_DAILY_LIMIT", "ANTHROPIC_API_KEY", "AI_VERIFY_OPUS"):
        monkeypatch.delenv(k, raising=False)


def enable(monkeypatch, **extra):
    monkeypatch.setenv("AI_EXPLAIN_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def run(client: FakeClient, facts: dict | None = None, events: list | None = None):
    f = ax.clean_facts(facts or facts_sample())
    return ax.run_pipeline(f, client, events.append if events is not None else None)


# ---------------------------------------------------------------- 段の順と呼び出しの形（2026-11-08: 下書きは Haiku、Opus の確かめは既定で止める）
def test_default_pipeline_is_two_haiku_calls_and_machine_check_without_opus():
    c = FakeClient()
    events: list = []
    res = run(c, events=events)
    assert [k["model"] for k in c.calls] == ["claude-haiku-5-5", "claude-haiku-5-5"]  # Opus も Sonnet も呼ばない
    h, d = c.calls
    assert h["system"] == ax.SYS_CHECK and d["system"] == ax.SYS_DRAFT
    assert [k["max_tokens"] for k in c.calls] == [2000, 6000]  # 思考の分を含む
    assert h["output_config"]["effort"] == "low" and d["output_config"]["effort"] == "low"
    assert h["output_config"]["format"]["type"] == "json_schema" and "format" not in d["output_config"]
    for k in c.calls:
        assert "thinking" not in k
        assert [m["role"] for m in k["messages"]] == ["user"]
    assert res["ok"] is True and res["draft"] == DRAFT_OK and res["fallback"] is False
    assert [st["step"] for st in res["steps"]] == ["check", "draft", "verify"]
    v = res["steps"][2]
    assert v["model"] == "machine" and v["cost_usd"] == 0.0 and v["verified"] is True
    assert [(e["event"], e["step"]) for e in events] == [("start", "check"), ("done", "check"), ("start", "draft"), ("done", "draft"), ("start", "verify"), ("done", "verify")]


def test_opus_verify_runs_only_with_AI_VERIFY_OPUS(monkeypatch):
    monkeypatch.setenv("AI_VERIFY_OPUS", "1")
    c = FakeClient()
    res = run(c)
    assert [k["model"] for k in c.calls] == ["claude-haiku-5-5", "claude-haiku-5-5", "claude-opus-5-5"]
    o = c.calls[2]
    assert o["max_tokens"] == 6000 and o["output_config"]["effort"] == "medium" and o["output_config"]["format"]["type"] == "json_schema"
    assert o["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert res["ok"] is True and res["steps"][2]["model"] == "claude-opus-5-5"
    monkeypatch.setenv("AI_VERIFY_OPUS", "0")
    c2 = FakeClient()
    run(c2)
    assert all(k["model"] != ax.MODEL_OPUS for k in c2.calls)


def test_payload_has_no_original_names_or_plate_numbers():
    raw = facts_raw()
    with pytest.raises(ax.AiError) as e:   # 置き換える前の事実はサーバーが断る
        ax.clean_facts(raw)
    assert e.value.status == 400
    ps, mapping = ax.pseudonymize(raw)
    c = FakeClient()
    run(c, ps)
    sent = json.dumps([k["messages"] for k in c.calls], ensure_ascii=False)
    for needle in ("架空 元請A物流", "江東区", "地点07", "山田太郎", "品川 100"):
        assert needle not in sent, needle
    assert "荷主A" in sent and "地点1" in sent and "（車番）" in sent
    assert mapping == {"荷主A": "架空 元請A物流", "地点1": "江東区 地点07"}
    assert all(r["handler"] == "他" for r in ps["rows"])   # 役割の語でない荷役者（運転者名）は残さない
    assert ps["rows"][0]["wait_min"] == 90 and ps["totals"] == raw["totals"]   # 数字はそのまま


def test_pseudonyms_follow_order_of_appearance_and_longest_name_first():
    f = facts_sample(3)
    f["partner"] = "山田運輸"
    f["rows"][0]["place"], f["rows"][1]["place"], f["rows"][2]["place"] = "東京 倉庫", "東京", "東京 倉庫"
    f["consult"] = ["東京 倉庫は山田運輸の", "東京に着いた"]
    ps, m = ax.pseudonymize(f)
    assert [r["place"] for r in ps["rows"]] == ["地点1", "地点2", "地点1"]
    assert ps["consult"] == ["地点1は荷主Aの", "地点2に着いた"] and m == {"荷主A": "山田運輸", "地点1": "東京 倉庫", "地点2": "東京"}


def test_server_rejects_fields_outside_the_allowlist():
    for mutate, word in ((lambda f: f.update(raw_text="貼り付けた元の文"), "raw_text"),
                         (lambda f: f["rows"][0].update(vehicle_no="1234"), "vehicle_no"),
                         (lambda f: f["rows"][0].update(driver_name="山田"), "driver_name")):
        f = facts_sample()
        mutate(f)
        with pytest.raises(ax.AiError) as e:
            ax.clean_facts(f)
        assert e.value.code == "bad_field" and word in str(e.value)
    for mutate in (lambda f: f.update(partner="架空 元請A物流"), lambda f: f["rows"][0].update(place="江東区 地点07"),
                   lambda f: f["rows"][0].update(handler="山田太郎"), lambda f: f.update(consult=["品川 100 あ 1234 が着いた"])):
        f = facts_sample()
        mutate(f)
        with pytest.raises(ax.AiError) as e:
            ax.clean_facts(f)
        assert e.value.code == "not_pseudonymized"


def test_status_reports_remaining_and_estimate(monkeypatch):
    enable(monkeypatch, AI_DAILY_LIMIT="5")
    monkeypatch.setattr(ax, "make_client", lambda: FakeClient())
    st = ax.status(False)
    assert st["available"] is True and st["remaining"] == 5 and st["verify_opus"] is False and st["public"] is False
    assert [g["model"] for g in st["cost"]["stages"]] == ["claude-haiku-5-5", "claude-haiku-5-5"]
    ax.start(facts_sample(), "c", False)
    assert ax.status(False)["remaining"] == 4
    ax.start(facts_sample(), "c", False)   # 同じ内容は数えない
    assert ax.status(False)["remaining"] == 4
    monkeypatch.setenv("AI_VERIFY_OPUS", "1")
    assert ax.status(False)["cost"]["stages"][2]["model"] == "claude-opus-5-5" and ax.status(False)["limits"]["models"]["verify"] == "claude-opus-5-5"


def test_per_run_cost_estimate_is_about_a_cent_or_less():
    f = ax.clean_facts(EXAMPLE)
    usd = ax.estimate_cost_usd(f)
    assert usd <= 0.01, usd
    assert ax.estimate_cost_usd(f, verify=True) > 0.03   # Opus を足すと桁が変わる


def test_reason_texts():
    assert ax.reason_text("public_off") == "この公開版では止めています（費用の上限のため）"
    assert ax.reason_text("disabled", True) == "この公開版では止めています（費用の上限のため）"
    assert "AI_EXPLAIN_ENABLED" in ax.reason_text("disabled")
    assert "サーバーで開いたときだけ" not in "".join(ax.reason_text(r) for r in ("disabled", "public_off", "no_key", "no_sdk"))


def test_only_known_fields_reach_the_model_and_no_raw_text():
    c = FakeClient()
    run(c)
    sent = json.dumps([k["messages"] for k in c.calls], ensure_ascii=False)
    assert "荷主A" in sent and "vehicle_no" not in sent


def test_check_issues_are_cut_to_30_chars_and_reported_with_count():
    res = run(FakeClient())
    assert len(res["issues"]) == 1 and len(res["issues"][0]["message"]) <= 30
    assert res["steps"][0]["count"] == 1 and "指摘 1 件" in res["steps"][0]["detail"]


def test_cost_estimate_uses_the_stated_prices():
    assert ax.cost_usd(ax.MODEL_HAIKU, 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert ax.cost_usd(ax.MODEL_SONNET, 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert ax.cost_usd(ax.MODEL_OPUS, 1_000_000, 1_000_000) == pytest.approx(24.0)
    t = ax.totals(run(FakeClient())["steps"])
    assert t["input_tokens"] == 300 + 900 and t["output_tokens"] == 40 + 200 and 0 < t["cost_usd"] < 0.001


# ---------------------------------------------------------------- 失敗したら型どおりに戻る
def test_refusal_at_draft_falls_back_to_template():
    c = FakeClient()
    c.plan["draft"] = c.resp("", stop="refusal")
    res = run(c)
    assert res["ok"] is False and res["fallback"] is True and res["draft"] is None and "断りました" in res["message"]
    assert len(c.calls) == 2  # Opus は呼ばない


def test_refusal_at_verify_withholds_the_draft(monkeypatch):
    monkeypatch.setenv("AI_VERIFY_OPUS", "1")
    c = FakeClient()
    c.plan["verify"] = c.resp("", stop="refusal")
    res = run(c)
    assert res["ok"] is False and res["draft"] is None and res["fallback"] is True and res["problems"]


def test_refusal_at_check_does_not_stop_the_rest():
    c = FakeClient()
    c.plan["check"] = c.resp("", stop="refusal")
    res = run(c)
    assert res["steps"][0]["status"] == "failed" and res["ok"] is True and res["issues"] == []


def test_verify_not_ok_returns_template_and_problems_not_the_draft(monkeypatch):
    monkeypatch.setenv("AI_VERIFY_OPUS", "1")
    c = FakeClient()
    c.plan["verify"] = c.resp(json.dumps({"ok": False, "problems": [{"quote": "3361 円", "reason": "合計が合わない"}]}, ensure_ascii=False))
    res = run(c)
    assert res["ok"] is False and res["fallback"] is True and res["draft"] is None
    assert res["problems"][0]["reason"] == "合計が合わない"
    assert res["steps"][-1]["status"] == "mismatch" and "要確認" in res["steps"][-1]["detail"]


def test_numbers_not_in_the_facts_fail_even_if_the_model_says_ok():
    c = FakeClient()
    c.plan["draft"] = c.resp(DRAFT_OK + " 追加で 987654 円も見込めます。")
    res = run(c)
    assert res["ok"] is False and res["draft"] is None
    assert any("987654" in p["quote"] for p in res["problems"])


def test_unmatched_numbers_helper():
    f = ax.clean_facts(facts_sample())
    assert ax.unmatched_numbers("2026-10-01 の 3,361 円、90 分は 1 時間 30 分", f) == []
    assert ax.unmatched_numbers("777 円", f) == ["777"]


def test_truncated_draft_and_invalid_json_fall_back(monkeypatch):
    monkeypatch.setenv("AI_VERIFY_OPUS", "1")
    c = FakeClient()
    c.plan["draft"] = c.resp("途中で", stop="max_tokens")
    assert run(c)["fallback"] is True
    c2 = FakeClient()
    c2.plan["verify"] = c2.resp("これは JSON ではない")
    r2 = run(c2)
    assert r2["ok"] is False and r2["draft"] is None


def test_api_error_falls_back_with_class_name_only():
    c = FakeClient()
    c.plan["draft"] = RuntimeError(f"upstream said key={SENTINEL}")
    res = run(c)
    assert res["fallback"] is True and SENTINEL not in json.dumps(res, ensure_ascii=False)


# ---------------------------------------------------------------- 入力の検め・上限
def test_clean_facts_rejects_empty_and_oversize():
    with pytest.raises(ax.AiError):
        ax.clean_facts({"rows": []})
    with pytest.raises(ax.AiError):
        ax.clean_facts("x")
    big = facts_sample()
    big["rows"] = big["rows"] * 400
    with pytest.raises(ax.AiError) as e:
        ax.clean_facts(big)
    assert e.value.status == 413


def test_clean_facts_keeps_only_allowed_fields():
    f = ax.clean_facts(facts_sample())
    assert set(f) <= set(ax.FACT_KEYS) and set(f["rows"][0]) <= set(ax.ROW_KEYS)


def test_daily_cap(monkeypatch):
    enable(monkeypatch, AI_DAILY_LIMIT="2")
    monkeypatch.setattr(ax, "make_client", lambda: FakeClient())
    for i in range(2):
        ax.start(facts_sample(i + 1), f"c{i}", False)
    with pytest.raises(ax.AiError) as e:
        ax.start(facts_sample(3), "c9", False)
    assert e.value.status == 429 and e.value.code == "daily_cap"


def test_daily_cap_resets_next_day(monkeypatch):
    from datetime import date, timedelta
    day = [date(2026, 11, 7)]
    lim = ax.Limiter(today=lambda: day[0])
    monkeypatch.setenv("AI_DAILY_LIMIT", "1")
    lim.reserve("a")
    with pytest.raises(ax.AiError):
        lim.reserve("b")
    day[0] += timedelta(days=1)
    lim.reserve("b")


def test_per_client_cap_is_10_per_hour():
    t = [1000.0]
    lim = ax.Limiter(clock=lambda: t[0])
    for _ in range(10):
        lim.reserve("me")
    with pytest.raises(ax.AiError) as e:
        lim.reserve("me")
    assert e.value.code == "client_cap"
    lim.reserve("other")  # 別の来訪者は数えない
    t[0] += 3601
    lim.reserve("me")


def test_default_daily_limit_is_100(monkeypatch):
    assert ax.daily_limit() == 100
    monkeypatch.setenv("AI_DAILY_LIMIT", "abc")
    assert ax.daily_limit() == 100


# ---------------------------------------------------------------- 状態
def test_status_disabled_by_default_and_without_key(monkeypatch):
    assert ax.status(False)["available"] is False and ax.status(False)["reason"] == "disabled"
    monkeypatch.setenv("AI_EXPLAIN_ENABLED", "1")
    st = ax.status(False)
    assert st["available"] is False and st["reason"] == "no_key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    st = ax.status(False)
    assert st["available"] is True and st["limits"]["daily"] == 100 and st["limits"]["per_client_per_hour"] == 10
    assert SENTINEL not in json.dumps(st)


def test_status_public_needs_its_own_switch(monkeypatch):
    enable(monkeypatch)
    st = ax.status(True)
    assert st["available"] is False and st["reason"] == "public_off"
    monkeypatch.setenv("AI_EXPLAIN_PUBLIC", "1")
    assert ax.status(True)["available"] is True


def test_status_without_sdk_is_unavailable(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(ax.importlib.util, "find_spec", lambda name: None)
    st = ax.status(False)
    assert st["available"] is False and st["reason"] == "no_sdk" and ax.reason_text("no_sdk")


def test_rejected_start_creates_no_job(monkeypatch):
    enable(monkeypatch, AI_DAILY_LIMIT="1")
    monkeypatch.setattr(ax, "make_client", lambda: FakeClient())
    ax.start(facts_sample(1), "a", False)
    n = len(ax.JOBS._d)
    with pytest.raises(ax.AiError):
        ax.start(facts_sample(2), "b", False)
    assert len(ax.JOBS._d) == n


def test_result_with_failed_check_is_not_cached(monkeypatch):
    enable(monkeypatch)
    c = FakeClient({"check": RuntimeError("x")})
    monkeypatch.setattr(ax, "make_client", lambda: c)
    ax.start(facts_sample(), "c", False)
    assert ax.CACHE.get(ax.cache_key(ax.clean_facts(facts_sample()))) is None
    assert ax.start(facts_sample(), "c", False)["cached"] is False


def test_client_uses_no_automatic_retry():
    import inspect
    assert "max_retries=0" in inspect.getsource(ax.make_client)


@pytest.mark.parametrize("draft", ["参考額は合計 3 円です。", "2 回のうち、合計 5 回の荷待ちです。", "合計は十二回、参考額は三万円です。", "合計は１２回、参考額は３円です。"])
def test_number_context_check_rejects_loose_numbers(draft):
    f = ax.clean_facts(facts_sample())
    assert ax.draft_issues(draft, f), draft
    c = FakeClient()
    c.plan["draft"] = c.resp(draft)  # Opus が一致と言っても通さない
    res = run(c)
    assert res["ok"] is False and res["draft"] is None


def test_number_context_check_accepts_the_facts_numbers():
    f = ax.clean_facts(facts_sample())
    assert ax.draft_issues("2 回の荷待ちで、参考額は 3,361 円、1 行目は 1680 円です。", f) == []
    assert ax.draft_issues("参考額は３３６１円、２回です。", f) == []  # 全角は半角にそろえる


def test_forwarded_client_counts_from_the_right():
    fc = serve_web._forwarded_client
    assert fc("1.1.1.1, 2.2.2.2, 3.3.3.3", True, {}) == "3.3.3.3"
    assert fc("1.1.1.1, 2.2.2.2, 3.3.3.3", True, {"TRUSTED_PROXY_HOPS": "2"}) == "2.2.2.2"
    assert fc("9.9.9.9", True, {"TRUSTED_PROXY_HOPS": "3"}) == ""
    assert fc("1.1.1.1", False, {}) == "" and fc(None, True, {}) == ""


def test_start_refuses_when_unavailable(monkeypatch):
    monkeypatch.setattr(ax, "make_client", lambda: pytest.fail("呼んではいけない"))
    with pytest.raises(ax.AiError) as e:
        ax.start(facts_sample(), "c", False)
    assert e.value.status == 403


# ---------------------------------------------------------------- 同じ入力の再利用
def test_identical_input_is_served_from_cache(monkeypatch):
    enable(monkeypatch)
    c = FakeClient()
    monkeypatch.setattr(ax, "make_client", lambda: c)
    j1 = ax.start(facts_sample(), "c", False)
    n = len(c.calls)
    assert n == 2 and j1["cached"] is False
    j2 = ax.start(facts_sample(), "c", False)
    assert j2["cached"] is True and len(c.calls) == n and "job" not in j2  # 仕事は作らず、結果をその場で返す
    res = j2["result"]
    assert res["cached"] is True and res["ok"] is True
    assert ax.LIMITER.used_today() == 1  # 再利用は上限に数えない
    k1 = ax.cache_key(ax.clean_facts(facts_sample()))
    assert re.fullmatch(r"[0-9a-f]{64}", k1)
    other = facts_sample()
    other["rows"][0]["wait_min"] = 91
    assert ax.cache_key(ax.clean_facts(other)) != k1


# ---------------------------------------------------------------- HTTP
@pytest.fixture
def server(monkeypatch):
    """手元の版のサーバーを 1 つ。終わりに必ず止める。"""
    httpd = serve_web.make_server(port=0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=5)


def http(base: str, path: str, body=None, ctype="application/json"):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8"))
    req = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def test_http_status_and_post_when_disabled(server):
    code, text = http(server, "/api/ai/status")
    st = json.loads(text)
    assert code == 200 and st["available"] is False and st["reason"] == "disabled" and st["reason_text"]
    code, text = http(server, "/api/ai/waiting_explain", facts_sample())
    assert code == 403 and json.loads(text)["code"] == "unavailable"


def test_http_full_flow_and_key_never_leaks(server, monkeypatch, caplog):
    enable(monkeypatch)
    monkeypatch.setattr(ax, "_RUN_ASYNC", True)
    c = FakeClient()
    c.plan["check"] = RuntimeError(f"echo {SENTINEL}")  # 点検が壊れても先へ進み、メッセージは出ない
    monkeypatch.setattr(ax, "make_client", lambda: c)
    caplog.set_level(logging.DEBUG)
    seen = []
    code, text = http(server, "/api/ai/status")
    seen.append(text)
    assert json.loads(text)["available"] is True
    code, text = http(server, "/api/ai/waiting_explain", facts_sample())
    seen.append(text)
    assert code == 200
    job = json.loads(text)["job"]
    import time
    res = None
    for _ in range(100):
        code, text = http(server, f"/api/ai/job?id={job}")
        seen.append(text)
        j = json.loads(text)
        if j["state"] == "done":
            res = j["result"]
            break
        time.sleep(0.1)
    assert res and res["ok"] is True and res["draft"] == DRAFT_OK
    assert res["steps"][0]["status"] == "failed"
    assert SENTINEL not in "".join(seen) and SENTINEL not in caplog.text
    code, text = http(server, "/api/ai/job?id=nope")
    assert code == 404


def test_http_rejects_oversize_bad_type_and_wrong_method(server, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(ax, "make_client", lambda: pytest.fail("呼んではいけない"))
    big = facts_sample()
    big["rows"] = big["rows"] * 400
    code, text = http(server, "/api/ai/waiting_explain", big)
    assert code == 413
    code, _ = http(server, "/api/ai/waiting_explain", b"x", ctype="text/plain")
    assert code == 400
    code, _ = http(server, "/api/ai/waiting_explain")  # GET
    assert code == 405


def test_http_per_client_cap_returns_429(server, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(ax, "make_client", lambda: FakeClient())
    codes = []
    for i in range(11):
        f = facts_sample(1)
        f["rows"][0]["wait_min"] = 100 + i  # キャッシュに当たらないように変える
        codes.append(http(server, "/api/ai/waiting_explain", f)[0])
    assert codes[:10] == [200] * 10 and codes[10] == 429


# ---------------------------------------------------------------- 静的な確認
def test_page_files_keep_network_code_only_in_ai_explain_js():
    page = (ROOT / "web" / "waiting.html").read_text(encoding="utf-8")
    art = (ROOT / "web" / "publish" / "waiting_artifact.html").read_text(encoding="utf-8")
    for html in (page, art):
        assert "fetch(" not in html
    assert "loadAi();" in page and "loadAi();" not in art  # 単独の版（Artifact）は読み込みを呼ばない
    own = (ROOT / "web" / "own.js").read_text(encoding="utf-8")
    assert "/api/ai/" not in own and "AiExplain" in own  # own.js は通信を持たず、ai_explain.js に任せる
    js = (ROOT / "web" / "ai_explain.js").read_text(encoding="utf-8")
    assert "/api/ai/status" in js and "ANTHROPIC" not in js and "sk-ant" not in js
    assert "Claude で説明文を作る" in js and "説明文の自動作成: ファイルで開いたときは使えません（今は型どおりの書面です）" in page
    assert (ROOT / "web" / "index.html").read_text(encoding="utf-8").index("ai_explain.js") < (ROOT / "web" / "index.html").read_text(encoding="utf-8").index('src="own.js"')


def test_env_example_has_names_only():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("ANTHROPIC_API_KEY=", "AI_EXPLAIN_ENABLED=", "AI_EXPLAIN_PUBLIC=", "AI_DAILY_LIMIT="):
        assert re.search(rf"^#?\s*{name}\s*$", text, re.M), name


# ---------------------------------------------------------------- 画面（ヘッドレス Edge）
def _edge():
    import capture_web as cw
    return cw


def test_ui_button_only_when_available_and_progress_steps(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(ax, "_RUN_ASYNC", True)
    slow = threading.Event()

    def haiku(kw):
        slow.wait(5)  # 画面が「点検中」を出す間を作る
        return FakeClient().resp(json.dumps({"issues": [{"row": 1, "field": "start", "message": "荷役開始が早すぎます"}]}, ensure_ascii=False), tin=300, tout=40)

    c = FakeClient({"check": haiku})
    # ページの事実から出る数字（待機・金額）に合わせて、Sonnet は事実の数字だけを使った文を返す
    def sonnet(kw):
        user = kw["messages"][0]["content"]
        facts = json.loads(user.split("事実（JSON）:\n", 1)[1].split("\n\n", 1)[0])
        t = facts["totals"]
        return FakeClient().resp(f"{facts['partner']} 様。参考額は {t['amount_yen_ex_tax']} 円（税抜）、待機は合計 {t['wait_total']} です。協議のお願いです。", tin=900, tout=200)
    c.plan["draft"] = sonnet
    monkeypatch.setattr(ax, "make_client", lambda: c)
    cw = _edge()
    httpd = serve_web.make_server(port=0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with cw.edge_page(1280, 1000) as page:
            page.goto(base + "/waiting.html?example=1")
            page.wait(lambda: page.js("!!document.querySelector('#wait-doc')"), 30, "書面")
            page.wait(lambda: page.js("!!document.querySelector('#ai-box [data-ai=ask]')"), 15, "ボタン")
            assert page.js("document.querySelector('#ai-box [data-ai=ask]').textContent") == "Claude で説明文を作る"
            page.js("document.querySelector('#ai-box [data-ai=ask]').click()")
            page.wait(lambda: page.js("!!document.querySelector('.ai-confirm [data-ai=send]')"), 5, "確認")   # 準備中の表示のあと、残りの回数を読んで出る
            conf = page.js("document.querySelector('.ai-confirm').textContent")
            assert "記録" in conf and "行" in conf and "送らないもの" in conf and "車番" in conf
            # 確認の枠: 送る JSON そのもの（置き換え後）、費用の目安（ドルと円）、今日の残り
            sent_json = page.js("document.querySelector('.ai-confirm .ai-json-text').textContent")
            assert json.loads(sent_json) == ax.pseudonymize(EXAMPLE_RAW)[0]   # Python の置き換えと同じ結果
            assert "架空 元請A物流" not in conf and "江東区" not in conf and "荷主A" in conf and "地点1" in conf
            assert "1 回の費用の目安: 約 $0.00" in conf and " 円" in conf and "今日の残り 100 回" in conf
            assert page.js("!!document.querySelector('.ai-confirm [data-ai=send]') && !!document.querySelector('.ai-confirm [data-ai=cancel]')")
            page.js("document.querySelector('.ai-confirm [data-ai=cancel]').click()")
            assert page.js("document.querySelector('.ai-confirm').hidden")
            assert c.calls == []  # やめたら何も送らない
            page.js("document.querySelector('#ai-box [data-ai=ask]').click()")
            page.wait(lambda: page.js("!!document.querySelector('.ai-confirm [data-ai=send]')"), 5, "送るボタン")   # 残りの回数を読んでから出る
            page.js("document.querySelector('.ai-confirm [data-ai=send]').click()")
            page.wait(lambda: "Haiku 5.5 が記録を点検中" in page.js("document.querySelector('.ai-steps').textContent"), 10, "点検中")
            slow.set()
            page.wait(lambda: page.js("!!document.querySelector('.ai-result') && !document.querySelector('.ai-result').hidden"), 20, "結果")
            steps = page.js("document.querySelector('.ai-steps').textContent")
            assert "Haiku 5.5 が記録を点検 → 完了（指摘 1 件）" in steps
            assert "Haiku 5.5 が説明文を下書き" in steps and "数字を機械で照合 → 一致" in steps and "Opus" not in steps and "Sonnet" not in steps
            assert "トークン" in steps and "目安" in steps
            body = page.js("document.querySelector('.ai-result .ai-body').textContent")
            assert "一致" in body and "参考額は" in body and "説明文をコピー" in body and "荷役開始が早すぎます" in body
            assert "架空 元請A物流 様" in body and "荷主A" not in body   # 表示のときだけ元の名前に戻す
            assert page.js("document.querySelector('.ai-result').open")
            # 送った事実に、車番などは入っていない
            sent = json.dumps([k["messages"] for k in c.calls], ensure_ascii=False)
            assert "vehicle_no" not in sent and "架空 元請A物流" not in sent and "江東区" not in sent and ax.LIMITER.used_today() == 1
        # サーバーが使えないとき（キーなし）は、ボタンを出さず 1 行だけ
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        with cw.edge_page(1280, 1000) as page:
            page.goto(base + "/waiting.html?example=1")
            page.wait(lambda: page.js("!!document.querySelector('#ai-box')"), 30, "欄")
            page.js("new Promise(r => setTimeout(r, 1500))")
            assert page.js("!document.querySelector('[data-ai=ask]')")
            txt = page.js("document.querySelector('#ai-box').textContent.trim()")
            assert txt.startswith("説明文の自動作成") and "サーバーで開いたときだけ" not in txt and "（今は型どおりの書面です）" in txt, txt
            assert "API キーが設定されていません" in txt, txt   # 理由を本文に出す（吹き出しだけにしない）   # サーバーで開いているのに「サーバーで開いたときだけ」と出さない（2026-11-08）
    finally:
        slow.set()
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=5)


def test_ui_file_url_shows_the_one_line_only():
    cw = _edge()
    with cw.edge_page(1280, 1000) as page:
        page.goto((ROOT / "web" / "waiting.html").as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#ai-box')"), 30, "欄")
        assert page.js("!document.querySelector('[data-ai=ask]')")
        assert page.js("document.querySelector('#ai-box').textContent.trim()") == "説明文の自動作成: ファイルで開いたときは使えません（今は型どおりの書面です）"
        assert page.js("!document.querySelector('script[src*=ai_explain]')")


# ---------------------------------------------------------------- 実運用の指摘（2026-11-07）への対策
EXAMPLE_RAW = json.loads((ROOT / "tests" / "data" / "waiting_example_facts.json").read_text(encoding="utf-8"))  # waiting.html?example=1 の aiFacts()（置き換え前）
EXAMPLE = ax.pseudonymize(EXAMPLE_RAW)[0]   # 送る形

# 規則どおりに書いた下書き（一覧の数字だけ、約束・謝罪なし、算用数字、3 段落）
DRAFT_EXAMPLE = (
    "いつもお世話になっております。2026-10-01 から 2026-10-16 の期間に、荷待ちが 12 件ありました。待ちは合計で 682 分（11 時間 22 分）で、"
    "そのうち 1 時間以上の待ちが 5 回、2 時間以上の待ちが 1 回です。\n\n"
    "標準的運賃の告示の表に当てた参考額は 33,550 円（税抜）です。これは計算の目安であり、請求を決めるものではありません。\n\n"
    "つきましては、今後の荷待ちを減らす方法について、一度ご相談の時間をいただけないでしょうか。どうぞよろしくお願いいたします。")


def test_example_facts_pass_machine_check_and_reach_verify(monkeypatch):
    monkeypatch.setenv("AI_VERIFY_OPUS", "1")
    f = ax.clean_facts(EXAMPLE)
    assert f["count"] == 12 and f["totals"]["amount_yen_ex_tax"] == 33550 and f["totals"]["from_reception"] is False
    assert ax.draft_issues(DRAFT_EXAMPLE, f) == []
    c = FakeClient()
    c.plan["draft"] = c.resp(DRAFT_EXAMPLE, tin=3000, tout=500)
    res = ax.run_pipeline(f, c)
    assert res["ok"] is True and res["draft"] == DRAFT_EXAMPLE and res["problems"] == []
    sonnet, opus = c.calls[1], c.calls[2]   # 下書き（Haiku）と確かめ（Opus。AI_VERIFY_OPUS=1 のとき）
    assert DRAFT_EXAMPLE in opus["messages"][0]["content"]  # 確かめの段に下書きが渡る
    s_user = sonnet["messages"][0]["content"]
    for needle in ("682 分（11 時間 22 分）", "33,550 円", "5 回", "1 回", "12 件", "2026-10-01 〜 2026-10-16", "着いた時刻（到着）から"):
        assert needle in s_user, needle


def test_figures_list_is_built_from_the_facts():
    text = ax.figures_text(ax.clean_facts(EXAMPLE))
    assert "件数（回数）: 12 件" in text and "合計の待ち: 682 分（11 時間 22 分）" in text and "参考額（税抜）: 33,550 円" in text
    assert "1 時間以上の荷待ちの回数: 5 回" in text and "2 時間以上の荷待ちの回数: 1 回" in text


def test_prompts_carry_the_rules_against_invented_promises_and_false_blank_alarms():
    d = ax.SYS_DRAFT
    for s in ("事実と『使ってよい数字の一覧』に書かれたことだけ", "約束・謝罪・記録の出来についての言及をしない", "空欄の任意欄", "相談のお願い" if "相談のお願い" in d else "丁寧な相談のお願い",
              "450 字以内", "2〜3 つの短い段落", "配車課長", "添え書き", "『〜とします』", "3 文以上続けず", "漢数字"):
        assert s in d, s
    k = ax.SYS_CHECK
    for s in ("任意欄の空欄", "受付", "指定の到着時刻", "荷役終了", "空欄そのものを理由にした指摘は 1 件も出さない", "必須欄"):
        assert s in k, s
    c = FakeClient()
    run(c, EXAMPLE)
    assert c.calls[1]["system"] == ax.SYS_DRAFT and c.calls[0]["system"] == ax.SYS_CHECK
    # 点検の指摘は下書きの段に渡さない（記録の出来の文を誘わないため）
    assert "荷役開始が到着より前" not in c.calls[1]["messages"][0]["content"]


def test_truncated_draft_is_never_verified_and_is_logged():
    c = FakeClient()
    c.plan["draft"] = c.resp("今回を一つの区切りとして、改善策や記録の確認方法を一緒に", stop="max_tokens", tout=8000)
    res = run(c, EXAMPLE)
    assert res["ok"] is False and res["fallback"] is True and res["draft"] is None
    assert len(c.calls) == 2 and all(k["model"] != ax.MODEL_OPUS for k in c.calls)  # 確かめには回さない
    st = res["steps"][1]
    assert st["status"] == "failed" and "max_tokens" in st["detail"] and st["stop_reason"] == "max_tokens" and st["output_tokens"] == 8000


def test_draft_ending_mid_sentence_or_too_long_is_rejected_without_cutting():
    c = FakeClient()
    c.plan["draft"] = c.resp("参考額は 33,550 円です。改善策を一緒に")  # stop は end_turn でも、文の途中は出さない
    res = run(c, EXAMPLE)
    assert res["ok"] is False and len(c.calls) == 2 and "途中" in res["steps"][1]["detail"]
    c2 = FakeClient()
    c2.plan["draft"] = c2.resp("あ" * 701 + "。")
    res2 = run(c2, EXAMPLE)
    assert res2["ok"] is False and len(c2.calls) == 2 and "長すぎ" in res2["steps"][1]["detail"]


def test_numbers_in_the_allowed_list_pass_even_with_counters():
    f = ax.clean_facts(EXAMPLE)
    # 2 回・4 回は場所別の集計（consult）の回数、5 回・1 回は 1・2 時間を超えた回数
    ok = "渋谷系の拠点で 2 回、別の時間帯で 4 回、5 回、1 回、12 件、682 分、11 時間 22 分、33,550 円、30 分ごとです。"
    assert ax.draft_issues(ok, f) == [], ax.draft_issues(ok, f)
    for bad, quote in (("7 回です。", "7 回"), ("999 円です。", "999 円"), ("13 件です。", "13 件"), ("11 時間 23 分です。", "23 分"), ("9 時間です。", "9 時間")):
        iss = ax.draft_issues(bad, f)
        assert iss and any(quote in i["quote"] for i in iss), bad


def test_kanji_numerals_only_rejected_with_counters():
    f = ax.clean_facts(EXAMPLE)
    for ok in ("一緒に考えたく、一つ目の案を一度ご相談します。一方で、十分に配慮します。", "一緒に改善策を話し合いたいです。"):
        assert ax.draft_issues(ok, f) == [], ok
    for bad in ("参考額は三万円です。", "十二回の荷待ちです。", "二時間を超えました。", "三十分ごとです。", "1 万円です。"):
        assert ax.draft_issues(bad, f), bad


def test_issues_are_grouped_and_optional_blank_fields_are_dropped():
    raw = [{"row": i, "field": "reception", "message": "受付時刻が空欄"} for i in range(1, 13)]
    assert ax.group_issues(raw) == []  # 任意欄の空欄は指摘にしない
    raw2 = [{"row": i, "field": "start", "message": "荷役開始が到着より前"} for i in (3, 4, 5)] + [{"row": 9, "field": "end", "message": "終了が開始より前"}]
    g = ax.group_issues(raw2)
    assert len(g) == 2 and g[0]["n"] == 3 and g[0]["rows"] == [3, 4, 5] and g[0]["row"] == 3 and g[1]["n"] == 1
    # 空欄でも必須欄（開始）は残す
    assert ax.group_issues([{"row": 1, "field": "start", "message": "荷役開始が空欄"}])
    c = FakeClient({"check": FakeClient().resp(json.dumps({"issues": raw}, ensure_ascii=False))})
    res = run(c, EXAMPLE)
    assert res["issues"] == [] and res["steps"][0]["count"] == 0


def test_ui_groups_identical_issue_messages():
    js = (ROOT / "web" / "ai_explain.js").read_text(encoding="utf-8")
    assert "（${esc(i.n)} 件）" in js


def test_ui_unavailable_reasons_are_visible_text(monkeypatch):
    """使えないとき、理由を本文に出す（吹き出しだけにしない）。ファイルで開いたときの 1 行はページ側の持ち分。"""
    cw = _edge()
    httpd = serve_web.make_server(port=0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with cw.edge_page(1280, 1000) as page:   # AI_EXPLAIN_ENABLED なし
            page.goto(base + "/waiting.html?example=1")
            page.wait(lambda: page.js("!!document.querySelector('#ai-box .ai-off') && document.querySelector('#ai-box .ai-off').textContent.includes('使えない設定')"), 30, "理由")
            assert page.js("document.querySelector('#ai-box').title") == ""
            assert page.js("!document.querySelector('[data-ai=ask]')")
            r = page.js("""JSON.stringify(['public_off', 'network', 'http', 'no_sdk'].map((reason) => AiExplain.reasonText({ reason, http: 404 }))
                .concat([AiExplain.reasonText({ reason: 'disabled', public: true })]))""")
            texts = json.loads(r)
            assert texts[0] == "この公開版では止めています（費用の上限のため）" and texts[4] == texts[0]
            assert texts[1] == "サーバーに接続できませんでした" and "HTTP 404" in texts[2] and "anthropic" in texts[3]
        assert "ファイルで開いたときは使えません" in (ROOT / "web" / "ai_explain.js").read_text(encoding="utf-8")
    finally:
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=5)
