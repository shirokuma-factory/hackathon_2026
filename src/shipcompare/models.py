"""荷物・条件の版・見積回答・判断の型。"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

# 区間 ID（§5.1）。other のときは distance_km を使う
ROUTE_OTHER = "other"

# 見積回答の費用内訳の項目（§6）。キーと表示名
COST_KEYS: dict[str, str] = {
    "pickup": "集荷",
    "trunk": "幹線",
    "delivery": "配達",
    "handling": "荷役",
    "fuel": "燃料",
    "highway": "高速",
    "other": "その他",
}

KIND_AMOUNT = "amount"
KIND_INCLUDED = "included"
KIND_UNKNOWN = "unknown"

TAX_EX = "税抜"
TAX_IN = "税込"


MAX_NUMBER = Decimal(10) ** 15  # これ以上の絶対値は数として受けない（1e400 や 400 桁の数を、計算にも文にも通さない）


def to_decimal(value: Any) -> Decimal | None:
    """数値を Decimal にする。None・真偽値・数値以外は None。float は文字列経由で変換する。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() and abs(value) < MAX_NUMBER else None
    if isinstance(value, int) and value.bit_length() > 60:
        return None  # 桁数の多い整数は文字列にせず断る（Python の整数→文字列の桁数の上限にも当たらないように）
    if isinstance(value, (int, float)):
        d = Decimal(str(value))
        return d if d.is_finite() and abs(d) < MAX_NUMBER else None  # Infinity・NaN・巨大な数は数として受けない（r1b #11、r3b P1-3）
    return None


def parse_number_text(value: Any) -> Decimal | None:
    """数か、数の文字列（全角の数字・空白も NFKC で半角にして読む）を有限の Decimal にする。読めなければ None（r1a F11）。"""
    if isinstance(value, str):
        t = unicodedata.normalize("NFKC", value).strip()
        if len(t) > 40 or "e" in t.lower():
            return None  # 「1e400」のような指数の書き方と、桁の多すぎる文字列は数として受けない
        try:
            d = Decimal(t)
        except ArithmeticError:
            return None
        return d if d.is_finite() and abs(d) < MAX_NUMBER else None
    return to_decimal(value)


@dataclass
class Item:
    """荷物の1行。None は不明（未入力）。"""

    count: int | None = None
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    weight_kg: float | None = None  # 1個の重さ

    def problems(self, index: int) -> tuple[list[str], list[str]]:
        """(未入力の項目, 0以下などの不正な項目) を返す。index は 1 始まり。"""
        missing: list[str] = []
        invalid: list[str] = []
        fields = [
            ("個数", self.count),
            ("縦", self.length_cm),
            ("横", self.width_cm),
            ("高さ", self.height_cm),
            ("1個の重さ", self.weight_kg),
        ]
        for name, value in fields:
            label = f"荷物{index}行目の{name}"
            if value is None:
                missing.append(label)
                continue
            d = to_decimal(value)
            if d is None or d <= 0:
                invalid.append(label)
            elif name == "個数" and (isinstance(value, float) or d != d.to_integral_value()):
                invalid.append(label)
        return missing, invalid


@dataclass
class Shipment:
    """出荷条件（§4）。"""

    items: list[Item] = field(default_factory=list)
    route: str | None = None
    distance_km: float | None = None  # route が other のときの距離
    origin_in_kanto: bool | None = None  # route が other のときだけ使う（貸切の範囲判定）
    destinations: int | None = None
    sender_type: str | None = None  # 法人 / 個人事業主 / 個人
    receiver_type: str | None = None
    pickup: str | None = None  # 集荷 / 持込
    delivery: str | None = None  # 配達 / 引取
    ship_date: date | None = None
    desired_date: date | None = None
    forklift: bool | None = None
    tail_lift: bool | None = None
    manual_load: bool | None = None
    cargo_name: str | None = None
    fragile_note: str | None = None
    monthly_shipments: int | None = None  # 月の出荷回数（任意、目安額の計算には使わない）


@dataclass
class ConditionVersion:
    """確定した条件の版。見積回答は number に結び付く。"""

    number: int
    shipment: Shipment
    confirmed_at: str | None = None
    # 確定したときの場面の情報 {id, name, label, modified}。場面を使わなければ None
    scenario: dict[str, str | bool | None] | None = None


@dataclass
class CostLine:
    """費用内訳の1項目。kind は amount / included / unknown。"""

    kind: str = KIND_UNKNOWN
    amount: int | None = None


@dataclass
class QuoteResponse:
    """見積回答（§6）。lines に無い項目は「不明」として扱う。"""

    id: str
    carrier: str
    product: str
    version: int
    received_on: date | None = None
    valid_until: date | None = None
    tax: str = TAX_EX
    lines: dict[str, CostLine] = field(default_factory=dict)
    is_demo: bool = False  # 場面の架空の回答例


@dataclass
class Case:
    """1件の出荷の保存単位（§8）。"""

    versions: list[ConditionVersion] = field(default_factory=list)
    quotes: list[QuoteResponse] = field(default_factory=list)
    chosen_quote_id: str | None = None
    reason: str = ""
    open_issues: list[str] = field(default_factory=list)
    saved_at: str | None = None
    schema_version: int = 2

    @property
    def scenario(self) -> dict[str, str | bool | None] | None:
        """いまの版に記録した場面の情報。"""
        return self.versions[-1].scenario if self.versions else None

    @property
    def current_version(self) -> int | None:
        return self.versions[-1].number if self.versions else None

    def confirm(self, shipment: Shipment, confirmed_at: str | None = None,
                scenario: dict[str, str | bool | None] | None = None) -> ConditionVersion:
        """「この条件で確定」。最新の版と内容が同じなら版を増やさない。"""
        if self.versions and self.versions[-1].shipment == shipment:
            return self.versions[-1]  # 内容が同じなら版も場面の情報も変えない
        number = (self.versions[-1].number + 1) if self.versions else 1
        version = ConditionVersion(number=number, shipment=shipment, confirmed_at=confirmed_at, scenario=scenario)
        self.versions.append(version)
        return version
