"""積付けの 3D 表示のための配置計算（SPEC §20）。

荷物を荷室に並べる位置を、決まった手順で計算する。同じ入力なら必ず同じ結果になる（乱数・時刻を使わない）。
結果は「入るかどうかの目安」であり、実際の積付け・固縛・荷役は運送会社が決める。

手順（説明できる単純なもの）:
  1. 個数を1個ずつに展開し、床の占有面積の大きい順（同じなら高い順、それも同じなら入力の順）に並べる。
  2. 床に「棚」詰めで置く。荷室の長さ方向（x）に棚を並べ、1つの棚の中では幅方向（y）に並べる。
     棚の奥行は、その棚の最初の1個で決まる。向きは床の上で 90 度回すことだけを許し、傾けない。
  3. 床に置けなかった荷を、積み重ねてよい場合だけ、既に置いた荷の真上に載せる（下の荷の床面に収まるときだけ）。
     載せる先は、いちばん低い山を先に選ぶ。高さが荷室の内高を超える積み方はしない。
  4. それでも置けない荷は「置けなかった荷」として理由つきで返す。
重さは荷室の最大積載量と合計で比べるだけで、軸重・重心・荷崩れは見ていない。

単位は mm（入力の cm は 10 倍して四捨五入）。座標は荷室の床の手前左下を原点とし、
x が長さ、y が幅、z が高さ。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .models import Shipment, to_decimal
from .tariffs import deck_of, load_tariff

MAX_PIECES = 3000  # 1回の計算で扱う個数の上限（画面が重くならないため）

# 画面に出す注意（SPEC §20.4）
LIMIT_NOTE = "目安です。実際の積付け・固縛・荷役は運送会社に確認してください。"


@dataclass(frozen=True)
class Container:
    """荷室の寸法（内寸 mm）と最大積載量。basis は数値の確かさ、note は出典と注意。"""

    id: str
    name: str
    length_mm: int
    width_mm: int
    height_mm: int
    payload_kg: int
    basis: str  # 「公表例」「目安」「推定」のどれか
    note: str
    sellable_slot: bool = True  # False は販売枠ではなく積付けの属性（RB 上部）

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "length_mm": self.length_mm, "width_mm": self.width_mm,
            "height_mm": self.height_mm, "payload_kg": self.payload_kg, "basis": self.basis, "note": self.note,
            "sellable_slot": self.sellable_slot,
        }


def _box_container() -> Container:
    """JITBOX のボックスの内寸と最大積載量。estimate.py と同じ data/tariffs/yamato_jitbox.json から読む（別の数値を持たない）。"""
    box = load_tariff("jitbox")["box"]
    ln, wd, ht = (int(x) for x in box["inner_mm"])
    return Container("jitbox", "ボックス（JITBOX 1本）", ln, wd, ht, int(box["max_kg"]), "公表例",
                     "提供会社の公表値（内寸 1,040×1,040×1,700mm、最大積載量 500kg）。"
                     "最初の計画資料 の 04 と 05 に出典。")


def _deck(deck_id: str) -> tuple[int, int, int, int]:
    """貸切の車の荷台（長さ, 幅, 高さ mm と最大積載量 kg）。data/vehicle_decks.json から読む（比べるページ・空きを探すと共通の値）。"""
    d = deck_of(deck_id)
    return int(d["length_mm"]), int(d["width_mm"]), int(d["height_mm"]), int(d["payload_kg"])


def containers() -> list[Container]:
    """選べる荷室の一覧。数値は目安で、実車は車種・架装で違う（05 の表も「全車種を代表する規格ではない」としている）。"""
    return [
        # 軽バンは公開された1例ではなく一般的な荷室の目安。車種で数％〜1割ほど違う。
        Container("kei_van", "軽バン荷室", 1800, 1300, 1200, 350, "目安",
                  "一般的な軽バンの荷室の目安（長さ・幅・高さとも車種で数％〜1割違う）。最大積載量 350kg は軽貨物の上限。"),
        # 05 の表「2tショート箱・オーシャンリンクス」の1行をそのまま使う（行をまたいで組み合わせない）。
        Container("box_2t_short", "2t 箱車（ショート）", *_deck("box_2t_short"), "公表例",
                  "05 の表の公開例（オーシャンリンクス）。最大積載量は約 2,000kg。"),
        # 05 の表「2t・テスココンポ」の1行。
        Container("box_2t", "2t 箱車（標準）", *_deck("box_2t"), "公表例",
                  "05 の表の公開例（テスココンポ）。同じ 2t でも荷室高は 1,700〜2,000mm の幅がある。"),
        # 05 の表「4tウイング・オーシャンリンクス」の1行。
        Container("wing_4t", "4t ウイング", *_deck("wing_4t"), "公表例",
                  "05 の表の公開例（オーシャンリンクス）。最大積載量は約 2,700kg。ゲート装備・自重で変わる。"),
        # 05 の表「10tウイング・オーシャンリンクス」の1行。
        Container("wing_10t", "10t ウイング", *_deck("wing_10t"), "公表例",
                  "05 の表の公開例（オーシャンリンクス）。最大積載量は約 13,500kg。"),
        # 04 の JR 貨物の説明: 19D 形式は内寸 3,647×2,275×2,252mm、積載重量 5,000kg。
        Container("jr_12ft", "JR 12ft コンテナ", 3647, 2275, 2252, 5000, "公表例",
                  "JR 貨物の 19D 形式の内寸と積載重量（04 に記載）。実際は扉の開口 3,635×2,187mm が先に効くことがある。"),
        # 31ft は repo 内に一次資料がない。JR 貨物の 31ft の内寸・積載量の目安を丸めた値（公式の表で要確認）。
        Container("jr_31ft", "JR 31ft コンテナ", 9200, 2300, 2250, 8000, "目安",
                  "31ft コンテナの内寸・積載量の目安を丸めた値。形式（U47・U54 など）で違うため、JR 貨物の資料で要確認。"),
        # カゴ台車は依頼の「約 800×600×1700」の目安。05 のワコー WKP-1180（内寸 1040×745×1450）とは別。
        Container("roll_cage", "カゴ台車", 800, 600, 1700, 300, "目安",
                  "一般的なカゴ台車の内寸の目安（約 800×600×1700mm）。05 の公開例（内寸 1040×745×1450、積載 500kg）とは別。"),
        _box_container(),
        # RB 上部: 05 の「RB 1台の占有床面に対応する上部空間」。販売枠ではなく積付けの属性として扱う。
        # 上段有効高は 05 の 4t 行の範囲の上端（RB 外高 1700 → 200〜500、外高 2000 → 0〜200）を使う。
        # 控除寸法 200〜300mm は 05 で AI が置いた感度分析の仮定であり、法令の値でも機材の設計値でもない。
        Container("rb_upper_1700", "RB 上部の空間（RB 外高 1700・4t）", 1100, 800, 500, 300, "推定",
                  "推定・販売枠ではありません。05 の 4t・RB 外高 1700mm の上段有効高 200〜500mm の上端を使用。"
                  "最大積載 300kg は棚デッキ 1 セットの耐荷重で、RB の積載荷重ではない。実機の図面で置き換える。",
                  sellable_slot=False),
        Container("rb_upper_2000", "RB 上部の空間（RB 外高 2000・4t）", 1100, 1100, 200, 300, "推定",
                  "推定・販売枠ではありません。05 の 4t・RB 外高 2000mm（JITBOX 型）の上段有効高 0〜200mm の上端を使用。"
                  "下限の 0mm では荷を扱えない。最大積載 300kg は棚デッキ 1 セットの耐荷重。",
                  sellable_slot=False),
    ]


def container_by_id(cid: str) -> Container:
    for c in containers():
        if c.id == cid:
            return c
    raise ValueError(f"荷室 {cid!r} はありません")


@dataclass
class LoadItem:
    """積付けの入力1行。寸法は cm、重さは 1 個 kg。stackable は他の荷を上に載せてよい・他の荷の上に載せてよい。"""

    count: int
    length_cm: float
    width_cm: float
    height_cm: float
    weight_kg: float
    stackable: bool = True


def items_from_shipment(shipment: Shipment, stackable: bool = True) -> list[LoadItem]:
    """出荷条件の荷物の行を LoadItem にする。未入力・0 以下・個数が整数でない行は ValueError（日本語）。"""
    if not shipment.items:
        raise ValueError("荷物の行がありません")
    out: list[LoadItem] = []
    for n, it in enumerate(shipment.items, start=1):
        missing, invalid = it.problems(n)
        if missing:
            raise ValueError("未入力の項目があります: " + "、".join(missing))
        if invalid:
            raise ValueError("0 以下または不正な項目があります: " + "、".join(invalid))
        out.append(LoadItem(int(it.count), float(it.length_cm), float(it.width_cm), float(it.height_cm),
                            float(it.weight_kg), stackable))
    return out


def _mm(cm: float) -> int:
    d = to_decimal(cm)
    if d is None or d <= 0:
        raise ValueError("寸法は 0 より大きい数で指定してください")
    return max(1, int((d * 10).quantize(Decimal(1), rounding=ROUND_HALF_UP)))


@dataclass
class _Box:
    item: int  # 入力の行（0 始まり）
    nth: int  # その行の何個目か（0 始まり）
    l: int
    w: int
    h: int
    stackable: bool
    seq: int  # 展開した順（並べ替えの最後の決め手）


@dataclass
class _Placed:
    box: _Box
    x: int
    y: int
    z: int
    dx: int
    dy: int
    rotated: bool


def _orientations(b: _Box) -> list[tuple[int, int, bool]]:
    """床の上での向き（x 方向の長さ, y 方向の長さ, 回したか）。傾けない。"""
    o = [(b.l, b.w, False)]
    if b.l != b.w:
        o.append((b.w, b.l, True))
    return o


# 試す選び方（SPEC §34.4）: (並べる順の鍵, 新しい棚で長い辺を長さ方向に向けるか)。最初のものが従来の手順。
_STRATEGIES: list[tuple[Any, bool]] = [
    (lambda b: (-(b.l * b.w), -b.h, b.seq), False),
    (lambda b: (-(b.l * b.w), -b.h, b.seq), True),
    (lambda b: (-max(b.l, b.w), -(b.l * b.w), -b.h, b.seq), True),
    (lambda b: (-max(b.l, b.w), -(b.l * b.w), -b.h, b.seq), False),
    (lambda b: (-b.h, -(b.l * b.w), b.seq), True),
    (lambda b: (-min(b.l, b.w), -(b.l * b.w), -b.h, b.seq), True),
]


def _run_plan(order: list[_Box], L: int, W: int, H: int, long_x: bool) -> tuple[list[_Placed], dict[int, str]]:
    """決まった順の荷を、棚詰め → 積み重ねの順に置く。long_x が True なら、新しい棚で長い辺を荷室の長さ方向に向ける。"""
    placed: list[_Placed] = []
    reasons: dict[int, str] = {}  # seq → 置けなかった理由
    shelves: list[list[int]] = []  # [x0, 奥行, 使った幅]
    next_x = 0
    leftover: list[_Box] = []

    for b in order:
        if b.h > H:
            reasons[b.seq] = f"高さ {b.h}mm が荷室の内高 {H}mm を超えます"
            continue
        if not any(dx <= L and dy <= W for dx, dy, _ in _orientations(b)):
            reasons[b.seq] = f"床の寸法 {b.l}×{b.w}mm が荷室の床 {L}×{W}mm に入りません（床の上で回しても入らない）"
            continue
        done = False
        for sh in shelves:  # 既存の棚に入れる。入る向きのうち幅を使わない方を選ぶ
            cands = [(dy, dx, rot) for dx, dy, rot in _orientations(b) if dx <= sh[1] and sh[2] + dy <= W]
            if cands:
                dy, dx, rot = min(cands, key=lambda c: (c[0], c[2]))
                placed.append(_Placed(b, sh[0], sh[2], 0, dx, dy, rot))
                sh[2] += dy
                done = True
                break
        if done:
            continue
        cands2 = [(dx, dy, rot) for dx, dy, rot in _orientations(b) if next_x + dx <= L and dy <= W]
        if cands2:  # 新しい棚。入る向きのうち長さを使わない方を選ぶ
            dx, dy, rot = min(cands2, key=lambda c: ((-c[0] if long_x else c[0]), c[2]))
            shelves.append([next_x, dx, dy])
            placed.append(_Placed(b, next_x, 0, 0, dx, dy, rot))
            next_x += dx
            continue
        leftover.append(b)

    # 床に置けなかった荷を、積み重ねてよいものだけ既存の山の上に載せる
    stacks: list[_Placed] = list(placed)  # 各山の一番上の荷（山ごとに1つ）
    for b in leftover:
        if not b.stackable:
            reasons[b.seq] = "床に空きがなく、積み重ね不可の荷です"
            continue
        if not any(t.box.stackable for t in stacks):
            reasons[b.seq] = "下の荷がすべて積み重ね不可のため、載せる場所がありません"
            continue
        best: tuple[int, int, bool] | None = None  # (山の番号, 上面の z, 回転)
        best_key: tuple[int, int] | None = None
        for si, top in enumerate(stacks):
            if not top.box.stackable:
                continue
            z = top.z + top.box.h
            if z + b.h > H:
                continue
            for dx, dy, rot in _orientations(b):
                if dx <= top.dx and dy <= top.dy:
                    key = (z, si)  # いちばん低い山、同じなら先に作った山
                    if best_key is None or key < best_key:
                        best_key, best = key, (si, z, rot)
                    break
        if best is None:
            reasons[b.seq] = "床に空きがなく、積み重ねる余地（高さまたは下の荷の広さ）もありません"
            continue
        si, z, rot = best
        top = stacks[si]
        dx, dy = (b.w, b.l) if rot else (b.l, b.w)
        p = _Placed(b, top.x, top.y, z, dx, dy, rot)
        placed.append(p)
        stacks[si] = p
    return placed, reasons


LOAD_UNCERTAIN = "配置の確認が必要"  # △ のとき: 手順では置き方を見つけられなかっただけで、載らないとは言い切れない

# 料金の車格（告示の区分）と、積付けの車（公表例）は別のもの（SPEC §34.5）
WING_4T_ID = "wing_4t"
TARIFF_4T_LABEL = "4t 車の区分（〜4,000kg）"
LOAD_4T_LABEL = "4t ウイングの公表例（最大積載量 約 2,700kg）"
TARIFF_4T_MAX_KG = 4000
TARIFF_4T_NOTE = ("公表例の 4t ウイング（約 2,700kg）には載りません。"
                  "同じ 4t 車の区分でも、車によって最大積載量が違います")


def tariff_class_note(container_id: str, total_kg: float) -> str:
    """積付けの車が 4t ウイングの公表例で、重さがその最大積載量を超え、料金の 4t の区分には収まるときの文。それ以外は空。"""
    payload = next((c.payload_kg for c in containers() if c.id == WING_4T_ID), 0)
    if container_id == WING_4T_ID and payload < total_kg <= TARIFF_4T_MAX_KG:
        return TARIFF_4T_NOTE
    return ""


def _failed_verdict(boxes: list[_Box], weight_over: bool, container: Container, reasons: dict[int, str],
                    placed_seq: set[int], over_kg: float) -> tuple[str, str]:
    """全部は載らなかったときの判定。確実に載らない理由があるときだけ × にし、手順で見つけられなかっただけなら △ にする。"""
    L, W, H = container.length_mm, container.width_mm, container.height_mm
    if weight_over:
        return "×", f"重さが最大積載量を {over_kg:,.1f}kg 超えます"
    def fits_as_is(b: _Box) -> bool:
        return b.h <= H and any(dx <= L and dy <= W for dx, dy, _ in _orientations(b))

    def fits_if_tilted(b: _Box) -> bool:
        # 3 辺を並べ替えて荷室に収まるか（この手順では傾けないので、置けるとは限らない）
        return any(x <= L and y <= W and z <= H for x, y, z in itertools.permutations((b.l, b.w, b.h)))

    misfit = [b for b in boxes if not fits_as_is(b)]
    if any(not fits_if_tilted(b) for b in misfit):
        return "×", "1 個の寸法が荷室より大きい荷があります"
    # 積み重ね不可の荷は床をふさぐ。積み重ねてよい荷も、最も広い 1 個は床か、より広い荷の上に要る
    # （傾ければ入る荷は床の面積が決まらないので、ここでは数えない）
    fitting = [b for b in boxes if fits_as_is(b)]
    nonstack_area = sum(b.l * b.w for b in fitting if not b.stackable)
    max_stack_area = max((b.l * b.w for b in fitting if b.stackable), default=0)
    if nonstack_area + max_stack_area > L * W:
        if max_stack_area:
            return "×", "積み重ね不可の荷が床をふさぎ、積み重ねてよい荷を置く場所がありません（下の荷がすべて積み重ね不可です）"
        return "×", "積み重ね不可で、荷の床面積の合計が荷室の床より大きいです"
    if sum(b.l * b.w * b.h for b in boxes) > L * W * H:
        return "×", "荷の容積の合計が荷室の容積より大きいです"
    if misfit:
        return "△", LOAD_UNCERTAIN + "（立てれば入る可能性があります（この手順では傾けません））"
    return "△", LOAD_UNCERTAIN + "（手順では置き方を見つけられませんでしたが、載らないとは言い切れません）"


def plan_load(items: list[LoadItem], container: Container) -> dict[str, Any]:
    """荷物を荷室に配置する。戻りは placements（置いた順）、unplaced、summary、container の辞書。"""
    if not items:
        raise ValueError("荷物の行がありません")
    boxes: list[_Box] = []
    total_kg = Decimal(0)
    for i, it in enumerate(items):
        if isinstance(it.count, bool) or not isinstance(it.count, int) or it.count < 1:
            raise ValueError(f"荷物{i + 1}行目の個数は 1 以上の整数にしてください")
        wt = to_decimal(it.weight_kg)
        if wt is None or wt <= 0:
            raise ValueError(f"荷物{i + 1}行目の1個の重さは 0 より大きい数にしてください")
        if len(boxes) + it.count > MAX_PIECES:
            raise ValueError(f"個数の合計が多すぎます（{MAX_PIECES:,} 個まで）")
        l, w, h = _mm(it.length_cm), _mm(it.width_cm), _mm(it.height_cm)
        total_kg += wt * it.count
        for n in range(it.count):
            boxes.append(_Box(i, n, l, w, h, bool(it.stackable), len(boxes)))

    L, W, H = container.length_mm, container.width_mm, container.height_mm

    # 並べる順と向きの選び方を変えて何通りか試し、置けた数がいちばん多いものを使う（全部置けたら打ち切る）。
    # 選び方は固定の短い一覧で、乱数も時刻も使わない（SPEC §34.4）。
    best: tuple[list[_Placed], dict[int, str]] | None = None
    for key, long_x in _STRATEGIES:
        run = _run_plan(sorted(boxes, key=key), L, W, H, long_x)
        if best is None or len(run[0]) > len(best[0]):
            best = run
        if len(best[0]) == len(boxes):
            break
    assert best is not None
    placed, reasons = best

    placements = [
        {"order": n, "item": p.box.item, "nth": p.box.nth, "x": p.x, "y": p.y, "z": p.z,
         "l": p.dx, "w": p.dy, "h": p.box.h, "rotated": p.rotated}
        for n, p in enumerate(placed)
    ]

    placed_seq = {p.box.seq for p in placed}
    by_item: dict[int, dict[str, Any]] = {}
    for b in boxes:
        if b.seq in placed_seq:
            continue
        e = by_item.setdefault(b.item, {"item": b.item, "count": 0, "reason": reasons.get(b.seq, "置けませんでした")})
        e["count"] += 1
    unplaced = [by_item[k] for k in sorted(by_item)]

    n_total, n_placed = len(boxes), len(placed)
    floor_area = sum(p.dx * p.dy for p in placed if p.z == 0)
    vol = sum(p.dx * p.dy * p.box.h for p in placed)
    over = total_kg - Decimal(container.payload_kg)
    weight_ok = over <= 0
    fits = n_placed == n_total and weight_ok
    volume_pct = round(vol * 100 / (L * W * H), 1)
    verdict_reason = ""
    if not fits:
        verdict, verdict_reason = _failed_verdict(boxes, weight_over=not weight_ok, container=container, reasons=reasons,
                                                  placed_seq=placed_seq, over_kg=float(max(Decimal(0), over)))
    elif container.basis != "公表例" or volume_pct >= 90:
        verdict = "△"  # 載るが、数値が目安・推定の荷室か、容積の余裕が小さい
    else:
        verdict = "○"
    summary = {
        "fits": fits, "all_placed": n_placed == n_total, "requested": n_total, "placed": n_placed,
        "unplaced_count": n_total - n_placed,
        "floor_area_pct": round(floor_area * 100 / (L * W), 1),
        "volume_pct": volume_pct,
        "total_weight_kg": float(total_kg),
        "payload_kg": container.payload_kg,
        "weight_ok": weight_ok,
        "weight_over_kg": float(max(Decimal(0), over)),
        "stacked": any(p.z > 0 for p in placed),
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "note": LIMIT_NOTE,
    }
    tariff_note = tariff_class_note(container.id, float(total_kg))
    if tariff_note:
        summary["tariff_note"] = tariff_note
    # items: 要求した荷物の行の要約（placements[].item と unplaced[].item は、この何番目か。0 始まり）。web/kit/schema/placement.json の必須項目
    items_out = [{"count": it.count, "length_cm": float(to_decimal(it.length_cm)), "width_cm": float(to_decimal(it.width_cm)),
                  "height_cm": float(to_decimal(it.height_cm)), "weight_kg": float(to_decimal(it.weight_kg)),
                  "stackable": bool(it.stackable)} for it in items]
    return {"container": container.to_dict(), "items": items_out, "placements": placements, "unplaced": unplaced,
            "summary": summary}
