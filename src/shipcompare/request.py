"""見積依頼文と不足項目（SPEC §7）。送信はしない。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .estimate import Totals, summarize
from .models import ROUTE_OTHER, Shipment, to_decimal
from .tariffs import ROUTES

STATUS_DRAFT = "下書き"
STATUS_READY = "依頼準備完了"

CONFIRM_ITEMS = [
    "この荷を受託できるか",
    "集荷・配達・荷役・燃料・高速の費用が含まれるか、別に加算されるか",
    "税区分（税抜か税込か）",
    "見積の有効期限",
    "積付けの条件（ボックスや車両に積めるか）",
]


@dataclass
class RequestDraft:
    status: str
    missing_required: list[str]
    missing_optional: list[str]
    text: str


def _fmt(d) -> str:
    return f"{d.normalize():f}" if d is not None else "不明"


def _yn(v: bool | None, yes: str, no: str) -> str:
    return "未入力" if v is None else (yes if v else no)


def missing_items(shipment: Shipment, totals: Totals | None = None) -> tuple[list[str], list[str]]:
    """(必須の不足, 任意の不足)。"""
    totals = totals or summarize(shipment)
    required: list[str] = list(totals.problems)
    route = ROUTES.get(shipment.route or "")
    if route is None:
        required.append("発地域・着地域")
    elif shipment.route == ROUTE_OTHER:
        d = to_decimal(shipment.distance_km)
        if d is None or d <= 0:
            required.append("距離 km")
    dest = shipment.destinations
    if dest is None or isinstance(dest, bool) or dest < 1:
        required.append("届け先の数")
    if not shipment.sender_type:
        required.append("発送者の種別")
    if not shipment.receiver_type:
        required.append("受取人の種別")
    if not shipment.pickup:
        required.append("集荷か持込か")
    if not shipment.delivery:
        required.append("配達か引取か")
    if shipment.ship_date is None:
        required.append("出荷日")
    if shipment.desired_date is None:
        required.append("希望着日")

    optional: list[str] = []
    if shipment.forklift is None:
        optional.append("フォークリフトの有無")
    if shipment.tail_lift is None:
        optional.append("ゲート車の要否")
    if shipment.manual_load is None:
        optional.append("手積みの可否")
    if not shipment.cargo_name:
        optional.append("品目")
    if not shipment.fragile_note:
        optional.append("壊れ物の扱い")
    if shipment.monthly_shipments is None:
        optional.append("月の出荷回数")
    return required, optional


def build_request(shipment: Shipment, scenario: dict | None = None) -> RequestDraft:
    totals = summarize(shipment)
    required, optional = missing_items(shipment, totals)
    status = STATUS_READY if not required else STATUS_DRAFT

    lines: list[str] = []
    lines.append(f"【見積依頼（{status}）】")
    lines.append("")
    lines.append("■ 荷物")
    for i, it in enumerate(shipment.items, start=1):
        lines.append(
            f"  {i}. 個数 {it.count if it.count is not None else '不明'} / "
            f"縦 {_fmt(to_decimal(it.length_cm))} × 横 {_fmt(to_decimal(it.width_cm))} × "
            f"高さ {_fmt(to_decimal(it.height_cm))} cm / 1個 {_fmt(to_decimal(it.weight_kg))} kg"
        )
    lines.append(f"  合計重量: {_fmt(totals.weight_kg)} kg")
    vol = totals.volume_m3.quantize(Decimal("0.001")) if totals.volume_m3 is not None else None
    lines.append(f"  合計容積: {_fmt(vol)} m³")
    lines.append(f"  計算重量: {_fmt(totals.chargeable_kg)} kg（容積 × 280kg/m³ と重量の大きい方）")
    lines.append("")
    lines.append("■ 区間・日程")
    route = ROUTES.get(shipment.route or "")
    if route is None:
        lines.append("  発着: 未入力")
    elif shipment.route == ROUTE_OTHER:
        lines.append(f"  発着: その他（距離 {_fmt(to_decimal(shipment.distance_km))} km）")
    else:
        lines.append(f"  発着: {route.label}（想定の道路距離 {route.km:,}km）")
    lines.append(f"  出荷日: {shipment.ship_date.isoformat() if shipment.ship_date else '未入力'}")
    lines.append(f"  希望着日: {shipment.desired_date.isoformat() if shipment.desired_date else '未入力'}")
    lines.append(f"  集荷/持込: {shipment.pickup or '未入力'}")
    lines.append(f"  配達/引取: {shipment.delivery or '未入力'}")
    lines.append(f"  届け先の数: {shipment.destinations if shipment.destinations is not None else '未入力'}")
    if shipment.monthly_shipments is not None:
        lines.append(f"  月の出荷回数の見込み: {shipment.monthly_shipments} 回")
    lines.append(f"  発送者: {shipment.sender_type or '未入力'} / 受取人: {shipment.receiver_type or '未入力'}")
    lines.append("")
    lines.append("■ 荷役設備・品目")
    lines.append(f"  フォークリフト: {_yn(shipment.forklift, 'あり', 'なし')}")
    lines.append(f"  ゲート車: {_yn(shipment.tail_lift, '必要', '不要')}")
    lines.append(f"  手積み: {_yn(shipment.manual_load, '可', '不可')}")
    lines.append(f"  品目: {shipment.cargo_name or '未入力'}")
    lines.append(f"  壊れ物: {shipment.fragile_note or '未入力'}")
    lines.append("")
    lines.append("■ 運送会社に確かめること")
    for c in CONFIRM_ITEMS:
        lines.append(f"  - {c}")
    if required or optional:
        lines.append("")
        lines.append("■ 荷主側で未入力の項目")
        for m in required:
            lines.append(f"  - {m}（必須）")
        for m in optional:
            lines.append(f"  - {m}")
    lines.append("")
    lines.append("※ 目安額は公表運賃からの計算で、最終の運賃ではありません。")
    if scenario and scenario.get("name"):
        lines.append(f"※ この条件は{scenario['name']}（{scenario.get('label') or '想定の荷主'}）の例です。")
    return RequestDraft(status, required, optional, "\n".join(lines) + "\n")
