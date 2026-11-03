"""B 空きを探す・つなぐ（SPEC §16.2、架空のデモ）。

便の読み込み、照合（作成者の構想の通知の条件）、仮予約の盤（Board）を持つ。
照合は純粋な関数。盤は重さの余りをメモリに持ち、仮予約・承諾・辞退・期限切れで増減する。
サーバーは再起動で初期の架空データに戻る。決済・メール・通知の送信はしない。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from .models import to_decimal
from .tariffs import data_dir

SPACE_TYPES = ("free_bed", "inside_rb_upper", "above_rb_rear", "shelf_container")
SHELF_TYPES = ("above_rb_rear", "shelf_container")  # 棚の耐荷重を持つ形（形 2・3）
HEIGHT_MARGIN_MM = Decimal(50)  # 箱の高さは空きの高さ − 50mm まで
DEFAULT_HOLD_MINUTES = 30
MAX_HOLD_MINUTES = 24 * 60
FICTION_NOTICE = "架空の運送会社と便です。"
BOOKING_NOTICE = "積むかどうか、どこにどう積むかは運送会社が決めます。受付で量って条件に合わないときは受け付けません。"

HELD, ACCEPTED, DECLINED, EXPIRED = "held", "accepted", "declined", "expired"


class BoardError(Exception):
    """盤の操作の誤り。メッセージは利用者に見せる日本語。"""


@dataclass(frozen=True)
class Piece:
    """荷物の1行。寸法は mm、重さは1個の kg。"""

    count: int
    length_mm: Decimal
    width_mm: Decimal
    height_mm: Decimal
    weight_kg: Decimal


@dataclass(frozen=True)
class BShipment:
    pieces: tuple[Piece, ...]
    dest_code: str
    category: str | None = None  # 品目（受けない品目と照合する）
    in_container: bool = False
    fragile: bool = False
    is_box: bool = True  # 段ボールなどの直方体の箱か

    @property
    def total_kg(self) -> Decimal:
        return sum((p.weight_kg * p.count for p in self.pieces), Decimal(0))


# ---------------------------------------------------------------- 読み込み

def trips_path() -> Path:
    return data_dir() / "demo" / "carriers.json"


def load_demo() -> dict[str, Any]:
    with open(trips_path(), encoding="utf-8") as f:
        return json.load(f)


def load_trips() -> list[dict[str, Any]]:
    """便の一覧。運送会社の名前を carrier_name として足す。"""
    data = load_demo()
    names = {c["id"]: c["name"] for c in data["carriers"]}
    out = []
    for t in data["trips"]:
        t = dict(t)
        t["carrier_name"] = names[t["carrier_id"]]
        out.append(t)
    return out


def parse_shipment(body: dict[str, Any]) -> BShipment:
    """API の本文から荷物を作る。shipment.items は 15.2 の荷物（cm と kg）。誤りは ValueError（日本語）。"""
    raw = body.get("shipment")
    if not isinstance(raw, dict):
        raise ValueError("shipment（荷物）がありません")
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("荷物の行（items）が1行以上必要です")
    pieces = []
    for n, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            raise ValueError(f"荷物{n}行目が辞書ではありません")
        count = it.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError(f"荷物{n}行目の個数は1以上の整数で入れてください")
        vals = {}
        for key, label in (("length_cm", "縦"), ("width_cm", "横"), ("height_cm", "高さ"), ("weight_kg", "1個の重さ")):
            d = to_decimal(it.get(key))
            if d is None or d <= 0:
                raise ValueError(f"荷物{n}行目の{label}は0より大きい数で入れてください")
            vals[key] = d
        pieces.append(Piece(count, vals["length_cm"] * 10, vals["width_cm"] * 10, vals["height_cm"] * 10,
                            vals["weight_kg"]))

    def pick(key: str, default: Any) -> Any:
        return body[key] if key in body else raw.get(key, default)

    dest = pick("dest_code", None)
    if not isinstance(dest, str) or not dest:
        raise ValueError("dest_code（受け取り場所の市区町村コード）がありません")
    category = pick("category", None)
    if category is not None and not isinstance(category, str):
        raise ValueError("category（品目）は文字列で入れてください")
    flags = {}
    for key, default in (("in_container", False), ("fragile", False), ("is_box", True)):
        v = pick(key, default)
        if not isinstance(v, bool):
            raise ValueError(f"{key} は true か false で入れてください")
        flags[key] = v
    return BShipment(tuple(pieces), dest, (category or "").strip() or None, **flags)


# ---------------------------------------------------------------- 照合

def _dec(v: Any) -> Decimal:
    d = to_decimal(v)
    if d is None:
        raise ValueError(f"数値が読めません: {v!r}")
    return d


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def match(shipment: BShipment, trip: dict[str, Any], slack_kg: Any = None, shelf_kg: Any = None) -> tuple[bool, list[str]]:
    """荷物が便の空きに載るか。(合否, 合わない理由の一覧)。理由は日本語で、合えば空。

    slack_kg・shelf_kg は盤が持つ「いまの残り」。省略すると便の登録値を使う。
    """
    reasons: list[str] = []
    st = trip["space_type"]
    slack = _dec(trip["weight_slack_kg"] if slack_kg is None else slack_kg)

    # 1 個の重さ
    max_piece = _dec(trip["max_piece_kg"])
    heaviest = max(p.weight_kg for p in shipment.pieces)
    if heaviest > max_piece:
        reasons.append(f"1個の重さ {_fmt(heaviest)}kg がこの便の上限 {_fmt(max_piece)}kg を超えます")

    # 箱の高さ
    limit_h = _dec(trip["clear_height_mm"]) - HEIGHT_MARGIN_MM
    tallest = max(p.height_mm for p in shipment.pieces)
    if tallest > limit_h:
        reasons.append(f"箱の高さ {_fmt(tallest)}mm が空きの高さ − 50mm（{_fmt(limit_h)}mm）を超えます")

    # 底面（縦横どちら向きでも）
    fw, fd = (_dec(x) for x in trip["unit_footprint_mm"])
    for p in shipment.pieces:
        a, b = p.length_mm, p.width_mm
        if not ((a <= fw and b <= fd) or (b <= fw and a <= fd)):
            reasons.append(f"箱の底面 {_fmt(a)}×{_fmt(b)}mm が1口の底面 {_fmt(fw)}×{_fmt(fd)}mm に収まりません")
            break

    # 合計の重さ（棚の形は梁と板の残りとの小さい方）
    total = shipment.total_kg
    cap, cap_label = slack, "重さの余り"
    if st in SHELF_TYPES:
        shelf = _dec(trip["shelf_rated_kg"] if shelf_kg is None else shelf_kg)
        if shelf < cap:
            cap, cap_label = shelf, "棚の耐荷重の残り"
    if total > cap:
        reasons.append(f"合計の重さ {_fmt(total)}kg が{cap_label} {_fmt(cap)}kg を超えます")

    # 降ろせる地点
    if shipment.dest_code not in trip["drop_points"]:
        reasons.append("受け取り場所がこの便の降ろせる地点にありません")

    # 容器
    if trip["container_required"] and not shipment.in_container:
        reasons.append("この便は容器に入れた荷だけを受けます")

    # RB の中の上段
    if st == "inside_rb_upper":
        if not trip["rb_owner_consent"]:
            reasons.append("RB の持ち主と主荷主の同意がない便です")
        per_rb = _dec(trip["max_upper_per_rb_kg"])
        if total > per_rb:
            reasons.append(f"1口の重さ {_fmt(total)}kg が RB 1台の上段の上限 {_fmt(per_rb)}kg を超えます")

    # 形と品目
    if not shipment.is_box:
        reasons.append("段ボールなどの直方体の箱だけを受けます（袋物・筒物・はみ出す物は受けません）")
    if shipment.fragile:
        reasons.append("壊れ物は受けません")
    if shipment.category and shipment.category in trip["refused_categories"]:
        reasons.append(f"品目「{shipment.category}」はこの便が受けない品目です")

    return (not reasons), reasons


# ---------------------------------------------------------------- 仮予約の盤

@dataclass
class _Hold:
    id: str
    trip_id: str
    weight_kg: Decimal
    created_at: datetime
    expires_at: datetime
    state: str = HELD


@dataclass
class _Cap:
    slack: Decimal
    shelf: Decimal | None


class Board:
    """便ごとの重さの余りと仮予約をメモリに持つ。操作はすべて Lock の中で行う。"""

    def __init__(self, trips: list[dict[str, Any]] | None = None, clock: Callable[[], datetime] | None = None):
        self._clock = clock or datetime.now
        self._lock = threading.Lock()
        self._trips = trips if trips is not None else load_trips()
        self._by_id = {t["id"]: t for t in self._trips}
        self._reset_locked()

    def _reset_locked(self) -> None:
        self._cap = {t["id"]: _Cap(_dec(t["weight_slack_kg"]),
                                   _dec(t["shelf_rated_kg"]) if t["space_type"] in SHELF_TYPES else None)
                     for t in self._trips}
        self._holds: dict[str, _Hold] = {}
        self._seq = 0

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    # --- 内部（Lock を持っているときだけ呼ぶ）
    def _sweep(self) -> None:
        now = self._clock()
        for h in self._holds.values():
            if h.state == HELD and now >= h.expires_at:
                self._restore(h)
                h.state = EXPIRED

    def _restore(self, h: _Hold) -> None:
        cap = self._cap[h.trip_id]
        cap.slack += h.weight_kg
        if cap.shelf is not None:
            cap.shelf += h.weight_kg

    def _trip(self, trip_id: Any) -> dict[str, Any]:
        t = self._by_id.get(trip_id) if isinstance(trip_id, str) else None
        if t is None:
            raise BoardError(f"便 {trip_id!r} が見つかりません")
        return t

    @staticmethod
    def _hold_dict(h: _Hold) -> dict[str, Any]:
        return {"hold_id": h.id, "trip_id": h.trip_id, "state": h.state, "weight_kg": float(h.weight_kg),
                "created_at": h.created_at.isoformat(timespec="seconds"),
                "expires_at": h.expires_at.isoformat(timespec="seconds")}

    def _trip_dict(self, t: dict[str, Any]) -> dict[str, Any]:
        cap = self._cap[t["id"]]
        d = dict(t)
        d["remaining_slack_kg"] = float(cap.slack)
        d["remaining_shelf_kg"] = float(cap.shelf) if cap.shelf is not None else None
        return d

    # --- 公開
    def trips(self) -> list[dict[str, Any]]:
        """便の一覧（いまの残りつき）。"""
        with self._lock:
            self._sweep()
            return [self._trip_dict(t) for t in self._trips]

    def holds(self) -> list[dict[str, Any]]:
        with self._lock:
            self._sweep()
            return [self._hold_dict(h) for h in self._holds.values()]

    def match_all(self, shipment: BShipment) -> list[dict[str, Any]]:
        """便ごとの合否と理由（いまの残りで照合する）。"""
        with self._lock:
            self._sweep()
            out = []
            for t in self._trips:
                cap = self._cap[t["id"]]
                ok, reasons = match(shipment, t, cap.slack, cap.shelf)
                out.append({"trip_id": t["id"], "ok": ok, "reasons": reasons})
            return out

    def hold(self, trip_id: Any, shipment: BShipment, hold_minutes: Any = DEFAULT_HOLD_MINUTES) -> dict[str, Any]:
        """期限付きの仮予約。照合と残りの減らしを1つの Lock の中で行う。合わなければ BoardError。"""
        if isinstance(hold_minutes, bool) or not isinstance(hold_minutes, (int, float)) \
                or not 0 < hold_minutes <= MAX_HOLD_MINUTES:
            raise BoardError(f"仮予約の期限（hold_minutes）は 0 より大きく {MAX_HOLD_MINUTES} 分以下で入れてください")
        with self._lock:
            self._sweep()
            trip = self._trip(trip_id)
            cap = self._cap[trip["id"]]
            ok, reasons = match(shipment, trip, cap.slack, cap.shelf)
            if not ok:
                raise BoardError("この便には仮予約できません: " + "。".join(reasons))
            weight = shipment.total_kg
            cap.slack -= weight
            if cap.shelf is not None:
                cap.shelf -= weight
            now = self._clock()
            self._seq += 1
            h = _Hold(f"H{self._seq}", trip["id"], weight, now, now + timedelta(minutes=hold_minutes))
            self._holds[h.id] = h
            return self._hold_dict(h)

    def decide(self, hold_id: Any, decision: Any) -> dict[str, Any]:
        """運送会社の役の承諾（accept）か辞退（decline）。辞退で重さの余りを戻す。"""
        if decision not in ("accept", "decline"):
            raise BoardError("decision は accept か decline で入れてください")
        with self._lock:
            self._sweep()
            h = self._holds.get(hold_id) if isinstance(hold_id, str) else None
            if h is None:
                raise BoardError(f"仮予約 {hold_id!r} が見つかりません")
            if h.state == EXPIRED:
                raise BoardError("仮予約の期限が切れています。重さの余りは戻しました")
            if h.state != HELD:
                raise BoardError("この仮予約はすでに決まっています")
            if decision == "accept":
                h.state = ACCEPTED
            else:
                self._restore(h)
                h.state = DECLINED
            return self._hold_dict(h)
