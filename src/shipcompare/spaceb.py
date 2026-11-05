"""B 空きを探す・つなぐ（SPEC §16.2、架空のデモ）。

便の読み込み、照合（v3 §4.2 の通知の条件）、仮予約の盤（Board）を持つ。
照合は純粋な関数。盤は重さの余りをメモリに持ち、仮予約・承諾・辞退・期限切れで増減する。
サーバーは再起動で初期の架空データに戻る。決済・メール・通知の送信はしない。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from . import geo
from .estimate import kashikiri_fares, round_yen
from .models import to_decimal
from .tariffs import KASHIKIRI_REGION_FILES, data_dir, load_kashikiri

SPACE_TYPES = ("free_bed", "inside_rb_upper", "above_rb_rear", "shelf_container")
SHELF_TYPES = ("above_rb_rear", "shelf_container")  # 棚の耐荷重を持つ形（形 2・3）
HEIGHT_MARGIN_MM = Decimal(50)  # 箱の高さは空きの高さ − 50mm まで
DEFAULT_HOLD_MINUTES = 30
DEFAULT_PICKUP_RADIUS_KM = 20  # 便に pickup_radius_km が無いときの集荷の上限（SPEC §21.2）
MAX_HOLD_MINUTES = 72 * 60  # 仮予約の期限（締切）の上限。SPEC §21.2
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
    origin_code: str | None = None  # 集荷の地点の市区町村コード（SPEC §21.2、集荷の距離の照合）
    window_from: date | None = None  # 届けばよい日の幅（SPEC §21.2）
    window_to: date | None = None

    @property
    def total_kg(self) -> Decimal:
        return sum((p.weight_kg * p.count for p in self.pieces), Decimal(0))

    @property
    def total_floor_m2(self) -> Decimal:
        """各個の縦 × 横 × 個数の合計（m²）。"""
        return sum((p.length_mm * p.width_mm * p.count for p in self.pieces), Decimal(0)) / Decimal(1_000_000)


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
    origin = pick("origin_code", None)
    if origin is not None and (not isinstance(origin, str) or not origin):
        raise ValueError("origin_code（集荷の地点の市区町村コード）は文字列で入れてください")
    wf, wt = _parse_day(pick("window_from", None), "window_from"), _parse_day(pick("window_to", None), "window_to")
    if (wf is None) != (wt is None):
        raise ValueError("届けばよい日の幅は window_from と window_to を両方入れてください")
    if wf and wt and wf > wt:
        raise ValueError("window_from は window_to より前の日にしてください")
    return BShipment(tuple(pieces), dest, (category or "").strip() or None, origin_code=origin,
                     window_from=wf, window_to=wt, **flags)


def _parse_day(v: Any, name: str) -> date | None:
    if v is None or v == "":
        return None
    try:
        if not isinstance(v, str):
            raise ValueError
        return date.fromisoformat(v[:10])
    except ValueError:
        raise ValueError(f"{name} は YYYY-MM-DD の日付で入れてください") from None


# ---------------------------------------------------------------- 照合

def _dec(v: Any) -> Decimal:
    d = to_decimal(v)
    if d is None:
        raise ValueError(f"数値が読めません: {v!r}")
    return d


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _md(d: date) -> str:
    return f"{d.month}/{d.day}"


def _arrive_day(trip: dict[str, Any]) -> date | None:
    v = trip.get("arrive_at")
    return date.fromisoformat(v[:10]) if isinstance(v, str) and v else None


def match(shipment: BShipment, trip: dict[str, Any], slack_kg: Any = None, shelf_kg: Any = None,
          floor_m2: Any = None) -> tuple[bool, list[str]]:
    """荷物が便の空きに載るか。(合否, 合わない理由の一覧)。理由は日本語で、合えば空。

    slack_kg・shelf_kg・floor_m2 は盤が持つ「いまの残り」。省略すると便の登録値を使う。
    SPEC §21.2: 日の幅と集荷の距離は荷物に日の幅・出発地があれば全便で調べる。床の合計は便に floor_free_m2 があるときだけ。
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

    # 届けばよい日の幅（到着日で比べる）
    arrive = _arrive_day(trip)
    if shipment.window_from and shipment.window_to and arrive:
        if not shipment.window_from <= arrive <= shipment.window_to:
            reasons.append(f"この便は {_md(arrive)} 着で、届けばよい日の幅 {_md(shipment.window_from)}〜{_md(shipment.window_to)} に入りません")

    # 集荷の地点（市区町村の代表点どうしの直線距離）。便の pickup_code が無ければ出発地、上限が無ければ 20km。
    # 荷物に出発地（origin_code）が無いときだけ調べない
    if shipment.origin_code:
        pts = geo.load_points()
        a, b = pts.get(shipment.origin_code), pts.get(trip.get("pickup_code") or trip.get("origin_code"))
        if a and b:
            km = geo.haversine_km(a["lon"], a["lat"], b["lon"], b["lat"])
            radius = _dec(trip["pickup_radius_km"] if trip.get("pickup_radius_km") is not None else DEFAULT_PICKUP_RADIUS_KM)
            if km > float(radius):
                reasons.append(f"集荷の地点が便の空く地点から {km:.1f}km で、この便の上限 {_fmt(radius)}km を超えます")

    # 床の合計
    if trip.get("floor_free_m2") is not None:
        free = _dec(trip["floor_free_m2"] if floor_m2 is None else floor_m2)
        need = shipment.total_floor_m2
        if need > free:
            reasons.append(f"床の合計 {need:.2f}m² がこの便の床の空き {free:.2f}m² を超えます")

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
    if not shipment.is_box and not trip.get("accepts_unboxed"):
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
    floor_m2: Decimal = Decimal(0)
    shipment: BShipment | None = None
    shipper: dict[str, Any] | None = None  # 荷主の名称・住所・法人番号（架空、書面の下書きに使う）


@dataclass
class _Cap:
    slack: Decimal
    shelf: Decimal | None
    floor: Decimal | None = None  # 床の空き m²（便に floor_free_m2 があるときだけ）


class Board:
    """便ごとの重さの余りと仮予約をメモリに持つ。操作はすべて Lock の中で行う。"""

    def __init__(self, trips: list[dict[str, Any]] | None = None, clock: Callable[[], datetime] | None = None):
        self._clock = clock or datetime.now
        self._lock = threading.Lock()
        self._base_trips = list(trips if trips is not None else load_trips())
        self._trips = list(self._base_trips)
        self._by_id = {t["id"]: t for t in self._trips}
        self._reset_locked()

    def _reset_locked(self) -> None:
        self._trips = list(self._base_trips)  # 登録した便（SPEC §31.4）は戻すときに消える
        self._by_id = {t["id"]: t for t in self._trips}
        self._reg_seq = 0
        self._cap = {t["id"]: _Cap(_dec(t["weight_slack_kg"]),
                                   _dec(t["shelf_rated_kg"]) if t["space_type"] in SHELF_TYPES else None,
                                   _dec(t["floor_free_m2"]) if t.get("floor_free_m2") is not None else None)
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
        if cap.floor is not None:
            cap.floor += h.floor_m2

    def _trip(self, trip_id: Any) -> dict[str, Any]:
        t = self._by_id.get(trip_id) if isinstance(trip_id, str) else None
        if t is None:
            raise BoardError(f"便 {trip_id!r} が見つかりません")
        return t

    @staticmethod
    def _hold_dict(h: _Hold) -> dict[str, Any]:
        return {"hold_id": h.id, "trip_id": h.trip_id, "state": h.state, "weight_kg": float(h.weight_kg),
                "floor_m2": float(h.floor_m2),
                "created_at": h.created_at.isoformat(timespec="seconds"),
                "expires_at": h.expires_at.isoformat(timespec="seconds")}

    def _trip_dict(self, t: dict[str, Any]) -> dict[str, Any]:
        cap = self._cap[t["id"]]
        d = dict(t)
        d["remaining_slack_kg"] = float(cap.slack)
        d["remaining_shelf_kg"] = float(cap.shelf) if cap.shelf is not None else None
        d["remaining_floor_m2"] = float(cap.floor) if cap.floor is not None else None
        return d

    # --- 公開
    def trips(self) -> list[dict[str, Any]]:
        """便の一覧（いまの残りつき）。"""
        with self._lock:
            self._sweep()
            return [self._trip_dict(t) for t in self._trips]

    def trip(self, trip_id: Any) -> dict[str, Any]:
        """便 1 件（登録値のまま。残りは含まない）。見つからなければ BoardError。"""
        with self._lock:
            return dict(self._trip(trip_id))

    def extra_carriers(self) -> list[dict[str, Any]]:
        """登録した便があるときだけ、その運送会社（架空 自社）を返す。"""
        with self._lock:
            if not any(t.get("registered") for t in self._trips):
                return []
            return [dict(SELF_CARRIER)]

    def register(self, trip: dict[str, Any]) -> dict[str, Any]:
        """検証済みの便を足す（SPEC §31.4）。この盤（来訪者）の中だけ。上限 MAX_REGISTERED 件。"""
        with self._lock:
            n = sum(1 for t in self._trips if t.get("registered"))
            if n >= MAX_REGISTERED:
                raise BoardError(f"登録できる便は 1 人 {MAX_REGISTERED} 件までです。「最初の状態に戻す」で消せます")
            self._reg_seq += 1
            t = dict(trip, id=f"R{self._reg_seq}")
            self._trips.append(t)
            self._by_id[t["id"]] = t
            self._cap[t["id"]] = _Cap(_dec(t["weight_slack_kg"]), None, None)
            return self._trip_dict(t)

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
                ok, reasons = match(shipment, t, cap.slack, cap.shelf, cap.floor)
                out.append({"trip_id": t["id"], "ok": ok, "reasons": reasons})
            return out

    def hold(self, trip_id: Any, shipment: BShipment, hold_minutes: Any = DEFAULT_HOLD_MINUTES,
             hold_until: Any = None, shipper: dict[str, Any] | None = None) -> dict[str, Any]:
        """期限付きの仮予約。照合と残りの減らしを1つの Lock の中で行う。合わなければ BoardError。

        期限は hold_minutes（分）か hold_until（現地時刻の ISO 日時）。hold_until があればそちらを使う。
        どちらも仮予約の時刻から MAX_HOLD_MINUTES（72 時間）まで。
        """
        if hold_until is None and (isinstance(hold_minutes, bool) or not isinstance(hold_minutes, (int, float))
                                   or not 0 < hold_minutes <= MAX_HOLD_MINUTES):
            raise BoardError(f"仮予約の期限（hold_minutes）は 0 より大きく {MAX_HOLD_MINUTES} 分以下で入れてください")
        with self._lock:
            self._sweep()
            trip = self._trip(trip_id)
            now = self._clock()
            expires = self._parse_until(hold_until, now) if hold_until is not None \
                else now + timedelta(minutes=hold_minutes)
            cap = self._cap[trip["id"]]
            ok, reasons = match(shipment, trip, cap.slack, cap.shelf, cap.floor)
            if not ok:
                raise BoardError("この便には仮予約できません: " + "。".join(reasons))
            weight, floor = shipment.total_kg, shipment.total_floor_m2
            cap.slack -= weight
            if cap.shelf is not None:
                cap.shelf -= weight
            if cap.floor is not None:
                cap.floor -= floor
            self._seq += 1
            h = _Hold(f"H{self._seq}", trip["id"], weight, now, expires,
                      floor_m2=floor if cap.floor is not None else Decimal(0), shipment=shipment, shipper=shipper)
            self._holds[h.id] = h
            return self._hold_dict(h)

    @staticmethod
    def _parse_until(v: Any, now: datetime) -> datetime:
        try:
            if not isinstance(v, str):
                raise ValueError
            t = datetime.fromisoformat(v)
        except ValueError:
            raise BoardError("締切（hold_until）は 2026-11-10T17:00 のような日時で入れてください") from None
        if t.tzinfo is not None:
            t = t.astimezone().replace(tzinfo=None)
        if t <= now:
            raise BoardError("締切（hold_until）は仮予約の時刻より後にしてください")
        if t - now > timedelta(minutes=MAX_HOLD_MINUTES):
            raise BoardError(f"締切（hold_until）は仮予約の時刻から {MAX_HOLD_MINUTES // 60} 時間以内にしてください")
        return t

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

    # --- 書面の下書き（SPEC §21.3）
    def contract(self, hold_id: Any, fuel_surcharge: Any = None, payment_method: Any = None) -> dict[str, Any]:
        """承諾済みの仮予約から、運送会社と荷主の間の書面の下書きを作る。承諾前は BoardError。

        燃料サーチャージなどの料金と支払方法は運送会社の役が選ぶ。選ばれていない項目は missing に入れ、ready を false にする。
        """
        with self._lock:
            self._sweep()
            h = self._holds.get(hold_id) if isinstance(hold_id, str) else None
            if h is None:
                raise BoardError(f"仮予約 {hold_id!r} が見つかりません")
            if h.state != ACCEPTED:
                raise BoardError("運送会社が承諾した仮予約だけ、書面の下書きを作れます")
            trip = self._by_id[h.trip_id]
            carrier = next((c for c in load_demo()["carriers"] + [SELF_CARRIER] if c["id"] == trip["carrier_id"]), {})
            options = carrier.get("payment_options") or []
            fuel = _text_or_none(fuel_surcharge, "fuel_surcharge")
            pay = _text_or_none(payment_method, "payment_method")
            if pay is not None and options and pay not in options:
                raise BoardError("支払方法は運送会社が示した選択肢から選んでください")
            return _contract_draft(h, trip, carrier, fuel, pay, self._clock().date())


def _text_or_none(v: Any, name: str) -> str | None:
    if v is None or v == "":
        return None
    if not isinstance(v, str) or len(v) > 200:
        raise BoardError(f"{name} は 200 文字以内の文字列で入れてください")
    return v.strip() or None


# 改正法の書面の項目（SPEC §21.3）。画面の並びもこの順
LEGAL_KEYS = ("transport", "ancillary", "surcharge", "parties", "payment", "issued_on")
CONTRACT_NOTICE = "この書面は運送会社と荷主の間で交わすものです。Backfull は運送の当事者ではなく、運賃を受け取りません。"


def muni_name(code: str) -> str:
    p = geo.load_points().get(code)
    return f"{p['pref']}{p['name']}" if p else code


def _contract_draft(h: _Hold, trip: dict[str, Any], carrier: dict[str, Any], fuel: str | None, pay: str | None,
                    today: date) -> dict[str, Any]:
    s, sh = h.shipment, (h.shipper or {})
    pieces = s.pieces if s else ()

    def hm(v: str) -> str:
        d = datetime.fromisoformat(v)
        return f"{d.month}/{d.day} {d:%H:%M}"

    cargo = "、".join(f"{_fmt(p.length_mm / 10)}×{_fmt(p.width_mm / 10)}×{_fmt(p.height_mm / 10)}cm {_fmt(p.weight_kg)}kg を {p.count} 点"
                      for p in pieces)
    price = f"{trip['price_ex_tax_yen']:,}円（税抜）／{trip['price_unit']}"
    transport = (f"{muni_name(trip['origin_code'])} から {muni_name(trip['dest_code'])} まで。{hm(trip['depart_at'])} 発、"
                 f"{hm(trip['arrive_at'])} 着の予定。荷物: {cargo}。対価: {price}")
    heavy = max((p.weight_kg for p in pieces), default=Decimal(0))
    handling = ("1個 %skg の荷があるため、積む側と降ろす側がそれぞれ 2 人を出す" % _fmt(heavy)) if heavy >= 100 \
        else "積み降ろしは運送会社と荷主で当日に決める"
    carrier_party = f"運送会社: {carrier.get('name', '')}"
    if carrier.get("address"):
        carrier_party += f"（{carrier['address']}）"
    shipper_party = f"荷主: {sh['name']}（{sh['address']}）" if sh.get("name") and sh.get("address") else None
    items = [
        {"key": "transport", "legal": True, "label": "運送の内容と対価", "value": transport},
        {"key": "ancillary", "legal": True, "label": "附帯業務の対価",
         "value": trip.get("ancillary_note") or "別料金なし（積み込み・荷降ろしの人手は荷役の欄のとおり）"},
        {"key": "surcharge", "legal": True, "label": "燃料サーチャージなどの料金", "value": fuel, "by": "carrier"},
        {"key": "parties", "legal": True, "label": "当事者の名称と住所",
         "value": f"{carrier_party}／{shipper_party}" if carrier.get("address") and shipper_party else None},
        {"key": "payment", "legal": True, "label": "支払方法", "value": pay, "by": "carrier",
         "options": carrier.get("payment_options") or []},
        {"key": "issued_on", "legal": True, "label": "交付日", "value": f"{today.year}/{today.month}/{today.day}"},
        {"key": "handling", "legal": False, "label": "荷役の人手", "value": handling},
        {"key": "cancel", "legal": False, "label": "取消しの条件", "value": carrier.get("cancel_terms")},
        {"key": "terms", "legal": False, "label": "運送約款", "value": carrier.get("terms")},
        {"key": "insurance", "legal": False, "label": "貨物保険", "value": carrier.get("insurance")},
        {"key": "license", "legal": False, "label": "許可番号", "value": carrier.get("license_no")},
        {"key": "corp_no", "legal": False, "label": "荷主の法人番号（架空の番号）", "value": sh.get("corporate_number")},
    ]
    missing = [i["label"] for i in items if not i["value"]]
    return {"hold_id": h.id, "trip_id": h.trip_id, "state": h.state, "items": items, "missing": missing,
            "ready": not missing, "notice": CONTRACT_NOTICE, "fiction_notice": FICTION_NOTICE}


# ---------------------------------------------------------------- 帰り便の値段の式（SPEC §21.6）

DEFAULT_BASE_LOAD_RATE = Decimal("0.7")
FORMULA_NOTICE = ("標準的運賃（2024 年 3 月 22 日告示）の個建運賃とリードタイムの割引の考え方に当てた参考額です。"
                  "値段は運送会社が決めます")
FORMULA_TEXT = "個建運賃の参考額 = 距離制運賃 × max（重さの割合, 床の割合）÷ 基準積載率 ×（1 − リードタイムの割引）"
MIN_FEE_NOTE = "最低保証料を下回るときは最低保証料"
LEAD_DISCOUNTS = tuple(Decimal(i) / 10 for i in range(0, 6))  # 0〜50% を 10% 刻み


def _num(v: Any) -> Decimal | None:
    """数か、数の文字列を Decimal にする。読めなければ None。"""
    if isinstance(v, str):
        try:
            d = Decimal(v.strip())
        except ArithmeticError:
            return None
        return d if d.is_finite() else None
    return to_decimal(v)


def trip_road_km(trip: dict[str, Any]) -> tuple[Decimal, str | None]:
    """便の発地 → 着地の道路距離。荷を積む区間の距離は road_km を使う（SPEC §21.7）。"""
    return road_km(str(trip["origin_code"]), str(trip["dest_code"]))


def road_km(oc: str, dc: str) -> tuple[Decimal, str | None]:
    """発地 → 着地の道路距離（OSRM の事前計算、km）と、代用したときの注記。

    事前計算の出発地は関東の 7 か所だけなので、それ以外（江東区など）は最寄りの出発地（新宿区）の表で代用する。"""
    note = None
    if oc not in geo.ORIGINS:
        pts = geo.load_points()
        if oc not in pts:
            raise ValueError(f"発地 {oc} の位置が分かりません")
        a = pts[oc]
        used = min(geo.ORIGINS, key=lambda c: geo.haversine_km(a["lon"], a["lat"], pts[c]["lon"], pts[c]["lat"]))
        note = f"距離は最寄りの出発地（{pts[used]['name']}）の値で代用"
        oc = used
    info = geo.drive_info(oc, dc)
    if info is None or info["no_route"] or info["ferry"] or info.get("km") is None:
        raise ValueError("この区間の道路距離がありません（離島やフェリーを含む経路）")
    return Decimal(str(info["km"])), note


def per_unit_reference(trip: dict[str, Any], shipment: BShipment, base_load_rate: Any = DEFAULT_BASE_LOAD_RATE,
                       lead_discount: Any = 0, region: str = "近畿", km: Decimal | None = None,
                       km_note: str | None = None, min_fee_yen: Any = None) -> dict[str, Any]:
    """個建運賃の参考額（SPEC §21.6）。

    参考額 = 距離制運賃 × max(重さの割合, 床の割合) ÷ 基準積載率 × (1 − リードタイムの割引)。
    重さの割合 = 荷の重さ ÷ 最大積載量、床の割合 = 荷の床面 ÷ 荷台の床面。
    距離制運賃は region の運輸局の表を kashikiri_fares に当てる（車格は最大積載量が載る最小のもの）。
    距離は荷を積む区間（荷の集荷の地点 → 荷の届け先）の道路距離（SPEC §21.7）。集荷の地点が無い荷は便の発地を使う。
    km を渡さなければ road_km で出す（OSRM の事前計算。ここで通信はしない）。
    min_fee_yen（最低保証料、任意）があるときは max(式の額, 最低保証料)。誤りは ValueError（日本語）。"""
    rate, disc = _num(base_load_rate), _num(lead_discount)
    if rate is None or not Decimal("0") < rate <= Decimal("1"):
        raise ValueError("基準積載率は 0 より大きく 100% 以下で入れてください")
    if disc is None or disc not in LEAD_DISCOUNTS:
        raise ValueError("リードタイムの割引は 0〜50% を 10% 刻みで選んでください")
    if region not in KASHIKIRI_REGION_FILES:
        raise ValueError("地域の表は 近畿 か 関東 から選んでください")
    max_kg, floor_m2 = _num(trip.get("max_load_kg")), _num(trip.get("floor_total_m2"))
    if not max_kg or max_kg <= 0 or not floor_m2 or floor_m2 <= 0:
        raise ValueError("この便には最大積載量と荷台の床面積が登録されていないため、式を出せません")
    floor_fee = None
    if min_fee_yen not in (None, ""):
        floor_fee = _num(min_fee_yen)
        if floor_fee is None or floor_fee < 0 or floor_fee > Decimal(10_000_000):
            raise ValueError("最低保証料は 0 円以上の数で入れてください")
    if km is None:
        km, km_note = road_km(str(shipment.origin_code or trip["origin_code"]), str(shipment.dest_code))
    tariff = load_kashikiri(region)
    if km < Decimal(tariff["base_km"]):
        raise ValueError(f"距離 {km}km は距離制運賃の基本距離（{tariff['base_km']}km）に届きません")
    fares = kashikiri_fares(km, max_kg, tariff)
    if not fares:
        raise ValueError("最大積載量が表の車格を超えています")
    fare = fares[0]  # 最大積載量が載る最小の車格
    weight_ratio = shipment.total_kg / max_kg
    floor_ratio = shipment.total_floor_m2 / floor_m2
    ratio = max(weight_ratio, floor_ratio)
    raw = Decimal(fare["amount"]) * ratio / rate * (1 - disc)
    return {
        "formula": FORMULA_TEXT, "notice": FORMULA_NOTICE,
        "trip_id": trip.get("id"), "region": region, "tariff_class": fare["class"],
        "km": float(km), "km_note": km_note,
        "segment_origin_code": str(shipment.origin_code or trip["origin_code"]), "segment_dest_code": str(shipment.dest_code),
        "distance_fare_yen": fare["amount"],
        "steps": fare["steps"], "steps_over_500": fare["steps_over_500"],
        "cargo_kg": float(shipment.total_kg), "max_load_kg": float(max_kg),
        "cargo_floor_m2": float(shipment.total_floor_m2), "floor_total_m2": float(floor_m2),
        "weight_ratio": float(weight_ratio), "floor_ratio": float(floor_ratio),
        "ratio": float(ratio), "ratio_by": "床" if floor_ratio >= weight_ratio else "重さ",
        "base_load_rate": float(rate), "lead_discount": float(disc),
        "formula_yen": round_yen(raw),
        "min_fee_yen": round_yen(floor_fee) if floor_fee is not None else None,
        "min_fee_applied": floor_fee is not None and round_yen(raw) < round_yen(floor_fee),
        "min_fee_note": MIN_FEE_NOTE,
        "reference_yen": max(round_yen(raw), round_yen(floor_fee)) if floor_fee is not None else round_yen(raw),
        "trip_price_yen": trip.get("price_ex_tax_yen"),
    }


# ---------------------------------------------------------------- 運送会社が帰り便を登録する（SPEC §31.4）

MAX_REGISTERED = 10
SELF_CARRIER_ID = "C9"
SELF_CARRIER = {"id": SELF_CARRIER_ID, "name": "架空 自社", "note": "運送会社の役で登録した便の運送会社です（この来訪者の画面だけ）",
                "tariff_region": "関東"}
REGISTER_VEHICLES: dict[str, dict[str, Any]] = {
    "2t": {"label": "架空 2t 箱車", "max_load_kg": 2000, "floor_total_m2": 6.0, "footprint_mm": [3100, 1770],
           "clear_height_mm": 1800, "max_piece_kg": 150, "weight_slack_kg": 1000},
    "4t": {"label": "架空 4t ウイング", "max_load_kg": 3500, "floor_total_m2": 13.0, "footprint_mm": [6000, 2000],
           "clear_height_mm": 2100, "max_piece_kg": 300, "weight_slack_kg": 2000},
    "10t": {"label": "架空 10t ウイング", "max_load_kg": 9000, "floor_total_m2": 28.0, "footprint_mm": [9500, 2300],
            "clear_height_mm": 2300, "max_piece_kg": 500, "weight_slack_kg": 4500},
}
KINKI_PREFS = ("福井県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県")
REGISTER_PRICE_BASIS = ("値段の式（SPEC §21.6）の既定値（基準積載率 70%、リードタイムの割引 0%）で、いまの荷を載せたときの参考額です。"
                        "値段は運送会社が決めます")


def register_region(origin_code: str) -> str:
    """運賃の表は関東と近畿の 2 つだけ。発地が近畿の県なら近畿、それ以外は関東の表を当てる。"""
    p = geo.load_points().get(origin_code)
    return "近畿" if p and p["pref"] in KINKI_PREFS else "関東"


def _code(v: Any, label: str) -> str:
    if not isinstance(v, str) or v not in geo.load_points():
        raise ValueError(f"{label}の市区町村が見つかりません")
    return v


def _num_in(v: Any, label: str, lo: float, hi: float) -> Decimal:
    d = _num(v)
    if d is None or d < Decimal(str(lo)) or d > Decimal(str(hi)):
        raise ValueError(f"{label}は {lo:g}〜{hi:g} の数で入れてください")
    return d


def _local_dt(v: Any, label: str) -> datetime:
    try:
        if not isinstance(v, str) or not v:
            raise ValueError
        return datetime.fromisoformat(v).replace(tzinfo=None, second=0, microsecond=0)
    except ValueError:
        raise ValueError(f"{label}は 2026-11-13T17:00 のような日時で入れてください") from None


def build_registered_trip(body: dict[str, Any]) -> dict[str, Any]:
    """登録の入力から便の辞書を作る（値段と id 以外）。誤りは ValueError（日本語）。"""
    o, d = _code(body.get("origin_code"), "発地"), _code(body.get("dest_code"), "着地")
    if o == d:
        raise ValueError("発地と着地が同じ市区町村です")
    veh_key = body.get("vehicle")
    veh = REGISTER_VEHICLES.get(veh_key) if isinstance(veh_key, str) else None
    if veh is None:
        raise ValueError("車種は " + "・".join(v["label"] for v in REGISTER_VEHICLES.values()) + " から選んでください")
    dep, arr = _local_dt(body.get("depart_at"), "出発日時"), _local_dt(body.get("arrive_at"), "到着日時")
    if arr <= dep:
        raise ValueError("到着日時は出発日時より後にしてください")
    if arr - dep > timedelta(days=7):
        raise ValueError("出発から到着までは 7 日以内にしてください")
    slack = _num_in(body.get("weight_slack_kg"), "重さの余り（kg）", 1, veh["max_load_kg"])
    fp = body.get("unit_footprint_mm")
    if not isinstance(fp, (list, tuple)) or len(fp) != 2:
        raise ValueError("1口の底面は縦と横の 2 つの数で入れてください")
    fw, fd = _num_in(fp[0], "1口の底面の縦（mm）", 100, 20000), _num_in(fp[1], "1口の底面の横（mm）", 100, 20000)
    height = _num_in(body.get("clear_height_mm"), "空きの高さ（mm）", 100, 4000)
    piece = _num_in(body.get("max_piece_kg"), "1個の重さの上限（kg）", 1, veh["max_load_kg"])
    drops = body.get("drop_points")
    if not isinstance(drops, list) or not drops or len(drops) > 12:
        raise ValueError("降ろせる地点は 1〜12 か所入れてください")
    drops = list(dict.fromkeys(_code(c, "降ろせる地点") for c in drops))
    return {
        "carrier_id": SELF_CARRIER_ID, "carrier_name": SELF_CARRIER["name"], "vehicle": veh["label"],
        "route_label": f"{muni_name(o)}→{muni_name(d)}（登録した帰り便）",
        "origin_code": o, "dest_code": d, "depart_at": dep.strftime("%Y-%m-%dT%H:%M"), "arrive_at": arr.strftime("%Y-%m-%dT%H:%M"),
        "space_type": "free_bed", "clear_height_mm": int(height), "unit_footprint_mm": [int(fw), int(fd)],
        "max_load_kg": veh["max_load_kg"], "floor_total_m2": veh["floor_total_m2"],
        "max_piece_kg": float(piece), "weight_slack_kg": float(slack), "drop_points": drops,
        "container_required": False, "rb_owner_consent": False, "max_upper_per_rb_kg": None,
        "shelf_fixed": False, "shelf_rated_kg": None, "price_unit": "1件", "refused_categories": [],
        "registered": True, "tariff_region": register_region(o),
    }


def default_register_price(trip: dict[str, Any], cargo: BShipment) -> dict[str, Any]:
    """登録する便の値段の既定値（§21.6 の式。基準積載率 70%、割引 0%）。距離は便の発地 → 着地の道路距離。
    式を出せないとき（海を渡る経路など）は price を None にして理由を返す。"""
    try:
        km, note = road_km(trip["origin_code"], trip["dest_code"])
        ref = per_unit_reference(trip, BShipment(cargo.pieces, trip["dest_code"], origin_code=trip["origin_code"]),
                                 DEFAULT_BASE_LOAD_RATE, 0, trip.get("tariff_region") or "関東", km=km, km_note=note)
    except ValueError as e:
        return {"price_ex_tax_yen": None, "basis": f"値段の既定値を出せません（{e}）。値段を入れてください", "reference": None}
    price = int(round(ref["reference_yen"] / 100) * 100)  # 百円の単位に丸める（入力しやすくするため）
    return {"price_ex_tax_yen": price, "reference": ref,
            "basis": f"{REGISTER_PRICE_BASIS}。{ref['region']}運輸局 {ref['tariff_class']}、道路 {ref['km']:,.0f}km"
                     + (f"。{note}" if note else "")}
