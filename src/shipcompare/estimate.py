"""手段ごとの目安額と、使える・使えないの判定（SPEC §5）。金額は税抜の整数円。"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from .models import Shipment, to_decimal
from .tariffs import ROUTES, Source, load_all_tariffs, source_of

# 判定の状態
ST_OK = "公開条件に合う"
ST_CHECK = "要確認"
ST_UNUSABLE = "使えない"
ST_NOCALC = "計算できない"

# 問題の区分（G05: 寸法・重量の超過と積付けの要確認を区別する）
ISSUE_TAKUHAI_OVER = "takuhai_over_limit"  # 宅配の寸法・重量の超過
ISSUE_OVERSIZE = "oversize_surcharge"  # 路線便の2割増
ISSUE_OVER_1000KG = "over_1000kg"  # 路線便の1,000kg超
ISSUE_LOADING = "loading_check"  # 積付けの要確認
ISSUE_MULTI_DEST = "multi_destination"
ISSUE_CORPORATE = "corporate_check"
ISSUE_OUT_OF_RANGE = "out_of_range"
ISSUE_NOT_MODELED = "not_modeled"
ISSUE_NEEDS_INPUT = "needs_input"
ISSUE_PICKUP_UNKNOWN = "pickup_unknown"

# 利用者が手入力した額から出した行の出典（運送会社の表ではない）
USER_INPUT_SOURCE = Source("利用者の入力", "", None, "")

# 手段の名前（SPEC §30.1）。実在の会社名は出さない
TAKUHAI_LABEL = "宅配便（大手の公表運賃からの試算）"
ROSEN_LABEL = "路線便（積合せの公表運賃からの試算）"
JITBOX_LABEL = "カゴ台車 1 本単位（公表運賃からの試算）"
# 出典の行（SPEC §30.1）。各社の表の名前・ファイル名・頁は出さない
ANON_SOURCE_NAME = "大手の公表運賃をもとにした試算。会社名と各社の表は載せません（各社の利用条件のため）"
TAKUHAI_LIMIT_TEXT = "大手の宅配の上限（3辺合計 {cm}cm・{kg}kg まで）"


def anon_source(tariff: dict[str, Any]) -> Source:
    """宅配・路線便・カゴ台車の行の出典。会社名と各社の表への URL は載せない。"""
    return Source(ANON_SOURCE_NAME, "", tariff.get("applies_from"), tariff["checked_on"])


def round_100(value: int | None) -> int | None:
    """表示用に百円の単位へ丸める（SPEC §30.1）。"""
    if value is None:
        return None
    return int((Decimal(value) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 100)

TAX_RATE = Decimal("1.1")
CM3_PER_M3 = Decimal(1_000_000)


@dataclass
class Issue:
    code: str
    message: str


@dataclass
class EstimateRow:
    mode: str
    label: str
    status: str
    amount: int | None = None  # 税抜
    low: int | None = None  # 路線便の最低額
    high: int | None = None  # 路線便の最高額
    reasons: list[str] = field(default_factory=list)  # 使えない・計算できない理由
    missing: list[str] = field(default_factory=list)  # 不足している入力
    issues: list[Issue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source: Source | None = None
    needs_input: str | None = None  # 手入力欄のキー（manual の引数に対応）
    detail: dict[str, Any] = field(default_factory=dict)

    def has_amount(self) -> bool:
        return self.amount is not None and self.status in (ST_OK, ST_CHECK)

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}


@dataclass(frozen=True)
class Totals:
    weight_kg: Decimal | None  # 合計重量
    volume_m3: Decimal | None  # 合計容積（丸めない）
    chargeable_kg: Decimal | None  # 計算重量（小数2桁）
    missing: tuple[str, ...]
    invalid: tuple[str, ...]

    @property
    def problems(self) -> list[str]:
        return list(self.missing) + [f"{x}（0以下または不正）" for x in self.invalid]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.invalid


def round_yen(value: Decimal) -> int:
    """円未満四捨五入。"""
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def ceil_int(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def summarize(shipment: Shipment, kg_per_m3: int = 280) -> Totals:
    """合計重量・合計容積・計算重量。容積は cm³ で合計してから m³ に換算する。"""
    missing: list[str] = []
    invalid: list[str] = []
    if not shipment.items:
        missing.append("荷物の行")
    for i, item in enumerate(shipment.items, start=1):
        m, v = item.problems(i)
        missing += m
        invalid += v
    if missing or invalid:
        return Totals(None, None, None, tuple(missing), tuple(invalid))
    weight = Decimal(0)
    cm3 = Decimal(0)
    for item in shipment.items:
        n = to_decimal(item.count)
        weight += n * to_decimal(item.weight_kg)
        cm3 += n * to_decimal(item.length_cm) * to_decimal(item.width_cm) * to_decimal(item.height_cm)
    volume = cm3 / CM3_PER_M3
    chargeable = max(weight, volume * kg_per_m3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Totals(weight, volume, chargeable, (), ())


def _nocalc(row: EstimateRow, totals: Totals) -> EstimateRow:
    row.status = ST_NOCALC
    row.missing = totals.problems
    row.reasons = [f"計算できない（不足: {'、'.join(totals.problems)}）"]
    return row


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _route_km(shipment: Shipment) -> Decimal | None:
    route = ROUTES.get(shipment.route or "")
    if route is None:
        return None
    if route.km is not None:
        return Decimal(route.km)
    d = to_decimal(shipment.distance_km)
    return d if d is not None and d > 0 else None


# ---------------------------------------------------------------- 宅配

def estimate_takuhai(shipment: Shipment, totals: Totals, tariff: dict[str, Any],
                     manual: dict[str, int]) -> EstimateRow:
    row = EstimateRow("takuhai", TAKUHAI_LABEL, ST_OK, source=anon_source(tariff))
    if not totals.ok:
        return _nocalc(row, totals)

    sizes = tariff["sizes"]
    picked: list[tuple[int, dict[str, Any], int]] = []  # (行番号, サイズ, 個数)
    reasons: list[str] = []
    for i, item in enumerate(shipment.items, start=1):
        total_cm = sum(to_decimal(x) for x in (item.length_cm, item.width_cm, item.height_cm))
        kg = to_decimal(item.weight_kg)
        size = next((s for s in sizes if total_cm <= s["max_sum_cm"] and kg <= s["max_kg"]), None)
        if size is None:
            max_cm = sizes[-1]["max_sum_cm"]
            max_kg = sizes[-1]["max_kg"]
            parts = []
            if total_cm > max_cm:
                parts.append(f"3辺の合計が{total_cm.normalize():f}cm")
            if kg > max_kg:
                parts.append(f"1個の重さが{kg.normalize():f}kg")
            if parts:
                parts = ["、".join(parts) + "で、" + TAKUHAI_LIMIT_TEXT.format(cm=max_cm, kg=max_kg) + "を超える"]
            if not parts:
                parts.append("サイズと重さの組み合わせが表にない")
            reasons.append(f"荷物{i}行目: " + "、".join(parts))
        else:
            picked.append((i, size, int(item.count)))
    if reasons:
        row.status = ST_UNUSABLE
        row.reasons = reasons
        row.issues.append(Issue(ISSUE_TAKUHAI_OVER, "宅配の寸法・重量の上限を超える荷物があるため使えません"))
        return row

    if shipment.route not in ROUTES:
        row.status = ST_NOCALC
        row.missing = ["区間"]
        row.reasons = ["計算できない（不足: 区間）"]
        return row

    if shipment.route != "kanto_kansai":
        value = _positive_int(manual.get("takuhai_ex_tax"))
        if value is None:
            row.status = ST_NOCALC
            row.needs_input = "takuhai_ex_tax"
            row.reasons = ["この区間の表は持っていません。公表運賃の表で調べた額を入れてください"]
            row.issues.append(Issue(ISSUE_NEEDS_INPUT, row.reasons[0]))
            return row
        row.amount = value
        row.source = USER_INPUT_SOURCE
        row.notes.append("利用者が入力した税抜額です")
        return row

    discount = tariff["rules"]["dropoff_discount_per_piece"] if shipment.pickup == "持込" else 0
    if shipment.pickup not in ("集荷", "持込"):
        row.issues.append(Issue(ISSUE_PICKUP_UNKNOWN, "集荷か持込かが未入力のため、持込の減額は引いていません"))
    gross = 0
    breakdown = []
    for i, size, count in picked:
        gross += (size["fare"] - discount) * count
        breakdown.append({"line": i, "size": size["size"], "count": count, "fare_incl": size["fare"]})
    row.detail = {"sizes": breakdown, "total_incl_tax": gross, "dropoff_discount": discount}
    row.amount = round_yen(Decimal(gross) / TAX_RATE)
    return row


# ---------------------------------------------------------------- 路線便

def _rosen_base_fare(tariff: dict[str, Any], km: Decimal, band: int) -> int | None:
    """表にある段だけ引く。無ければ None。"""
    columns = tariff["columns_by_distance_km"]
    key = str(km.to_integral_value()) if km == km.to_integral_value() else None
    if key in columns:
        return columns[key][tariff["weight_bands_kg"].index(band)]
    for cell in tariff["cells"]:
        if cell["weight_band_kg"] != band:
            continue
        if Decimal(cell["distance_band_km"]) == km:
            return cell["fare"]
        add = cell.get("add_per_100km_over_1000")
        if add is not None and km > cell["distance_band_km"] == 1000:
            steps = ceil_int((km - 1000) / 100)
            value = Decimal(cell["fare"] + add * steps)
            return int((value / 10).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 10)
    return None


def _oversize_reasons(shipment: Shipment) -> list[str]:
    out = []
    for i, item in enumerate(shipment.items, start=1):
        dims = [to_decimal(item.length_cm), to_decimal(item.width_cm), to_decimal(item.height_cm)]
        if max(dims) > 450:
            out.append(f"荷物{i}行目の長さが4.5m超")
        if to_decimal(item.weight_kg) > 500:
            out.append(f"荷物{i}行目の重さが500kg超")
        if dims[0] * dims[1] * dims[2] / CM3_PER_M3 > 2:
            out.append(f"荷物{i}行目の容積が2m³超")
    return out


def estimate_rosen(shipment: Shipment, totals: Totals, tariff: dict[str, Any],
                   manual: dict[str, int]) -> EstimateRow:
    row = EstimateRow("rosen", ROSEN_LABEL, ST_OK, source=anon_source(tariff))
    if shipment.receiver_type == "個人":
        row.status = ST_UNUSABLE
        row.reasons = ["使えない（法人向けの商品で、個人あての配送はしていません）"]
        row.issues.append(Issue(ISSUE_CORPORATE, row.reasons[0]))
        return row
    if not totals.ok:
        return _nocalc(row, totals)
    row.detail = {
        "chargeable_kg": totals.chargeable_kg,
        "weight_kg": totals.weight_kg,
        "volume_m3": totals.volume_m3,
    }
    row.notes.append("ほかに加算があります（集配距離の加算、地区割増、冬期割増などは入っていません）")
    row.issues.append(Issue(ISSUE_NOT_MODELED, "集配距離の加算、地区割増、冬期割増は計算に入れていません"))

    bands = tariff["weight_bands_kg"]
    band = next((b for b in bands if totals.chargeable_kg <= b), None)
    if band is None:
        row.status = ST_NOCALC
        row.reasons = [f"計算重量が{bands[-1]:,}kgを超えるため、見積で確認してください"]
        row.issues.append(Issue(ISSUE_OVER_1000KG, row.reasons[0]))
        return row
    row.detail["weight_band_kg"] = band

    if shipment.route not in ROUTES:
        row.status = ST_NOCALC
        row.missing = ["区間"]
        row.reasons = ["計算できない（不足: 区間）"]
        return row
    km = _route_km(shipment)
    if km is None:
        row.status = ST_NOCALC
        row.missing = ["距離 km"]
        row.reasons = ["計算できない（不足: 距離 km）"]
        return row

    base = _rosen_base_fare(tariff, km, band)
    if base is None:
        base = _positive_int(manual.get("rosen_base_fare"))
        if base is None:
            row.status = ST_NOCALC
            row.needs_input = "rosen_base_fare"
            row.reasons = [f"この距離・重量の段（{band}kg）の表は持っていません。表を見て基準運賃を入れてください"]
            row.issues.append(Issue(ISSUE_NEEDS_INPUT, row.reasons[0]))
            return row
        row.source = USER_INPUT_SOURCE
        row.notes.append("基準運賃は利用者が入力した値です")

    amount = Decimal(base)
    over = _oversize_reasons(shipment)
    if over:
        amount = amount * Decimal("1.2")
        row.issues.append(Issue(ISSUE_OVERSIZE, "2割増: " + "、".join(over)))
    row.detail["base_fare"] = base
    row.amount = round_yen(amount)
    row.low = round_yen(amount * Decimal("0.9"))
    row.high = round_yen(amount * Decimal("1.1"))
    if shipment.sender_type == "個人":
        row.status = ST_CHECK
        msg = "要確認（法人のお客様が対象の商品です）"
        row.reasons.append(msg)
        row.issues.append(Issue(ISSUE_CORPORATE, msg))
    return row


# ---------------------------------------------------------------- カゴ台車1本単位

def estimate_jitbox(shipment: Shipment, totals: Totals, tariff: dict[str, Any],
                    manual: dict[str, int]) -> EstimateRow:
    row = EstimateRow("jitbox", JITBOX_LABEL, ST_OK, source=anon_source(tariff))
    row.notes.append("区間の代表（東京→大阪府、東京→宮城、道央→東京）の運賃を使っています")

    kinds = {shipment.sender_type, shipment.receiver_type}
    if "個人" in kinds:
        row.status = ST_UNUSABLE
        row.reasons = ["個人が発送者または受取人のため使えません（法人間の商品）"]
        return row
    if not totals.ok:
        return _nocalc(row, totals)

    route = tariff["routes"].get(shipment.route or "")
    if route is None:
        row.status = ST_NOCALC
        row.needs_input = None
        row.reasons = ["この区間の運賃は持っていません"]
        row.missing = ["この区間のカゴ台車の運賃"]
        return row

    box = tariff["box"]
    by_weight = ceil_int(totals.weight_kg / Decimal(box["max_kg"]))
    by_volume = ceil_int(totals.volume_m3 / Decimal(str(box["volume_m3"])))
    boxes = max(by_weight, by_volume)
    row.amount = route["fare_per_box"] * boxes
    row.detail = {"boxes": boxes, "fare_per_box": route["fare_per_box"], "route_label": route["label"]}
    row.notes.append(f"最低 {boxes} 本の目安。積付けは要確認")
    row.issues.append(Issue(ISSUE_LOADING, "ボックス内寸 1,040×1,040×1,700mm への積付けは要確認"))

    inner = sorted(Decimal(str(x)) / 10 for x in box["inner_mm"])  # cm
    too_big = [i for i, item in enumerate(shipment.items, start=1)
               if any(d > b for d, b in zip(sorted(to_decimal(x) for x in (item.length_cm, item.width_cm, item.height_cm)), inner))]
    if too_big:
        row.status = ST_CHECK
        row.reasons.append("1個がボックスの内寸を超えます。荷を分けて詰め直せる場合だけ使えます"
                           f"（荷物{'・'.join(str(i) for i in too_big)}行目）")
        row.issues.append(Issue(ISSUE_LOADING, row.reasons[-1]))
        if not (shipment.sender_type == "法人" and shipment.receiver_type == "法人"):
            row.issues.append(Issue(ISSUE_CORPORATE, "法人間の商品。利用できるかは要確認"))
    elif shipment.sender_type == "法人" and shipment.receiver_type == "法人":
        row.status = ST_OK
    else:
        row.status = ST_CHECK
        row.issues.append(Issue(ISSUE_CORPORATE, "法人間の商品。利用できるかは要確認"))
    return row


# ---------------------------------------------------------------- 貸切

def kashikiri_fares(km: Decimal, weight_kg: Decimal, tariff: dict[str, Any]) -> list[dict[str, Any]]:
    """距離 km（base_km 以上）と合計重量に載る車格ごとの額（SPEC §5.5 とその訂正）。小さい車格から並べる。

    200km 超 500km までは step_km（20km）ごと、500km 超は over_500_step_km（50km）ごとに加算する。
    steps は 20km ごとの区分の数、steps_over_500 は 50km ごとの区分の数。
    表に 500km 超の定めがなければ steps_over_500 は 0 で、20km ごとのまま数える。
    地域の表は tariffs.load_kashikiri(region) で選ぶ（既定は関東）。"""
    over_km = tariff.get("over_500_km")
    over_step = tariff.get("over_500_step_km")
    if over_km is None or over_step is None or km <= over_km:
        steps, steps_over = ceil_int((km - tariff["base_km"]) / Decimal(tariff["step_km"])), 0
    else:
        steps = ceil_int((Decimal(over_km) - tariff["base_km"]) / Decimal(tariff["step_km"]))
        steps_over = ceil_int((km - over_km) / Decimal(over_step))
    return [
        {"class": v["class"], "max_kg": v["max_kg"], "steps": steps, "steps_over_500": steps_over,
         "amount": v["base_fare"] + steps * v["add_per_step"] + steps_over * v.get("add_per_step_over_500", 0)}
        for v in tariff["vehicles"] if weight_kg <= v["max_kg"]
    ]


def estimate_kashikiri(shipment: Shipment, totals: Totals, tariff: dict[str, Any],
                       manual: dict[str, int]) -> list[EstimateRow]:
    def base_row(mode: str = "kashikiri", label: str = "貸切（標準的な運賃）") -> EstimateRow:
        r = EstimateRow(mode, label, ST_OK, source=source_of(tariff))
        r.notes.append("国の目安で、実際の運賃は会社ごとに違います")
        return r

    route = ROUTES.get(shipment.route or "")
    if route is None:
        r = base_row()
        r.status = ST_NOCALC
        r.missing = ["区間"]
        r.reasons = ["計算できない（不足: 区間）"]
        return [r]
    origin_kanto = route.origin_in_kanto if route.origin_in_kanto is not None else shipment.origin_in_kanto
    if origin_kanto is None:
        r = base_row()
        r.status = ST_NOCALC
        r.missing = ["発地が関東かどうか"]
        r.reasons = ["計算できない（不足: 発地が関東かどうか）"]
        return [r]
    if not origin_kanto:
        r = base_row()
        r.status = ST_UNUSABLE
        r.reasons = ["この表の範囲外（発地が関東ではありません）"]
        r.issues.append(Issue(ISSUE_OUT_OF_RANGE, r.reasons[0]))
        return [r]
    km = _route_km(shipment)
    if km is None:
        r = base_row()
        r.status = ST_NOCALC
        r.missing = ["距離 km"]
        r.reasons = ["計算できない（不足: 距離 km）"]
        return [r]
    if km < tariff["base_km"]:
        r = base_row()
        r.status = ST_UNUSABLE
        r.reasons = [f"この表の範囲外（距離{tariff['base_km']}km未満）"]
        r.issues.append(Issue(ISSUE_OUT_OF_RANGE, r.reasons[0]))
        return [r]
    if not totals.ok:
        r = base_row()
        return [_nocalc(r, totals)]

    rows = []
    for f in kashikiri_fares(km, totals.weight_kg, tariff):
        r = base_row(f"kashikiri_{f['class']}", f"貸切 {f['class']}車（標準的な運賃）")
        r.amount = f["amount"]
        r.detail = {"vehicle": f["class"], "max_kg": f["max_kg"], "steps": f["steps"], "steps_over_500": f["steps_over_500"]}
        r.issues.append(Issue(ISSUE_LOADING, "積めるかどうかは判定していません。積付けは要確認"))
        rows.append(r)
    if not rows:
        r = base_row()
        r.status = ST_UNUSABLE
        r.reasons = ["合計重量が10tの最大積載量を超えます"]
        rows.append(r)
    return rows


# ---------------------------------------------------------------- まとめ

def estimate_all(shipment: Shipment, manual: dict[str, int] | None = None,
                 tariffs: dict[str, dict[str, Any]] | None = None) -> list[EstimateRow]:
    """全手段の行を、目安額の安い順（使えない・計算できないは下）に並べて返す。

    manual は手入力欄の値（takuhai_ex_tax: 宅配の税抜額、rosen_base_fare: 路線便の基準運賃）。
    """
    tariffs = tariffs or load_all_tariffs()
    manual = manual or {}
    totals = summarize(shipment, tariffs["rosen"]["volume_to_weight_kg_per_m3"])

    rows: list[EstimateRow] = [
        estimate_takuhai(shipment, totals, tariffs["takuhai"], manual),
        estimate_rosen(shipment, totals, tariffs["rosen"], manual),
        estimate_jitbox(shipment, totals, tariffs["jitbox"], manual),
        *estimate_kashikiri(shipment, totals, tariffs["kashikiri"], manual),
    ]

    # 届け先が2以上（または未入力・不正）なら宅配以外は要確認
    dest_ok = shipment.destinations is not None and not isinstance(shipment.destinations, bool) \
        and shipment.destinations >= 1
    if not dest_ok or shipment.destinations >= 2:
        msg = "届け先の数が未入力または不正です" if not dest_ok else \
            f"届け先が{shipment.destinations}か所のため、宅配以外は要確認"
        for r in rows:
            if r.mode != "takuhai" and r.status == ST_OK:
                r.status = ST_CHECK
                r.issues.append(Issue(ISSUE_MULTI_DEST, msg))

    return sort_rows(rows)


def sort_rows(rows: list[EstimateRow]) -> list[EstimateRow]:
    priced = sorted((r for r in rows if r.has_amount()), key=lambda r: r.amount)
    rest = [r for r in rows if not r.has_amount()]
    return priced + rest
