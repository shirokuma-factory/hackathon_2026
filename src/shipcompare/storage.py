"""JSON の保存と読み込み（SPEC §8）、場面データの読み込み。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .models import (
    Case,
    ConditionVersion,
    CostLine,
    Item,
    QuoteResponse,
    Shipment,
)
from .tariffs import data_dir

SCHEMA_VERSION = 1


def _d(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _pd(value: str | None) -> date | None:
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
    }


def shipment_from_dict(d: dict[str, Any]) -> Shipment:
    return Shipment(
        items=[Item(**{k: i.get(k) for k in ("count", "length_cm", "width_cm", "height_cm", "weight_kg")})
               for i in d.get("items", [])],
        route=d.get("route"),
        distance_km=d.get("distance_km"),
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
        lines={k: CostLine(kind=v["kind"], amount=v.get("amount")) for k, v in d.get("lines", {}).items()},
    )


def case_to_dict(c: Case) -> dict[str, Any]:
    return {
        "schema_version": c.schema_version,
        "saved_at": c.saved_at,
        "versions": [
            {"number": v.number, "confirmed_at": v.confirmed_at, "shipment": shipment_to_dict(v.shipment)}
            for v in c.versions
        ],
        "quotes": [quote_to_dict(q) for q in c.quotes],
        "chosen_quote_id": c.chosen_quote_id,
        "reason": c.reason,
        "open_issues": list(c.open_issues),
    }


def case_from_dict(d: dict[str, Any]) -> Case:
    version = d.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"対応していないスキーマの版です: {version}（対応: {SCHEMA_VERSION}）")
    return Case(
        versions=[
            ConditionVersion(v["number"], shipment_from_dict(v["shipment"]), v.get("confirmed_at"))
            for v in d.get("versions", [])
        ],
        quotes=[quote_from_dict(q) for q in d.get("quotes", [])],
        chosen_quote_id=d.get("chosen_quote_id"),
        reason=d.get("reason", ""),
        open_issues=list(d.get("open_issues", [])),
        saved_at=d.get("saved_at"),
        schema_version=version,
    )


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
