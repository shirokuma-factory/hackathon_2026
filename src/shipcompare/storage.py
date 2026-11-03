"""JSON の保存と読み込み（SPEC §8）、場面データの読み込み。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .estimate import estimate_all
from .models import (
    COST_KEYS,
    Case,
    ConditionVersion,
    CostLine,
    Item,
    QuoteResponse,
    Shipment,
)
from .tariffs import data_dir

SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (1, 2)
# 回答を選んでも発注は確定しないことの表示。保存する JSON にも入れる（SPEC §13.4）
ORDER_NOTICE = "ここで回答を選んでも、運送の発注は確定していません。発注は運送会社と直接行ってください。"


def _d(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _pd(value: str | None) -> date | None:
    if value is not None and not isinstance(value, str):
        raise TypeError("日付が文字列ではありません")
    return date.fromisoformat(value) if value else None


def shipment_to_dict(s: Shipment) -> dict[str, Any]:
    return {
        "items": [
            {
                "count": i.count,
                "length_cm": i.length_cm,
                "width_cm": i.width_cm,
                "height_cm": i.height_cm,
                "weight_kg": i.weight_kg,
            }
            for i in s.items
        ],
        "route": s.route,
        "distance_km": s.distance_km,
        "origin_in_kanto": s.origin_in_kanto,
        "destinations": s.destinations,
        "sender_type": s.sender_type,
        "receiver_type": s.receiver_type,
        "pickup": s.pickup,
        "delivery": s.delivery,
        "ship_date": _d(s.ship_date),
        "desired_date": _d(s.desired_date),
        "forklift": s.forklift,
        "tail_lift": s.tail_lift,
        "manual_load": s.manual_load,
        "cargo_name": s.cargo_name,
        "fragile_note": s.fragile_note,
        "monthly_shipments": s.monthly_shipments,
    }


def _float_or_same(v: Any) -> Any:
    """距離の整数は小数にそろえる（入力欄が小数だけを受けるため）。"""
    return float(v) if isinstance(v, int) and not isinstance(v, bool) else v


def shipment_from_dict(d: dict[str, Any]) -> Shipment:
    if not isinstance(d, dict):
        raise TypeError("shipment が辞書ではありません")
    raw_items = d.get("items", [])
    if not isinstance(raw_items, list) or not all(isinstance(i, dict) for i in raw_items):
        raise TypeError("items が辞書の一覧ではありません")
    return Shipment(
        items=[Item(**{k: i.get(k) for k in ("count", "length_cm", "width_cm", "height_cm", "weight_kg")})
               for i in raw_items],
        route=d.get("route"),
        distance_km=_float_or_same(d.get("distance_km")),
        origin_in_kanto=d.get("origin_in_kanto"),
        destinations=d.get("destinations"),
        sender_type=d.get("sender_type"),
        receiver_type=d.get("receiver_type"),
        pickup=d.get("pickup"),
        delivery=d.get("delivery"),
        ship_date=_pd(d.get("ship_date")),
        desired_date=_pd(d.get("desired_date")),
        forklift=d.get("forklift"),
        tail_lift=d.get("tail_lift"),
        manual_load=d.get("manual_load"),
        cargo_name=d.get("cargo_name"),
        fragile_note=d.get("fragile_note"),
        monthly_shipments=d.get("monthly_shipments"),
    )


def quote_to_dict(q: QuoteResponse) -> dict[str, Any]:
    return {
        "id": q.id,
        "carrier": q.carrier,
        "product": q.product,
        "version": q.version,
        "received_on": _d(q.received_on),
        "valid_until": _d(q.valid_until),
        "tax": q.tax,
        "is_demo": q.is_demo,
        "lines": {k: {"kind": v.kind, "amount": v.amount} for k, v in q.lines.items()},
    }


def quote_from_dict(d: dict[str, Any]) -> QuoteResponse:
    return QuoteResponse(
        id=d["id"],
        carrier=d["carrier"],
        product=d["product"],
        version=d["version"],
        received_on=_pd(d.get("received_on")),
        valid_until=_pd(d.get("valid_until")),
        tax=d["tax"],
        is_demo=d.get("is_demo", False),
        lines={k: CostLine(kind=v["kind"], amount=v.get("amount")) for k, v in d.get("lines", {}).items()
               if k in COST_KEYS},
    )


def estimates_snapshot(c: Case, manual_by_version: dict[int, dict[str, int]] | None = None) -> list[dict[str, Any]]:
    """確定した版ごとの目安額の記録（保存の時点で計算。読み込みでは使わない）。

    manual_by_version は版の番号ごとの手入力額。無い版は手入力なしで計算する。
    """
    out: list[dict[str, Any]] = []
    for v in c.versions:
        rows = []
        for r in estimate_all(v.shipment, (manual_by_version or {}).get(v.number)):
            src = r.source
            rows.append({
                "mode": r.mode,
                "label": r.label,
                "status": r.status,
                "amount_ex_tax": r.amount if r.has_amount() else None,
                "source_name": src.name if src else None,
                "source_url": (src.url or None) if src else None,
                "applies_from": src.applies_from if src else None,
                "checked_on": src.checked_on if src else None,
            })
        out.append({"version": v.number, "rows": rows})
    return out


def case_to_dict(c: Case, manual_by_version: dict[int, dict[str, int]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "notice": ORDER_NOTICE,
        "saved_at": c.saved_at,
        "scenario": dict(c.scenario) if c.scenario else None,
        "estimates": estimates_snapshot(c, manual_by_version),
        "versions": [
            {"number": v.number, "confirmed_at": v.confirmed_at, "shipment": shipment_to_dict(v.shipment),
             "scenario": dict(v.scenario) if v.scenario else None}
            for v in c.versions
        ],
        "quotes": [quote_to_dict(q) for q in c.quotes],
        "chosen_quote_id": c.chosen_quote_id,
        "reason": c.reason,
        "open_issues": list(c.open_issues),
    }


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise ValueError(message)


def validate_shipment(s: Shipment) -> None:
    """画面の入力欄に入れられない値を、読み込みの時点で ValueError にする。"""
    from .tariffs import ROUTES

    _check(isinstance(s.items, list), "荷物の行が一覧になっていません")
    for n, item in enumerate(s.items, start=1):
        for name in ("count", "length_cm", "width_cm", "height_cm", "weight_kg"):
            v = getattr(item, name)
            _check(v is None or _is_num(v), f"荷物{n}行目の {name} が数値ではありません")
    _check(s.route is None or s.route in ROUTES, f"区間 {s.route!r} は対応していません")
    _check(s.distance_km is None or (_is_num(s.distance_km) and s.distance_km >= 0), "距離が不正です")
    _check(s.monthly_shipments is None or (isinstance(s.monthly_shipments, int)
                                            and not isinstance(s.monthly_shipments, bool)
                                            and s.monthly_shipments >= 1), "月の出荷回数が 1 以上の整数ではありません")
    _check(s.destinations is None or (isinstance(s.destinations, int) and not isinstance(s.destinations, bool)
                                       and s.destinations >= 1), "届け先の数が 1 以上の整数ではありません")
    for name, allowed in (("sender_type", ("法人", "個人事業主", "個人")), ("receiver_type", ("法人", "個人事業主", "個人")),
                          ("pickup", ("集荷", "持込")), ("delivery", ("配達", "引取"))):
        v = getattr(s, name)
        _check(v is None or v in allowed, f"{name} の値 {v!r} は対応していません")
    for name in ("origin_in_kanto", "forklift", "tail_lift", "manual_load"):
        v = getattr(s, name)
        _check(v is None or isinstance(v, bool), f"{name} が真偽値ではありません")
    for name in ("cargo_name", "fragile_note"):
        v = getattr(s, name)
        _check(v is None or isinstance(v, str), f"{name} が文字列ではありません")


def validate_case(c: Case) -> None:
    for v in c.versions:
        _check(isinstance(v.number, int) and not isinstance(v.number, bool), "条件の版の番号が整数ではありません")
        validate_shipment(v.shipment)
    ids = [q.id for q in c.quotes]
    for q in c.quotes:
        _check(isinstance(q.id, str) and isinstance(q.carrier, str) and isinstance(q.product, str),
               "見積回答の id・会社名・商品名が文字列ではありません")
        _check(isinstance(q.version, int) and not isinstance(q.version, bool), "見積回答の版が整数ではありません")
    _check(len(set(ids)) == len(ids), "見積回答の id が重複しています")
    _check(all(isinstance(q.is_demo, bool) for q in c.quotes), "見積回答の is_demo が真偽値ではありません")
    for v in c.versions:
        sc = v.scenario
        _check(sc is None or (isinstance(sc, dict) and all(
            k in sc and (sc[k] is None or isinstance(sc[k], str)) for k in ("id", "name", "label"))
            and isinstance(sc.get("modified"), bool)), "scenario の形が違います")
    _check(c.chosen_quote_id is None or c.chosen_quote_id in ids, "選んだ回答が見積回答の一覧にありません")
    _check(isinstance(c.reason, str), "選んだ理由が文字列ではありません")
    _check(all(isinstance(x, str) for x in c.open_issues), "残る確認事項が文字列ではありません")


def case_from_dict(d: dict[str, Any]) -> Case:
    """保存した辞書から Case を作る。形が合わないときは ValueError にそろえる。"""
    if not isinstance(d, dict):
        raise ValueError("保存ファイルの形式が違います")
    version = d.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"対応していないスキーマの版です: {version}（対応: {'、'.join(map(str, SUPPORTED_SCHEMA_VERSIONS))}）")
    try:
        case = _case_from_dict(d, version)
    except (KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"保存ファイルの項目が足りないか、形が違います（{type(e).__name__}: {e}）") from e
    validate_case(case)
    return case


def _scenario_from_dict(raw: Any) -> dict[str, str | None] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TypeError("scenario が辞書ではありません")
    out: dict[str, str | bool | None] = {k: raw.get(k) for k in ("id", "name", "label")}
    out["modified"] = raw.get("modified", False)
    return out


def _case_from_dict(d: dict[str, Any], version: int) -> Case:
    case = Case(
        versions=[
            ConditionVersion(v["number"], shipment_from_dict(v["shipment"]), v.get("confirmed_at"),
                             _scenario_from_dict(v.get("scenario")))
            for v in d.get("versions", [])
        ],
        quotes=[quote_from_dict(q) for q in d.get("quotes", [])],
        chosen_quote_id=d.get("chosen_quote_id"),
        reason=d.get("reason", ""),
        open_issues=list(d.get("open_issues", [])),
        saved_at=d.get("saved_at"),
        schema_version=version,
    )
    top = _scenario_from_dict(d.get("scenario"))
    if top is not None and case.versions and not any(v.scenario for v in case.versions):
        case.versions[-1].scenario = top  # 版ごとの scenario が無い形式は、最上位のものを今の版に写す
    return case


def save_case(path: str | Path, case: Case, now: datetime | None = None) -> Case:
    """保存日時を入れて JSON に書く。LF で書き、保存した Case を返す。"""
    case.saved_at = (now or datetime.now()).isoformat(timespec="seconds")
    case.schema_version = SCHEMA_VERSION
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(case_to_dict(case), f, ensure_ascii=False, indent=2)
        f.write("\n")
    return case


def load_case(path: str | Path) -> Case:
    with open(path, encoding="utf-8") as f:
        return case_from_dict(json.load(f))


# ---------------------------------------------------------------- 場面データ

def load_scenario(scenario_id: str, base: Path | None = None) -> dict[str, Any]:
    """場面 JSON を読む。返す辞書の shipment は Shipment に変換済み。"""
    path = (base or data_dir()) / "scenarios" / f"{scenario_id}.json"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw["shipment"] = shipment_from_dict(raw["shipment"])
    return raw


def list_scenarios(base: Path | None = None) -> list[dict[str, Any]]:
    folder = (base or data_dir()) / "scenarios"
    return [load_scenario(p.stem, base) for p in sorted(folder.glob("S*.json"))]


def scenario_info(sc: dict[str, Any], modified: bool = False) -> dict[str, str | bool | None]:
    """確定した版に持たせる場面の情報（id・name・label・modified）。"""
    return {"id": sc.get("id"), "name": sc.get("name"), "label": sc.get("label"), "modified": modified}


def demo_quotes_for(sc: dict[str, Any], version: int, today: date) -> list[QuoteResponse]:
    """場面の demo_quotes から架空の見積回答を作る（id は空。呼び出し側で付ける）。

    受領日は today、有効期限は today + valid_days（既定 14 日）。
    """
    out: list[QuoteResponse] = []
    for raw in sc.get("demo_quotes", []):
        days = raw.get("valid_days", 14)
        out.append(QuoteResponse(
            id="", carrier=raw["carrier"], product=raw.get("product", ""), version=version,
            received_on=today, valid_until=today + timedelta(days=days), tax=raw.get("tax", "税抜"),
            lines={k: CostLine(kind=v["kind"], amount=v.get("amount")) for k, v in raw.get("lines", {}).items()
                   if k in COST_KEYS},
            is_demo=True,
        ))
    return out
