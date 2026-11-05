"""荷待ち・荷役の記録から、標準的運賃の表に当てた参考額を計算する（SPEC §23）。

純粋な関数だけで、ファイルや通信には触らない（記録は呼び出しの間だけメモリにある）。
表は data/tariffs/mlit_standard_fare_waiting.json（標準的運賃 別紙、2024 年 3 月 22 日告示）。

数え方（根拠は docs/plan/2026-11-04_顧客の今のやり方の調査.md §G・§I-6）:
- 待機の分 W = 荷役開始 − 到着。「荷主等の都合」が いいえ なら 0。荷役を荷主側がしたら、その時間も W に含める。
- 待機時間料: 2 時間の境は到着 + 120 分。W から最初の 30 分を差し引き（Q29）、
  到着 + 30 分〜到着 + 120 分を 30 分までごとに通常の額、到着 + 120 分を超える部分を 30 分までごとに 2 時間超の額。端数は切り上げ。
- 積込料・取卸料: 運転者が荷役をした行だけ。荷役の時間を到着 + 120 分の前後に分け、30 分までごとに額を付ける。
  最初の 30 分は差し引かない（告示の文言からの読み取り。Q&A の例で確かめたのは待機時間料だけ）。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .tariffs import load_waiting_tariff

MAX_ROWS = 5000

# 入力の列（貼り付けのときの並び順）
COLUMNS = ("date", "vehicle_no", "vehicle_class", "partner", "place", "arrive", "start", "end", "depart",
           "handler", "method", "shipper_fault")

_HEADER_ALIASES = {
    "日付": "date", "車番": "vehicle_no", "車種": "vehicle_class", "契約の相手": "partner", "場所": "place",
    "到着": "arrive", "荷役開始": "start", "荷役終了": "end", "出発": "depart", "荷役をした人": "handler",
    "荷役の方法": "method", "荷主等の都合の待機か": "shipper_fault", "荷主等の都合": "shipper_fault",
}

_CLASS_ALIASES = {
    "小型": "小型", "小型車": "小型", "2t": "小型", "2トン": "小型", "２t": "小型",
    "中型": "中型", "中型車": "中型", "4t": "中型", "4トン": "中型", "４t": "中型",
    "大型": "大型", "大型車": "大型", "10t": "大型", "10トン": "大型", "１０t": "大型",
    "トレーラー": "トレーラー", "トレーラ": "トレーラー", "トレーラ車": "トレーラー",
}
_YES = {"はい", "yes", "y", "true", "1", "○", "〇", "有", "あり"}
_NO = {"いいえ", "no", "n", "false", "0", "×", "✕", "無", "なし"}


def _norm(s: Any) -> str:
    return str(s if s is not None else "").strip()


def _normalize_class(raw: str) -> str | None:
    key = re.sub(r"[\s（）()]", "", raw).lower()
    if key in _CLASS_ALIASES:
        return _CLASS_ALIASES[key]
    # 「小型（2t）」のような書き方は先頭の語で見る
    for name in ("トレーラー", "小型", "中型", "大型"):
        if key.startswith(name.lower()):
            return name
    return None


def parse_time(text: str) -> int | None:
    """HH:MM を分にする。読めなければ None。"""
    m = re.fullmatch(r"(\d{1,2})[:：](\d{2})", text.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h * 60 + mi


def parse_date(text: str, year: int | None) -> date | None:
    """YYYY-MM-DD / YYYY/M/D か、年を渡したときの M/D。読めない日付は None。"""
    t = text.strip()
    try:
        m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", t)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", t)
        if m and year is not None:
            return date(year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    return None


def _bool(raw: str) -> bool | None:
    k = raw.strip().lower()
    if k in _YES:
        return True
    if k in _NO:
        return False
    return None


def parse_paste(text: str) -> list[dict[str, str]]:
    """Excel から貼り付けたタブ区切りを行の辞書にする。1 行目が見出しなら読み飛ばす（見出しの順が違えば見出しに従う）。"""
    lines = [ln for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return []
    cols = list(COLUMNS)
    first = [c.strip() for c in lines[0].split("\t")]
    if first and (first[0] in ("日付", "date") or sum(1 for c in first if c in _HEADER_ALIASES) >= 3):
        mapped = [_HEADER_ALIASES.get(c) for c in first]
        if sum(1 for c in mapped if c) >= 6:
            cols = [c or f"_extra{i}" for i, c in enumerate(mapped)]
        lines = lines[1:]
    rows = []
    for ln in lines:
        cells = ln.split("\t")
        rows.append({cols[i]: cells[i].strip() for i in range(min(len(cols), len(cells)))})
    return rows


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b) if a > 0 else 0


def _calc_row(n: int, raw: dict[str, Any], year: int | None, tariff: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """1 行を読んで計算する。読めなければ (None, 理由のリスト)。"""
    errs: list[str] = []
    g = {k: _norm(raw.get(k)) for k in COLUMNS}

    d = None
    if not g["date"]:
        errs.append("日付がありません")
    else:
        d = parse_date(g["date"], year)
        if d is None:
            if re.fullmatch(r"\d{1,2}/\d{1,2}", g["date"]) and year is None:
                errs.append(f"日付「{g['date']}」の年が分かりません（年を選んでください）")
            else:
                errs.append(f"日付「{g['date']}」を読めません（YYYY-MM-DD か M/D で入れてください）")

    vclass = None
    if not g["vehicle_class"]:
        errs.append("車種がありません")
    else:
        vclass = _normalize_class(g["vehicle_class"])
        if vclass is None:
            errs.append(f"車種「{g['vehicle_class']}」を読めません（小型（2t）/ 中型（4t）/ 大型（10t）/ トレーラー）")

    if not g["partner"]:
        errs.append("契約の相手がありません")

    def need_time(key: str, label: str, required: bool) -> int | None:
        if not g[key]:
            if required:
                errs.append(f"{label}がありません")
            return None
        v = parse_time(g[key])
        if v is None:
            errs.append(f"{label}「{g[key]}」を読めません（HH:MM で入れてください）")
        return v

    arrive = need_time("arrive", "到着", True)
    start = need_time("start", "荷役開始", True)
    end = need_time("end", "荷役終了", False)
    depart = need_time("depart", "出発", False)

    handler = g["handler"]
    if handler in ("荷主", "荷主側"):
        handler = "荷主側"
    elif handler in ("運転者", "ドライバー", "運転手"):
        handler = "運転者"
    elif handler:
        errs.append(f"荷役をした人「{handler}」を読めません（運転者 / 荷主側）")
        handler = ""
    has_handling = bool(g["end"])
    if not handler and (has_handling or g["method"]):
        if not any("荷役をした人" in e for e in errs):
            errs.append("荷役をした人がありません（運転者 / 荷主側）")
    if handler and not has_handling:
        errs.append("荷役をした人を入れたときは、荷役終了も入れてください")

    method = g["method"]
    if method in ("フォークリフト", "フォークリフト等", "フォーク", "機械"):
        method = "フォークリフト等"
    if handler == "運転者":
        if not method:
            errs.append("運転者が荷役をしたときは、荷役の方法（手積み / フォークリフト等）が必要です")
        elif method not in tariff["handling"]:
            errs.append(f"荷役の方法「{g['method']}」を読めません（手積み / フォークリフト等）")
            method = ""
    elif method and method not in tariff["handling"]:
        method = ""  # 荷主側の荷役では使わないので、読めなくても行は落とさない

    fault = None
    if not g["shipper_fault"]:
        errs.append("荷主等の都合の待機か（はい / いいえ）がありません")
    else:
        fault = _bool(g["shipper_fault"])
        if fault is None:
            errs.append(f"荷主等の都合の待機か「{g['shipper_fault']}」を読めません（はい / いいえ）")

    if errs:
        return None, errs

    # 日付をまたぐ時刻は翌日として扱う（到着を基準に、前の時刻より小さければ +24 時間）
    s_rel = start - arrive
    crosses = False
    if s_rel < 0:
        s_rel += 1440
        crosses = True
    e_rel = None
    if end is not None:
        e_rel = end - arrive
        while e_rel < s_rel:
            e_rel += 1440
            crosses = True
    if depart is not None:
        dep_rel = depart - arrive
        floor = e_rel if e_rel is not None else s_rel
        while dep_rel < floor:
            dep_rel += 1440
            crosses = True
    handling_min = (e_rel - s_rel) if e_rel is not None else 0

    unit = int(tariff["unit_minutes"])
    free = int(tariff["free_minutes"])
    thr = int(tariff["threshold_minutes"])

    # 待機
    wait_raw = s_rel + (handling_min if handler == "荷主側" else 0)
    w = wait_raw if fault else 0
    wn_min = max(0, min(w, thr) - free)
    wo_min = max(0, w - thr)
    wn_units, wo_units = _ceil_div(wn_min, unit), _ceil_div(wo_min, unit)
    wn_rate, wo_rate = tariff["waiting"]["normal"][vclass], tariff["waiting"]["over_2h"][vclass]
    wait_fee = wn_units * wn_rate + wo_units * wo_rate

    # 積込料・取卸料（運転者が荷役をした行だけ）
    hn_units = ho_units = hn_rate = ho_rate = 0
    driver_handling_min = 0
    if handler == "運転者":
        driver_handling_min = handling_min
        before = max(0, min(e_rel, thr) - min(s_rel, thr))
        after = max(0, e_rel - max(s_rel, thr))
        hn_units, ho_units = _ceil_div(before, unit), _ceil_div(after, unit)
        hn_rate = tariff["handling"][method]["normal"][vclass]
        ho_rate = tariff["handling"][method]["over_2h"][vclass]
    handling_fee = hn_units * hn_rate + ho_units * ho_rate

    return {
        "row": n,
        "date": d.isoformat(),
        "month": d.strftime("%Y-%m"),
        "vehicle_no": g["vehicle_no"],
        "vehicle_class": vclass,
        "partner": g["partner"],
        "place": g["place"],
        "arrive": g["arrive"],
        "start": g["start"],
        "end": g["end"],
        "depart": g["depart"],
        "handler": handler,
        "method": method,
        "shipper_fault": fault,
        "crosses_midnight": crosses,
        "wait_raw_min": s_rel,                    # 荷役開始 − 到着（責任の有無によらない）
        "wait_min": w,                            # 待機として数える分（荷主側の荷役を含む。責任なしは 0）
        "handling_min": driver_handling_min,      # 運転者の荷役の分
        "wait_normal_units": wn_units, "wait_normal_rate": wn_rate,
        "wait_over_units": wo_units, "wait_over_rate": wo_rate,
        "wait_fee": wait_fee,
        "handling_normal_units": hn_units, "handling_normal_rate": hn_rate,
        "handling_over_units": ho_units, "handling_over_rate": ho_rate,
        "handling_fee": handling_fee,
        "amount": wait_fee + handling_fee,
        # SPEC §23.6: 1 時間以上は待機（荷主側の荷役を含む）だけで数え、2 時間以上は待機と運転者の荷役の合計で数える
        "over_1h": w >= 60,
        "over_2h_total": (w + driver_handling_min) >= thr,
    }, []


def _sum(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "count": len(rows),
        "wait_minutes": sum(r["wait_min"] for r in rows),
        "handling_minutes": sum(r["handling_min"] for r in rows),
        "wait_fee": sum(r["wait_fee"] for r in rows),
        "handling_fee": sum(r["handling_fee"] for r in rows),
        "amount": sum(r["amount"] for r in rows),
        "over_1h_count": sum(1 for r in rows if r["over_1h"]),
        "over_2h_count": sum(1 for r in rows if r["over_2h_total"]),
    }


def aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """契約の相手ごと・月ごとの合計。参考額の大きい相手から並べる。"""
    partners: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        partners.setdefault(r["partner"], []).append(r)
    out = []
    for name, rs in partners.items():
        months = []
        for m in sorted({r["month"] for r in rs}):
            months.append({"month": m, **_sum([r for r in rs if r["month"] == m])})
        out.append({"partner": name, **_sum(rs), "date_from": min(r["date"] for r in rs),
                    "date_to": max(r["date"] for r in rs), "months": months})
    out.sort(key=lambda p: (-p["amount"], p["partner"]))
    return out


def calc(rows: list[Any], year: int | None = None, tariff: dict[str, Any] | None = None) -> dict[str, Any]:
    """記録の行を計算する。返り値は {rows, by_partner, errors, tariff}。

    errors は {row, message}（row は 1 始まりの行番号）。読めない行は計算から外す。"""
    if not isinstance(rows, list):
        raise ValueError("rows は配列で渡してください")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"行が多すぎます（{MAX_ROWS:,} 行まで）")
    if year is not None and (isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 2100):
        raise ValueError("year は 2000〜2100 の整数で渡してください")
    t = tariff or load_waiting_tariff()
    done: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for i, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            errors.append({"row": i, "message": "行の形が正しくありません"})
            continue
        res, errs = _calc_row(i, raw, year, t)
        if res is None:
            errors.extend({"row": i, "message": e} for e in errs)
        else:
            done.append(res)
    return {
        "rows": done,
        "by_partner": aggregate(done),
        "errors": errors,
        "tariff": {"source_url": t["source_url"], "applies_from": t["applies_from"], "checked_on": t["checked_on"],
                   "tax": t["tax"], "unit_minutes": t["unit_minutes"], "free_minutes": t["free_minutes"],
                   "threshold_minutes": t["threshold_minutes"], "class_labels": t.get("class_labels", {}),
                   "waiting": t["waiting"], "handling": t["handling"]},
    }
