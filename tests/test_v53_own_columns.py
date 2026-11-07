"""SPEC §53.1 自社の運行記録の「AI に列を整理してもらう」（POST /api/ai/own_columns）。SDK は差し替える（本物の API は呼ばない。キーも使わない）。

- 送る内容に値そのもの（車番・運転者・地名）が入らない（語彙にない値の形・余計な欄はサーバーで捨てる）
- 列の選択肢にない役割・知らない列番号は捨てる。ignore は使える
- 既定は使えない（AI_EXPLAIN_ENABLED、公開版は AI_EXPLAIN_PUBLIC）。使えるときは §53 と同じ 1 日の上限に数える
"""

from __future__ import annotations

import json
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
RAW = ("品川 100 あ 1234", "山田太郎", "江東区辰巳", "鹿屋市", "D01")  # 値そのもの。どこにも出てはいけない
OPTS_H = ["date", "plate", "from", "to", "weight", "time", "mark", "type", "skip"]


class FakeClient:
    def __init__(self, reply=None, stop="end_turn", exc: Exception | None = None):
        self.calls: list[dict] = []
        self.reply, self.stop, self.exc = reply, stop, exc
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        text = json.dumps(self.reply if self.reply is not None else {"columns": []}, ensure_ascii=False)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=self.stop, usage=SimpleNamespace(input_tokens=400, output_tokens=60))


def body(n_extra=0):
    cols = [{"index": 0, "header": "発時刻", "options": OPTS_H, "shape": {"n": 120, "fill": 1.0, "distinct": 40, "classes": {"time_hm": 1.0}, "len_min": 5, "len_max": 5}},
            {"index": 1, "header": "走行km", "options": OPTS_H, "shape": {"n": 120, "fill": 0.9, "distinct": 60, "classes": {"decimal": 0.95, "int_3_4": 0.05}, "len_min": 3, "len_max": 6}}]
    return {"columns": cols[: 2 + n_extra]}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(ax, "LIMITER", ax.Limiter())
    ax.CACHE.clear()
    for k in ("AI_EXPLAIN_ENABLED", "AI_EXPLAIN_PUBLIC", "AI_DAILY_LIMIT", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def enable(monkeypatch, client, **extra):
    monkeypatch.setenv("AI_EXPLAIN_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(ax, "make_client", lambda: client)


def reply(*items):
    return {"columns": [{"index": i, "role": r, "confidence": c, "reason_ja": why} for i, r, c, why in items]}


# ---------------------------------------------------------------- 送る内容
def test_payload_has_no_raw_values_even_if_the_page_sends_extra_fields(monkeypatch):
    c = FakeClient(reply((0, "skip", "high", "時刻の形です")))
    enable(monkeypatch, c)
    b = body()
    b["columns"][0]["samples"] = list(RAW)                                  # 余計な欄
    b["columns"][1]["shape"]["classes"].update({RAW[0]: 0.5, RAW[2]: 0.2})  # 語彙にない値の形（値そのもの）
    b["columns"][1]["shape"]["examples"] = [RAW[1]]
    b["rows"] = [list(RAW)]
    ax.own_columns(b, "me", False)
    sent = json.dumps(c.calls[0], ensure_ascii=False)
    for raw in RAW:
        assert raw not in sent, raw
    user = c.calls[0]["messages"][0]["content"]
    assert "発時刻" in user and "走行km" in user and "HH:MM の時刻" in user  # 見出しと値の形の語彙だけ


def test_call_shape_haiku_json_schema_low_effort_no_thinking_no_prefill(monkeypatch):
    c = FakeClient(reply((0, "ignore", "low", "分かりません")))
    enable(monkeypatch, c)
    ax.own_columns(body(), "me", False)
    k = c.calls[0]
    assert k["model"] == "claude-haiku-5-5" and k["max_tokens"] == 2000
    assert k["output_config"]["effort"] == "low" and k["output_config"]["format"]["type"] == "json_schema"
    sch = k["output_config"]["format"]["schema"]
    assert sch["additionalProperties"] is False and "ignore" in json.dumps(sch)
    assert "thinking" not in k and [m["role"] for m in k["messages"]] == ["user"]


def test_clean_rejects_empty_and_too_many_columns():
    with pytest.raises(ax.AiError):
        ax.clean_own_columns({"columns": []})
    with pytest.raises(ax.AiError):
        ax.clean_own_columns("x")
    big = {"columns": [{"index": i, "header": f"c{i}", "options": OPTS_H, "shape": {}} for i in range(41)]}
    with pytest.raises(ax.AiError) as e:
        ax.clean_own_columns(big)
    assert e.value.status == 413


def test_options_outside_the_known_roles_are_dropped():
    cols = ax.clean_own_columns({"columns": [{"index": 0, "header": "x", "options": ["date", "drop_table", 5], "shape": {}}]})
    assert cols[0]["options"] == ["date"]


# ---------------------------------------------------------------- 答えの検め
def test_invalid_roles_unknown_columns_and_duplicates_are_rejected(monkeypatch):
    c = FakeClient(reply((0, "start_time", "high", "配車表の選択肢にない役割"), (1, "ignore", "medium", "使わない列です"), (1, "date", "high", "重複"),
                         (7, "date", "high", "知らない列"), (0, "rm -rf", "high", "でたらめ")))
    enable(monkeypatch, c)
    res = ax.own_columns(body(), "me", False)
    assert [(s["index"], s["role"]) for s in res["suggestions"]] == [(1, "ignore")]


def test_valid_suggestion_is_passed_with_confidence_and_short_reason(monkeypatch):
    c = FakeClient(reply((0, "time", "medium", "あ" * 200), (1, "skip", "weird", "距離は使いません")))
    enable(monkeypatch, c)
    res = ax.own_columns(body(), "me", False)
    s0, s1 = res["suggestions"]
    assert s0["role"] == "time" and s0["confidence"] == "medium" and len(s0["reason_ja"]) == 60
    assert s1["role"] == "skip" and s1["confidence"] == "low"  # 知らない確からしさは low
    assert set(res["step"]) >= {"model", "input_tokens", "output_tokens", "ms", "cost_usd", "stop_reason"}


def test_refusal_truncation_and_api_error_give_a_short_japanese_error_without_details(monkeypatch):
    for client, code in ((FakeClient(reply(), stop="refusal"), "refused"), (FakeClient(reply(), stop="max_tokens"), "bad_output"),
                         (FakeClient(exc=RuntimeError("boom " + SENTINEL)), "api_error")):
        ax.CACHE.clear()
        enable(monkeypatch, client)
        with pytest.raises(ax.AiError) as e:
            ax.own_columns(body(), "me", False)
        assert e.value.code == code and e.value.status == 502 and SENTINEL not in str(e.value) and "boom" not in str(e.value)
    assert "RuntimeError" in str(e.value)  # 型の名前だけ


# ---------------------------------------------------------------- 既定は使えない・上限
def test_disabled_by_default_and_nothing_is_called(monkeypatch):
    c = FakeClient(reply((0, "skip", "high", "x")))
    monkeypatch.setattr(ax, "make_client", lambda: c)
    with pytest.raises(ax.AiError) as e:
        ax.own_columns(body(), "me", False)
    assert e.value.status == 403 and e.value.code == "unavailable" and c.calls == []
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)  # キーだけでは使えない
    with pytest.raises(ax.AiError):
        ax.own_columns(body(), "me", False)
    assert c.calls == []


def test_public_host_needs_its_own_switch(monkeypatch):
    c = FakeClient(reply((0, "skip", "high", "x")))
    enable(monkeypatch, c)
    with pytest.raises(ax.AiError) as e:
        ax.own_columns(body(), "me", True)
    assert e.value.status == 403 and c.calls == []
    monkeypatch.setenv("AI_EXPLAIN_PUBLIC", "1")
    assert ax.own_columns(body(), "me", True)["ok"] is True


def test_shares_the_daily_limit_and_cached_input_is_not_counted(monkeypatch):
    c = FakeClient(reply((0, "skip", "high", "x")))
    enable(monkeypatch, c, AI_DAILY_LIMIT="1")
    ax.own_columns(body(), "me", False)
    again = ax.own_columns(body(), "me", False)  # 同じ入力はその場で返り、上限に数えない
    assert again["cached"] is True and len(c.calls) == 1
    with pytest.raises(ax.AiError) as e:
        ax.own_columns({"columns": [dict(body()["columns"][0], header="別の列")]}, "me", False)
    assert e.value.status == 429 and len(c.calls) == 1
    assert ax.LIMITER.used_today() == 1


# ---------------------------------------------------------------- HTTP と画面側の決まり
@pytest.fixture()
def server():
    httpd = serve_web.make_server(port=0)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=5)


def post(base, payload):
    req = urllib.request.Request(base + "/api/ai/own_columns", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_http_disabled_returns_403_and_enabled_returns_validated_suggestions(server, monkeypatch):
    code, j = post(server, body())
    assert code == 403 and "使えない" in j["error"]
    c = FakeClient(reply((0, "time", "high", "着く時刻に近いです"), (1, "bogus", "high", "x")))
    enable(monkeypatch, c)
    code, j = post(server, body())
    assert code == 200 and [s["role"] for s in j["suggestions"]] == ["time"] and SENTINEL not in json.dumps(j)
    code, j = post(server, {"columns": []})
    assert code == 400


def test_own_js_has_no_network_code_and_ai_explain_js_owns_the_endpoint():
    own = (ROOT / "web" / "own.js").read_text(encoding="utf-8")
    assert "/api/ai/" not in own and "AiExplain.ownColumns" in own
    js = (ROOT / "web" / "ai_explain.js").read_text(encoding="utf-8")
    assert "/api/ai/own_columns" in js and "ANTHROPIC" not in js
    assert "window.confirm" not in own  # 送る前の確認は画面の中に出す（ダイアログを使わない）
