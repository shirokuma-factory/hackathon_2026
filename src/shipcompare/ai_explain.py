"""荷待ち協議の書面の「説明文の自動作成」（SPEC §53）。Claude を 3 段で使う。

1. Haiku 5.5 が記録を点検する（時刻の抜け・食い違い）
2. Haiku 5.5 が、渡された事実だけから荷主あての説明文を下書きする（2026-11-08 に Sonnet から変更。費用のため）
3. 数字の機械照合（Python）。環境変数 AI_VERIFY_OPUS=1 のときだけ、Opus 5.5 が下書きの数字と主張を事実と突き合わせる（既定は止めている）

送る事実は、相手の名前と地点名を「荷主A」「地点1」に置き換えたものだけを受け付ける（clean_facts が形を検める）。

確かめが通らないとき、下書きは返さず、型どおりの書面（ページが持っている文）に戻す。
キーは環境変数 ANTHROPIC_API_KEY だけから SDK が読む。このモジュールは値を読まず、書かず、ログにも出さない。
anthropic は make_client() の中で初めて読み込む（公開版の Docker イメージには入っていない）。
"""

from __future__ import annotations

import collections
import hashlib
import importlib.util
import json
import os
import re
import threading
import time
import unicodedata
from datetime import date
from typing import Any, Callable

MODEL_HAIKU = "claude-haiku-5-5"
MODEL_SONNET = "claude-sonnet-5-5"
MODEL_OPUS = "claude-opus-5-5"

# max_tokens は思考（thinking）の分を含む。Opus 5.5 は常に考え、Haiku・Sonnet 5.5 も既定で考えるので、出力だけの量より大きく取る
# 1 回の最悪の費用の目安（仮定）: 事実 30 KB を約 2.4 万トークン（0.8 トークン/字）として、上の上限まで出力した場合、
# Haiku 2 段だけで約 $0.009、AI_VERIFY_OPUS=1 で Opus の確かめを足すと約 $0.23
# 下書きは effort を low にして、思考で上限を使い切らないようにする（実測: 上限 4000・medium で文の途中で切れた）
MAX_TOKENS = {"check": 2000, "draft": 6000, "verify": 6000}
EFFORT = {"check": "low", "draft": "low", "verify": "medium"}
DRAFT_MODEL = MODEL_HAIKU  # 下書きは Haiku。Sonnet だと 1 回の費用の大半を占めた（SPEC §53.3）
# 100 万トークンあたりのドル（入力, 出力）。画面に出す額は目安
PRICE = {MODEL_HAIKU: (0.10, 0.50), MODEL_SONNET: (2.0, 10.0), MODEL_OPUS: (4.0, 20.0)}
MODEL_LABEL = {MODEL_HAIKU: "Haiku 5.5", MODEL_SONNET: "Sonnet 5.5", MODEL_OPUS: "Opus 5.5"}

# 1 回の費用の見積もり（仮定）。画面の確認の枠が、送る内容の大きさからこの表で計算する
TOKENS_PER_CHAR = 0.8   # 日本語まじりの JSON の 1 字あたりのトークン数（仮定。実測でないので「目安」と出す）
USD_JPY = 150           # 円の換算（仮定）
# in_base: 指示文などの固定の入力トークン、scale: 事実のどこを入力に足すか（rows = 記録の行だけ、all = 事実全体）、out: 出力（思考を含む）の見込み
STAGE_EST = {
    "check": {"in_base": 300, "scale": "rows", "out": 700},
    "draft": {"in_base": 1000, "scale": "all", "out": 1800},
    "verify": {"in_base": 800, "scale": "all", "out": 1500},
}

CARGO_IN_BASE = 650      # 荷物の表づくり: 指示文と出力の形の入力トークン（仮定。SPEC §53.4）
CARGO_OUT_TYPICAL = 600  # 同・出力の見込み（思考を含む。仮定）
MAX_FACTS_BYTES = 30_000
DEFAULT_DAILY_LIMIT = 100
PER_CLIENT_PER_HOUR = 10
DRAFT_MAX_CHARS = 700  # 依頼は 450 字以内。これを超えたら切らずに失敗にする（切ると文の途中で終わる）
DRAFT_ASK_CHARS = 450
ISSUE_MAX_CHARS = 30
MAX_ISSUES = 20
MAX_HISTORY = 5

# 相手の名前・地点名は置き換えたものだけを受け付ける（SPEC §53.3）。荷役をした人は役割の語だけ
PARTNER_RE = re.compile(r"^荷主[A-Z]{1,2}$")
PLACE_RE = re.compile(r"^地点\d{1,3}$")
HANDLER_OK = ("", "運転者", "荷主側", "荷主", "施設側", "センター側", "倉庫側", "他")
PLATE_RE = re.compile(r"[一-龥]{2,4}\s*\d{2,3}\s*[ぁ-ん]\s*\d{1,4}")  # 「品川 100 あ 1234」のような車番

FACT_KEYS = ("partner", "period", "count", "rows", "totals", "basis", "consult", "history")
ROW_KEYS = ("row", "date", "place", "arrive", "reception", "appointed", "start", "end", "handler", "shipper_fault",
            "wait_min", "handling_min", "amount")


class AiError(Exception):
    """利用者に見せる日本語のメッセージを持つ。code は画面の分岐用。"""

    def __init__(self, message: str, code: str = "error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


# ---------------------------------------------------------------- 設定と制限

def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def daily_limit() -> int:
    raw = os.environ.get("AI_DAILY_LIMIT", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_DAILY_LIMIT


def verify_opus_on() -> bool:
    """Opus による確かめ。既定は止めている（AI_VERIFY_OPUS=1 のときだけ）。数字の機械照合は常に行う。"""
    return _truthy("AI_VERIFY_OPUS")


def cost_stages(verify: bool | None = None) -> list[dict[str, Any]]:
    v = verify_opus_on() if verify is None else verify
    plan = [("check", MODEL_HAIKU), ("draft", DRAFT_MODEL)] + ([("verify", MODEL_OPUS)] if v else [])
    return [{"step": st, "model": m, "label": MODEL_LABEL[m], "price_in": PRICE[m][0], "price_out": PRICE[m][1], **STAGE_EST[st]} for st, m in plan]


def estimate_cost_usd(facts: dict[str, Any], verify: bool | None = None) -> float:
    """送る事実から 1 回の費用（ドル）を見積もる。画面（ai_explain.js）も同じ表で計算する。"""
    all_t = len(canonical(facts)) * TOKENS_PER_CHAR
    rows_t = len(canonical(facts.get("rows", []))) * TOKENS_PER_CHAR
    total = 0.0
    for g in cost_stages(verify):
        tin = g["in_base"] + (rows_t if g["scale"] == "rows" else all_t)
        total += (tin * g["price_in"] + g["out"] * g["price_out"]) / 1_000_000
    return total


def status(public: bool) -> dict[str, Any]:
    """{available, reason, limits, remaining, ...}。キーの値は見ず、あるかないかだけを調べる。reason は利用者に見せてよい文。"""
    verify = verify_opus_on()
    models = {"check": MODEL_HAIKU, "draft": DRAFT_MODEL, "verify": MODEL_OPUS if verify else None}
    limits = {"daily": daily_limit(), "per_client_per_hour": PER_CLIENT_PER_HOUR, "max_request_bytes": MAX_FACTS_BYTES, "models": models}
    used = LIMITER.used_today()
    extra = {"public": bool(public), "remaining": max(0, daily_limit() - used), "verify_opus": verify,
             "cost": {"tokens_per_char": TOKENS_PER_CHAR, "usd_jpy": USD_JPY, "stages": cost_stages(verify), "assumed": True},
             "cargo_cost": {"in_base": CARGO_IN_BASE, "out_typical": CARGO_OUT_TYPICAL, "out_max": CARGO_MAX_TOKENS,
                            "price_in": PRICE[MODEL_HAIKU][0], "price_out": PRICE[MODEL_HAIKU][1]},
             "own_cost": {"in_base": OWN_IN_BASE, "out_typical": OWN_OUT_TYPICAL, "out_max": OWN_MAX_TOKENS,
                          "price_in": PRICE[MODEL_HAIKU][0], "price_out": PRICE[MODEL_HAIKU][1]}}
    if not _truthy("AI_EXPLAIN_ENABLED"):
        return {"available": False, "reason": "disabled", "limits": limits, **extra}
    if public and not _truthy("AI_EXPLAIN_PUBLIC"):
        return {"available": False, "reason": "public_off", "limits": limits, **extra}
    if importlib.util.find_spec("anthropic") is None:
        return {"available": False, "reason": "no_sdk", "limits": limits, **extra}
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return {"available": False, "reason": "no_key", "limits": limits, **extra}
    return {"available": True, "reason": "", "limits": limits, **extra}


class Limiter:
    """1 日の回数と、来訪者ごとの 1 時間の回数。メモリだけで持つ（再起動で消える）。"""

    def __init__(self, clock: Callable[[], float] = time.time, today: Callable[[], date] = date.today):
        self._clock, self._today = clock, today
        self._lock = threading.Lock()
        self._day = today()
        self._count = 0
        self._clients: dict[str, collections.deque[float]] = {}

    def used_today(self) -> int:
        with self._lock:
            self._roll()
            return self._count

    def _roll(self) -> None:
        d = self._today()
        if d != self._day:
            self._day, self._count = d, 0

    def reserve(self, client: str) -> None:
        """1 回分を確保する。足りなければ AiError(429)。"""
        with self._lock:
            self._roll()
            now = self._clock()
            q = self._clients.setdefault(client, collections.deque())
            while q and now - q[0] >= 3600:
                q.popleft()
            if self._count >= daily_limit():
                raise AiError("今日の自動作成の上限に達しました。明日また使えます（今は型どおりの書面です）", "daily_cap", 429)
            if len(q) >= PER_CLIENT_PER_HOUR:
                raise AiError(f"自動作成は 1 時間に {PER_CLIENT_PER_HOUR} 回までです。しばらくしてからもう一度押してください", "client_cap", 429)
            self._count += 1
            q.append(now)
            if len(self._clients) > 2000:  # 古い来訪者を捨てる
                for k in [k for k, v in self._clients.items() if not v or now - v[-1] >= 3600]:
                    self._clients.pop(k, None)


LIMITER = Limiter()


class ResultCache:
    """同じ事実には同じ結果を返す（SHA-256 がキー）。"""

    def __init__(self, max_items: int = 200):
        self._d: collections.OrderedDict[str, dict[str, Any]] = collections.OrderedDict()
        self._max = max_items
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self._max:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


CACHE = ResultCache()


# ---------------------------------------------------------------- 入力

def _strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def clean_facts(body: Any) -> dict[str, Any]:
    """ページが送ってきた事実を検める。許す欄以外が 1 つでもあれば断る（黙って捨てない）。
    相手の名前と地点名は置き換えたもの（荷主A・地点1）だけを通す。車番らしい文字列があれば断る。"""
    if not isinstance(body, dict):
        raise AiError("事実の形が違います")
    if len(canonical(body).encode("utf-8")) > MAX_FACTS_BYTES:
        raise AiError(f"送る事実が大きすぎます（{MAX_FACTS_BYTES // 1000} KB まで）", "too_large", 413)
    extra = [k for k in body if k not in FACT_KEYS]
    if extra:
        raise AiError(f"送れない項目が含まれています（{'、'.join(str(k)[:20] for k in extra[:3])}）", "bad_field", 400)
    facts = {k: body[k] for k in FACT_KEYS if k in body}
    rows = facts.get("rows")
    if not isinstance(rows, list) or not rows:
        raise AiError("記録の行がありません")
    if not all(isinstance(r, dict) for r in rows):
        raise AiError("記録の行の形が違います")
    bad = sorted({k for r in rows for k in r if k not in ROW_KEYS})
    if bad:
        raise AiError(f"送れない項目が含まれています（行の {'、'.join(str(k)[:20] for k in bad[:3])}）", "bad_field", 400)
    if "partner" in facts and not (isinstance(facts["partner"], str) and PARTNER_RE.match(facts["partner"])):
        raise AiError("相手の名前は「荷主A」の形に置き換えて送ってください", "not_pseudonymized", 400)
    for r in rows:
        pl = r.get("place", "")
        if pl not in ("", None) and not (isinstance(pl, str) and PLACE_RE.match(pl)):
            raise AiError("地点名は「地点1」の形に置き換えて送ってください", "not_pseudonymized", 400)
        if r.get("handler", "") not in HANDLER_OK:
            raise AiError("荷役をした人は役割の語だけを送れます", "not_pseudonymized", 400)
    if any(PLATE_RE.search(t) for t in _strings(facts)):
        raise AiError("車番らしい文字列が含まれているため送れません", "not_pseudonymized", 400)
    facts["rows"] = [dict(r) for r in rows]
    if isinstance(facts.get("history"), list):
        facts["history"] = [str(x)[:300] for x in facts["history"][:MAX_HISTORY]]
    elif "history" in facts:
        del facts["history"]
    if len(canonical(facts).encode("utf-8")) > MAX_FACTS_BYTES:
        raise AiError(f"送る事実が大きすぎます（{MAX_FACTS_BYTES // 1000} KB まで）", "too_large", 413)
    return facts


def pseudonymize(facts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """相手の名前を「荷主A」、地点名を「地点1」「地点2」…に置き換える（出てきた順）。車番は消し、荷役をした人は役割の語だけ残す。
    戻り値は (置き換えた事実, {置き換え後: 元})。web/ai_explain.js の pseudonymize と同じ規則。"""
    mapping: dict[str, str] = {}
    names: list[tuple[str, str]] = []
    partner = str(facts.get("partner") or "")
    if partner:
        mapping["荷主A"] = partner
        names.append((partner, "荷主A"))
    places: dict[str, str] = {}
    for r in facts.get("rows", []):
        pl = str(r.get("place") or "")
        if pl and pl not in places:
            places[pl] = f"地点{len(places) + 1}"
            mapping[places[pl]] = pl
    names = sorted(names + list(places.items()), key=lambda t: -len(t[0]))

    def scrub(v: Any) -> Any:
        if isinstance(v, str):
            for old, new in names:
                v = v.replace(old, new)
            return PLATE_RE.sub("（車番）", v)
        if isinstance(v, list):
            return [scrub(x) for x in v]
        if isinstance(v, dict):
            return {k: scrub(x) for k, x in v.items()}
        return v

    out: dict[str, Any] = {}
    for k in FACT_KEYS:
        if k not in facts:
            continue
        if k == "partner":
            out[k] = "荷主A" if partner else ""
        elif k == "rows":
            rows = []
            for r in facts[k]:
                n = {rk: scrub(r[rk]) for rk in ROW_KEYS if rk in r}
                if "place" in n:
                    n["place"] = places.get(str(r.get("place") or ""), "")
                if "handler" in n and n["handler"] not in HANDLER_OK:
                    n["handler"] = "他"
                rows.append(n)
            out[k] = rows
        else:
            out[k] = scrub(facts[k])
    return out, mapping


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cache_key(facts: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(facts).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 呼び出し

def make_client():
    """SDK のクライアント。キーは環境変数から SDK が読む（ここでは値を扱わない）。試験ではこの関数を差し替える。"""
    import anthropic  # noqa: PLC0415

    return anthropic.Anthropic(timeout=120.0, max_retries=0)  # 自動の再試行はしない（黙って二重に課金されないように）


CHECK_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {
        "type": "object",
        "properties": {"row": {"type": "integer"}, "field": {"type": "string"}, "message": {"type": "string"}},
        "required": ["row", "field", "message"], "additionalProperties": False}}},
    "required": ["issues"], "additionalProperties": False}

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "problems": {"type": "array", "items": {
        "type": "object",
        "properties": {"quote": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["quote", "reason"], "additionalProperties": False}}},
    "required": ["ok", "problems"], "additionalProperties": False}

SYS_CHECK = ("あなたは荷待ち・荷役の記録の点検係です。渡された行の時刻を調べ、矛盾と必須欄の抜けだけを指摘します。"
             "指摘してよいもの: 時刻の順序の食い違い（順序は 到着 → 受付 → 荷役開始 → 荷役終了 です）、終わりが始まりより前、同じ行の重複、あり得ない値、"
             "必須欄（日付・到着・荷役開始）の抜け。"
             "指摘しないもの: 任意欄の空欄。受付、指定の到着時刻（appointed）、荷役終了（運転者や荷主側が荷役をしていない行）、荷役者（handler）が空なのは正常です。"
             "空欄そのものを理由にした指摘は 1 件も出さないでください。"
             f"各指摘は row（行番号）、field（欄名）、message（{ISSUE_MAX_CHARS} 字以内の日本語）です。同じ内容を行ごとに繰り返さず、問題がなければ issues は空にします。"
             "計算や金額には触れません。")
SYS_DRAFT = ("あなたは運送会社の配車課長です。荷主の担当者に送る相談のメール（件名・宛先・本文・記録の表・署名の定型の文面）に添える、短い説明の段落を書きます。"
             "読み手は付き合いのある荷主の担当者です。普段の仕事で使う、丁寧で飾らない話し言葉に近い文体で、人が書いたように読める文章にします。"
             f"{DRAFT_ASK_CHARS} 字以内、2〜3 つの短い段落、本文だけを書きます（見出し・箇条書き・記号の装飾は使いません）。"
             "書く内容は、いつもの礼、この期間に荷待ちが何回あり合計でどれだけ待ったか、参考額とその根拠（告示の表に当てた計算で、請求を決めるものではないこと）、"
             "そして、荷待ちを減らす方法を一度相談させてほしいというお願いです。"
             "文体の決まり: 硬い言い回し（『〜とします』『〜するものとします』『〜に関しまして』『〜の件』『下記のとおり』）を重ねない。"
             "『〜です。』『〜ます。』を 3 文以上続けず、文の終わり方と長さを変える。同じ言い回しを繰り返さない。定型の文面の言い直しではなく、添え書きとして一言添える気持ちで書く。"
             "守ること: "
             "(1) 事実と『使ってよい数字の一覧』に書かれたことだけを使う。事実にないことを足さない。"
             "(2) 数字は一覧にあるものだけを、算用数字（半角）で書く。漢数字（一回、三万円など）は使わない。計算し直さない。足し算・割り算・丸めをしない。"
             "(3) 約束・謝罪・記録の出来についての言及をしない（『今後は補います』『記録の不備』のような、事実から言えない約束や評価は書かない）。"
             "(4) 空欄の任意欄（受付、指定の到着時刻、荷役終了など）には触れない。"
             "(5) 相手を責めない。相談のお願いの文にする。気持ちを打ち明ける言い回し（『正直に申しますと』『気がかりです』『残念ながら』など）は使わない。(6) これまでの協議の履歴（history）があれば、その続きとして触れる。"
             "(7) 相手の名前と場所は、事実に書かれた呼び名（荷主A、地点1 など）のまま書く。言い換えたり、実在の名前を推測して書いたりしない。")
SYS_VERIFY = ("あなたは説明文の検算係です。事実（facts）と下書き（draft）を突き合わせ、下書きの数字・日付・場所・名前・主張の"
              "すべてが事実から言えるかを確かめます。事実にない数字、事実と違う数字、事実から言えない断定（約束、謝罪、記録の出来への評価を含む）があれば problems に入れます"
              "（quote は下書きからの引用、reason は理由）。すべて一致するときだけ ok を true にし、problems は空にします。")


def _text_of(resp: Any) -> str:
    for b in resp.content:
        if getattr(b, "type", "") == "text":
            return b.text
    return ""


def _usage(resp: Any) -> tuple[int, int]:
    u = getattr(resp, "usage", None)
    return int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0)


def cost_usd(model: str, tin: int, tout: int) -> float:
    pi, po = PRICE[model]
    return (tin * pi + tout * po) / 1_000_000


class _Refused(Exception):
    pass


def _call(client: Any, step: str, model: str, system: str, user: str, schema: dict | None) -> tuple[Any, dict[str, Any]]:
    """1 段の呼び出し。(応答, 記録) を返す。thinking は送らない（Opus 5.5 は常にオン）。assistant の書き出しも使わない。"""
    kw: dict[str, Any] = dict(model=model, max_tokens=MAX_TOKENS[step], system=system,
                              messages=[{"role": "user", "content": user}])
    oc: dict[str, Any] = {"effort": EFFORT[step]}
    if schema is not None:
        oc["format"] = {"type": "json_schema", "schema": schema}
    kw["output_config"] = oc
    t0 = time.perf_counter()
    resp = client.messages.create(**kw)
    ms = int((time.perf_counter() - t0) * 1000)
    tin, tout = _usage(resp)
    rec = {"step": step, "model": model, "label": MODEL_LABEL[model], "input_tokens": tin, "output_tokens": tout, "ms": ms,
           "cost_usd": round(cost_usd(model, tin, tout), 6), "stop_reason": getattr(resp, "stop_reason", None)}
    if getattr(resp, "stop_reason", None) == "refusal":
        raise _Refused(rec)  # type: ignore[arg-type]
    return resp, rec


def _json_of(resp: Any) -> Any:
    if getattr(resp, "stop_reason", None) == "max_tokens":
        raise ValueError("出力が途中で切れました")
    return json.loads(_text_of(resp))


# 数字の機械照合（モデルに頼らず Python で行う）。カンマを外した数字の列が、事実の中に同じ形で現れるか
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_HM_RE = re.compile(r"(\d+)\s*時間\s*(\d+)\s*分")


def _numbers(text: str) -> set[str]:
    return {(m.lstrip("0") or "0") if m.isdigit() else m for m in _NUM_RE.findall(text.replace(",", ""))}


def _hm_to_min(s: Any) -> int | None:
    m = _HM_RE.search(str(s or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _tot(facts: dict[str, Any]) -> dict[str, Any]:
    return facts["totals"] if isinstance(facts.get("totals"), dict) else {}


def allowed_figures(facts: dict[str, Any]) -> list[tuple[str, str]]:
    """下書きに使ってよい数字の一覧（ラベル, 表記）。Sonnet に渡し、機械照合も同じ一覧で行う。"""
    tot = _tot(facts)
    out: list[tuple[str, str]] = []
    if facts.get("partner"):
        out.append(("相手", str(facts["partner"])))
    if facts.get("period"):
        out.append(("期間", str(facts["period"])))
    out.append(("件数（回数）", f"{facts.get('count', len(facts.get('rows', [])))} 件"))
    for key, label in (("wait_total", "合計の待ち"), ("handling_total", "運転者の荷役の合計")):
        mins = _hm_to_min(tot.get(key))
        if mins is not None:
            out.append((label, f"{mins} 分（{tot[key]}）"))
    if "amount_yen_ex_tax" in tot:
        v = tot["amount_yen_ex_tax"]
        out.append(("参考額（税抜）", f"{v:,} 円" if isinstance(v, int) else f"{v} 円"))
    if "over_1h_count" in tot:
        out.append(("1 時間以上の荷待ちの回数", f"{tot['over_1h_count']} 回"))
    if "over_2h_count" in tot:
        out.append(("2 時間以上の荷待ちの回数", f"{tot['over_2h_count']} 回"))
    if "from_reception" in tot:
        out.append(("待ちの数え方", "受付の時刻から" if tot["from_reception"] else "着いた時刻（到着）から"))
    b = facts.get("basis") if isinstance(facts.get("basis"), dict) else {}
    if b.get("tariff_applies_from"):
        out.append(("根拠の表の適用日", str(b["tariff_applies_from"])))
    if "unit_minutes" in b:
        out.append(("計算の単位", f"{b['unit_minutes']} 分ごと"))
    if "free_minutes" in b:
        out.append(("計算に入れない最初の時間", f"{b['free_minutes']} 分"))
    for i, line in enumerate(facts.get("consult") or [], 1):
        out.append((f"場所別の集計 {i}（この文の数字は使ってよい）", str(line)))
    return out


def figures_text(facts: dict[str, Any]) -> str:
    return "\n".join(f"- {label}: {val}" for label, val in allowed_figures(facts))


def _num_set(values: Any) -> set[float]:
    out: set[float] = set()
    for v in values:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out.add(float(v))
    return out


def _allowed_sets(facts: dict[str, Any]) -> dict[str, set[float]]:
    """単位ごとに許す数。円、回と件、分、時間。"""
    tot = _tot(facts)
    rows = [r for r in facts.get("rows", []) if isinstance(r, dict)]
    consult = " ".join(str(x) for x in (facts.get("consult") or []))

    def in_consult(unit: str) -> set[float]:
        return {float(x.replace(",", "")) for x in re.findall(rf"(\d[\d,]*)\s*{unit}", consult)}

    basis = facts.get("basis") if isinstance(facts.get("basis"), dict) else {}
    yen = _num_set([v for k, v in tot.items() if "yen" in k or "amount" in k] + [r.get("amount") for r in rows]) | in_consult("円")
    times = _num_set([facts.get("count"), len(rows)] + [v for k, v in tot.items() if "count" in k]) | in_consult("回") | in_consult("件")
    mins = _num_set([r.get(k) for r in rows for k in ("wait_min", "handling_min")] + [basis.get(k) for k in ("unit_minutes", "free_minutes")]) | in_consult("分")
    hours: set[float] = {1.0, 2.0} | in_consult("時間")
    for key in ("wait_total", "handling_total"):
        m = _hm_to_min(tot.get(key))
        if m is not None:
            mins |= {float(m), float(m % 60)}
            hours.add(float(m // 60))
    for v in list(mins):
        mins.add(v % 60)
        hours.add(float(int(v) // 60))
    return {"yen": yen, "times": times, "min": mins, "hour": hours}


def unmatched_numbers(draft: str, facts: dict[str, Any]) -> list[str]:
    known = _numbers(canonical(facts)) | _numbers(figures_text(facts))
    for key in ("wait_total", "handling_total"):  # 分に直した値（682 など）も許す
        m = _hm_to_min(_tot(facts).get(key))
        if m is not None:
            known.add(str(m))
    # 換算は、事実にある分の値（合計・行・基準）だけを時間と分に直したものに限る（例: 682 分 = 11 時間 22 分）
    for v in _allowed_sets(facts)["min"]:
        n = int(v)
        known.update({str(n), str(n // 60), str(n % 60)})
    return sorted(n for n in _numbers(draft) if n not in known)


# 数字に付く助数詞の前の漢数字だけを断る（一緒・一つ・一度・一方 などの普通の語は通す）
_KANJI_COUNTER_RE = re.compile(r"[〇一二三四五六七八九十百千万]+\s*(?:円|回|件|分|時間|日|台|人|か所|ヶ所|箇所|割|倍|%)|半額")
_URL_RE = re.compile(r"https?://|www\.|[a-z0-9-]+\.(?:com|jp|net|org|io)\b", re.I)
_INSTR_RE = re.compile(r"転送|クリック|リンク|振込|振り込|送金|ダウンロード|ログイン")
_RATIO_RE = re.compile(r"\d[\d,.]*\s*(?:%|倍|割|日以内|日まで|日間|日)")
_MD_RE = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日")
_PSEUDO_RE = re.compile(r"荷主[A-Z]|地点\d+|【[^】]*】")
_JUBUN_OK_RE = re.compile(r"十分(?=[になでをはもだ、。]|$)")  # 「十分に」は数ではない
_YEN_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*円")
_MAN_YEN_RE = re.compile(r"\d[\d,]*\s*万\s*円")
_TIMES_RE = re.compile(r"(\d[\d,]*)\s*[回件]")
_MIN_RE = re.compile(r"(\d[\d,]*)\s*分")
_HOUR_RE = re.compile(r"(\d[\d,]*)\s*時間")


def draft_issues(draft: str, facts: dict[str, Any]) -> list[dict[str, str]]:
    """下書きの数字の機械照合。全角の数字は半角にそろえてから調べる。
    1. 事実に無い数字  2. 「円」の前は額  3. 「回」「件」の前は回数・件数  4. 「分」「時間」の前は一覧の時間
    5. 助数詞（円・回・件・分・時間・日・台・人・か所）に付く漢数字は使わせない（照合できないため）"""
    text = unicodedata.normalize("NFKC", draft)
    out: list[dict[str, str]] = []
    odd = unmatched_numbers(text, facts)
    if odd:
        out.append({"quote": "、".join(odd), "reason": "事実にない数字が下書きに入っています（機械照合）"})
    ok = _allowed_sets(facts)
    for m in _YEN_RE.finditer(text):
        if float(m.group(1).replace(",", "")) not in ok["yen"]:
            out.append({"quote": m.group(0), "reason": "「円」の前の数字が、合計の額にも行の額にも一致しません（機械照合）"})
    for m in _MAN_YEN_RE.finditer(text):
        out.append({"quote": m.group(0), "reason": "金額は「33,550 円」のように円までの算用数字で書く決まりです（機械照合）"})
    for m in _TIMES_RE.finditer(text):
        if float(m.group(1).replace(",", "")) not in ok["times"]:
            out.append({"quote": m.group(0), "reason": "「回」「件」の前の数字が、一覧の回数・件数に一致しません（機械照合）"})
    for m in _MIN_RE.finditer(text):
        if float(m.group(1).replace(",", "")) not in ok["min"]:
            out.append({"quote": m.group(0), "reason": "「分」の前の数字が、一覧の時間に一致しません（機械照合）"})
    for m in _HOUR_RE.finditer(text):
        if float(m.group(1).replace(",", "")) not in ok["hour"]:
            out.append({"quote": m.group(0), "reason": "「時間」の前の数字が、一覧の時間に一致しません（機械照合）"})
    for m in _KANJI_COUNTER_RE.finditer(_JUBUN_OK_RE.sub("", text)):
        out.append({"quote": m.group(0), "reason": "漢数字が使われていて数字を照合できません（機械照合）"})
    ftxt = unicodedata.normalize("NFKC", canonical(facts) + "\n" + figures_text(facts))
    fcomp = re.sub(r"[\s,]", "", ftxt)
    for m in _URL_RE.finditer(text):
        out.append({"quote": m.group(0), "reason": "事実にない URL が入っています（機械照合）"})
    for m in _INSTR_RE.finditer(text):
        if m.group(0) not in ftxt:
            out.append({"quote": m.group(0), "reason": "事実にない依頼・指示の言い方が入っています（機械照合）"})
    for m in list(_RATIO_RE.finditer(text)) + list(_MD_RE.finditer(text)):
        if re.sub(r"[\s,]", "", m.group(0)) not in fcomp:
            out.append({"quote": m.group(0), "reason": "割合・倍率・期限・日付が、事実の中に見つかりません（機械照合）"})
    for m in _PSEUDO_RE.finditer(text):
        if m.group(0) not in ftxt:
            out.append({"quote": m.group(0), "reason": "事実にない呼び名・差し込みの欄が入っています（機械照合）"})
    return out


OPTIONAL_FIELDS = ("reception", "appointed", "end", "handler", "受付", "指定", "荷役終了", "荷役者", "終了", "担当")
_BLANK_WORDS = ("空欄", "空白", "未入力", "記載がな", "記入がな", "入力がな")


def group_issues(raw: list[Any]) -> list[dict[str, Any]]:
    """点検の指摘を整える。任意欄の空欄だけを言う指摘は捨て、同じ文はまとめる（rows と n を付ける）。"""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for it in raw:
        try:
            row, field, msg = int(it["row"]), str(it["field"])[:20], str(it["message"])[:ISSUE_MAX_CHARS]
        except (KeyError, TypeError, ValueError):
            continue
        if any(w in field for w in OPTIONAL_FIELDS) and any(w in msg for w in _BLANK_WORDS):
            continue
        g = grouped.setdefault((field, msg), {"row": row, "rows": [], "field": field, "message": msg, "n": 0})
        g["rows"].append(row)
        g["n"] += 1
    return list(grouped.values())[:MAX_ISSUES]


Progress = Callable[[dict[str, Any]], None]


def run_pipeline(facts: dict[str, Any], client: Any, progress: Progress | None = None) -> dict[str, Any]:
    """3 段を順に走らせる。戻り値は画面に返す辞書。失敗しても例外にせず fallback=True で返す。"""
    emit = progress or (lambda _e: None)
    steps: list[dict[str, Any]] = []
    out: dict[str, Any] = {"ok": False, "fallback": True, "draft": None, "issues": [], "problems": [], "steps": steps,
                           "message": "", "disclaimer": "金額は目安です（概算）。"}
    facts_json = canonical(facts)

    def done(rec: dict[str, Any], **extra: Any) -> None:
        rec.update(extra)
        steps.append(rec)
        emit({"event": "done", **rec})

    def fail_step(step: str, model: str, message: str, rec: dict[str, Any] | None = None) -> None:
        r = rec or {"step": step, "model": model, "label": MODEL_LABEL[model], "input_tokens": 0, "output_tokens": 0, "ms": 0, "cost_usd": 0.0}
        done(r, status="failed", detail=message)

    # 1 記録の点検（失敗しても先へ進む）
    emit({"event": "start", "step": "check", "model": MODEL_HAIKU, "label": MODEL_LABEL[MODEL_HAIKU]})
    try:
        resp, rec = _call(client, "check", MODEL_HAIKU, SYS_CHECK, "記録の行（JSON）:\n" + canonical(facts["rows"]), CHECK_SCHEMA)
        data = _json_of(resp)
        issues = group_issues(data.get("issues", []))
        out["issues"] = issues
        done(rec, status="ok", detail=f"指摘 {len(issues)} 件", count=len(issues))
    except _Refused as e:
        fail_step("check", MODEL_HAIKU, "点検は断られました", e.args[0])
    except Exception as e:  # noqa: BLE001  メッセージにキーが入ることはないが、型名だけを返す
        fail_step("check", MODEL_HAIKU, f"点検できませんでした（{type(e).__name__}）")

    # 2 下書き（失敗したら型どおりに戻す）
    emit({"event": "start", "step": "draft", "model": DRAFT_MODEL, "label": MODEL_LABEL[DRAFT_MODEL]})
    try:
        # 点検の指摘は渡さない（記録の出来や約束についての文を誘うため）。数字は一覧のものだけ
        user = ("事実（JSON）:\n" + facts_json + "\n\n使ってよい数字の一覧（これ以外の数字は書かない）:\n" + figures_text(facts)
                + f"\n\n上の事実と一覧だけを使って、定型の書面に添える短い添え書きを {DRAFT_ASK_CHARS} 字以内、2〜3 つの短い段落で書いてください。")
        resp, rec = _call(client, "draft", DRAFT_MODEL, SYS_DRAFT, user, None)
        if getattr(resp, "stop_reason", None) == "max_tokens":  # 切れた下書きは確かめに回さない
            fail_step("draft", DRAFT_MODEL, "下書きが途中で切れました（max_tokens）", rec)
            out["message"] = "説明文が途中で切れたため、出していません。もう一度試すか、型どおりの書面をお使いください。"
            return out
        draft = _text_of(resp).strip()
        if not draft:
            raise ValueError("下書きが空でした")
        if len(draft) > DRAFT_MAX_CHARS:
            fail_step("draft", DRAFT_MODEL, f"下書きが長すぎました（{len(draft)} 字）", rec)
            out["message"] = "説明文が長すぎたため、出していません。もう一度試すか、型どおりの書面をお使いください。"
            return out
        if draft[-1] not in "。」）)":  # 文の途中で終わっている
            fail_step("draft", DRAFT_MODEL, "下書きが文の途中で終わっていました", rec)
            out["message"] = "説明文が途中で切れたため、出していません。もう一度試すか、型どおりの書面をお使いください。"
            return out
        done(rec, status="ok", detail=f"{len(draft)} 字")
    except _Refused as e:
        fail_step("draft", DRAFT_MODEL, "下書きは断られました", e.args[0])
        out["message"] = "Claude が説明文の作成を断りました。型どおりの書面をお使いください。"
        return out
    except Exception as e:  # noqa: BLE001
        fail_step("draft", DRAFT_MODEL, f"下書きできませんでした（{type(e).__name__}）")
        out["message"] = "説明文を作れませんでした。型どおりの書面をお使いください。"
        return out

    # 3 数字の確かめ。機械照合は常に行う。Opus による確かめは AI_VERIFY_OPUS=1 のときだけ（通らなければ下書きは返さない）
    if not verify_opus_on():
        emit({"event": "start", "step": "verify", "model": "machine", "label": "数字の機械照合"})
        problems = draft_issues(draft, facts)
        ok = not problems
        rec = {"step": "verify", "model": "machine", "label": "数字の機械照合", "input_tokens": 0, "output_tokens": 0, "ms": 0,
               "cost_usd": 0.0, "stop_reason": None}
        done(rec, status="ok" if ok else "mismatch", detail="一致" if ok else f"要確認（{len(problems)} 件）", verified=ok)
    else:
        emit({"event": "start", "step": "verify", "model": MODEL_OPUS, "label": MODEL_LABEL[MODEL_OPUS]})
        try:
            user = "事実（JSON）:\n" + facts_json + "\n\n下書き:\n" + draft
            resp, rec = _call(client, "verify", MODEL_OPUS, SYS_VERIFY, user, VERIFY_SCHEMA)
            data = _json_of(resp)
            problems = [{"quote": str(p["quote"])[:200], "reason": str(p["reason"])[:200]} for p in data.get("problems", [])[:10]]
            model_ok = bool(data.get("ok")) and not problems
            odd = draft_issues(draft, facts)
            problems.extend(odd)
            ok = model_ok and not odd
            done(rec, status="ok" if ok else "mismatch", detail="一致" if ok else f"要確認（{len(problems)} 件）", verified=ok)
        except _Refused as e:
            fail_step("verify", MODEL_OPUS, "確かめは断られました", e.args[0])
            out["problems"] = [{"quote": "", "reason": "数字を確かめられなかったため、下書きは出していません"}]
            out["message"] = "数字を確かめられなかったので、型どおりの書面をお使いください。"
            return out
        except Exception as e:  # noqa: BLE001
            fail_step("verify", MODEL_OPUS, f"確かめられませんでした（{type(e).__name__}）")
            out["problems"] = [{"quote": "", "reason": "数字を確かめられなかったため、下書きは出していません"}]
            out["message"] = "数字を確かめられなかったので、型どおりの書面をお使いください。"
            return out

    out["problems"] = problems
    if ok:
        out.update(ok=True, fallback=False, draft=draft, message="")
    else:
        out["message"] = "数字の確かめで食い違いがあったので、下書きは出していません。型どおりの書面をお使いください。"
    return out


def totals(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"input_tokens": sum(s.get("input_tokens", 0) for s in steps), "output_tokens": sum(s.get("output_tokens", 0) for s in steps),
            "cost_usd": round(sum(s.get("cost_usd", 0.0) for s in steps), 6), "ms": sum(s.get("ms", 0) for s in steps)}


# ---------------------------------------------------------------- 仕事（サーバーが使う）

class Jobs:
    """進み具合を GET で取れるようにする、メモリだけの仕事の表。"""

    def __init__(self, max_items: int = 100):
        self._d: collections.OrderedDict[str, dict[str, Any]] = collections.OrderedDict()
        self._max = max_items
        self._lock = threading.Lock()

    def create(self) -> str:
        jid = hashlib.sha256(os.urandom(16)).hexdigest()[:20]
        with self._lock:
            self._d[jid] = {"id": jid, "state": "running", "events": [], "result": None}
            while len(self._d) > self._max:
                self._d.popitem(last=False)
        return jid

    def event(self, jid: str, ev: dict[str, Any]) -> None:
        with self._lock:
            if jid in self._d:
                self._d[jid]["events"].append(ev)

    def finish(self, jid: str, result: dict[str, Any]) -> None:
        with self._lock:
            if jid in self._d:
                self._d[jid].update(state="done", result=result)

    def get(self, jid: str) -> dict[str, Any] | None:
        with self._lock:
            j = self._d.get(jid)
            return json.loads(json.dumps(j)) if j else None


JOBS = Jobs()
_RUN_ASYNC = True  # 試験は False にして、その場で終わらせる


def start(body: Any, client_id: str, public: bool) -> dict[str, Any]:
    """POST /api/ai/waiting_explain。同じ事実の結果があればその場で {cached:true, result} を返す（仕事も上限も使わない）。
    なければ上限を確かめてから仕事を作り、{job} を返す。上限で断った仕事は作らない。"""
    st = status(public)
    if not st["available"]:
        raise AiError(reason_text(st["reason"], public), "unavailable", 403)
    facts = clean_facts(body)
    key = cache_key(facts) + (":opus" if verify_opus_on() else "")  # 確かめの有無で結果が変わる
    hit = CACHE.get(key)
    if hit is not None:
        return {"cached": True, "result": dict(hit, cached=True)}
    LIMITER.reserve(client_id)  # 断られたら AiError。仕事は作らない
    jid = JOBS.create()

    def work() -> None:
        try:
            client = make_client()
            res = run_pipeline(facts, client, lambda e: JOBS.event(jid, e))
            res["totals"] = totals(res["steps"])
            res["cached"] = False
            if not any(s.get("status") == "failed" for s in res["steps"]):  # 点検を含め、呼び出しが失敗した結果は覚えない
                CACHE.put(key, res)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "fallback": True, "draft": None, "issues": [], "problems": [], "steps": [], "cached": False,
                   "message": f"説明文を作れませんでした（{type(e).__name__}）。型どおりの書面をお使いください。"}
        JOBS.finish(jid, res)

    if _RUN_ASYNC:
        threading.Thread(target=work, daemon=True).start()
    else:
        work()
    return {"job": jid, "cached": False}


def job_state(jid: str) -> dict[str, Any]:
    j = JOBS.get(jid)
    if j is None:
        raise AiError("その仕事は見つかりません", "no_job", 404)
    return j


# ---------------------------------------------------------------- 自社の運行記録の列の整理（SPEC §53.1）
# 列の見出しと「値の形の要約」だけを Haiku に送る。セルの値（車番・運転者・地点名など）は送らない。
# 値の形は決まった語彙（SHAPE_LABELS）の割合だけで、語彙にない文字列はこちらで捨てる。

OWN_MODEL = MODEL_HAIKU
OWN_MAX_TOKENS = 2000
OWN_EFFORT = "low"
OWN_IN_BASE = 450       # 列の整理: 指示文と出力の形の入力トークン（仮定）
OWN_OUT_TYPICAL = 400   # 同・出力の見込み（仮定）
OWN_MAX_COLUMNS = 40
OWN_HEADER_MAX = 30
OWN_REASON_MAX = 60
# 画面（web/own_calc.js の roleOptions）が選ばせる役割。この外の役割はサーバーで捨てる。ignore は「使わない」
OWN_ROLES = ("date", "plate", "type", "from", "to", "weight", "time", "mark", "kind", "start_time", "end_time", "distance_km",
             "loaded_flag", "place_name", "driver_code", "loaded_km", "empty_km", "wait_min", "restraint", "skip")
OWN_IGNORE = "ignore"
OWN_CONFIDENCE = ("high", "medium", "low")
SHAPE_LABELS = {
    "date_ymd": "YYYY/MM/DD の日付", "date_md": "M/D の日付", "datetime": "YYYY-MM-DD HH:MM の日時", "time_hm": "HH:MM の時刻",
    "int_1_2": "整数 1〜2 桁", "int_3_4": "整数 3〜4 桁", "int_5p": "整数 5 桁以上", "decimal": "小数まじりの数",
    "number_unit": "数 + 単位（km・分・t など）", "plate_like": "車番らしい文字列", "place_like": "漢字・かなの名称（地点名らしい）",
    "code_like": "英数字の符号", "other_text": "そのほかの文字列",
}
OWN_SCHEMA = {
    "type": "object",
    "properties": {"columns": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, "role": {"type": "string", "enum": [*[r for r in OWN_ROLES if r != "skip"], OWN_IGNORE]},
                       "confidence": {"type": "string", "enum": list(OWN_CONFIDENCE)}, "reason_ja": {"type": "string"}},
        "required": ["index", "role", "confidence", "reason_ja"], "additionalProperties": False}}},
    "required": ["columns"], "additionalProperties": False}
SYS_OWN = ("あなたは運送会社の運行記録（配車表・デジタコ）の列の整理係です。渡されるのは、各列の見出しと、その列の値の形の要約だけで、値そのものはありません。"
           "各列について、options の中から役割を 1 つ選びます。使わない列（備考・金額・メモなど）は ignore にします。"
           "見出しと値の形の両方が合うときだけ confidence を high にし、片方だけなら medium、あてずっぽうに近ければ low にします。"
           "同じ役割を 2 つの列に付けてよいのは、本当に別の列のときだけです。"
           f"reason_ja は {OWN_REASON_MAX} 字以内の日本語 1 文です。値の具体例を作ってはいけません（渡されていません）。")


def clean_own_columns(body: Any) -> list[dict[str, Any]]:
    """画面が送ってきた列の情報を検める。知っている欄だけ残し、値の形は決まった語彙の割合だけを通す。"""
    cols_in = body.get("columns") if isinstance(body, dict) else None
    if not isinstance(cols_in, list) or not cols_in:
        raise AiError("整理する列がありません")
    if len(cols_in) > OWN_MAX_COLUMNS:
        raise AiError(f"一度に整理できる列は {OWN_MAX_COLUMNS} 本までです", "too_large", 413)
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for c in cols_in:
        if not isinstance(c, dict) or isinstance(c.get("index"), bool) or not isinstance(c.get("index"), int):
            continue
        idx = c["index"]
        if idx in seen or idx < 0:
            continue
        seen.add(idx)
        header = unicodedata.normalize("NFKC", str(c.get("header", ""))).strip()[:OWN_HEADER_MAX]
        raw_opts = c.get("options")
        if raw_opts is not None and not isinstance(raw_opts, list):
            raise AiError("列の形が違います")
        opts = [r for r in (raw_opts or []) if isinstance(r, str) and r in OWN_ROLES]
        opts = list(dict.fromkeys(opts)) or [r for r in OWN_ROLES if r != "skip"]
        sh = c.get("shape") if isinstance(c.get("shape"), dict) else {}
        classes: dict[str, float] = {}
        raw_classes = sh.get("classes") if isinstance(sh.get("classes"), dict) else {}
        for k, v in raw_classes.items():
            if k in SHAPE_LABELS and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1:
                classes[k] = round(float(v), 2)
        shape: dict[str, Any] = {"classes": dict(sorted(classes.items(), key=lambda kv: -kv[1])[:5])}
        v = sh.get("fill")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1:
            shape["fill"] = round(float(v), 2)
        for k in ("n", "distinct", "len_min", "len_max"):
            v = sh.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10_000_000:
                shape[k] = v
        out.append({"index": idx, "header": header, "options": opts, "shape": shape})
    if not out:
        raise AiError("整理する列がありません")
    return out


def own_columns_prompt(cols: list[dict[str, Any]]) -> str:
    items = [{"index": c["index"], "header": c["header"], "options": c["options"] + [OWN_IGNORE],
              "shape": {**c["shape"], "classes": {SHAPE_LABELS[k]: v for k, v in c["shape"]["classes"].items()}}} for c in cols]
    return "列の一覧（JSON。値そのものはありません）:\n" + canonical(items)


def validate_own_result(data: Any, cols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """モデルの答えを検める。列の options にない役割・知らない index・重複は捨てる（画面には検めた分だけ渡す）。"""
    allowed = {c["index"]: set(c["options"]) | {OWN_IGNORE} for c in cols}
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    items = data.get("columns") if isinstance(data, dict) else None
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        idx, role = it.get("index"), it.get("role")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx in seen or idx not in allowed or role not in allowed[idx]:
            continue
        conf = it.get("confidence") if it.get("confidence") in OWN_CONFIDENCE else "low"
        seen.add(idx)
        out.append({"index": idx, "role": role, "confidence": conf, "reason_ja": str(it.get("reason_ja", ""))[:OWN_REASON_MAX]})
    return out


def _call_own(client: Any, cols: list[dict[str, Any]]) -> tuple[Any, dict[str, Any]]:
    kw: dict[str, Any] = dict(model=OWN_MODEL, max_tokens=OWN_MAX_TOKENS, system=SYS_OWN,
                              messages=[{"role": "user", "content": own_columns_prompt(cols)}],
                              output_config={"effort": OWN_EFFORT, "format": {"type": "json_schema", "schema": OWN_SCHEMA}})
    t0 = time.perf_counter()
    resp = client.messages.create(**kw)
    ms = int((time.perf_counter() - t0) * 1000)
    tin, tout = _usage(resp)
    rec = {"step": "own_columns", "model": OWN_MODEL, "label": MODEL_LABEL[OWN_MODEL], "input_tokens": tin, "output_tokens": tout, "ms": ms,
           "cost_usd": round(cost_usd(OWN_MODEL, tin, tout), 6), "stop_reason": getattr(resp, "stop_reason", None)}
    if getattr(resp, "stop_reason", None) == "refusal":
        raise _Refused(rec)  # type: ignore[arg-type]
    return resp, rec


def own_columns(body: Any, client_id: str, public: bool) -> dict[str, Any]:
    """POST /api/ai/own_columns。同期で返す（Haiku 1 回）。同じ入力の結果は上限に数えない。"""
    st = status(public)
    if not st["available"]:
        raise AiError(own_reason_text(st["reason"]), "unavailable", 403)
    cols = clean_own_columns(body)
    key = "own:" + cache_key({"columns": cols})
    hit = CACHE.get(key)
    if hit is not None:
        return dict(hit, cached=True)
    LIMITER.reserve(client_id)
    try:
        resp, rec = _call_own(make_client(), cols)
    except _Refused:
        raise AiError("AI が列の整理を断りました。列の役割は手で選んでください", "refused", 502) from None
    except Exception as e:  # noqa: BLE001  メッセージにキーが入ることはないが、型名だけを返す
        raise AiError(f"AI に列を整理してもらえませんでした（{type(e).__name__}）。手で選んでください", "api_error", 502) from None
    try:
        data = _json_of(resp)
    except Exception:  # noqa: BLE001
        raise AiError("AI の答えを読めませんでした。手で選んでください", "bad_output", 502) from None
    suggestions = validate_own_result(data, cols)
    res = {"ok": True, "suggestions": suggestions, "step": rec, "cached": False}
    if suggestions:
        CACHE.put(key, res)
    return res


# ---------------------------------------------------------------- 帰り便の案内文を読みやすく整える（SPEC §53.2）
# 送るのは、画面で確認を取った案内文の本文だけ。Haiku 1 回、構造化出力なし（本文をそのまま受ける）。
# 戻りの文は Python で機械照合する（【】・曜日・…周辺・数字・長さ）。通らなければ「確認が必要な案」として返す。再試行はしない。

POLISH_MODEL = MODEL_HAIKU
POLISH_MAX_TOKENS = 1500
POLISH_EFFORT = "low"
POLISH_MAX_INPUT_CHARS = 4000
SYS_POLISH = ("あなたは運送会社の配車担当です。帰り便の空きを荷主に知らせる最初の連絡の案内文を、自然で短い日本語のビジネスメールに書き直します。"
              "守ること: (1) 事実（曜日、場所の言い方「…周辺」、車両や荷物の言葉）は元の文のとおりに残す。"
              "(2) 【 】で囲んだ部分（宛先・社名・担当者名など）は、文字を変えず、すべてそのまま残す。"
              "(3) 数字は元の文にあるものだけを、元の書き方（「1 回」など）のまま使う。新しい数字・金額・約束・条件を足さない。"
              "(4) 荷主への質問は消さない。とくに、積み込みの設備（荷役の設備・方法）、日程・時間帯（広げられる範囲を含む）、受け取りの条件（混載の可否を含む）に関する質問は、すべて残す。"
              "似た質問は 1 つの文にまとめてよいが、聞く内容は減らさない。"
              "(5) 元の文より長くしない。出力は書き直した案内文の本文だけ（前置き・説明・見出しの記号を付けない）。")
_PH_RE = re.compile(r"【[^】]*】")
_WD_RE = re.compile(r"([月火水木金土日])曜|[（(]([月火水木金土日])[）)]")
_WD_EN_RE = re.compile(r"\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", re.I)
_PLACE_RE = re.compile(r"[一-龥々ヶァ-ヶー]+周辺|[ぁ-ん]+周辺")
_MONEY_RE = re.compile(r"\d[\d,.]*\s*円|万\s*円|[一二三四五六七八九十百千万]+\s*円")
_PLATE_LIKE_RE = re.compile(r"[一-龥々]{2,4}\s*\d{2,3}\s*[ぁ-んァ-ヶ]\s*[・.\-\d]{1,6}")


def clean_polish(body: Any) -> str:
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise AiError("整える文がありません")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    if len(text) > POLISH_MAX_INPUT_CHARS:
        raise AiError(f"整えられる文は {POLISH_MAX_INPUT_CHARS} 字までです", "too_large", 413)
    norm = unicodedata.normalize("NFKC", text)
    if PLATE_RE.search(norm) or _PLATE_LIKE_RE.search(norm):
        raise AiError("車番のような文字列が入っているので、整えられません", "plate", 400)
    if _MONEY_RE.search(norm):
        raise AiError("金額のような文字列が入っているので、整えられません", "money", 400)
    return text


def polish_problems(src: str, out: str) -> list[str]:
    """戻りの文の機械照合。問題の文（日本語）の一覧を返す。空なら通った。"""
    a, b = unicodedata.normalize("NFKC", src), unicodedata.normalize("NFKC", out)
    probs: list[str] = []
    if not out.strip():
        return ["AI の答えが空でした"]
    ph_in, ph_out = set(_PH_RE.findall(src)), set(_PH_RE.findall(out))
    for k in sorted(ph_in - ph_out):
        probs.append(f"{k} が消えています")
    for k in sorted(ph_out - ph_in):
        probs.append(f"元の文にない {k} が入っています")
    wd_in = {x or y for x, y in _WD_RE.findall(a)}
    wd_out = {x or y for x, y in _WD_RE.findall(b)}
    for k in sorted({w.lower()[:3] for w in _WD_EN_RE.findall(b)} - {w.lower()[:3] for w in _WD_EN_RE.findall(a)}):
        probs.append(f"元の文にない英語の曜日「{k}」が入っています")
    for k in sorted(wd_in - wd_out):
        probs.append(f"曜日「{k}曜」が消えています")
    for k in sorted(wd_out - wd_in):
        probs.append(f"元の文にない曜日「{k}曜」が入っています")
    pl_in, pl_out = set(_PLACE_RE.findall(a)), set(_PLACE_RE.findall(b))
    for k in sorted(pl_in - pl_out):
        probs.append(f"場所「{k}」が消えています")
    for k in sorted(pl_out - pl_in):
        probs.append(f"元の文にない場所「{k}」が入っています")
    seq_in = list(dict.fromkeys(_PLACE_RE.findall(a)))
    seq_out = list(dict.fromkeys(_PLACE_RE.findall(b)))
    if set(seq_in) == set(seq_out) and seq_in != seq_out:
        probs.append("場所の順番（…から…へ）が元の文と入れ替わっています")
    pairs_in = re.findall(r"(\S+?周辺)から(\S+?周辺)へ", a)
    for pa, pb in re.findall(r"(\S+?周辺)から(\S+?周辺)へ", b):
        if (pb, pa) in pairs_in:
            probs.append("場所の向き（…から…へ）が元の文と逆です")
    if b.count("周辺") > a.count("周辺"):
        probs.append("「周辺」の言い方が増えています")
    k_in = set(_KANJI_COUNTER_RE.findall(_JUBUN_OK_RE.sub("", a)))
    for m in sorted(set(_KANJI_COUNTER_RE.findall(_JUBUN_OK_RE.sub("", b))) - k_in):
        probs.append(f"元の文にない漢数字・割合の言い方「{m}」が入っています")
    for m in re.findall(r"[一二三四五六七八九十]+[百千万]|半額|倍|割", b):
        if m not in a:
            probs.append(f"元の文にない数量・割合の言い方「{m}」が入っています")
    for m in _URL_RE.findall(b):
        if m not in a:
            probs.append(f"元の文にない URL「{m}」が入っています")
    for m in _INSTR_RE.findall(b):
        if m not in a:
            probs.append(f"元の文にない依頼の言い回し「{m}」が入っています")
    n_in, n_out = _numbers(a), _numbers(b)
    for k in sorted(n_in - n_out, key=str):
        probs.append(f"数字「{k}」が消えています")
    for k in sorted(n_out - n_in, key=str):
        probs.append(f"元の文にない数字「{k}」が入っています")
    if re.search(r"[〇一二三四五六七八九十百千万]+\s*円", b) and not re.search(r"[〇一二三四五六七八九十百千万]+\s*円", a):
        probs.append("元の文にない金額が入っています")
    if len(out.strip()) > len(src.strip()):
        probs.append(f"元の文より長くなっています（{len(out.strip())} 字 / 元 {len(src.strip())} 字）")
    return probs


POLISH_KEEP_RATIO = 0.6
_BULLET_RE = re.compile(r"^\s*(?:[-*・●○■□◆◇▼▽▶▷・]|\d+[.)）]|[①-⑳])\s*")


def _norm_line(line: str) -> str:
    """箇条書きの記号・空白・句読点を除いて、同じ項目かどうかを比べる形にする。"""
    t = unicodedata.normalize("NFKC", _BULLET_RE.sub("", line))
    return re.sub(r"[\s、。，．,.！!？?:：;；・「」『』（）()]", "", t)


def _bullet_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if _BULLET_RE.match(ln) and _norm_line(ln)]


def polish_removed(src: str, out: str) -> list[str]:
    """元の文の箇条書きのうち、AI の案に残っていない行（記号を除いて比べる）。元の文に箇条書きが無ければ空。"""
    out_n = [_norm_line(x) for x in out.splitlines()]
    out_all = "".join(out_n)
    gone: list[str] = []
    for ln in _bullet_lines(src):
        n = _norm_line(ln)
        if n in out_all:
            continue
        grams = {n[i:i + 2] for i in range(len(n) - 1)}
        if grams and sum(g in out_all for g in grams) / len(grams) >= POLISH_KEEP_RATIO:   # 言い回しを変えても、2 文字の並びの 6 割が残っていれば残っているとみなす
            continue
        gone.append(_BULLET_RE.sub("", ln).strip())
    return gone


def _call_polish(client: Any, text: str) -> tuple[Any, dict[str, Any]]:
    kw: dict[str, Any] = dict(model=POLISH_MODEL, max_tokens=POLISH_MAX_TOKENS, system=SYS_POLISH,
                              messages=[{"role": "user", "content": text}], output_config={"effort": POLISH_EFFORT})
    t0 = time.perf_counter()
    resp = client.messages.create(**kw)
    ms = int((time.perf_counter() - t0) * 1000)
    tin, tout = _usage(resp)
    rec = {"step": "offer_polish", "model": POLISH_MODEL, "label": MODEL_LABEL[POLISH_MODEL], "input_tokens": tin, "output_tokens": tout, "ms": ms,
           "cost_usd": round(cost_usd(POLISH_MODEL, tin, tout), 6), "stop_reason": getattr(resp, "stop_reason", None)}
    if getattr(resp, "stop_reason", None) == "refusal":
        raise _Refused(rec)  # type: ignore[arg-type]
    return resp, rec


def offer_polish(body: Any, client_id: str, public: bool) -> dict[str, Any]:
    """POST /api/ai/offer_polish。同期で返す（Haiku 1 回）。同じ文の結果は上限に数えない。"""
    st = status(public)
    if not st["available"]:
        raise AiError(polish_reason_text(st["reason"]), "unavailable", 403)
    text = clean_polish(body)
    key = "polish:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    hit = CACHE.get(key)
    if hit is not None:
        return dict(hit, cached=True)
    LIMITER.reserve(client_id)
    try:
        resp, rec = _call_polish(make_client(), text)
    except _Refused:
        raise AiError("AI が文の整えを断りました。今の文のままお使いください", "refused", 502) from None
    except Exception as e:  # noqa: BLE001  メッセージにキーが入ることはないが、型名だけを返す
        raise AiError(f"AI に整えてもらえませんでした（{type(e).__name__}）。今の文のままお使いください", "api_error", 502) from None
    if getattr(resp, "stop_reason", None) == "max_tokens":
        raise AiError("AI の答えが途中で切れました。今の文のままお使いください", "truncated", 502)
    out = _text_of(resp).strip()
    problems = polish_problems(text, out)
    res = {"ok": True, "ready": not problems, "text": out, "problems": problems, "removed": polish_removed(text, out),
           "step": rec, "cached": False}
    if not problems:
        CACHE.put(key, res)
    return res



# ---------------------------------------------------------------- 荷物の条件を文で書いて表にしてもらう（SPEC §53.4）
# 送るのは、利用者が書いた文だけ（1,000 字まで）。Haiku 1 回、構造化出力。戻りの数字は Python で入力の文と照合する。
# 宅配の「120 サイズ」は 3 辺の合計の上限なので、縦・横・高さにしない（備考と「分からなかったこと」に書く）。

CARGO_MODEL = MODEL_HAIKU
CARGO_MAX_TOKENS = 1000
CARGO_EFFORT = "low"
CARGO_MAX_CHARS = 1000
CARGO_MAX_ROWS = 20
CARGO_KINDS = ("段ボール", "パレット", "その他")
CARGO_NOTE_MAX = 60
CARGO_PLACE_MAX = 40
CARGO_UNKNOWN_MAX = 60
CARGO_MAX_UNKNOWNS = 10
_NULL_NUM = {"anyOf": [{"type": "number"}, {"type": "null"}]}
CARGO_SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": list(CARGO_KINDS)}, "n": _NULL_NUM, "l_cm": _NULL_NUM, "w_cm": _NULL_NUM,
                           "h_cm": _NULL_NUM, "kg": _NULL_NUM, "note": {"type": "string"}},
            "required": ["kind", "n", "l_cm", "w_cm", "h_cm", "kg", "note"], "additionalProperties": False}},
        "origin": {"type": "string"}, "destination": {"type": "string"},
        "unknowns": {"type": "array", "items": {"type": "string"}}},
    "required": ["rows", "origin", "destination", "unknowns"], "additionalProperties": False}
SYS_CARGO = ("あなたは運送の見積依頼の入力係です。荷主が文で書いた荷物の条件を、荷物の表の行にします。"
             "守ること: (1) 文に書かれていない数字（個数・寸法・重さ）を作らない。分からない欄は null にする。"
             "(2) n は個数、l_cm・w_cm・h_cm は 1 個の縦・横・高さ（cm）、kg は 1 個の重さ（kg）。mm や m で書かれていれば cm に直す。"
             "重さが合計でしか書かれていないときは kg を null にして、note に書く。"
             "(3) 「120 サイズ」「80 サイズ」のような宅配のサイズは 3 辺の合計の上限で、縦・横・高さは分からない。l_cm・w_cm・h_cm は null にし、"
             "note に「120サイズ（3辺合計120cm以内）」の形で書き、unknowns にも縦・横・高さが分からないことを書く。"
             "(4) kind は 段ボール・パレット・その他 のどれか。(5) 発地・着地は文に書かれている地名の文字をそのまま使い、書かれていなければ空の文字列。"
             "(6) unknowns は、見積に必要で文に書かれていなかったことだけを（例: 寸法、重さ、個数、日付）1 件 40 字以内の短い日本語で並べる。"
             "文に書かれていて分かっていること・確定していることは unknowns に入れない。分からないことが無ければ空の配列にする。note は 40 字以内。")

_NUM = r"\d+(?:\.\d+)?"
_CARGO_CHAIN_RE = re.compile(rf"{_NUM}\s*(?:mm|cm|m(?![a-z]))?(?:\s*[x*×]\s*{_NUM}\s*(?:mm|cm|m(?![a-z]))?){{1,2}}")
_CARGO_NUM_RE = re.compile(rf"({_NUM})\s*(mm|cm|kg|g(?![a-z])|m(?![a-z])|t(?![a-z])|トン|サイズ)?")
_KNOWN_RE = re.compile(r"確定|決まっ|判明|分かっ(?:て|た)|わかっ(?:て|た)|明記|記載あり|書かれて(?:い)?る")   # 「分かっていること」を「分からなかったこと」に入れない
_UNSURE_RE = re.compile(r"未|ない|ません|不明|不確|分から|わから|不足|なし|無し|ず$")
_CARGO_SIZE_RE = re.compile(r"(\d+)\s*サイズ")


def clean_cargo(body: Any) -> str:
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise AiError("表にする文がありません")
    if set(body) - {"text"}:
        raise AiError("送れない項目が含まれています", "bad_field", 400)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    if len(text) > CARGO_MAX_CHARS:
        raise AiError(f"表にできる文は {CARGO_MAX_CHARS} 字までです", "too_large", 413)
    return text


def _fnum(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x)


def cargo_allowed(text: str) -> dict[str, set[float]]:
    """入力の文から、欄ごとに出してよい数を作る。単位の換算（mm・m → cm、t・g → kg）はここで計算する。
    「120 サイズ」の数は宅配の区分なので、どの欄にも入れない。"""
    t = unicodedata.normalize("NFKC", text).lower().replace(",", "")
    t = re.sub(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}\s*/\s*\d{1,2}|\d{1,2}\s*月\s*\d{1,2}\s*日", " ", t)   # 日付の数は、どの欄にも入れない
    dims: set[float] = set()
    kg: set[float] = set()
    cnt: set[float] = set()
    used: list[tuple[int, int]] = []

    def dim_val(v: float, u: str | None) -> float:
        return v / 10 if u == "mm" else v * 100 if u == "m" else v

    for m in _CARGO_CHAIN_RE.finditer(t):   # 110×110×150cm のように並んだ寸法は、最後の単位をすべてに当てる
        seg = m.group(0)
        units = re.findall(r"(mm|cm|m)(?![a-z])", seg)
        u = units[-1] if units else None
        for n, nu in re.findall(rf"({_NUM})\s*(mm|cm|m(?![a-z]))?", seg):   # 自分の単位があればそれ、なければ最後の単位
            dims.add(round(dim_val(float(n), nu or u), 4))
        used.append(m.span())
    for m in _CARGO_NUM_RE.finditer(t):
        v, u = float(m.group(1)), m.group(2)
        if u == "サイズ":
            continue
        if any(a <= m.start() < b for a, b in used):
            continue
        if not u:
            cnt.add(round(v, 4))   # 単位なしの数は個数だけ（寸法と重さには入れない。寸法の並びの中の数は除く）
            continue
        if u in ("mm", "cm", "m"):
            dims.add(round(dim_val(v, u), 4))
        elif u == "kg":
            kg.add(round(v, 4))
        elif u == "g":
            kg.add(round(v / 1000, 4))
        elif u in ("t", "トン"):
            kg.add(round(v * 1000, 4))
    return {"dims": dims, "kg": kg, "n": cnt}


def cargo_validate(text: str, data: Any) -> tuple[list[dict[str, Any]], str, str, list[str], list[str]]:
    """モデルの答えを検める。入力の文にない数字の行は外し、理由を返す。(行, 発地, 着地, 分からなかったこと, 外した理由)"""
    allowed = cargo_allowed(text)
    norm = unicodedata.normalize("NFKC", text)
    sizes = list(dict.fromkeys(_CARGO_SIZE_RE.findall(norm)))   # 宅配のサイズは、縦・横・高さにしない
    note_ok = allowed["dims"] | allowed["kg"] | allowed["n"] | {float(s) for s in sizes} | {3.0}
    rows_in = data.get("rows") if isinstance(data, dict) else None
    rows: list[dict[str, Any]] = []
    dropped: list[str] = []
    for i, r in enumerate(rows_in if isinstance(rows_in, list) else []):
        if not isinstance(r, dict):
            continue
        if len(rows) >= CARGO_MAX_ROWS:
            dropped.append(f"行が多いので {CARGO_MAX_ROWS} 行までにしました")
            break
        kind = r.get("kind") if r.get("kind") in CARGO_KINDS else "その他"
        bad: list[str] = []
        out: dict[str, Any] = {"kind": kind}
        for key, fld, label, hi in (("n", "n", "個数", 9999), ("l_cm", "dims", "縦", 5000), ("w_cm", "dims", "横", 5000),
                                    ("h_cm", "dims", "高さ", 5000), ("kg", "kg", "重さ", 100000)):
            raw = r.get(key)
            if raw is None:
                out[key] = None
                continue
            v = _fnum(raw)
            if v is None or v <= 0 or v > hi:
                bad.append(f"{label}が正しい数ではありません")
                continue
            if key == "n" and v != int(v):
                bad.append("個数が整数ではありません")
                continue
            if round(v, 4) not in allowed[fld]:
                shown = int(v) if v == int(v) else v
                bad.append(f"{label}の {shown} が、文にある数（単位の換算を含む）と合いません")
                continue
            out[key] = int(v) if v == int(v) else round(v, 2)
        note = str(r.get("note", "")).strip()[:CARGO_NOTE_MAX]
        n_note = {round(float(x), 4) for x in re.findall(_NUM, unicodedata.normalize("NFKC", note).replace(",", ""))}
        if _URL_RE.search(note):
            note = ""
            dropped.append(f"{i + 1} 行目の備考に URL があったので消しました")
        if n_note - note_ok:
            note = ""
            dropped.append(f"{i + 1} 行目の備考に、文にない数字があったので消しました")
        out["note"] = note
        if bad:
            dropped.append(f"{i + 1} 行目（{kind}）を外しました: " + "、".join(bad))
            continue
        if all(out.get(k) is None for k in ("n", "l_cm", "w_cm", "h_cm", "kg")):
            dropped.append(f"{i + 1} 行目（{kind}）は数字が 1 つもないので外しました")
            continue
        rows.append(out)

    for row in rows:
        for s in sizes:
            phrase = f"{s}サイズ（3辺合計{s}cm以内）"
            if re.search(rf"{s}\s*サイズ", row["note"]) and phrase not in row["note"]:
                row["note"] = re.sub(rf"{s}\s*サイズ(（[^）]*）)?", phrase, row["note"])[:CARGO_NOTE_MAX]
    unknowns: list[str] = []
    raw_unk = data.get("unknowns") if isinstance(data, dict) else None
    for u in raw_unk if isinstance(raw_unk, list) else []:
        if isinstance(u, str) and u.strip():
            if _URL_RE.search(u) or _INSTR_RE.search(u) or (_KNOWN_RE.search(u) and not _UNSURE_RE.search(u)):
                continue
            unknowns.append(u.strip()[:CARGO_UNKNOWN_MAX])
    for s in sizes:
        unknowns.append(f"{s}サイズは3辺合計{s}cm以内の区分で、縦・横・高さは分かりません")
    unknowns = list(dict.fromkeys(unknowns))[:CARGO_MAX_UNKNOWNS]
    places: list[str] = []
    for key, label in (("origin", "発地"), ("destination", "着地")):
        v = data.get(key) if isinstance(data, dict) else ""
        v = v.strip()[:CARGO_PLACE_MAX] if isinstance(v, str) else ""
        if v and (len(v) < 2 or re.fullmatch(r"[ぁ-ん]{1,3}", v)):
            dropped.append(f"{label}「{v}」は短すぎるので入れませんでした")
            v = ""
        if v and v not in norm and v not in text:
            dropped.append(f"{label}「{v}」は文にないので入れませんでした")
            v = ""
        places.append(v)
    if places[0] and places[1]:
        io, idd = norm.find(places[0]), norm.find(places[1])
        if io >= 0 and idd >= 0 and io > idd:
            dropped.append("発地と着地の順番が文と逆なので、どちらも入れませんでした")
            places = ["", ""]
    return rows, places[0], places[1], unknowns, dropped


def _call_cargo(client: Any, text: str) -> tuple[Any, dict[str, Any]]:
    kw: dict[str, Any] = dict(model=CARGO_MODEL, max_tokens=CARGO_MAX_TOKENS, system=SYS_CARGO,
                              messages=[{"role": "user", "content": text}],
                              output_config={"effort": CARGO_EFFORT, "format": {"type": "json_schema", "schema": CARGO_SCHEMA}})
    t0 = time.perf_counter()
    resp = client.messages.create(**kw)
    ms = int((time.perf_counter() - t0) * 1000)
    tin, tout = _usage(resp)
    rec = {"step": "cargo_rows", "model": CARGO_MODEL, "label": MODEL_LABEL[CARGO_MODEL], "input_tokens": tin, "output_tokens": tout, "ms": ms,
           "cost_usd": round(cost_usd(CARGO_MODEL, tin, tout), 6), "stop_reason": getattr(resp, "stop_reason", None)}
    if getattr(resp, "stop_reason", None) == "refusal":
        raise _Refused(rec)  # type: ignore[arg-type]
    return resp, rec


def cargo_rows(body: Any, client_id: str, public: bool) -> dict[str, Any]:
    """POST /api/ai/cargo_rows。同期で返す（Haiku 1 回）。同じ文の結果は上限に数えない。"""
    st = status(public)
    if not st["available"]:
        raise AiError(cargo_reason_text(st["reason"]), "unavailable", 403)
    text = clean_cargo(body)
    key = "cargo:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    hit = CACHE.get(key)
    if hit is not None:
        return dict(hit, cached=True)
    LIMITER.reserve(client_id)
    try:
        resp, rec = _call_cargo(make_client(), text)
    except _Refused:
        raise AiError("AI が表にするのを断りました。荷物の表は手で入れてください", "refused", 502) from None
    except Exception as e:  # noqa: BLE001  メッセージにキーが入ることはないが、型名だけを返す
        raise AiError(f"AI に表にしてもらえませんでした（{type(e).__name__}）。荷物の表は手で入れてください", "api_error", 502) from None
    try:
        data = _json_of(resp)   # max_tokens で切れたときもここで失敗にする
    except Exception:  # noqa: BLE001
        raise AiError("AI の答えを読めませんでした（途中で切れたか、形が違いました）。荷物の表は手で入れてください", "bad_output", 502) from None
    rows, origin, dest, unknowns, dropped = cargo_validate(text, data)
    res = {"ok": True, "rows": rows, "origin": origin, "destination": dest, "unknowns": unknowns, "dropped": dropped, "step": rec, "cached": False}
    CACHE.put(key, res)
    return res


def cargo_reason_text(reason: str) -> str:
    return {"disabled": "AI による荷物の表づくりは、このサーバーでは使えない設定です",
            "public_off": "公開版では AI による荷物の表づくりを止めています",
            "no_key": "AI による荷物の表づくりに必要な設定がありません",
            "no_sdk": "AI による荷物の表づくりに必要な部品（anthropic）が入っていません"}.get(reason, "AI による荷物の表づくりは今は使えません")


def polish_reason_text(reason: str) -> str:
    return {"disabled": "AI による文の整えは、このサーバーでは使えない設定です",
            "public_off": "公開版では AI による文の整えを止めています",
            "no_key": "AI による文の整えに必要な設定がありません",
            "no_sdk": "AI による文の整えに必要な部品（anthropic）が入っていません"}.get(reason, "AI による文の整えは今は使えません")


def own_reason_text(reason: str) -> str:
    return {"disabled": "AI による列の整理は、このサーバーでは使えない設定です",
            "public_off": "公開版では AI による列の整理を止めています",
            "no_key": "AI による列の整理に必要な設定がありません",
            "no_sdk": "AI による列の整理に必要な部品（anthropic）が入っていません"}.get(reason, "AI による列の整理は今は使えません")


def reason_text(reason: str, public: bool = False) -> str:
    if reason == "public_off" or (reason == "disabled" and public):
        return "この公開版では止めています（費用の上限のため）"
    return {"disabled": "このサーバーでは使えない設定です（AI_EXPLAIN_ENABLED を 1 にすると使えます）",
            "no_key": "API キーが設定されていません（サーバーの環境変数 ANTHROPIC_API_KEY）",
            "no_sdk": "必要な部品（anthropic）が入っていません"}.get(reason, "今は使えません")
