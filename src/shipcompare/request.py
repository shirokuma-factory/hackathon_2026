"""見積依頼文と不足項目（SPEC §7）。送信はしない。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .estimate import Totals, summarize
from .models import ROUTE_OTHER, Shipment, to_decimal
from .tariffs import ROUTES

STATUS_DRAFT = "下書き"
STATUS_READY = "依頼準備完了"

# 日本語の直し 2026-11-08（C03・C12）: 依頼文の中で運送会社に答えてもらう項目。相手あての言い方にする
CONFIRM_ITEMS = [
    "この荷物をお引き受けいただけるか",
    "集荷・配達・荷役・燃料・高速の費用が運賃に含まれるか、別にかかるか",
    "税抜か税込か",
    "お見積りの有効期限",
    "車両に積めるか（積付けの条件）",
]


@dataclass
class RequestDraft:
    status: str
    missing_required: list[str]
    missing_optional: list[str]
    text: str


def _fmt(d) -> str:
    return f"{d.normalize():f}" if d is not None else "未入力"


def _with_unit(d, unit: str) -> str:
    """値があるときだけ単位を付ける（「不明 kg」と並べない）。"""
    return f"{_fmt(d)} {unit}" if d is not None else "未入力"


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
    if shipment.ship_date is not None and shipment.desired_date is not None and shipment.desired_date < shipment.ship_date:
        required.append("希望着日が出荷日より前です。日付を直してください。")

    optional: list[str] = []
    if shipment.forklift is None:
        optional.append("フォークリフトの有無")
    if shipment.tail_lift is None:
        optional.append("パワーゲート車の要否")
    if shipment.manual_load is None:
        optional.append("手積みの可否")
    if not shipment.cargo_name:
        optional.append("品目")
    if not shipment.fragile_note:
        optional.append("壊れ物の扱い")
    if shipment.monthly_shipments is None:
        optional.append("月の出荷回数")
    return required, optional


# 見積依頼文に書く送り方（画面の選択の値）。鉄道・船・飛行機は公表の運賃表を持たないので、額の目安は書かない
MODE_REQUEST_TEXT = {
    "宅配": "宅配",
    "路線便": "路線便",
    "カゴ台車": "カゴ台車 1 本単位",
    "貸切": "貸切",
    "鉄道": "鉄道（コンテナ）。日数と運賃をお知らせください",
    "船": "船（フェリー）。日数と運賃をお知らせください",
    "飛行機": "飛行機（航空便）。日数と運賃をお知らせください",
}


def build_request(shipment: Shipment, scenario: dict | None = None, place: dict | None = None,
                  mode: str | None = None, packaging: str | None = None, today: date | None = None) -> RequestDraft:
    """place は地図で選んだ発地・着地（label は「新宿区 → 大阪市中央区」、km は道路距離、note は距離の注記）。
    あれば「発着」の行に区間の想定の値ではなく、この市区町村と道路距離を書く（SPEC §31.5 #16）。"""
    totals = summarize(shipment)
    required, optional = missing_items(shipment, totals)
    if place and place.get("label") and mode in ("鉄道", "船", "飛行機"):
        required = [x for x in required if x != "距離 km"]   # 道路の距離では測れない送り方。届け先の名前があれば足りる
    # 出荷日が今日より前（打ち間違い）は完了にしない。today は呼び出し側の時計（デモの日付。省略すると確かめない）
    if today is not None and shipment.ship_date is not None and shipment.ship_date < today:
        required.append(f"出荷日が今日（{today.isoformat()}）より前です。日付を直してください。")
    # パレット渡しの荷は、フォークリフトの有無を必須にする（手積みの荷と読まれないように）
    pallet = packaging == "pallet"
    if pallet and shipment.forklift is None:
        required.append("フォークリフトの有無（パレットのため）")
        optional = [x for x in optional if x != "フォークリフトの有無"]
    status = STATUS_READY if not required else STATUS_DRAFT

    lines: list[str] = []
    # 日本語の直し 2026-11-08（C01・C07）: 下書き・依頼準備完了の別は画面にだけ出す。コピーする文は取引先あての書き方にする
    lines.append("【お見積りのお願い】")
    lines.append("（運送会社名）御中")
    lines.append("お世話になっております。（荷主の会社名）の（担当者名）です。")
    lines.append("下記の荷物について、運賃のお見積りをお願いいたします。")
    if mode in MODE_REQUEST_TEXT:   # 案内の表示は本文の最初の数行だけ見せるので、送り方は先頭に置く
        lines.append(f"検討している送り方: {MODE_REQUEST_TEXT[mode]}")
    lines.append("")
    lines.append("■ 荷物")
    for i, it in enumerate(shipment.items, start=1):
        dims = [to_decimal(it.length_cm), to_decimal(it.width_cm), to_decimal(it.height_cm)]
        size = (f"縦 {_fmt(dims[0])} × 横 {_fmt(dims[1])} × 高さ {_fmt(dims[2])} cm"
                if all(x is not None for x in dims) else "寸法 未入力")
        lines.append(
            f"  {i}. 個数 {it.count if it.count is not None else '未入力'} / {size} / "
            f"1 個の重さ {_with_unit(to_decimal(it.weight_kg), 'kg')}"
            + ("（パレット渡し）" if pallet else "")
        )
    lines.append(f"  合計重量: {_with_unit(totals.weight_kg, 'kg')}")
    vol = totals.volume_m3.quantize(Decimal("0.001")) if totals.volume_m3 is not None else None
    lines.append(f"  合計容積: {_with_unit(vol, 'm³')}")
    lines.append(f"  計算重量: {_with_unit(totals.chargeable_kg, 'kg')}（容積 × 280 kg/m³ と重量の大きい方）")
    lines.append("")
    lines.append("■ 区間・日程")
    route = ROUTES.get(shipment.route or "")
    if route is None:
        lines.append("  発着: 未入力")
    elif place and place.get("label") and place.get("km"):
        note = f"。{place['note']}" if place.get("note") else ""
        lines.append(f"  発着: {place['label']}（道路距離 {place['km']:,.1f} km{note}）")
    elif place and place.get("label"):   # 距離が無い着地（船・飛行機でしか行けない）: 届け先の名前だけ書く
        lines.append(f"  発着: {place['label']}")
    elif shipment.route == ROUTE_OTHER:
        lines.append(f"  発着: その他（距離 {_fmt(to_decimal(shipment.distance_km))} km）")
    else:
        lines.append(f"  発着: {route.label}（想定の道路距離 {route.km:,} km）")
    lines.append(f"  出荷日: {shipment.ship_date.isoformat() if shipment.ship_date else '未入力'}")
    lines.append(f"  希望着日: {shipment.desired_date.isoformat() if shipment.desired_date else '未入力'}")
    lines.append(f"  集荷/持込: {shipment.pickup or '未入力'}")
    lines.append(f"  配達/引取: {shipment.delivery or '未入力'}")
    lines.append(f"  届け先の数: {str(shipment.destinations) + ' か所' if shipment.destinations is not None else '未入力'}")
    if shipment.monthly_shipments is not None:
        lines.append(f"  月の出荷回数の見込み: {shipment.monthly_shipments} 回")
    lines.append(f"  発送者: {shipment.sender_type or '未入力'} / 受取人: {shipment.receiver_type or '未入力'}")
    lines.append("")
    lines.append("■ 荷役設備・品目")
    lines.append(f"  フォークリフト: {_yn(shipment.forklift, 'あり', 'なし')}")
    lines.append(f"  パワーゲート車: {_yn(shipment.tail_lift, '必要', '不要')}")
    lines.append(f"  手積み: {_yn(shipment.manual_load, '可', '不可')}")
    lines.append(f"  品目: {shipment.cargo_name or '未入力'}")
    lines.append(f"  壊れ物: {shipment.fragile_note or '未入力'}")
    lines.append("")
    lines.append("■ あわせてお知らせいただきたいこと")
    for c in CONFIRM_ITEMS:
        lines.append(f"  - {c}")
    # 画面への指示（日付を直してください）は依頼文に入れない。画面の警告にだけ出す（C05）
    pending = [m for m in required if "日付を直してください" not in m] + optional
    if pending:
        lines.append("")
        lines.append("■ まだ決まっていない項目（決まりしだいお知らせします）")
        for m in pending:
            lines.append(f"  - {m}")
    if scenario and scenario.get("name"):
        tail = "の例を元に変えたものです" if scenario.get("modified") else "の例です"
        lines.append("")
        lines.append(f"※ この条件は{scenario['name']}（{scenario.get('label') or '想定の荷主'}）{tail}。")
    lines.append("")
    lines.append("お忙しいところ恐れ入りますが、ご確認のほどよろしくお願いいたします。")
    lines.append("")
    lines.append("（荷主の会社名）")
    lines.append("（担当者名）")
    lines.append("（電話番号・メールアドレス）")
    return RequestDraft(status, required, optional, "\n".join(lines) + "\n")
