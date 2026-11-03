"""荷物・条件の版・見積回答・判断の型。"""

from __future__ import annotations

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


def to_decimal(value: Any) -> Decimal | None:
    """数値を Decimal にする。None・真偽値・数値以外は None。float は文字列経由で変換する。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return None


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


@dataclass
class ConditionVersion:
    """確定した条件の版。見積回答は number に結び付く。"""

    number: int
    shipment: Shipment
    confirmed_at: str | None = None


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


@dataclass
class Case:
    """1件の出荷の保存単位（§8）。"""

    versions: list[ConditionVersion] = field(default_factory=list)
    quotes: list[QuoteResponse] = field(default_factory=list)
    chosen_quote_id: str | None = None
    reason: str = ""
    open_issues: list[str] = field(default_factory=list)
    saved_at: str | None = None
    schema_version: int = 1

    @property
    def current_version(self) -> int | None:
        return self.versions[-1].number if self.versions else None

    def confirm(self, shipment: Shipment, confirmed_at: str | None = None) -> ConditionVersion:
        """「この条件で確定」。最新の版と内容が同じなら版を増やさない。"""
        if self.versions and self.versions[-1].shipment == shipment:
            return self.versions[-1]
        number = (self.versions[-1].number + 1) if self.versions else 1
        version = ConditionVersion(number=number, shipment=shipment, confirmed_at=confirmed_at)
        self.versions.append(version)
        return version
