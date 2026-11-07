"""SPEC §53.2 帰り便の案内文を AI で読みやすく整える（POST /api/ai/offer_polish）。SDK は差し替える（本物の API は呼ばない。キーも使わない）。

- 送る内容は、渡した案内文の本文だけ（system の指示と本文の 2 つ。ほかの欄は送らない）
- 戻りの文の機械照合: 【】・曜日・…周辺・数字・金額・長さ。通らなければ ready が偽で理由が付く
- 既定は使えない（403）。使えるときは §53 と同じ 1 日の上限に数える。同じ文の結果は上限に数えない
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
SRC = """【宛先（会社名・ご担当者名）】 様

突然のご連絡で失礼します。【運送会社名】の【担当者名】です。

毎週火曜日に、鹿屋市周辺から江東区周辺へ戻るトラックに空きがあります。この方面へ出荷される荷物があれば、この便でお運びできます。
お運びできるかを確かめるため、次の点をお聞かせください。

■お聞かせいただきたいこと
・荷物の品名・荷姿（箱・パレットなど）、1 個あたりの寸法・重量、数量
・積込を希望する日時と、日程を広げられる範囲
・積込場所の荷役設備（フォークリフト・ドックなど）と、積込にかかる時間
・ほかの荷物との混載の可否と、積み降ろしの順の希望
・納品先（受取側）が受け取れる日時
・現在の送り方と、その条件（比べるため）

■進め方
まずは 1 回、お試しでいかがでしょうか。条件を伺ったうえで、お受けできるかとお見積りをお送りします。"""
GOOD = """【宛先（会社名・ご担当者名）】 様

突然のご連絡で失礼します。【運送会社名】の【担当者名】です。

毎週火曜日に、鹿屋市周辺から江東区周辺へ戻るトラックに空きがあります。この方面の荷物があれば、お運びできます。

最初に、次の点を教えてください。
・荷物の品名・荷姿、1 個あたりの寸法・重量、数量
・積込を希望する日時
・納品先が受け取れる日時
・現在の送り方と、その条件
詳しい条件は、ご関心があればあらためて伺います。

まずは 1 回、お試しでいかがでしょうか。"""


class FakeClient:
    def __init__(self, text=GOOD, stop="end_turn", exc: Exception | None = None):
        self.calls: list[dict] = []
        self.text, self.stop, self.exc = text, stop, exc
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], stop_reason=self.stop, usage=SimpleNamespace(input_tokens=1100, output_tokens=420))


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


def test_call_shape_and_payload_is_exactly_the_confirmed_text(monkeypatch):
    c = FakeClient()
    enable(monkeypatch, c)
    r = ax.offer_polish({"text": SRC, "plate": "鹿児島 100 か 9001", "rows": [1, 2]}, "u1", False)
    assert r["ok"] and r["ready"] and r["problems"] == [] and r["text"] == GOOD.strip()
    (kw,) = c.calls
    assert kw["model"] == "claude-haiku-5-5" and kw["max_tokens"] == 1500 and kw["output_config"] == {"effort": "low"}
    assert kw["messages"] == [{"role": "user", "content": SRC}]            # 本文だけ。ほかの欄は送らない
    assert set(kw) == {"model", "max_tokens", "system", "messages", "output_config"}
    assert "thinking" not in kw and "【" in kw["system"] and "消さない" in kw["system"]   # 質問を消さない方針に 2026-11-08
    assert "鹿児島" not in json.dumps(kw, ensure_ascii=False) and SENTINEL not in json.dumps(kw, ensure_ascii=False)
    assert r["step"]["model"] == "claude-haiku-5-5" and r["step"]["cost_usd"] < 0.002


def test_sdk_client_has_no_retries_and_key_only_from_env():
    src = (ROOT / "src" / "shipcompare" / "ai_explain.py").read_text(encoding="utf-8")
    assert "max_retries=0" in src and "api_key=" not in src


@pytest.mark.parametrize("bad, needle", [
    (GOOD.replace("【担当者名】", "田中"), "【担当者名】 が消えています"),
    (GOOD.replace("火曜日", "水曜日"), "火曜"),
    (GOOD.replace("鹿屋市周辺", "志布志市周辺"), "鹿屋市周辺"),
    (GOOD.replace("1 個あたり", "2 個あたり"), "数字"),
    (GOOD + "\n運賃は 12,000 円でお受けします。", "12000"),
    (GOOD + "\n運賃は一万円です。", "金額"),
    (GOOD.replace("お試し", "お試し【追加】"), "【追加】"),
    (SRC + "\nさらに長い文。", "長く"),
])
def test_machine_check_catches_changed_or_invented_facts(monkeypatch, bad, needle):
    c = FakeClient(text=bad)
    enable(monkeypatch, c)
    r = ax.offer_polish({"text": SRC}, "u1", False)
    assert r["ok"] and r["ready"] is False and any(needle in p for p in r["problems"]), r["problems"]
    assert len(c.calls) == 1                         # 自動の再試行はしない


def test_failed_check_is_not_cached_but_ready_result_is_reused_without_counting(monkeypatch):
    c = FakeClient(text="短い。")
    enable(monkeypatch, c, AI_DAILY_LIMIT="2")
    assert ax.offer_polish({"text": SRC}, "u1", False)["ready"] is False
    assert ax.offer_polish({"text": SRC}, "u1", False)["ready"] is False
    assert len(c.calls) == 2 and ax.LIMITER.used_today() == 2
    with pytest.raises(ax.AiError) as e:
        ax.offer_polish({"text": SRC}, "u1", False)
    assert e.value.status == 429 and len(c.calls) == 2
    ax.LIMITER = ax.Limiter()
    c.text = GOOD
    assert ax.offer_polish({"text": SRC}, "u1", False)["ready"] is True
    r = ax.offer_polish({"text": SRC}, "u1", False)
    assert r["cached"] is True and len(c.calls) == 3 and ax.LIMITER.used_today() == 1


def test_disabled_by_default_public_needs_switch_and_nothing_is_called(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(ax, "make_client", lambda: c)
    with pytest.raises(ax.AiError) as e:
        ax.offer_polish({"text": SRC}, "u1", False)
    assert e.value.status == 403 and e.value.code == "unavailable" and c.calls == []
    enable(monkeypatch, c)
    with pytest.raises(ax.AiError) as e:
        ax.offer_polish({"text": SRC}, "u1", True)
    assert e.value.status == 403 and c.calls == []


def test_bad_input_refusal_truncation_and_api_error(monkeypatch):
    c = FakeClient()
    enable(monkeypatch, c)
    for b in (None, {}, {"text": "  "}, {"text": 5}):
        with pytest.raises(ax.AiError) as e:
            ax.offer_polish(b, "u1", False)
        assert e.value.status == 400
    with pytest.raises(ax.AiError) as e:
        ax.offer_polish({"text": "あ" * 4001}, "u1", False)
    assert e.value.status == 413 and c.calls == []
    for client, code in ((FakeClient(stop="refusal"), "refused"), (FakeClient(stop="max_tokens"), "truncated"), (FakeClient(exc=RuntimeError(SENTINEL)), "api_error")):
        enable(monkeypatch, client)
        ax.CACHE.clear()
        with pytest.raises(ax.AiError) as e:
            ax.offer_polish({"text": SRC}, "u2", False)
        assert e.value.code == code and e.value.status == 502 and SENTINEL not in str(e.value)


@pytest.fixture
def server():
    httpd = serve_web.make_server(port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def post(base, payload):
    req = urllib.request.Request(base + "/api/ai/offer_polish", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_disabled_is_403_then_enabled_returns_the_proposal(server, monkeypatch):
    code, j = post(server, {"text": SRC})
    assert code == 403 and "使えない" in j["error"]
    c = FakeClient()
    enable(monkeypatch, c)
    code, j = post(server, {"text": SRC})
    assert code == 200 and j["ready"] is True and SENTINEL not in json.dumps(j)
    assert post(server, {"text": ""})[0] == 400


def test_negotiate_js_has_no_network_code_and_ai_explain_js_owns_the_endpoint():
    neg = (ROOT / "web" / "negotiate.js").read_text(encoding="utf-8")
    assert "/api/ai/" not in neg and "fetch(" not in neg and "AiExplain.offerPolish" in neg and "window.confirm" not in neg
    js = (ROOT / "web" / "ai_explain.js").read_text(encoding="utf-8")
    assert "/api/ai/offer_polish" in js and "ANTHROPIC" not in js
