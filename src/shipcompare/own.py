"""自社の運行（SPEC §22、解決の筋書き 第 2 部）の純粋な関数。

通信もファイルの読み書きもしない。配車表（横の形）とデジタコの記録を、共通の形（区間・停車・運行）に直し、
実車率などの数字と「気づき」6 規則を出す。入力と出力は dict と list だけで、JSON にそのまま書ける。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Callable

WEEKDAYS = "月火水木金土日"

# ---------------------------------------------------------------- 気づきの規則のしきい値（筋書き §11.2、AI の提案）
R1_MIN_TRIPS = 4  # 規則 1: 同じ区間・向き・曜日で 4 便以上のうち
R1_RATIO = (3, 4)  # 3/4 以上が空車
R3_MIN_STOP_MIN = 30  # 規則 3: 30 分以上の停車を
R3_MIN_COUNT = 5  # 同じ地点で月 5 回以上
R4_BASE_H = 13.0  # 規則 4: 拘束時間の原則 13 時間
R4_LONG_H = 14.0  # 14 時間を超える日は週 2 回までが目安
R4_MAX_H = 15.0
R4_WEEK_GUIDE = 2
R5_GVW_KG = 8000  # 規則 5: 積載の状況の記録の対象（車両総重量 8t 以上か最大積載量 5t 以上）
R5_LOAD_KG = 5000
R6_UNKNOWN_RATIO = 0.15  # 規則 6: 実車か空車か不明の区間が 15% 以上

LINK_TIME_MIN = 60  # 時刻が 60 分以上ずれたら確認待ち
LINK_DIST_RATIO = 0.15  # 距離が 15% 以上ずれたら確認待ち
SHIFT_GAP_MIN = 8 * 60  # 休息とみなす間（これ以上あくと、次は新しい始業）

CONF_GREEN = 0.9
CONF_YELLOW = 0.6

# ---------------------------------------------------------------- 車番


_PLATE4 = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_PLATE22 = re.compile(r"(?<!\d)(\d{2})\s*[-‐‑–−ー・]\s*(\d{2})(?!\d)")


def normalize_plate(s: Any) -> str | None:
    """「神戸 100 か 1234」「1234」「4t-1234」から、一連の番号 4 桁を取り出す。取れなければ None。"""
    if s is None:
        return None
    t = unicodedata.normalize("NFKC", str(s)).strip()
    if not t:
        return None
    found = _PLATE4.findall(t)
    if found:
        return found[-1]  # 分類番号（3 桁）やクラスの印（4t-）は 4 桁に当たらない。末尾の 4 桁が一連の番号
    m = _PLATE22.search(t)
    if m:
        return m.group(1) + m.group(2)
    return None


# ---------------------------------------------------------------- 配車表（横の形）→ 縦の形


_DATE_HEAD = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{1,2})")
_DATE_FULL = re.compile(r"^\s*(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})")
_DATE_JA = re.compile(r"^\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_DAY_ONLY = re.compile(r"^\s*(\d{1,2})\s*日\s*$")
_WD_PAREN = re.compile(r"\(\s*([月火水木金土日])\s*\)")

# 曜日の見出し（「10/1(木)」）から年を探す範囲。時計（今日の日付）は使わない（SPEC §39）
YEAR_MIN, YEAR_MAX = 2020, 2036
NO_HINT_YEARS = tuple(range(2024, 2031))  # 曜日も年も手掛かりが無いときの、年を選ぶ欄の候補
DEFAULT_YEAR = 2026  # 同じく、選ぶ欄の初期値（作成時の年。手掛かりが無いときだけ使う）


def _date_head(h: str) -> tuple[int | None, int, int] | None:
    """見出しの文字から (年か None, 月, 日)。「10/1(木)」「10月1日」「2026/10/1」を読む。"""
    t = unicodedata.normalize("NFKC", h or "").strip()
    m = _DATE_FULL.match(t)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = _DATE_HEAD.match(t) or _DATE_JA.match(t)
    return (None, int(m.group(1)), int(m.group(2))) if m else None


def assign_head_years(heads: list[str], start_year: int) -> dict[int, tuple[int, int, int]]:
    """見出しの列ごとに (年, 月, 日) を決める。年の無い見出しは start_year から始め、月が前の列より小さくなったら翌年にする（12/11〜1/10）。

    年つきの見出し（2026/10/1）が来たら、その年に合わせる。
    """
    out: dict[int, tuple[int, int, int]] = {}
    cur, prev = start_year, None
    for i, h in enumerate(heads):
        p = _date_head(h)
        if p is None:
            continue
        y, mo, d = p
        if y is not None:
            cur = y
        elif prev is not None and 1 <= mo <= 12 and mo < prev:
            cur += 1
        if 1 <= mo <= 12:
            prev = mo
        out[i] = (cur, mo, d)
    return out


def _weekday_of_head(h: str) -> int | None:
    m = _WD_PAREN.search(unicodedata.normalize("NFKC", h or ""))
    return WEEKDAYS.index(m.group(1)) if m else None


def infer_year(head: list[str]) -> dict[str, Any]:
    """配車表の見出しの曜日（「10/1(木)」の木）と合う年を探す。時計は使わない。

    返す dict: year（使う年）, candidates（合う年の一覧）, ambiguous（利用者に選ばせるか）, source（'weekday' 曜日で決めた / 'explicit' 年つきの見出しだけ /
    'none' 曜日が無い / 'mismatch' 曜日がどの年にも合わない）。合う年が複数あるときは、その中でいちばん新しい年を初期値にして ambiguous を真にする。
    """
    heads = [(h or "").strip() for h in head]
    parsed = {i: _date_head(h) for i, h in enumerate(heads)}
    implicit = [i for i, p in parsed.items() if p is not None and p[0] is None]
    if not any(p is not None for p in parsed.values()):
        return {"year": DEFAULT_YEAR, "candidates": list(NO_HINT_YEARS), "ambiguous": True, "source": "none"}
    if not implicit:
        first = next(p for p in parsed.values() if p is not None)
        return {"year": first[0], "candidates": [first[0]], "ambiguous": False, "source": "explicit"}
    wd_cols = {i: _weekday_of_head(heads[i]) for i in implicit if _weekday_of_head(heads[i]) is not None}
    if not wd_cols:
        return {"year": DEFAULT_YEAR, "candidates": list(NO_HINT_YEARS), "ambiguous": True, "source": "none"}
    cands: list[int] = []
    for y in range(YEAR_MIN, YEAR_MAX + 1):
        got = assign_head_years(heads, y)
        ok = True
        for i, wd in wd_cols.items():
            yy, mo, d = got[i]
            try:
                if date(yy, mo, d).weekday() != wd:
                    ok = False
                    break
            except ValueError:
                ok = False
                break
        if ok:
            cands.append(y)
    if not cands:
        return {"year": DEFAULT_YEAR, "candidates": list(NO_HINT_YEARS), "ambiguous": True, "source": "mismatch"}
    return {"year": max(cands), "candidates": cands, "ambiguous": len(cands) > 1, "source": "weekday"}


def _blank(row: list[str]) -> bool:
    return not any((c or "").strip() for c in row)


def wide_to_long(rows: list[list[str]], year: int = 2026) -> dict[str, Any]:
    """日付が列に並ぶ横の形を、1 行 = 1 車両 × 1 日に直す。年の無い見出しは year から始め、月が前の列より小さくなったら翌年にする。

    rows は CSV を読んだ行の一覧（先頭が見出し）。見出しは「車番」「車種」と、「10/1(木)」のような日付の列。
    車番が空欄の行は、上の行の車番で埋める（結合セルの代わりの行）。空行が来たら、その先は上の車番を引き継がない。
    同じ車番の 2 行目以降は備考の行（荷主など）で、同じ日の `notes` に入れる。

    返す dict:
      rows: [{plate, plate_raw, vehicle_type, date, text, notes, line, col}]  line は CSV の行番号（見出しが 1）、col は列番号（0 始まり）
      unreadable: [{line, col, original, reason}]
      stats: {filled_rows, groups, date_cols, bad_cols, data_rows}
    """
    out: dict[str, Any] = {"rows": [], "unreadable": [], "stats": {}}
    if not rows:
        out["stats"] = {"filled_rows": 0, "groups": 0, "date_cols": 0, "bad_cols": 0, "data_rows": 0}
        return out
    head = [(c or "").strip() for c in rows[0]]
    plate_col = next((i for i, h in enumerate(head) if "車番" in h), 0)
    type_col = next((i for i, h in enumerate(head) if "車種" in h), None)
    date_cols: dict[int, str] = {}
    bad_cols: dict[int, str] = {}
    ymd = assign_head_years([h if i not in (plate_col, type_col) else "" for i, h in enumerate(head)], year)
    for i, h in enumerate(head):
        if i == plate_col or i == type_col or not h:
            continue
        if i in ymd:
            yy, mo, dd = ymd[i]
            try:
                date_cols[i] = date(yy, mo, dd).isoformat()
            except ValueError:
                bad_cols[i] = "日付が読めません"
        elif _DAY_ONLY.match(unicodedata.normalize("NFKC", h)):
            bad_cols[i] = "月が分かりません"
        else:
            bad_cols[i] = "日付が読めません"

    groups: list[dict[str, Any]] = []  # 同じ車番の行のまとまり
    cur: dict[str, Any] | None = None
    filled = 0
    data_rows = 0
    for ri, row in enumerate(rows[1:], start=2):
        row = [(c or "").strip() for c in row] + [""] * (len(head) - len(row))
        if _blank(row):
            cur = None  # 空行は区切り
            continue
        data_rows += 1
        raw = row[plate_col]
        if raw:
            pn = normalize_plate(raw)
            cur = {"plate": pn, "raw": raw, "type": row[type_col] if type_col is not None else "", "rows": [(ri, row)], "bad": pn is None}
            groups.append(cur)
        elif cur is not None and not cur["bad"]:
            cur["rows"].append((ri, row))
            filled += 1
        else:  # 上に埋める車番が無い
            cur = {"plate": None, "raw": "", "type": "", "rows": [(ri, row)], "bad": True}
            groups.append(cur)

    for g in groups:
        first_line, first = g["rows"][0]
        if g["plate"] is None:
            reason = "車番がありません" if not g["raw"] else "車番が読めません"
            for line, row in g["rows"]:
                for ci, cell in enumerate(row):
                    if ci in (plate_col, type_col) or not cell or (ci not in date_cols and ci not in bad_cols):
                        continue
                    out["unreadable"].append({"line": line, "col": ci, "original": cell, "reason": reason})
            continue
        for ci, cell in enumerate(first):
            if ci in bad_cols and cell:
                out["unreadable"].append({"line": first_line, "col": ci, "original": cell, "reason": bad_cols[ci], "plate": g["plate"]})
                continue
            if ci not in date_cols:
                continue
            notes = [row[ci] for _, row in g["rows"][1:] if row[ci]]
            if not cell and not notes:
                continue
            out["rows"].append({
                "plate": g["plate"], "plate_raw": g["raw"], "vehicle_type": g["type"], "date": date_cols[ci],
                "text": cell, "notes": notes, "line": first_line, "col": ci,
            })
    out["stats"] = {"filled_rows": filled, "groups": len(groups), "date_cols": len(date_cols), "bad_cols": len(bad_cols), "data_rows": data_rows}
    return out


# ---------------------------------------------------------------- 配車表のセルの読み取り

_NONLEG = ("休", "整備", "休み", "車検", "待機")
_ARR = re.compile(r"着\s*([^\s（(]+)")
_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*[tｔ](?![a-zA-Z])")


def parse_time(tok: str) -> str | None:
    """「530」「5:30」「05:30」「5時30分」「5時」を HH:MM に。「8時半ごろ」のようなものは None。"""
    t = unicodedata.normalize("NFKC", tok or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t) or re.fullmatch(r"(\d{1,2})時(\d{1,2})分", t) or re.fullmatch(r"(\d{1,2})(\d{2})", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None
    m = re.fullmatch(r"(\d{1,2})時", t)
    if m and int(m.group(1)) < 24:
        return f"{int(m.group(1)):02d}:00"
    return None


def parse_cell(text: str) -> dict[str, Any]:
    """配車表の 1 セルから区間を取り出す。「積地 → 降ろし地 着530 6.5t（空）」。区間は「／」で 2 つまで続けられる。

    返す dict: legs=[{frm, to, empty, arr, weight_kg}]（frm・to は名前のまま。空なら None）、nonleg（休みなど）、errors=[理由]
    """
    t = unicodedata.normalize("NFKC", text or "").strip()
    res: dict[str, Any] = {"legs": [], "nonleg": False, "errors": []}
    if not t:
        return res
    if t in _NONLEG:
        res["nonleg"] = True
        return res
    for part in re.split(r"[／/]", t):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([^→>\s]*)\s*(?:→|->|>)\s*([^\s（(着]*)\s*(.*)$", part)
        if not m:
            res["errors"].append("区間が読めません")
            continue
        frm, to, rest = (m.group(1) or "").strip() or None, (m.group(2) or "").strip() or None, m.group(3)
        arr = None
        am = _ARR.search(rest)
        if am:
            arr = parse_time(am.group(1))
            if arr is None:
                res["errors"].append("時刻が読めません")
        wm = _WEIGHT.search(rest)
        res["legs"].append({
            "frm": frm, "to": to, "empty": ("（空）" in part or "(空)" in part or "空車" in part),
            "arr": arr, "weight_kg": float(wm.group(1)) * 1000 if wm else None,
        })
    return res


# ---------------------------------------------------------------- デジタコ


DIGITACO_DICT = {  # 列名の辞書（大文字にそろえて引く）
    "KBN": "kind", "ST_TIME": "start_time", "ED_TIME": "end_time", "ODO_KM": "distance_km",
    "JK_FLG": "loaded_flag", "POS_NAME": "place_name", "DRV_CD": "driver_code",
}
HAISHA_DICT = {"車番": "plate", "車種": "vehicle_type"}


END_BEFORE_START = "終了が開始より前（または同じ）です"


def parse_km(s: str) -> float | None:
    """走行距離の文字を km の数にする。全角・カンマ・末尾の km を直し、数字と小数点だけなら数、そうでなければ None（読めない）。"""
    t = re.sub(r"(?i)km$", "", unicodedata.normalize("NFKC", s or "").replace(",", "")).strip()
    return float(t) if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", t) else None


def parse_digitaco(rows: list[dict[str, str]], resolve: Callable[[str], str | None], plate: str | None = None,
                   roles: dict[str, str | None] | None = None) -> dict[str, Any]:
    """デジタコの CSV（列名は辞書で役割に直す）から、走行（run）と停車（stop）を取り出す。

    rows は csv.DictReader の行。resolve は地点名 → 地点 ID。run の frm は直前の停車の地点、to は次の停車の地点。
    各要素の `line` は CSV の行番号（見出しが 1）。roles は利用者が決めた列の役割 {大文字にした列名: 役割か None（読まない）} で、辞書より先に使う。
    読めない行は errors に理由を入れて飛ばす（終了が開始以前の行、時刻・区分が読めない行）。距離が読めない行は距離を空にして続ける。
    """
    col: dict[str, str] = {}
    if rows:
        for name in rows[0]:
            key = (name or "").strip().upper()
            role = roles[key] if roles and key in roles else DIGITACO_DICT.get(key)
            if role:
                col[role] = name
    runs: list[dict[str, Any]] = []
    stops: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    prev_stop: dict[str, Any] | None = None
    open_run: dict[str, Any] | None = None
    for i, r in enumerate(rows, start=2):
        try:
            st = datetime.fromisoformat(r[col["start_time"]].strip())
            ed = datetime.fromisoformat(r[col["end_time"]].strip())
        except (KeyError, ValueError):
            errors.append({"line": i, "reason": "時刻が読めません"})
            continue
        if ed <= st:
            errors.append({"line": i, "reason": END_BEFORE_START})
            continue
        kind = (r.get(col.get("kind", ""), "") or "").strip()
        driver = (r.get(col.get("driver_code", ""), "") or "").strip() or None
        if kind == "1":
            km_s = (r.get(col.get("distance_km", ""), "") or "").strip()
            jk_s = (r.get(col.get("loaded_flag", ""), "") or "").strip()
            km = None
            if km_s:
                km = parse_km(km_s)
                if km is None:
                    errors.append({"line": i, "reason": "距離が読めません"})
            run = {
                "plate": plate, "start": st.isoformat(timespec="minutes"), "end": ed.isoformat(timespec="minutes"),
                "km": km, "jk": int(jk_s) if jk_s in ("0", "1") else None,
                "frm": prev_stop["place"] if prev_stop else None, "to": None, "driver": driver, "line": i,
            }
            runs.append(run)
            open_run = run
        elif kind == "2":
            name = (r.get(col.get("place_name", ""), "") or "").strip()
            place = resolve(name) if name else None
            stop = {
                "plate": plate, "place": place, "name": name, "start": st.isoformat(timespec="minutes"), "end": ed.isoformat(timespec="minutes"),
                "minutes": int(round((ed - st).total_seconds() / 60)), "driver": driver, "line": i,
            }
            stops.append(stop)
            if open_run is not None:
                open_run["to"] = place
                open_run = None
            prev_stop = stop
        else:
            errors.append({"line": i, "reason": "区分が読めません"})
    return {"runs": runs, "stops": stops, "errors": errors}


# ---------------------------------------------------------------- 3 つの記録の結び付け


def _hm(s: str) -> int:
    return int(s[:2]) * 60 + int(s[3:5])


def _time_diff_min(hint: str, actual_iso: str) -> int:
    """配車表の時刻の目安 − デジタコの時刻（時刻だけで比べ、日付をまたぐ差は短いほうに寄せる）。"""
    a = int(actual_iso[11:13]) * 60 + int(actual_iso[14:16])
    return (_hm(hint) - a + 720) % 1440 - 720


def link(haisha: list[dict[str, Any]], digitaco: list[dict[str, Any]], est_km: dict[frozenset, float] | None = None) -> dict[str, Any]:
    """配車表の区間とデジタコの走行を結び付ける。

    鍵は始業の日時 × 車番（走行の開始日 × 車番。夜発・翌朝着の走行を 2 日に割らない）。
    同じ鍵に複数あるときは、配車表の着の時刻に近い走行を選ぶ（±60 分で対応させ、近いものが無ければ並び順）。
    値は距離がデジタコのメーター（無ければ est_km の推定）、時刻はデジタコ > 配車表の順で採る。
    距離が 15% 以上、時刻が 60 分以上ずれたら `flags` を付ける（確認待ち）。

    haisha の要素: id, plate, date, frm, to, empty, arr, weight_kg, ref。digitaco の要素: parse_digitaco の run に id を足したもの。
    """
    est_km = est_km or {}
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for d in digitaco:
        by_key.setdefault((d["plate"], d["start"][:10]), []).append(d)
    used: set[str] = set()
    legs: list[dict[str, Any]] = []
    for h in sorted(haisha, key=lambda x: (x["date"], x["plate"], x.get("idx", 0))):
        cands = [d for d in by_key.get((h["plate"], h["date"]), []) if d["id"] not in used]
        pick = None
        if cands:
            pick = cands[0]
            if h.get("arr") and len(cands) > 1:
                pick = min(cands, key=lambda d: abs(_time_diff_min(h["arr"], d["end"])))
            used.add(pick["id"])
        ends = frozenset(x for x in (h.get("frm") or (pick or {}).get("frm"), h.get("to") or (pick or {}).get("to")) if x)
        est = est_km.get(ends)
        leg: dict[str, Any] = {
            "plate": h["plate"], "date": h["date"], "hid": h["id"], "did": pick["id"] if pick else None,
            "frm": h.get("frm") or (pick or {}).get("frm"), "to": h.get("to") or (pick or {}).get("to"),
            "empty_mark": bool(h.get("empty")), "has_ends": bool(h.get("frm") and h.get("to")),
            "weight_kg": h.get("weight_kg"), "jk": pick["jk"] if pick else None, "driver": pick["driver"] if pick else None,
            "dep": pick["start"] if pick else None, "arr": pick["end"] if pick else None, "arr_hint": h.get("arr"),
            "flags": [], "sources": {}, "ref": h.get("ref"), "dref": pick.get("ref") if pick else None,
        }
        if pick and pick.get("km") is not None:
            leg["km"], leg["sources"]["km"] = pick["km"], "デジタコ"
            if est:
                ratio = round(abs(pick["km"] - est) / est, 9)
                if ratio >= LINK_DIST_RATIO:
                    leg["flags"].append({"type": "distance", "ratio": round(ratio, 4), "digitaco_km": pick["km"], "est_km": est})
        elif est:
            leg["km"], leg["sources"]["km"] = est, "OSRM 推定"
        else:
            leg["km"], leg["sources"]["km"] = None, None
        if pick:
            leg["sources"]["time"] = "デジタコ"
            if h.get("arr"):
                diff = _time_diff_min(h["arr"], pick["end"])
                if abs(diff) >= LINK_TIME_MIN:
                    leg["flags"].append({"type": "time", "diff_min": diff, "hint": h["arr"], "digitaco": pick["end"][11:16]})
        else:
            leg["sources"]["time"] = "配車表" if h.get("arr") else None
        leg["sources"]["kind"] = None
        legs.append(leg)
    only_d = []
    for d in digitaco:
        if d["id"] in used:
            continue
        ends = frozenset(x for x in (d.get("frm"), d.get("to")) if x)
        est = est_km.get(ends)
        only_d.append({
            "plate": d["plate"], "date": d["start"][:10], "hid": None, "did": d["id"], "frm": d.get("frm"), "to": d.get("to"),
            "empty_mark": False, "has_ends": bool(d.get("frm") and d.get("to")), "weight_kg": None, "jk": d["jk"], "driver": d["driver"],
            "dep": d["start"], "arr": d["end"], "arr_hint": None, "flags": [], "ref": None, "dref": d.get("ref"),
            "km": d["km"] if d.get("km") is not None else est,
            "sources": {"km": "デジタコ" if d.get("km") is not None else ("OSRM 推定" if est else None), "time": "デジタコ", "kind": None, "note": "配車表なし"},
        })
    return {"legs": legs + only_d, "digitaco_only": len(only_d), "haisha_only": sum(1 for x in legs if x["did"] is None)}


# ---------------------------------------------------------------- 実車・空車


def classify_legs(legs: list[dict[str, Any]]) -> dict[str, Any]:
    """実車・空車・不明を、筋書き §10.2 の順で決める。

    1 デジタコの実空区分（jk: 1 実車、0 空車）> 2 配車表（「（空）」の印は空車、積地と降ろし地の両方があれば実車）
    > 3 請求書（invoiced が真なら実車）> 4 業務記録（log_loaded: 真なら実車、偽なら空車）> 5 不明。
    配車表だけの車は 2 で決まる。返す dict: legs（kind と kind_src を足した写し）, counts, unknown_ratio。
    """
    out = []
    counts = {"loaded": 0, "empty": 0, "unknown": 0}
    for l in legs:
        l = dict(l)
        kind, src = "unknown", None
        if l.get("jk") in (0, 1):
            kind, src = ("loaded" if l["jk"] == 1 else "empty"), "デジタコの実空区分"
        elif l.get("empty_mark"):
            kind, src = "empty", "配車表の（空）の印"
        elif l.get("has_ends"):
            kind, src = "loaded", "配車表の積地・降ろし地"
        elif l.get("invoiced"):
            kind, src = "loaded", "請求書の区間"
        elif l.get("log_loaded") in (True, False):
            kind, src = ("loaded" if l["log_loaded"] else "empty"), "業務記録の荷役"
        l["kind"], l["kind_src"] = kind, src
        l["sources"] = {**(l.get("sources") or {}), "kind": src}
        counts[kind] += 1
        out.append(l)
    n = len(out)
    return {"legs": out, "counts": counts, "unknown_ratio": (counts["unknown"] / n) if n else 0.0}


# ---------------------------------------------------------------- 列の対応づけの確かさ


def confidence(dict_hit: bool, shape_ratio: float, claude_agrees: bool) -> float:
    """確かさ = 0.5 × max(辞書の一致, Claude の一致) + 0.5 × 値の形の一致率（筋書き §10.7 を SPEC §22.2 で改めたもの）。"""
    m = 1.0 if (dict_hit or claude_agrees) else 0.0
    return round(0.5 * m + 0.5 * float(shape_ratio), 9)


def confidence_color(c: float) -> str:
    """0.9 以上が緑、0.6 以上が黄、それ未満が赤。"""
    c = round(c, 9)
    return "green" if c >= CONF_GREEN else ("yellow" if c >= CONF_YELLOW else "red")


_KANA_KANJI = re.compile(r"^[぀-ヿ一-鿿々]+$")
VALUE_SHAPES: dict[str, tuple[str, Callable[[str], bool]]] = {
    "datetime": ("YYYY-MM-DD HH:MM の日時", lambda v: bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", v))),
    "date_header": ("月/日 の日付の見出し", lambda v: bool(re.fullmatch(r"\d{1,2}/\d{1,2}(\(.\))?", v))),
    "flag01": ("0 か 1", lambda v: v in ("0", "1")),
    "code12": ("1 か 2", lambda v: v in ("1", "2")),
    "driver_code": ("英大文字 1 字と数字 2 桁", lambda v: bool(re.fullmatch(r"[A-Z]\d{2}", v))),
    "plate": ("4 桁の番号をふくむ車番", lambda v: normalize_plate(v) is not None),
    "vehicle_type": ("「大型」か「数字 + t」", lambda v: bool(re.fullmatch(r"大型|\d+t", v))),
    "decimal": ("小数まじりの数字", lambda v: bool(re.fullmatch(r"\d+(\.\d+)?", v))),
    "place_kanji": ("漢字・かなだけの名称", lambda v: bool(_KANA_KANJI.match(v))),
}
ROLE_SHAPE = {  # 役割 → 期待する値の形
    "kind": "code12", "start_time": "datetime", "end_time": "datetime", "distance_km": "decimal", "loaded_flag": "flag01",
    "place_name": "place_kanji", "driver_code": "driver_code", "plate": "plate", "vehicle_type": "vehicle_type", "date": "date_header",
}
ROLE_LABEL = {
    "kind": "区分（走行・停車）", "start_time": "開始の日時", "end_time": "終了の日時", "distance_km": "走行距離（km）",
    "loaded_flag": "実車・空車の区分", "place_name": "停車の地点名", "driver_code": "運転者コード", "plate": "車番",
    "vehicle_type": "車種", "date": "日付",
}


def shape_ratio(values: list[str], shape_key: str) -> float:
    vs = [(v or "").strip() for v in values if (v or "").strip()]
    if not vs:
        return 0.0
    ok = VALUE_SHAPES[shape_key][1]
    return sum(1 for v in vs if ok(v)) / len(vs)


def shape_summary(values: list[str]) -> dict[str, float]:
    """値そのものを含まない、形の説明の割合（Claude に送ってよい形）。"""
    vs = [(v or "").strip() for v in values if (v or "").strip()]
    out: dict[str, int] = {}
    for v in vs:
        label = None
        for key in ("datetime", "date_header", "driver_code", "decimal", "flag01", "code12", "plate", "vehicle_type", "place_kanji"):
            if VALUE_SHAPES[key][1](v):
                label = VALUE_SHAPES[key][0]
                break
        if label is None:
            label = "英数字をふくむ文字列" if re.search(r"[A-Za-z0-9０-９]", v) else "その他の文字列"
        out[label] = out.get(label, 0) + 1
    n = len(vs) or 1
    return {k: round(c / n, 4) for k, c in sorted(out.items(), key=lambda kv: -kv[1])}


def best_role_for(values: list[str]) -> tuple[str, float]:
    """辞書に無い列で、値の形がいちばん合う役割とその一致率（形の一致率の見積り）。"""
    best = ("", 0.0)
    for role, key in ROLE_SHAPE.items():
        r = shape_ratio(values, key)
        if r > best[1]:
            best = (role, r)
    return best


# ---------------------------------------------------------------- 拘束時間


def _dt(x: Any) -> datetime:
    return x if isinstance(x, datetime) else datetime.fromisoformat(str(x))


def kousoku_hours(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """改善基準告示の数え方で、1 日（始業から 24 時間）の拘束時間を出す。

    days は 1 人の運転者の業務の一覧 [{start, end}]（datetime か ISO の文字列）。
    各業務の始業から 24 時間の枠に入る業務の時間をすべて足す。翌日の早い始業の分は、前日の拘束時間に足される。
    返す list は始業の順で、各要素は {start, end, hours}（hours は小数 4 桁まで）。
    """
    ds = sorted(({"start": _dt(d["start"]), "end": _dt(d["end"]), "src": d} for d in days), key=lambda d: d["start"])
    out = []
    for i, d in enumerate(ds):
        w0, w1 = d["start"], d["start"] + timedelta(hours=24)
        total = 0.0
        for e in ds[i:]:
            a, b = max(e["start"], w0), min(e["end"], w1)
            if b > a:
                total += (b - a).total_seconds() / 3600
        out.append({**{k: v for k, v in d["src"].items() if k not in ("start", "end")}, "start": d["start"].isoformat(timespec="minutes"),
                    "end": d["end"].isoformat(timespec="minutes"), "hours": round(total, 4)})
    return out


def build_shifts(events: list[dict[str, Any]], gap_min: int = SHIFT_GAP_MIN) -> list[dict[str, Any]]:
    """走行・停車の記録（start, end の ISO）を、休息（gap_min 以上のあき）で区切って業務にする。"""
    evs = sorted(events, key=lambda e: e["start"])
    shifts: list[dict[str, Any]] = []
    for e in evs:
        s, t = _dt(e["start"]), _dt(e["end"])
        if shifts and (s - _dt(shifts[-1]["end"])).total_seconds() / 60 < gap_min:
            if t > _dt(shifts[-1]["end"]):
                shifts[-1]["end"] = t.isoformat(timespec="minutes")
            shifts[-1]["lines"].append(e.get("line"))
        else:
            shifts.append({"start": s.isoformat(timespec="minutes"), "end": t.isoformat(timespec="minutes"), "lines": [e.get("line")]})
    return shifts


# ---------------------------------------------------------------- 数字と表


def kpis(legs: list[dict[str, Any]], vehicles: list[dict[str, Any]]) -> dict[str, Any]:
    """実車率、大型の積載率、大型の積載効率。割合には、計算できた区間の割合を添える。legs は classify_legs の結果。"""
    cls = {v["plate"]: v for v in vehicles}
    n = len(legs)
    judged = [l for l in legs if l["kind"] in ("loaded", "empty") and l.get("km")]

    def rate(ls: list[dict[str, Any]]) -> float | None:
        tot = sum(l["km"] for l in ls)
        return (sum(l["km"] for l in ls if l["kind"] == "loaded") / tot) if tot else None

    large = [l for l in legs if cls.get(l["plate"], {}).get("cls") == "大型"]
    large_judged = [l for l in large if l["kind"] in ("loaded", "empty") and l.get("km")]
    with_w = [l for l in large if l["kind"] == "loaded" and l.get("weight_kg") and cls[l["plate"]].get("max_load_kg")]
    load_rate = (sum(l["weight_kg"] / cls[l["plate"]]["max_load_kg"] for l in with_w) / len(with_w)) if with_w else None
    large_rate = rate(large_judged)
    return {
        "actual_rate": {"value": rate(judged), "legs": len(judged), "of": n, "share": (len(judged) / n) if n else 0.0,
                        "km_loaded": round(sum(l["km"] for l in judged if l["kind"] == "loaded"), 1), "km_empty": round(sum(l["km"] for l in judged if l["kind"] == "empty"), 1),
                        "formula": "実車の km ÷ 走った km（実車か空車かが判定できた区間だけ）"},
        "load_rate": {"value": load_rate, "legs": len(with_w), "of_large": len(large), "of": n, "share": (len(with_w) / n) if n else 0.0,
                      "formula": "積んだ重さ ÷ 最大積載量（大型の実車の区間のうち、重さが分かる区間の平均）"},
        "large_actual_rate": {"value": large_rate, "legs": len(large_judged), "of_large": len(large)},
        "load_efficiency": {"value": (load_rate * large_rate) if (load_rate is not None and large_rate is not None) else None,
                            "formula": "大型の積載率 × 大型の実車率"},
    }


def weekday_table(legs: list[dict[str, Any]], stops: list[dict[str, Any]], dock: str | None = None) -> list[dict[str, Any]]:
    """区間 × 向き × 曜日の表。曜日は行きを出た日の曜日（帰りも同じ運行の行きの曜日）。

    各行: dir, wd, trips, empty, unknown, empty_km, long_stops（30 分以上の停車。行きの到着先の地点 `dock` のもの）。
    """
    rows = []
    for d in ("out", "back"):
        for wd in range(7):
            ls = [l for l in legs if l.get("dir") == d and l.get("cycle_wd") == wd]
            if not ls:
                continue
            rows.append({
                "dir": d, "wd": wd, "trips": len(ls),
                "empty": sum(1 for l in ls if l["kind"] == "empty"), "unknown": sum(1 for l in ls if l["kind"] == "unknown"),
                "empty_km": round(sum(l["km"] or 0 for l in ls if l["kind"] == "empty"), 1),
                "long_stops": (sum(1 for s in stops if s["minutes"] >= R3_MIN_STOP_MIN and s["place"] == dock and s.get("cycle_wd") == wd) if d == "out" else None),
            })
    return rows


# ---------------------------------------------------------------- 気づき（筋書き §11.2、6 規則）


def _md(iso_date: str) -> str:
    return f"{int(iso_date[5:7])}/{int(iso_date[8:10])}"


def _ev_leg(l: dict[str, Any]) -> dict[str, Any]:
    r = l.get("ref") or {}
    return {"id": l["id"], "label": f"{_md(l['date'])} 車番 {l['plate']}", "file": r.get("file"), "where": r.get("where"), "text": r.get("text")}


def insights(data: dict[str, Any]) -> list[dict[str, Any]]:
    """気づき 6 規則。各気づきに、根拠の行（evidence）と次の一手（next）を持たせる。

    data: legs（classify_legs の結果に dir・cycle_wd・cycle_dep・id・ref を足したもの）, stops, shifts（kousoku_hours の結果に driver・plate・ref を足したもの）,
          vehicles, places（{id: {pseudo, muni, short}}）, base（自社の地点 ID）, dock（主な行き先の地点 ID）。
    """
    legs, stops, shifts, vehicles = data["legs"], data.get("stops", []), data.get("shifts", []), data.get("vehicles", [])
    places = data.get("places", {})
    base, dock = data.get("base"), data.get("dock")
    short = lambda p: (places.get(p) or {}).get("short", "?")  # noqa: E731
    lbl = {"out": f"{short(base)} → {short(dock)}", "back": f"{short(dock)} → {short(base)}"}
    out: list[dict[str, Any]] = []

    def add(rule: int, text: str, numbers: list[dict[str, str]], nxt: dict[str, Any], evidence: list[dict[str, Any]]) -> None:
        out.append({"id": f"I{len(out) + 1}", "rule": rule, "text": text, "numbers": numbers, "next": nxt, "evidence": evidence})

    known = [l for l in legs if l["kind"] in ("loaded", "empty")]

    # 規則 1: ある区間・向き・曜日で、空車の便が 4 便以上のうち 3/4 以上
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for l in known:
        if l.get("cycle_wd") is not None and l.get("dir"):
            groups.setdefault((l["dir"], l["cycle_wd"]), []).append(l)
    hits = []
    for (d, wd), ls in groups.items():
        n, e = len(ls), sum(1 for l in ls if l["kind"] == "empty")
        if n >= R1_MIN_TRIPS and e * R1_RATIO[1] >= n * R1_RATIO[0]:
            hits.append((e, n, d, wd, ls))
    for e, n, d, wd, ls in sorted(hits, key=lambda h: (-h[0], h[1], h[2], h[3])):
        others = [l for g_key, g in groups.items() if g_key[0] == d and g_key[1] != wd for l in g]
        oe = sum(1 for l in others if l["kind"] == "empty")
        night = all((l.get("cycle_dep") or "")[11:13].isdigit() and int(l["cycle_dep"][11:13]) >= 18 for l in ls)
        when = f"{WEEKDAYS[wd]}曜" + ("の夜" if night else "")
        who = f"{short(base)}を{when}に出た " if d == "back" else f"{when}に出た "
        body = f"{n} 便の帰りがすべて空でした" if (e == n and d == "back") else (f"{n} 便がすべて空でした" if e == n else f"{n} 便のうち {e} 便が空でした")
        text = f"{lbl[d]}の{'帰り' if d == 'back' else '行き'}は、{who}{body}。ほかの曜日の同じ向きは {len(others)} 便中 {oe} 便が空です（実車か空車かが分かった便だけ）。"
        add(1, text, [{"k": f"{WEEKDAYS[wd]}曜発の{lbl[d]}", "v": f"{n} 便中 {e} 便が空"}, {"k": "ほかの曜日", "v": f"{len(others)} 便中 {oe} 便が空"}],
            {"label": "帰り便の下書きにする", "action": "draft_backhaul", "payload": {"dir": d, "wd": wd, "weekday": WEEKDAYS[wd], "trips": n, "empty": e, "night": night, "month": data.get("month")}},
            [_ev_leg(l) for l in sorted(ls, key=lambda x: x["date"])])

    # 規則 2: 空で走った区間
    empties = [l for l in legs if l["kind"] == "empty"]
    if empties:
        km = sum(l["km"] or 0 for l in empties)
        eb, eo = sum(1 for l in empties if l.get("dir") == "back"), sum(1 for l in empties if l.get("dir") == "out")
        add(2, f"空で走った区間は {len(empties)} 便・約 {round(km):,} km でした（距離はデジタコのメーターと OSRM の推定）。向きは {lbl['back']} が {eb} 便、{lbl['out']} が {eo} 便です。",
            [{"k": "空で走った便", "v": f"{len(empties)} 便"}, {"k": "空で走った距離", "v": f"約 {round(km):,} km"}],
            {"label": "帰り便の下書きにする", "action": "draft_backhaul", "payload": {"dir": "back"}},
            [_ev_leg(l) for l in sorted(empties, key=lambda x: (x["date"], x["plate"]))][:40])

    # 規則 3: 同じ地点で 30 分以上の停車が月 5 回以上
    by_place: dict[str, list[dict[str, Any]]] = {}
    for s in stops:
        if s["minutes"] >= R3_MIN_STOP_MIN and s.get("place"):
            by_place.setdefault(s["place"], []).append(s)
    for pid, ss in sorted(by_place.items(), key=lambda kv: -sum(s["minutes"] for s in kv[1])):
        if len(ss) >= R3_MIN_COUNT:
            tot = sum(s["minutes"] for s in ss)
            plates = sorted({s["plate"] for s in ss})
            p = places.get(pid, {})
            add(3, f"{p.get('muni', '')}の{p.get('pseudo', pid)} で、30 分以上待った記録が {len(ss)} 回、合わせて {round(tot / 60, 1)} 時間でした"
                   f"（デジタコのある {len(plates)} 台の記録だけ。紙の日報の分と、30 分未満の回は数えていません）。",
                [{"k": "30 分以上の停車", "v": f"{len(ss)} 回"}, {"k": "合計", "v": f"{round(tot / 60, 1)} 時間"}],
                {"label": "荷主に渡す記録の一覧を書き出す", "action": "show_evidence", "payload": {}},
                [{"id": s["id"], "label": f"{_md(s['start'])} 車番 {s['plate']}", "file": (s.get("ref") or {}).get("file"),
                  "where": (s.get("ref") or {}).get("where"), "text": f"{s['start'][11:16]}〜{s['end'][11:16]}（{s['minutes']} 分）"} for s in sorted(ss, key=lambda x: x["start"])])

    # 規則 4: 拘束時間
    over13 = [s for s in shifts if s["hours"] > R4_BASE_H]
    if over13:
        over14 = [s for s in shifts if s["hours"] > R4_LONG_H]
        over15 = [s for s in shifts if s["hours"] > R4_MAX_H]
        weeks: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for s in over14:
            y, w, _ = datetime.fromisoformat(s["start"]).isocalendar()
            weeks.setdefault((s["driver"], f"{y}-W{w:02d}"), []).append(s)
        bad_weeks = [(k, v) for k, v in weeks.items() if len(v) > R4_WEEK_GUIDE]
        tail = (f"うち 14 時間を超えた日は {len(over14)} 日で、同じ運転者で週 {max(len(v) for _, v in bad_weeks)} 回の週が {len(bad_weeks)} つあります（目安は週 {R4_WEEK_GUIDE} 回まで）。"
                if bad_weeks else (f"うち 14 時間を超えた日は {len(over14)} 日です。" if over14 else ""))
        tail15 = "15 時間を超えた日はありません。" if not over15 else f"15 時間を超えた日が {len(over15)} 日あります。"
        add(4, f"原則の 13 時間を超えた日が {len(over13)} 日ありました。{tail}{tail15}デジタコの記録から数えた目安で、始業・終業とは同じではありません。",
            [{"k": "13 時間超", "v": f"{len(over13)} 日"}, {"k": "14 時間超", "v": f"{len(over14)} 日"}, {"k": "15 時間超", "v": f"{len(over15)} 日"}],
            {"label": "その日の運行の一覧を開く", "action": "show_evidence", "payload": {}},
            [{"id": s.get("id"), "label": f"{_md(s['start'])} {s.get('driver_label', s.get('driver'))} 車番 {s['plate']}",
              "file": (s.get("ref") or {}).get("file"), "where": (s.get("ref") or {}).get("where"),
              "text": f"始業 {s['start'][11:16]} から 24 時間の拘束 {s['hours']:.1f} 時間"} for s in sorted(over13, key=lambda x: x["start"])])

    # 規則 5: 積載の状況の記録の対象で、積んだ重さが空
    flagged = []
    for v in vehicles:
        if v.get("gvw_kg", 0) >= R5_GVW_KG or v.get("max_load_kg", 0) >= R5_LOAD_KG:
            mine = [l for l in legs if l["plate"] == v["plate"] and l["kind"] == "loaded"]
            if not any(l.get("weight_kg") for l in mine):
                flagged.append(v)
    if flagged:
        groups5: dict[str, list[dict[str, Any]]] = {}
        for v in flagged:
            groups5.setdefault(v["cls"], []).append(v)
        parts = "、".join(f"{c} の {len(vs)} 台" for c, vs in groups5.items())
        add(5, f"{parts}は、車両総重量 8t 以上か最大積載量 5t 以上として登録されています。積載の状況の記録の対象で、積んだ重さの記録がありません。車検証を確かめてください。",
            [{"k": "対象で重さが空の車", "v": f"{len(flagged)} 台"}],
            {"label": "車両の設定を開く", "action": "show_evidence", "payload": {}},
            [{"id": f"V{v['plate']}", "label": f"車番 {v['plate']}（{v['cls']}）", "file": "車両の設定", "where": "車両",
              "text": f"車両総重量 {v['gvw_kg']:,} kg、最大積載量 {v['max_load_kg']:,} kg、運行記録計 {v['tachograph_label']}"} for v in flagged])

    # 規則 6: 不明の区間が 15% 以上
    unk = [l for l in legs if l["kind"] == "unknown"]
    if legs and len(unk) / len(legs) >= R6_UNKNOWN_RATIO:
        pct = round(100 * len(unk) / len(legs))
        add(6, f"区間の {pct}% は実車か空車かが分かりません。配車表の積地・降ろし地を入れると判定できます。",
            [{"k": "不明の区間", "v": f"{len(unk)} / {len(legs)} 区間"}],
            {"label": "配車表を取り込む", "action": "import", "payload": {}},
            [_ev_leg(l) for l in sorted(unk, key=lambda x: (x["date"], x["plate"]))])
    return out
