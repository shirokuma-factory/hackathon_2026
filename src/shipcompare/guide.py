"""問い合わせ先・お届け日数・ほかの輸送手段の早見表（SPEC §13.7〜13.9）。値は data/ の公表資料から読むだけで、推定はしない。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .estimate import ST_CHECK, ST_OK, EstimateRow
from .models import ROUTE_OTHER
from .tariffs import ROUTES, data_dir

MODE_LABELS = {
    "takuhai": "宅配",
    "rosen": "路線便",
    "jitbox": "カゴ台車",
    "kashikiri": "貸切",
}
NO_CONTACT = "窓口の確認が必要"
USABLE_STATUSES = (ST_OK, ST_CHECK)


def mode_group(mode: str) -> str:
    """kashikiri_2t のような行の手段名を、データの手段名（kashikiri）にする。"""
    return "kashikiri" if mode.startswith("kashikiri") else mode


def _load(rel: str, base: Path | None) -> dict[str, Any]:
    with open((base or data_dir()) / rel, encoding="utf-8") as f:
        return json.load(f)


def _need(d: dict[str, Any], keys: tuple[str, ...], where: str) -> None:
    for k in keys:
        if not d.get(k):
            raise ValueError(f"{where}: {k} がありません")


def _need_top(d: dict[str, Any], where: str) -> None:
    _need(d, ("source", "checked_on"), where)


# ---------------------------------------------------------------- 問い合わせ先

@dataclass(frozen=True)
class Contact:
    mode: str  # takuhai / rosen / jitbox / kashikiri
    mode_label: str
    name: str
    url: str | None
    condition: str
    checked_on: str

    @property
    def has_window(self) -> bool:
        return self.url is not None


def load_contacts(base: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    d = _load("contacts.json", base)
    _need_top(d, "contacts.json")
    contacts = d.get("contacts")
    if not isinstance(contacts, dict):
        raise ValueError("contacts.json: contacts がありません")
    for mode in MODE_LABELS:
        entries = contacts.get(mode)
        if not entries:
            raise ValueError(f"contacts.json: {mode} の窓口がありません")
        for e in entries:
            _need(e, ("name", "condition", "checked_on"), f"contacts.json {mode}")
            if "url" not in e:
                raise ValueError(f"contacts.json {mode}: url の項目がありません")
    return contacts


def contacts_for_rows(rows: list[EstimateRow], contacts: dict[str, list[dict[str, Any]]] | None = None,
                      base: Path | None = None) -> list[Contact]:
    """状態が「公開条件に合う」か「要確認」の手段だけの窓口。「使えない」「計算できない」の手段は出さない。"""
    contacts = contacts if contacts is not None else load_contacts(base)
    seen: list[str] = []
    for r in rows:
        g = mode_group(r.mode)
        if r.status in USABLE_STATUSES and g in MODE_LABELS and g not in seen:
            seen.append(g)
    return [Contact(g, MODE_LABELS[g], e["name"], e["url"], e["condition"], e["checked_on"])
            for g in seen for e in contacts[g]]


# ---------------------------------------------------------------- お届け日数

@dataclass(frozen=True)
class TransitDays:
    mode: str
    route: str
    text: str
    note: str | None
    url: str | None  # 照会画面など、本文の中のリンク
    source_name: str
    source_url: str
    checked_on: str
    published: bool  # 区間ごとの公表の値があるか


def load_transit_days(base: Path | None = None) -> dict[str, Any]:
    d = _load("transit_days.json", base)
    _need_top(d, "transit_days.json")
    modes = d.get("modes")
    if not isinstance(modes, dict):
        raise ValueError("transit_days.json: modes がありません")
    for mode in MODE_LABELS:
        m = modes.get(mode)
        if not m:
            raise ValueError(f"transit_days.json: {mode} がありません")
        _need(m, ("source_name", "source_url", "checked_on"), f"transit_days.json {mode}")
        for route in ROUTES:
            if not (m.get("routes", {}).get(route) or {}).get("text"):
                raise ValueError(f"transit_days.json {mode}: {route} の文がありません")
    return modes


def transit_days(mode: str, route: str | None, modes: dict[str, Any] | None = None,
                 base: Path | None = None) -> TransitDays | None:
    """(手段, 区間) の公表のお届け日数。区間が未選択・不明なら None。"""
    if route not in ROUTES:
        return None
    modes = modes if modes is not None else load_transit_days(base)
    g = mode_group(mode)
    if g not in modes:
        return None
    m = modes[g]
    r = m["routes"][route]
    text = r["text"]
    # 区間ごとの公表の値があるのは宅配の3区間だけ。カゴ台車は規則の文、路線便・貸切は公表なし
    published = g == "takuhai" and route != ROUTE_OTHER
    return TransitDays(g, route, text, r.get("note"), r.get("url"), m["source_name"], m["source_url"],
                       m["checked_on"], published)


def transit_days_text(td: TransitDays | None) -> str:
    """表のセルに出す文。宅配の値には注意書きを添える。"""
    if td is None:
        return ""
    text = f"{td.text}（{td.note}）" if td.note else td.text
    return f"{text} {td.url}" if td.url else text


# ---------------------------------------------------------------- ほかの輸送手段

@dataclass(frozen=True)
class GuideRow:
    name: str
    capacity: str
    who: str
    price: str | None
    price_note: str | None
    days: str
    source_name: str
    source_url: str
    checked_on: str
    note: str | None = None

    @property
    def price_text(self) -> str:
        return self.price or self.price_note or ""


def load_guide(base: Path | None = None) -> list[GuideRow]:
    d = _load("guide/other_modes.json", base)
    _need_top(d, "other_modes.json")
    rows = d.get("rows")
    if not rows:
        raise ValueError("other_modes.json: rows がありません")
    out = []
    for e in rows:
        _need(e, ("name", "capacity", "who", "days", "source_name", "source_url", "checked_on"),
              f"other_modes.json {e.get('name', '?')}")
        if not (e.get("price") or e.get("price_note")):
            raise ValueError(f"other_modes.json {e['name']}: price か price_note が必要です")
        out.append(GuideRow(e["name"], e["capacity"], e["who"], e.get("price"), e.get("price_note"),
                            e["days"], e["source_name"], e["source_url"], e["checked_on"], e.get("note")))
    return out
