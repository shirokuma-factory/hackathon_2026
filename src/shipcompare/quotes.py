"""見積回答の比較（SPEC §6）。自動で値引きや補正はしない。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

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


def evaluate_quote(quote: QuoteResponse, current_version: int, today: date) -> QuoteEvaluation:
    unknown: list[str] = []
    reasons: list[str] = []
    raw = 0
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
