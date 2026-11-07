"""SPEC §53.4 荷物の条件を文で書いて表にしてもらう（POST /api/ai/cargo_rows）。SDK は差し替える（本物の API は呼ばない。キーも使わない）。

- 送る内容は、渡した文だけ（system の指示と本文の 2 つ。ほかの欄は送らない）
- 戻りの行の数字は入力の文と照合する（mm・m・t・g の換算を含む）。文にない数字の行は外し、理由を返す
- 宅配の「120 サイズ」は縦・横・高さにしない（備考と「分からなかったこと」に書く）
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
TEXT = "段ボール 120サイズ 3箱 各40kg、パレット1枚 110×110×150cm 450kg、東京から大阪"


def row(kind="段ボール", n=None, l=None, w=None, h=None, kg=None, note=""):
    return {"kind": kind, "n": n, "l_cm": l, "w_cm": w, "h_cm": h, "kg": kg, "note": note}


GOOD = {"rows": [row("段ボール", 3, kg=40, note="120サイズ（3辺合計120cm以内）"), row("パレット", 1, 110, 110, 150, 450)],
        "origin": "東京", "destination": "大阪", "unknowns": ["出荷日"]}


class FakeClient:
    def __init__(self, data=GOOD, stop="end_turn", exc: Exception | None = None, raw: str | None = None):
        self.calls: list[dict] = []
        self.data, self.stop, self.exc, self.raw = data, stop, exc, raw
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        text = self.raw if self.raw is not None else json.dumps(self.data, ensure_ascii=False)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=self.stop, usage=SimpleNamespace(input_tokens=700, output_tokens=350))


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


def test_call_shape_and_payload_is_only_the_text(monkeypatch):
    c = FakeClient()
    enable(monkeypatch, c)
    r = ax.cargo_rows({"text": TEXT}, "u1", False)
    (kw,) = c.calls
    assert kw["model"] == "claude-haiku-5-5" and kw["max_tokens"] == 1000
    assert kw["messages"] == [{"role": "user", "content": TEXT}]       # 本文だけ
    assert kw["output_config"]["effort"] == "low" and kw["output_config"]["format"]["type"] == "json_schema"
    assert set(kw) == {"model", "max_tokens", "system", "messages", "output_config"} and "thinking" not in kw
    assert SENTINEL not in json.dumps(kw, ensure_ascii=False)
    assert r["ok"] and r["dropped"] == [] and r["origin"] == "東京" and r["destination"] == "大阪"
    assert [x["kind"] for x in r["rows"]] == ["段ボール", "パレット"] and r["rows"][1]["l_cm"] == 110 and r["rows"][1]["kg"] == 450
    assert r["step"]["model"] == "claude-haiku-5-5" and r["step"]["cost_usd"] < 0.001


def test_extra_body_fields_are_refused_and_nothing_is_called(monkeypatch):
    c = FakeClient()
    enable(monkeypatch, c)
    with pytest.raises(ax.AiError) as e:
        ax.cargo_rows({"text": TEXT, "partner": "A社"}, "u1", False)
    assert e.value.status == 400 and c.calls == [] and ax.LIMITER.used_today() == 0


def test_invented_numbers_are_dropped_with_reasons(monkeypatch):
    data = {"rows": [row("段ボール", 3, kg=40), row("パレット", 1, 110, 110, 150, 500), row("その他", 2, kg=40, note="10個入り"), row("パレット", 1, 110, 110, 150, 450)],
            "origin": "横浜", "destination": "大阪", "unknowns": []}
    enable(monkeypatch, FakeClient(data))
    r = ax.cargo_rows({"text": TEXT}, "u1", False)
    kept = [(x["kind"], x["kg"]) for x in r["rows"]]
    assert kept == [("段ボール", 40), ("その他", 40), ("パレット", 450)] or ("パレット", 500) not in kept
    assert ("パレット", 500) not in kept                                  # 500 kg は文にない
    assert any("2 行目" in d and "500" in d for d in r["dropped"])
    assert any("備考" in d for d in r["dropped"])                           # 備考の「10」も文にない
    assert r["origin"] == "" and any("横浜" in d for d in r["dropped"])   # 地名も文にあるものだけ
    assert r["destination"] == "大阪"


def test_unit_conversions_are_computed_and_checked(monkeypatch):
    t = "木箱 1200mm×800mm×1.5m を2個 重さは0.5トン ずつ、袋 500g が10袋"
    data = {"rows": [row("その他", 2, 120, 80, 150, 500), row("その他", 10, kg=0.5),
                     row("その他", 2, 1200, 800, 1.5, 500), row("その他", 1, kg=5000)], "origin": "", "destination": "", "unknowns": []}
    enable(monkeypatch, FakeClient(data))
    r = ax.cargo_rows({"text": t}, "u1", False)
    assert [(x["n"], x["l_cm"], x["kg"]) for x in r["rows"]] == [(2, 120, 500), (10, None, 0.5)]   # mm・m・トン・g の換算は通す
    assert len(r["dropped"]) == 2 and any("5000" in d for d in r["dropped"])
    assert ax.cargo_allowed("1.2m×0.8m×1.5m")["dims"] >= {120.0, 80.0, 150.0}
    assert 1.2 not in ax.cargo_allowed("1.2m×0.8m×1.5m")["dims"]


def test_takuhai_size_is_not_turned_into_dimensions(monkeypatch):
    t = "段ボール 120サイズ 3箱"
    data = {"rows": [row("段ボール", 3, 120, 120, 120, note="120サイズ")], "origin": "", "destination": "", "unknowns": []}
    enable(monkeypatch, FakeClient(data))
    r = ax.cargo_rows({"text": t}, "u1", False)
    assert r["rows"] == [] and len(r["dropped"]) == 1 and "縦の 120" in r["dropped"][0]   # 120 は寸法として使えない
    assert any("120サイズ" in u and "縦・横・高さは分かりません" in u for u in r["unknowns"])
    ax.CACHE.clear()
    ok = {"rows": [row("段ボール", 3, note="120 サイズ")], "origin": "", "destination": "", "unknowns": []}
    enable(monkeypatch, FakeClient(ok))
    r = ax.cargo_rows({"text": t}, "u1", False)
    (x,) = r["rows"]
    assert (x["l_cm"], x["w_cm"], x["h_cm"]) == (None, None, None) and x["note"] == "120サイズ（3辺合計120cm以内）"   # 備考は決まった形にそろう
    assert any("120サイズ" in u for u in r["unknowns"])


def test_disabled_is_403_and_public_needs_switch(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(ax, "make_client", lambda: c)
    with pytest.raises(ax.AiError) as e:
        ax.cargo_rows({"text": TEXT}, "u1", False)
    assert e.value.status == 403 and e.value.code == "unavailable" and c.calls == []
    enable(monkeypatch, c)
    with pytest.raises(ax.AiError) as e:
        ax.cargo_rows({"text": TEXT}, "u1", True)
    assert e.value.status == 403 and c.calls == []


def test_bad_input_limit_cache_and_failures(monkeypatch):
    c = FakeClient()
    enable(monkeypatch, c, AI_DAILY_LIMIT="1")
    for b in (None, {}, {"text": "  "}, {"text": 5}):
        with pytest.raises(ax.AiError) as e:
            ax.cargo_rows(b, "u1", False)
        assert e.value.status == 400
    with pytest.raises(ax.AiError) as e:
        ax.cargo_rows({"text": "あ" * 1001}, "u1", False)
    assert e.value.status == 413 and c.calls == []
    ax.cargo_rows({"text": TEXT}, "u1", False)
    assert ax.cargo_rows({"text": TEXT}, "u1", False)["cached"] is True and len(c.calls) == 1 and ax.LIMITER.used_today() == 1   # 同じ文は数えない
    with pytest.raises(ax.AiError) as e:
        ax.cargo_rows({"text": TEXT + "。"}, "u1", False)
    assert e.value.status == 429 and len(c.calls) == 1
    for client, code in ((FakeClient(stop="refusal"), "refused"), (FakeClient(stop="max_tokens", raw='{"rows": ['), "bad_output"),
                         (FakeClient(raw="これは JSON ではありません"), "bad_output"), (FakeClient(exc=RuntimeError(SENTINEL)), "api_error")):
        enable(monkeypatch, client)
        ax.CACHE.clear()
        ax.LIMITER = ax.Limiter()
        with pytest.raises(ax.AiError) as e:
            ax.cargo_rows({"text": TEXT}, "u2", False)
        assert e.value.code == code and e.value.status == 502 and SENTINEL not in str(e.value)


def test_status_has_cargo_cost(monkeypatch):
    enable(monkeypatch, FakeClient())
    c = ax.status(False)["cargo_cost"]
    assert c["out_max"] == 1000 and c["price_in"] == 0.10 and c["price_out"] == 0.50
    worst = (c["in_base"] + ax.CARGO_MAX_CHARS * ax.TOKENS_PER_CHAR) * c["price_in"] / 1e6 + c["out_max"] * c["price_out"] / 1e6
    assert worst < 0.001


@pytest.fixture
def server():
    httpd = serve_web.make_server(port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def post(base, payload):
    req = urllib.request.Request(base + "/api/ai/cargo_rows", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_disabled_is_403_then_enabled_returns_rows(server, monkeypatch):
    code, j = post(server, {"text": TEXT})
    assert code == 403 and "使えない" in j["error"]
    c = FakeClient()
    enable(monkeypatch, c)
    code, j = post(server, {"text": TEXT})
    assert code == 200 and len(j["rows"]) == 2 and SENTINEL not in json.dumps(j)
    assert post(server, {"text": ""})[0] == 400
