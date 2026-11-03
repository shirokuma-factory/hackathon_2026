"""見積回答の比較（SPEC §6）。自動で値引きや補正はしない。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from .estimate import TAX_RATE, round_yen
from .models import (
    COST_KEYS,
    KIND_AMOUNT,
    KIND_INCLUDED,
    TAX_EX,
    TAX_IN,
    QuoteResponse,
)

STATE_COMPLETE = "complete"
STATE_INCOMPLETE = "incomplete"
STATE_HISTORY = "history"  # 条件変更前


@dataclass
class QuoteEvaluation:
    quote: QuoteResponse
    state: str
    total_ex_tax: int | None = None  # 完全な回答だけ
    partial_ex_tax: int = 0  # 金額が入っている項目の合計（税抜）
    unknown_keys: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # 不完全・履歴の理由
    rank: int | None = None
    diff_from_lowest: int | None = None

    @property
    def summary(self) -> str:
        if self.state == STATE_COMPLETE:
            return f"合計 {self.total_ex_tax:,}円（税抜）"
        text = f"部分合計 {self.partial_ex_tax:,}円（税抜）"
        if self.unknown_keys:
            text += " + 不明: " + "・".join(COST_KEYS[k] for k in self.unknown_keys)
        if self.reasons:
            text += "（" + "、".join(self.reasons) + "）"
        return text


@dataclass
class Comparison:
    evaluations: list[QuoteEvaluation]
    ranking_shown: bool
    message: str

    def by_state(self, state: str) -> list[QuoteEvaluation]:
        return [e for e in self.evaluations if e.state == state]


def to_ex_tax(raw_total: int, tax: str) -> int:
    """税込の合計は 1.1 で割って円未満四捨五入。税抜はそのまま。"""
    if tax == TAX_IN:
        return round_yen(Decimal(raw_total) / TAX_RATE)
    return raw_total


def _counted_amount_lines(quote: QuoteResponse) -> dict[str, int]:
    """evaluate_quote が合計に足す項目（金額が正の「金額」行）だけを集める。"""
    out: dict[str, int] = {}
    for key in COST_KEYS:
        line = quote.lines.get(key)
        if line is None or line.kind != KIND_AMOUNT:
            continue
        if line.amount is None or isinstance(line.amount, bool) or line.amount <= 0:
            continue
        out[key] = line.amount
    return out


def line_amounts_ex_tax(quote: QuoteResponse) -> dict[str, int]:
    """内訳の「金額」行を税抜の円に直して返す（SPEC §13.3）。

    「含む」「不明」の行は数値ではないので返さない（呼び出し側が quote.lines の kind で判断する）。
    税込の回答は各行を 1.1 で割り、端数は最大剰余法で配って、
    戻り値の合計が evaluate_quote の合計（税抜）と必ず一致するようにする。
    """
    raw = _counted_amount_lines(quote)
    if quote.tax != TAX_IN:
        return raw
    target = to_ex_tax(sum(raw.values()), TAX_IN)
    exact = {k: Decimal(v) / TAX_RATE for k, v in raw.items()}
    floors = {k: int(x.to_integral_value(rounding=ROUND_FLOOR)) for k, x in exact.items()}
    remaining = target - sum(floors.values())
    # 小数部が大きい順（同じなら内訳の並び順）に1円ずつ配る
    order = sorted(raw, key=lambda k: (-(exact[k] - floors[k]), list(COST_KEYS).index(k)))
    i = 0
    while remaining > 0 and order:
        floors[order[i % len(order)]] += 1
        remaining -= 1
        i += 1
    return floors


def evaluate_quote(quote: QuoteResponse, current_version: int, today: date) -> QuoteEvaluation:
    unknown: list[str] = []
    reasons: list[str] = []
    raw = 0
    amount_lines = 0
    for key in COST_KEYS:
        line = quote.lines.get(key)
        if line is None or line.kind not in (KIND_AMOUNT, KIND_INCLUDED):
            unknown.append(key)
        elif line.kind == KIND_AMOUNT:
            if line.amount is None or isinstance(line.amount, bool) or line.amount < 0:
                reasons.append(f"{COST_KEYS[key]}の金額が不正")
                unknown.append(key)
            elif line.amount == 0:
                # 0円と不明を混同しない。無料なら「含む」を選ぶ
                reasons.append(f"{COST_KEYS[key]}が0円（含むなら「含む」を選んでください）")
                unknown.append(key)
            else:
                raw += line.amount
                amount_lines += 1

    if quote.tax not in (TAX_EX, TAX_IN):
        reasons.append("税区分が不明")
        partial = raw
    else:
        partial = to_ex_tax(raw, quote.tax)

    if quote.version != current_version:
        return QuoteEvaluation(
            quote, STATE_HISTORY, partial_ex_tax=partial, unknown_keys=unknown,
            reasons=[f"条件変更前（版{quote.version}、現在は版{current_version}）"],
        )

    if amount_lines == 0:
        reasons.append("金額の項目がありません")

    if quote.valid_until is None:
        reasons.append("有効期限が未入力")
    elif quote.valid_until < today:
        reasons.append("有効期限切れ")

    if unknown or reasons:
        return QuoteEvaluation(
            quote, STATE_INCOMPLETE, partial_ex_tax=partial, unknown_keys=unknown, reasons=reasons
        )
    return QuoteEvaluation(quote, STATE_COMPLETE, total_ex_tax=partial, partial_ex_tax=partial)


def compare_quotes(quotes: list[QuoteResponse], current_version: int, today: date) -> Comparison:
    evals = [evaluate_quote(q, current_version, today) for q in quotes]
    complete = sorted((e for e in evals if e.state == STATE_COMPLETE), key=lambda e: e.total_ex_tax)
    incomplete = [e for e in evals if e.state == STATE_INCOMPLETE]
    history = [e for e in evals if e.state == STATE_HISTORY]

    shown = len(complete) >= 2
    if shown:
        lowest = complete[0].total_ex_tax
        for i, e in enumerate(complete):
            # 同額は同順位
            e.rank = 1 if i == 0 else (complete[i - 1].rank if e.total_ex_tax == complete[i - 1].total_ex_tax else i + 1)
            e.diff_from_lowest = e.total_ex_tax - lowest
        message = f"完全な回答{len(complete)}件を比べました（税抜）"
    elif len(complete) == 1:
        message = "比べる相手がいません（完全な回答が1件だけです）"
    else:
        message = "完全な回答がまだありません"
    return Comparison(complete + incomplete + history, shown, message)
