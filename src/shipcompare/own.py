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


_PLATE_FULL = re.compile(r"([ぁ-んァ-ヶ一-鿿々]{1,4})\s*([0-9]{2,3})\s*([ぁ-んァ-ヶ])\s*[・\-‐]?\s*([0-9]{4}|[0-9]{2}\s*[-‐‑–−ー・]\s*[0-9]{2})")


HALL_WARD_NOTE = "市役所のある区に置きました（区が分かれば直せます）"
CITY_HALL_WARD = {  # 区の名前を書かずに市の名前だけが書かれたとき、市役所のある区に置く（政令指定都市。web/own_calc.js の CITY_HALL_WARD と同じ）
    "札幌": "中央区", "仙台": "青葉区", "さいたま": "浦和区", "千葉": "中央区", "横浜": "中区", "川崎": "川崎区", "相模原": "中央区", "新潟": "中央区",
    "静岡": "葵区", "浜松": "中区", "名古屋": "中区", "京都": "中京区", "大阪": "北区", "堺": "堺区", "神戸": "中央区", "岡山": "北区",
    "広島": "中区", "北九州": "小倉北区", "福岡": "中央区", "熊本": "中央区",
}


def hall_ward_code(text: str, points: dict[str, dict[str, Any]]) -> str | None:
    """「神戸」「神戸市」のように区が無い政令指定都市の名前で始まる文字を、市役所のある区の市区町村コードにする。points は municipality_points.json の points。
    市区町村の名前そのもの（大阪狭山市、神戸市東灘区）に当たる場合は、呼び出し側が先に処理する。当たらなければ None。"""
    t = unicodedata.normalize("NFKC", text or "").strip()
    for city in sorted(CITY_HALL_WARD, key=len, reverse=True):
        if t.startswith(city):
            want = f"{city}市{CITY_HALL_WARD[city]}"
            return next((c for c, p in points.items() if p["name"] == want), None)
    return None


_GOU = re.compile(r"^(?:NO\.?\s*)?(\d{1,3})\s*(?:号車|号|番車|番|号機)?$", re.I)


def gou_name(s: Any) -> str | None:
    """「1号車」「１１号車」「3号」「No.2」「7」→「1号車」「11号車」…。4 桁の登録番号が無い配車表で、車の名前として使う（web/own_calc.js の gouName と同じ）。"""
    t = unicodedata.normalize("NFKC", "" if s is None else str(s)).strip()
    m = _GOU.match(t)
    return f"{int(m.group(1))}号車" if m and int(m.group(1)) > 0 else None


def plate_identity(s: Any) -> tuple[str | None, str | None]:
    """車番の文字から (末尾 4 桁, 完全な書き方)。「品川 100 あ 1234」は ("1234", "品川100あ1234")、「4t-1234」「1234」は ("1234", None)。
    完全な書き方が違う 2 台（品川100あ1234 と 練馬100い1234）を、末尾 4 桁だけで同じ車にしないために使う。web/own_calc.js の plateIdentity と同じ。"""
    num = normalize_plate(s)
    if num is None:
        return None, None
    m = _PLATE_FULL.search(unicodedata.normalize("NFKC", str(s)))
    if m and re.sub(r"\D", "", m.group(4)) == num:
        return num, f"{m.group(1)}{m.group(2)}{m.group(3)}{num}"
    return num, None


# ---------------------------------------------------------------- 配車表（横の形）→ 縦の形


_DATE_HEAD = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{1,2})")
_DATE_FULL = re.compile(r"^\s*(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})")
_DATE_ERA = re.compile(r"^\s*(令和|平成|R|H)\s*(\d{1,2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?", re.I)  # 令和8年10月1日・R8.10.1（令和 = 2018 + n、平成 = 1988 + n）
_DATE_JA = re.compile(r"^\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_DATE_FULL_JA = re.compile(r"^\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_DAY_ONLY = re.compile(r"^\s*(\d{1,2})\s*日\s*$")
_WD_PAREN = re.compile(r"\(\s*([月火水木金土日])\s*\)")

# 曜日の見出し（「10/1(木)」）から年を探す範囲。時計（今日の日付）は使わない（SPEC §39）
YEAR_MIN, YEAR_MAX = 2020, 2036
DEMO_YEAR = 2026  # 合う年が複数あるとき、手掛かり（題名・ファイル名・デジタコ・前の月）が無ければ、この年にいちばん近い年を使う（時計は使わない。同じ近さなら前の年）
NO_HINT_YEARS = tuple(range(2024, 2031))  # 曜日も年も手掛かりが無いときの、年を選ぶ欄の候補
DEFAULT_YEAR = 2026  # 同じく、選ぶ欄の初期値（作成時の年。手掛かりが無いときだけ使う）


def _date_head(h: str) -> tuple[int | None, int, int] | None:
    """見出しの文字から (年か None, 月, 日)。「10/1(木)」「10月1日」「2026/10/1」を読む。"""
    t = unicodedata.normalize("NFKC", h or "").strip()
    me = _DATE_ERA.match(t)
    if me:
        return (2018 if me.group(1) in ("令和", "R", "r") else 1988) + int(me.group(2)), int(me.group(3)), int(me.group(4))
    m = _DATE_FULL.match(t) or _DATE_FULL_JA.match(t)
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


_YEAR_IN_NAME = (re.compile(r"令和\s*(\d{1,2})\s*年"), re.compile(r"(?<!\d)(20[2-3]\d)(?!\d)"), re.compile(r"(?<!\d)(20[2-3]\d)(?:0[1-9]|1[0-2])(?!\d)"))


def years_in_text(text: str) -> list[int]:
    """ファイル名や題名に書かれた年（2026、202610、令和8年）を、出てくる順に返す。web/own_calc.js の yearsInText と同じ。"""
    t = unicodedata.normalize("NFKC", text or "")
    found: list[tuple[int, int]] = []
    for m in _YEAR_IN_NAME[0].finditer(t):
        found.append((m.start(), 2018 + int(m.group(1))))
    for rx in _YEAR_IN_NAME[1:]:
        for m in rx.finditer(t):
            found.append((m.start(), int(m.group(1))))
    out: list[int] = []
    for _, y in sorted(found):
        if y not in out:
            out.append(y)
    return out


def nearest_year(cands: list[int], target: int = DEMO_YEAR) -> int:
    """候補のうち target にいちばん近い年。同じ近さなら前の年（2027 年 10/1(金) が 2021 年にならず、2025 年と 2027 年が並んだら 2025 年）。"""
    return min(cands, key=lambda c: (abs(c - target), c))


def infer_year(head: list[str], hints: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """配車表の見出しの曜日（「10/1(木)」の木）と合う年を探す。時計は使わない。

    返す dict: year（使う年）, candidates（合う年の一覧）, ambiguous（ほかの年も合うか、手掛かりが無いか）, year_assumed（利用者に断って決めた年か。
    「2026 年としました（変えられます）」を出す）, source（'weekday' 曜日で決めた / 'explicit' 年つきの見出しだけ / 'none' 曜日が無い / 'mismatch' 曜日がどの年にも合わない）,
    hint（候補から年を選んだ手掛かりの種類。無ければ None）。
    合う年が複数あるときは、hints（[{year, source}]。題名・ファイル名・デジタコ・保存した前の月の順に並べる）のうち候補に入る最初の年を使い（year_assumed は偽）、
    手掛かりが無ければ DEMO_YEAR にいちばん近い年を使う（year_assumed は真）。「2026 年以下で最新」にはしない（2027 年 10/1(金) が 2021 年になるため）。
    """
    heads = [(h or "").strip() for h in head]
    parsed = {i: _date_head(h) for i, h in enumerate(heads)}
    implicit = [i for i, p in parsed.items() if p is not None and p[0] is None]

    def no_hint(source: str) -> dict[str, Any]:
        return {"year": DEFAULT_YEAR, "candidates": list(NO_HINT_YEARS), "ambiguous": True, "year_assumed": True, "source": source, "hint": None}

    if not any(p is not None for p in parsed.values()):
        return no_hint("none")
    if not implicit:
        first = next(p for p in parsed.values() if p is not None)
        return {"year": first[0], "candidates": [first[0]], "ambiguous": False, "year_assumed": False, "source": "explicit", "hint": None}
    wd_cols = {i: _weekday_of_head(heads[i]) for i in implicit if _weekday_of_head(heads[i]) is not None}
    if not wd_cols:
        return no_hint("none")
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
        return no_hint("mismatch")
    if len(cands) == 1:
        return {"year": cands[0], "candidates": cands, "ambiguous": False, "year_assumed": False, "source": "weekday", "hint": None}
    for h in hints or []:
        if h.get("year") in cands:
            return {"year": h["year"], "candidates": cands, "ambiguous": True, "year_assumed": False, "source": "weekday", "hint": h.get("source")}
    return {"year": nearest_year(cands), "candidates": cands, "ambiguous": True, "year_assumed": True, "source": "weekday", "hint": None}


def _blank(row: list[str]) -> bool:
    return not any((c or "").strip() for c in row)


# ---------------------------------------------------------------- 配車表の見出し（別名）と、見出しの行・形の判定（SPEC §41）
# web/own_calc.js の headKey・haishaRole・detectHaisha と同じ表・同じ手順。

HEADER_SCAN_ROWS = 10  # 見出しの行を探す先頭からの行数


def head_key(h: Any) -> str:
    """見出しを別名の表と比べる形にする（NFKC・大文字・空白と記号を除く）。"""
    t = unicodedata.normalize("NFKC", "" if h is None else str(h)).upper()
    return re.sub(r"[\s.・:()\[\]/_\-]", "", t)


H_PLATE_STRONG = {"車番", "車両番号", "車両NO", "車番号", "ナンバー", "ナンバープレート", "登録番号", "車両ナンバー", "自動車番号", "車NO"}
H_PLATE_MID = {"車両", "車輌", "車両名"}
H_PLATE_WEAK = {"号車", "NO"}  # 他に車番の列が無いときだけ車番として読む（行の通し番号のことがある）
H_TYPE = {"車種", "車格", "トン数", "車型", "積載量", "積載トン数", "車両区分", "車種区分", "トン"}
H_DATE = {"日付", "日", "運行日", "配車日", "出発日", "出荷日", "年月日", "積日", "配送日", "納品日", "運行年月日", "月日", "稼働日"}
H_FROM = {"発地", "積地", "出発地", "積み地", "積込地", "積込場所", "出発", "発", "出発場所", "積地名", "積込", "積み込み地", "積み込み場所", "発地名", "集荷先", "積先", "積み先"}
H_TO = {"着地", "降ろし地", "卸地", "荷降地", "届け先", "届先", "納品先", "到着地", "着", "降ろし", "卸し地", "卸先", "配達先", "荷卸地", "卸", "降し地", "降地", "着地名", "配送先", "納入先", "卸し先", "降ろし先", "卸場所", "降ろし場所"}
H_TIME = {"時刻", "着時刻", "到着", "到着時刻", "着時間", "時間", "着予定", "到着予定", "納品時間", "指定時刻", "着予定時刻", "到着時間", "納品時刻"}
H_MARK = {"実空", "実空区分", "区分", "実車空車", "状態", "種別", "空車", "空実"}
H_IGNORE_IN = ("乗務員", "運転者", "運転手", "ドライバー", "氏名", "合計", "備考", "稼働", "日数", "摘要", "メモ", "実車率", "走行距離", "荷主", "得意先", "取引先", "顧客", "依頼主")
H_MARK_NOTE_IN = ("備考", "摘要", "メモ", "荷主", "得意先", "取引先", "顧客", "依頼主")  # この見出しの列は、読まないが、セルに空車の印（空車回送・帰荷なし）があれば空車にする
H_IGNORE_EQ = {"計", "小計", "総計", "担当", "台数", "TOTAL"}
TOTAL_TOKENS = {"合計", "計", "小計", "総計", "TOTAL", "合計台数", "台数"}  # 車番の欄がこれなら合計の行（読まない）


def haisha_role(h: Any, roles: dict[str, str | None] | None = None) -> str | None:
    """配車表の見出し → 役割。plate / plate2 / plate3（車番。3 段の強さ）, type, date, from, to, weight, time, mark, skip（読まない）。分からなければ None。
    利用者が決めた役割（roles。キーは col_key）を別名より先に使う。"""
    s = "" if h is None else str(h)
    if not s.strip():
        return None
    k = col_key(s)
    if roles is not None and k in roles:
        return roles[k] or "skip"
    hk = head_key(s)
    if hk in H_PLATE_STRONG or any(x in hk for x in ("車番", "車両番号", "ナンバー")):
        return "plate"
    if hk in H_PLATE_MID:
        return "plate2"
    if hk in H_PLATE_WEAK:
        return "plate3"
    if hk in H_TYPE or "車種" in hk:
        return "type"
    if hk in H_DATE:
        return "date"
    if hk in H_FROM:
        return "from"
    if hk in H_TO:
        return "to"
    if hk in H_TIME:
        return "time"
    if hk in H_MARK:
        return "mark"
    if "重量" in hk or hk in ("重さ", "荷重", "荷量"):
        return "weight"
    if any(x in hk for x in H_IGNORE_IN) or hk in H_IGNORE_EQ:
        return "skip"
    return None


_WD_CELL = re.compile(r"^[月火水木金土日](?:曜日?)?$")
_DAYNUM = re.compile(r"^(\d{1,2})日?(?:\s*\(\s*([月火水木金土日])\s*\))?$")  # 「1」「1日」「1日(木)」「1日（木）」（全角の括弧は NFKC で直る）
_TITLE_ERA = re.compile(r"(令和|平成|R|H)\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月", re.I)
_TITLE_YM = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")
_TITLE_M = re.compile(r"(\d{1,2})\s*月")


def _title_month(rows: list[list[str]], upto: int) -> tuple[int | None, int] | None:
    """見出しの行より上の題名の行から (年か None, 月)。「2026年10月 配車表」「10月分」。"""
    for r in rows[:upto]:
        t = unicodedata.normalize("NFKC", " ".join((c or "") for c in r))
        me = _TITLE_ERA.search(t)
        if me and 1 <= int(me.group(3)) <= 12:
            return (2018 if me.group(1) in ("令和", "R", "r") else 1988) + int(me.group(2)), int(me.group(3))
        m = _TITLE_YM.search(t)
        if m and 1 <= int(m.group(2)) <= 12:
            return int(m.group(1)), int(m.group(2))
        m = _TITLE_M.search(t)
        if m and 1 <= int(m.group(1)) <= 12:
            return None, int(m.group(1))
    return None


def _day_num(c: str) -> int | None:
    m = _DAYNUM.match(unicodedata.normalize("NFKC", c).strip())
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 31 else None


def _day_wd(c: str) -> int | None:
    """見出し自体に付いた曜日（「1日(木)」の木）。無ければ None。"""
    m = _DAYNUM.match(unicodedata.normalize("NFKC", c).strip())
    return WEEKDAYS.index(m.group(2)) if m and m.group(2) else None


def vdate(cell: str, title: tuple[int | None, int] | None) -> tuple[int | None, int, int] | None:
    """縦の一覧の日付のセル → (年か None, 月, 日)。「10/1」「R8.10.1」「2026/10/01」はそのまま、「1日」「1」は題名の年月（title）と合わせて読む。"""
    t = unicodedata.normalize("NFKC", cell or "").strip()
    p = _date_head(t)
    if p is not None:
        return p
    d = _day_num(t) if re.fullmatch(r"\d{1,2}日?", t) else None
    if d is not None and title:
        return title[0], title[1], d
    return None


def _layout_from_row(rows: list[list[str]], i: int, roles: dict[str, str | None] | None, month: int | None) -> dict[str, Any] | None:
    cells = [(c or "").strip() for c in rows[i]]
    rl = [haisha_role(c, roles) for c in cells]
    plate_col = next((j for cls in ("plate", "plate2", "plate3") for j, r in enumerate(rl) if r == cls), None)
    for j, r in enumerate(rl):
        if r in ("plate", "plate2", "plate3") and j != plate_col:
            rl[j] = "skip"
    if plate_col is None:
        return None
    type_col = next((j for j, r in enumerate(rl) if r == "type"), None)
    md = [j for j, c in enumerate(cells) if c and rl[j] in (None, "date") and _date_head(c) is not None]
    dayn = [j for j, c in enumerate(cells) if c and rl[j] in (None, "date") and _date_head(c) is None and _day_num(c) is not None]
    wide = len(md) >= 3 or (i == 0 and len(md) >= 1) or (not md and len(dayn) >= 3)
    if wide:
        cols = md or dayn
        nxt = rows[i + 1] if i + 1 < len(rows) else []
        wd = {j: WEEKDAYS.index(unicodedata.normalize("NFKC", nxt[j]).strip()[0]) for j in cols
              if j < len(nxt) and _WD_CELL.match(unicodedata.normalize("NFKC", (nxt[j] or "")).strip())}
        weekday_row = len(wd) >= min(3, len(cols))
        if not weekday_row:
            wd = {}
        heads = [""] * len(cells)
        needs_month = False
        title = _title_month(rows, i)
        if md:
            for j in md:
                h = cells[j]
                if j in wd and _weekday_of_head(h) is None:
                    h += f"({WEEKDAYS[wd[j]]})"
                heads[j] = h
        else:
            if month is not None:
                ty, mm = None, month
            elif title:
                ty, mm = title
            else:
                ty, mm = None, None
            if mm is None:
                needs_month = True
            else:
                for j in dayn:
                    d = _day_num(cells[j])
                    own_wd = _day_wd(cells[j])
                    heads[j] = (f"{ty}/{mm}/{d}" if ty else f"{mm}/{d}") + (f"({WEEKDAYS[wd[j]]})" if j in wd else f"({WEEKDAYS[own_wd]})" if own_wd is not None else "")
        return {"kind": "wide", "header_row": i, "head": cells, "data_start": i + (2 if weekday_row else 1), "weekday_row": weekday_row,
                "plate_col": plate_col, "type_col": type_col, "heads": heads, "day_cols": [] if md else dayn, "roles": rl,
                "needs_month": needs_month, "title_month": list(title) if title else None}
    c = {r: next((j for j, x in enumerate(rl) if x == r), None) for r in ("date", "from", "to", "weight", "time", "mark")}
    if c["date"] is not None and (c["from"] is not None or c["to"] is not None):
        title = _title_month(rows, i) or ((None, month) if month else None)
        seen: dict[str, tuple[int, int]] = {}
        day_only = 0
        for r in rows[i + 1:]:
            cell = unicodedata.normalize("NFKC", (r[c["date"]] if c["date"] < len(r) else "") or "").strip()
            p = vdate(cell, title)
            if p is None and re.fullmatch(r"\d{1,2}日?", cell) and _day_num(cell) is not None:
                day_only += 1
            if p is not None:
                key = cell if _date_head(cell) is not None else (f"{p[0]}/{p[1]}/{p[2]}" if p[0] else f"{p[1]}/{p[2]}")
                if key not in seen:
                    seen[key] = (p[1], p[2])
        heads = [k for k, _ in sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))]
        # 備考・摘要・荷主などの列は、読まない列のままにして、セルの中の空車の印（空車回送・帰荷なし）だけ見る。利用者が「読まない」と決めた列は見ない
        mark_cols = [j for j, x in enumerate(cells) if x and (rl[j] == "mark" or (rl[j] == "skip" and any(w in head_key(x) for w in H_MARK_NOTE_IN)
                                                                                 and not (roles is not None and col_key(x) in roles)))]
        return {"kind": "vertical", "header_row": i, "head": cells, "data_start": i + 1, "weekday_row": False, "plate_col": plate_col,
                "type_col": type_col, "cols": c, "heads": heads, "day_cols": [], "roles": rl, "needs_month": bool(day_only >= 3 and not title),
                "title_month": list(title) if title else None, "mark_cols": mark_cols}
    return None


def transpose_date_down(rows: list[list[str]]) -> tuple[list[list[str]], dict[str, Any]] | None:
    """日付が縦に並び、車が横に並ぶ配車表（左端の列が日付、見出しの行に 1234・5678 のような車番が並ぶ）を、日付が横の形（1 行 = 1 車両）に置き換える。
    見出しの行（先頭の 10 行）に、日付でも列の役割でもない車番らしいセルが 1 つ以上あり（日付の見出しは無い）、その下の日付らしい列（左から 3 列まで）に日付が 3 つ以上あるときだけ。
    戻り値は (置き換えた行, {header_row, cols, date_col}) か None。cols は新しい行 n（見出しの次から）が、元の何列目の車か。web/own_calc.js の transposeDateDown と同じ。"""
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        cells = [(c or "").strip() for c in row]
        cols = [j for j, c in enumerate(cells) if c and haisha_role(c) is None and _date_head(c) is None and _WD_CELL.match(unicodedata.normalize("NFKC", c)) is None
                and normalize_plate(c) is not None]
        if not cols or any(_date_head(c) is not None for c in cells if c):  # 見出しに日付が並ぶ表は、日付が横の形
            continue
        body = rows[i + 1:]
        for dc in range(min(3, len(cells))):
            if dc in cols:
                continue
            dates = [(r[dc] if dc < len(r) else "") for r in body]
            if sum(1 for x in dates if _date_head(x or "") is not None) < 3:
                continue
            wdc = dc + 1 if sum(1 for r in body if dc + 1 < len(r) and _WD_CELL.match(unicodedata.normalize("NFKC", (r[dc + 1] or "").strip()))) >= 3 else None  # 日付の隣の曜日の列
            def _dhead(r: list[str]) -> str:
                d = ((r[dc] if dc < len(r) else "") or "").strip()
                w = unicodedata.normalize("NFKC", (r[wdc] if wdc is not None and wdc < len(r) else "") or "").strip()
                return f"{d}({w[0]})" if d and w and _WD_CELL.match(w) and _date_head(d) is not None and "(" not in d else d
            head = ["車番"] + [_dhead(r) for r in body]
            new_rows = [list(r) for r in rows[:i]] + [head]
            for j in cols:
                new_rows.append([cells[j]] + [((r[j] if j < len(r) else "") or "") for r in body])
            return new_rows, {"header_row": i, "cols": cols, "date_col": dc}
    return None


def detect_haisha(rows: list[list[str]], roles: dict[str, str | None] | None = None, month: int | None = None) -> dict[str, Any] | None:
    """配車表の見出しの行と形（wide: 日付が列に並ぶ横の形 / vertical: 1 行 = 1 便）を、先頭の 10 行から探す。
    見出しの行は「車番らしい列」があり、日付らしい列が 3 つ以上（先頭の行だけは 1 つ以上）か、日付・車番・発地か着地の列があるもの。
    日付の見出しが「1」〜「31」（または「1日」）だけのときは、題名の行か month の月とみなし、次の行の曜日（月〜日）を見出しに足す。
    見つからなければ None。"""
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if _blank(row):
            continue
        lay = _layout_from_row(rows, i, roles, month)
        if lay:
            return lay
    return None


def _legacy_layout(rows: list[list[str]]) -> dict[str, Any]:
    """見出しが見つからないときの読み方（先頭の行を見出し、0 列目を車番とする。wide_to_long を単体で使う試験のため）。"""
    cells = [(c or "").strip() for c in rows[0]]
    rl = [haisha_role(c) for c in cells]
    plate_col = next((j for j, r in enumerate(rl) if r in ("plate", "plate2", "plate3")), 0)
    type_col = next((j for j, r in enumerate(rl) if r == "type" and j != plate_col), None)
    return {"kind": "wide", "header_row": 0, "head": cells, "data_start": 1, "weekday_row": False, "plate_col": plate_col, "type_col": type_col,
            "heads": [c if _date_head(c) else "" for c in cells], "day_cols": [], "roles": rl, "needs_month": False, "title_month": None}


def haisha_heads(layout: dict[str, Any] | None) -> list[str]:
    """年を決めるための見出しの一覧（日付の列の見出し。縦の形は日付のセルの文字）。"""
    return list(layout["heads"]) if layout else []


# ---------------------------------------------------------------- 配車表 → 縦の形


def _forms_by_num(cells: list[str]) -> dict[str, dict[str, set]]:
    out: dict[str, dict[str, set]] = {}
    for raw in cells:
        num, full = plate_identity(raw)
        if num is None:
            continue
        d = out.setdefault(num, {"full": set(), "bare": set()})
        (d["full"] if full else d["bare"]).add(full or unicodedata.normalize("NFKC", raw).strip())
    return out


def plate_notes_of(forms: dict[str, dict[str, set]], attr: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """車番の末尾 4 桁が重なる書き方の確認。split: 完全な書き方（品川100あ1234）が違うので別の車にした。
    merge: 片方が 4 桁だけ（1234）なので同じ車として扱った。
    attr: 末尾 4 桁が同じで、車格か運転者が違う（春日部130え4521 4t 高橋 と 4521 大型 中村）ので別の車にした。attr は {末尾 4 桁: ["書き方（車格・運転者）", ...]}。"""
    out = []
    attr = attr or {}
    for num in sorted(forms):
        f = forms[num]
        if num in attr:
            out.append({"kind": "attr", "num": num, "forms": list(attr[num])})
        elif len(f["full"]) >= 2:
            out.append({"kind": "split", "num": num, "forms": sorted(f["full"]) + sorted(f["bare"])})
        elif len(f["full"]) == 1 and f["bare"]:
            out.append({"kind": "merge", "num": num, "forms": sorted(f["full"]) + sorted(f["bare"])})
    return out


H_DRIVER_IN = ("乗務員", "運転者", "運転手", "ドライバー", "氏名")
_HIRED = re.compile(r"傭車|庸車|外注|協力会社")


def class_of(type_: Any) -> str:
    """車種の文字から車格。「大型」「4t」「4ｔ」は 4t、8t 以上は大型、空なら「不明」。web/own_calc.js の classOf と同じ。"""
    t = unicodedata.normalize("NFKC", "" if type_ is None else str(type_)).strip()
    if not t:
        return "不明"
    if "大型" in t:
        return "大型"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:t|ｔ|トン)", t, re.I)
    if m:
        v = float(m.group(1))
        return "大型" if v >= 8 else f"{v:g}t"
    return t


def is_hired(raw: str, type_: str = "") -> bool:
    """車番の欄（か車種の欄）に「傭車」「外注」などと書いてある行。自社の車ではないので、車番として読まず、傭車として分けて数える。"""
    return bool(_HIRED.search(unicodedata.normalize("NFKC", f"{raw} {type_}")))


def plate_attr_conflicts(entries: list[tuple[str, str, str]]) -> dict[str, list[str]]:
    """(車番の文字, 車種, 運転者) の一覧から、末尾 4 桁が同じで車格か運転者が違うものを探す。空の車格・運転者は比べない。
    戻り値は {末尾 4 桁: ["書き方（車格・運転者）", ...]}。"""
    by: dict[str, list[tuple[str, str, str]]] = {}
    for raw, ty, drv in entries:
        num, _ = plate_identity(raw)
        if num is None:
            continue
        cls = class_of(ty) if (ty or "").strip() else ""
        key = (unicodedata.normalize("NFKC", raw).strip(), cls, (drv or "").strip())
        if key not in by.setdefault(num, []):
            by[num].append(key)
    out: dict[str, list[str]] = {}
    for num, ks in by.items():
        classes = {k[1] for k in ks if k[1] and k[1] != "不明"}
        drivers = {k[2] for k in ks if k[2]}
        if len(ks) >= 2 and (len(classes) >= 2 or len(drivers) >= 2):
            out[num] = [f"{k[0]}（{'・'.join(x for x in (k[1], k[2]) if x)}）" if (k[1] or k[2]) else k[0] for k in ks]
    return out


def _total_row(row: list[str], layout: dict[str, Any]) -> bool:
    for j in {layout["plate_col"], 0, layout.get("type_col")}:
        if j is not None and j < len(row) and head_key(row[j]) in TOTAL_TOKENS:
            return True
    return False


def _extra_columns(rows: list[list[str]], layout: dict[str, Any], used: set[int]) -> list[dict[str, Any]]:
    """読まなかった列（見出しがあり、データのある列）。利用者が役割を選べるように返す。"""
    out = []
    for j, h in enumerate(layout["head"]):
        if not h or j in used:
            continue
        vals: list[str] = []
        for r in rows[layout["data_start"]:]:
            v = ((r[j] if j < len(r) else "") or "").strip()
            if v and v not in vals:
                vals.append(v)
        if vals:
            out.append({"col": j, "header": h, "key": col_key(h), "samples": vals[:3]})
    return out


def _vertical_weight(cell: str, header: str) -> dict[str, Any] | None:
    """縦の形の重さの列。単位があればそのまま、数だけなら見出しの単位（kg / t）、見出しに無ければ 100 以上を kg、未満を t とみなす。"""
    t = unicodedata.normalize("NFKC", cell or "").strip()
    if not t:
        return None
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?|[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?", t):
        v = float(t.replace(",", ""))
        hk = head_key(header)
        unit = "kg" if "KG" in hk else "t" if ("T" in hk or "トン" in hk) else ("kg" if v >= 100 else "t")
        t = f"{t}{unit}"
    return {"weights": parse_weights(t), "text": t}


def read_haisha(rows: list[list[str]], year: int = 2026, layout: dict[str, Any] | None = None,
                roles: dict[str, str | None] | None = None, month: int | None = None) -> dict[str, Any]:
    """配車表（横の形か縦の形）を、1 行 = 1 車両 × 1 日の rows に直す。年の無い見出しは year から始め、月が前の列より小さくなったら翌年にする。

    見出しの行は先頭の 10 行から探す（detect_haisha）。題名の行・空行・曜日の行・号車・乗務員・合計・備考の列・合計の行は読まない。
    車番が空欄の行は、上の行の車番で埋める（結合セルの代わりの行）。空行が来たら、その先は上の車番を引き継がない。
    同じ車番の 2 行目以降は備考の行（荷主など）で、同じ日の `notes` に入れる。
    車番は末尾 4 桁を使うが、「品川100あ1234」と「練馬100い1234」のように完全な書き方が違うものは別の車にする（plate は完全な書き方）。

    返す dict:
      rows: [{plate, plate_raw, vehicle_type, date, text, notes, line, col, parsed?}]  line は CSV の行番号（1 始まり）、col は列番号（0 始まり）。
            parsed は縦の形だけ（parse_cell と同じ形で、セルの文字を作らずに読んだ結果）
      unreadable: [{line, col, original, reason}]
      stats: {filled_rows, groups, date_cols, bad_cols, data_rows}
      plates: [{plate, raw, type}]、plate_notes: plate_notes_of の結果、layout: {kind, header_line, ...}、extra_cols: 読まなかった列、needs_month、skipped_total_rows
    """
    out: dict[str, Any] = {"rows": [], "unreadable": [], "stats": {}, "plates": [], "plate_notes": [], "extra_cols": [], "layout": None,
                           "needs_month": False, "skipped_total_rows": 0}
    if not rows:
        out["stats"] = {"filled_rows": 0, "groups": 0, "date_cols": 0, "bad_cols": 0, "data_rows": 0}
        return out
    lay = layout
    if lay is None:
        lay = detect_haisha(rows, roles, month)
        if lay is None and (tr := transpose_date_down(rows)):  # 日付が縦・車が横の配車表
            rows = tr[0]
            lay = detect_haisha(rows, roles, month)
        lay = lay or _legacy_layout(rows)
    out["layout"] = {"kind": lay["kind"], "header_line": lay["header_row"] + 1, "data_start_line": lay["data_start"] + 1, "weekday_row": lay["weekday_row"],
                     "title_month": lay.get("title_month"), "plate_col": lay["plate_col"]}
    out["needs_month"] = bool(lay.get("needs_month"))
    head = lay["head"]
    pc, tc = lay["plate_col"], lay["type_col"]
    body = [(ri + 1, [(c or "").strip() for c in r]) for ri, r in enumerate(rows) if ri >= lay["data_start"]]
    width = len(head)
    dc = next((j for j, h in enumerate(head) if h and any(x in head_key(h) for x in H_DRIVER_IN)), None)

    def cellat(r: list[str], j: int | None) -> str:
        return ((r[j] if j is not None and j < len(r) else "") or "").strip()

    plate_rows = [r for _, r in body if pc < len(r) and r[pc] and head_key(r[pc]) not in TOTAL_TOKENS]
    plate_cells = [r[pc] for r in plate_rows if not is_hired(r[pc], cellat(r, tc))]
    forms = _forms_by_num(plate_cells)
    attr = plate_attr_conflicts([(r[pc], cellat(r, tc), cellat(r, dc)) for r in plate_rows if not is_hired(r[pc], cellat(r, tc))])
    out["plate_notes"] = plate_notes_of(forms, attr)
    conflict = {n for n, f in forms.items() if len(f["full"]) >= 2}
    out["hired"] = []
    # 4 桁の登録番号がどの行にも無く、1〜3 桁の号車の番号が並ぶ配車表は、「N号車」をそのファイルの中の車の名前にする
    gou_mode = bool(plate_cells) and not any(normalize_plate(x) for x in plate_cells) and any(gou_name(x) for x in plate_cells)
    out["gou_mode"] = gou_mode

    def ident(raw: str, ty: str = "", drv: str = "") -> str | None:
        num, full = plate_identity(raw)
        if num is None:
            return gou_name(raw) if gou_mode else None
        if num in conflict and full:
            return full
        if num in attr:  # 車格か運転者が違う: 完全な書き方があればそれ、4 桁だけの書き方は 4 桁。同じ書き方で車格が違うときは車格を添える
            if full or forms[num]["full"]:
                return full or num
            part = class_of(ty) if (ty or "").strip() else (drv or "").strip()
            return f"{num}（{part}）" if part and part != "不明" else num
        return num

    def hired_note(raw: str, line: int, cells: int, legs: int) -> None:
        name = unicodedata.normalize("NFKC", raw).strip()
        h = next((x for x in out["hired"] if x["name"] == name), None)
        if h is None:
            h = {"name": name, "line": line, "cells": 0, "legs": 0}
            out["hired"].append(h)
        h["cells"] += cells
        h["legs"] += legs

    if lay["kind"] == "vertical":
        return _read_vertical(rows, body, lay, year, out, ident, hired_note, dc)

    date_cols: dict[int, str] = {}
    bad_cols: dict[int, str] = {}
    ymd = assign_head_years(lay["heads"], year)
    day_cols = set(lay.get("day_cols") or [])
    used = {pc} | ({tc} if tc is not None else set())
    for i, h in enumerate(head):
        if i in used or not h:
            continue
        if lay["roles"][i] == "skip":
            used.add(i)
            continue
        if i in ymd:
            used.add(i)
            yy, mo, dd = ymd[i]
            try:
                date_cols[i] = date(yy, mo, dd).isoformat()
            except ValueError:
                bad_cols[i] = "日付が読めません"
        elif i in day_cols or _DAY_ONLY.match(unicodedata.normalize("NFKC", h)):
            used.add(i)
            bad_cols[i] = "月が分かりません"

    groups: list[dict[str, Any]] = []  # 同じ車番の行のまとまり
    cur: dict[str, Any] | None = None
    filled = 0
    data_rows = 0
    for ri, row in body:
        row = row + [""] * (width - len(row))
        if _blank(row):
            cur = None  # 空行は区切り
            continue
        if _total_row(row, lay):
            out["skipped_total_rows"] += 1
            cur = None
            continue
        data_rows += 1
        raw = row[pc]
        if raw:
            ty = row[tc] if tc is not None else ""
            if is_hired(raw, ty):
                cur = {"plate": None, "raw": raw, "type": ty, "rows": [(ri, row)], "bad": True, "hired": True}
                groups.append(cur)
                continue
            pn = ident(raw, ty, cellat(row, dc))
            cur = {"plate": pn, "raw": raw, "type": ty, "driver": cellat(row, dc), "rows": [(ri, row)], "bad": pn is None}
            groups.append(cur)
        elif cur is not None and (not cur["bad"] or cur.get("hired")):
            cur["rows"].append((ri, row))
            filled += 1
        else:  # 上に埋める車番が無い
            cur = {"plate": None, "raw": "", "type": "", "rows": [(ri, row)], "bad": True}
            groups.append(cur)

    for g in groups:
        first_line, first = g["rows"][0]
        if g.get("hired"):  # 傭車: 自社の車ではないので、区間は読まず、傭車の行として数える
            cells = legs_n = 0
            for _, row in g["rows"]:
                for ci, cell in enumerate(row):
                    if ci in date_cols and cell:
                        pcell = parse_cell(cell)
                        if not pcell["nonleg"]:
                            cells += 1
                            legs_n += len(pcell["legs"])
            hired_note(g["raw"], first_line, cells, legs_n)
            continue
        if g["plate"] is None:
            reason = "車番がありません" if not g["raw"] else "車番が読めません"
            for line, row in g["rows"]:
                for ci, cell in enumerate(row):
                    if ci in (pc, tc) or not cell or (ci not in date_cols and ci not in bad_cols):
                        continue
                    out["unreadable"].append({"line": line, "col": ci, "original": cell, "reason": reason})
            continue
        if not any(p["plate"] == g["plate"] for p in out["plates"]):
            out["plates"].append({"plate": g["plate"], "raw": g["raw"], "type": g["type"], "driver": g.get("driver", "")})
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
    out["extra_cols"] = _extra_columns(rows, lay, used)
    out["stats"] = {"filled_rows": filled, "groups": len(groups), "date_cols": len(date_cols), "bad_cols": len(bad_cols), "data_rows": data_rows}
    return out


def _read_vertical(rows: list[list[str]], body: list[tuple[int, list[str]]], lay: dict[str, Any], year: int, out: dict[str, Any],
                   ident: Callable[..., str | None], hired_note: Callable[[str, int, int, int], None] | None = None, dc: int | None = None) -> dict[str, Any]:
    """縦の形（1 行 = 1 便）。日付・車番・発地・着地・重さ・時刻の列から、parse_cell と同じ形の parsed を作る。"""
    c = lay["cols"]
    pc, tc = lay["plate_col"], lay["type_col"]
    head = lay["head"]
    used = {pc, *(v for v in c.values() if v is not None)} | ({tc} if tc is not None else set())
    for j, r in enumerate(lay["roles"]):
        if r == "skip":
            used.add(j)
    data_rows = 0
    plates: list[dict[str, Any]] = []
    title = tuple(lay["title_month"]) if lay.get("title_month") else None
    mark_cols = lay.get("mark_cols") or []
    last_date, last_raw = "", ""
    filled_dates = filled_plates = marked = 0

    def cell(r: list[str], j: int | None) -> str:
        return ((r[j] if j is not None and j < len(r) else "") or "").strip()

    for ri, row in body:
        if _blank(row):
            continue
        if _total_row(row, lay):
            out["skipped_total_rows"] += 1
            continue
        data_rows += 1
        col = c["date"]
        shown = " → ".join(x for x in (cell(row, c["from"]), cell(row, c["to"])) if x) or "（記入なし）"
        raw = cell(row, pc)
        dtext = unicodedata.normalize("NFKC", cell(row, c["date"]))
        if not dtext and last_date:  # 日付が空欄（Excel の「上と同じ」）は、上の行の日付で読む
            dtext, filled_dates = last_date, filled_dates + 1
        elif dtext:
            last_date = dtext
        if not raw and last_raw:
            raw, filled_plates = last_raw, filled_plates + 1
        elif raw:
            last_raw = raw
        if raw and is_hired(raw, cell(row, tc)):
            if hired_note is not None:
                hired_note(raw, ri, 1, 1)
            continue
        pn = ident(raw, cell(row, tc), cell(row, dc)) if raw else None
        if pn is None:
            out["unreadable"].append({"line": ri, "col": col, "original": shown, "reason": "車番がありません" if not raw else "車番が読めません"})
            continue
        p = vdate(dtext, title)
        iso = None
        if p is not None:
            yy = p[0] if p[0] is not None else year
            try:
                iso = date(yy, p[1], p[2]).isoformat()
            except ValueError:
                iso = None
        if iso is None:
            out["unreadable"].append({"line": ri, "col": col, "original": shown, "reason": "日付が読めません", "plate": pn})
            continue
        if not any(x["plate"] == pn for x in plates):
            plates.append({"plate": pn, "raw": raw, "type": cell(row, tc), "driver": cell(row, dc)})
        frm, to = cell(row, c["from"]) or None, cell(row, c["to"]) or None
        mark = unicodedata.normalize("NFKC", cell(row, c["mark"]))
        empty = is_empty_word(mark) or is_empty_word(to or "") or is_empty_word(frm or "")
        if not empty and any(has_empty_mark(cell(row, j)) for j in mark_cols):  # 備考・荷主・摘要の列の「空車回送」「帰荷なし」
            empty = True
            marked += 1
        loaded_mark = (not empty) and mark.strip("()") in LOADED_WORDS
        if to and is_empty_word(to):
            to = None
        if frm and is_empty_word(frm):
            frm = None
        if to and not pref_name(to):
            to = pref_split(to)[1] or to  # 「東京都江東区」「東京 江東区」の都道府県は読み飛ばす
        if frm and not pref_name(frm):
            frm = pref_split(frm)[1] or frm
        errors: list[str] = []
        warnings: list[str] = []
        arr = None
        tcell = unicodedata.normalize("NFKC", cell(row, c["time"])).replace("着", "").strip()
        if tcell:
            arr = parse_time(tcell)
            if arr is None:
                mt = re.search(r"(\d{1,2}):(\d{2})", tcell)
                arr = parse_time(mt.group(0)) if mt else None
            if arr is None:
                warnings.append(TIME_DROPPED)  # 時刻だけ捨てて、区間は残す
        leg: dict[str, Any] = {"frm": frm, "to": to, "empty": empty, "arr": arr, "weight_kg": None}
        if loaded_mark:
            leg["loaded_mark"] = True
        elif not empty and mark.strip("()"):
            leg["note"] = mark.strip("()")[:24]  # 区分の列に書かれた、空車の印でも実車の印でもない語
        w = _vertical_weight(cell(row, c["weight"]), head[c["weight"]] if c["weight"] is not None else "")
        if w:
            sign = [x for x in w["weights"] if "error" in x]
            kgs = _unique([x["kg"] for x in w["weights"] if "kg" in x])
            if sign:
                errors.append(sign[0]["error"])
            if len(kgs) == 1:
                leg["weight_kg"] = kgs[0]
            elif len(kgs) > 1:
                leg["weights"] = kgs
        # 積地・卸地が空で、行のどこかのセルが「公休」「有給」などなら、走らない日の行（区間として数えない）
        nonleg = (frm in _NONLEG and not to) or (to in _NONLEG and not frm) or (not frm and not to and any(unicodedata.normalize("NFKC", x or "").strip() in _NONLEG for x in row))
        parsed = {"legs": [] if nonleg else [leg], "nonleg": bool(nonleg), "errors": errors, "warnings": warnings}
        shown_full = " ".join(x for x in (shown, tcell, w["text"] if w else "") if x)
        out["rows"].append({"plate": pn, "plate_raw": raw, "vehicle_type": cell(row, tc), "date": iso, "text": shown_full, "notes": [],
                            "line": ri, "col": col, "parsed": parsed})
    out["plates"] = plates
    out["extra_cols"] = _extra_columns(rows, lay, used)
    out["stats"] = {"filled_rows": 0, "groups": len(plates), "date_cols": 0, "bad_cols": 0, "data_rows": data_rows,
                    "filled_dates": filled_dates, "filled_plates": filled_plates, "marked_empty": marked}
    return out


def wide_to_long(rows: list[list[str]], year: int = 2026) -> dict[str, Any]:
    """read_haisha の別名（これまでの名前）。"""
    return read_haisha(rows, year)


def _unique(xs: list[Any]) -> list[Any]:
    out: list[Any] = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


# ---------------------------------------------------------------- 配車表のセルの読み取り

_NONLEG = ("休", "整備", "休み", "車検", "待機", "点検", "定期点検", "修理", "休日", "公休", "有休", "有給", "有給休暇", "欠勤", "代休", "振休", "特休", "年休")
# 重さ: 数の直後に t・kg・トン。直前が英数字・小数点なら重さではない。「4t車」「5トンウイング」のように、数の直後が車の種類を表す言葉なら、車の大きさで重さではない
_WEIGHT = re.compile(r"(?<![0-9A-Za-z.])([-−‐‑–+]?)\s*((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)\s*([kK][gG]|[tT]|トン)(?![a-zA-Z])")
_CLASS_AFTER = re.compile(r"^\s*(?:車|ウイング|ウィング|ワイド|ロング|箱|平|ユニック|クレーン|冷凍|冷蔵|保冷|ダンプ|トレーラー?|バン|アルミ|幌|ゲート|セミ|低床|高床|パッカー|タンク|ミキサー|コンテナ|キャリア|ローリー)")
WEIGHT_SIGN_ERR = "重さが読めません（マイナスやプラスの記号つきは入れられません）"
# 「1e3kg」「1e3t」のような指数の書き方。重さとしては読まず、理由を出す（黙って重さ無しの実車にしない）
_SCI_WEIGHT = re.compile(r"(?<![0-9A-Za-z.])[-−‐‑–+]?\s*[0-9]+(?:\.[0-9]+)?[eE][-+]?[0-9]+\s*(?:[kK][gG]|[tT]|トン)(?![a-zA-Z])")
WEIGHT_SCI_ERR = "重さが読めません（1e3 のような指数の書き方は入れられません。4600kg や 4.6t のように書いてください）"


def parse_weights(text: str) -> list[dict[str, Any]]:
    """文の中の重さをすべて取り出す。「6.5t」「4600kg」「4,600kg」「5トン」を kg にする。
    返す list の要素は {kg} か {error}（記号つきの重さ）。車の種類を表す「4t車」「5トンウイング」の数は重さではないので数えない。"""
    t = unicodedata.normalize("NFKC", text or "")
    out: list[dict[str, Any]] = []
    for m in _WEIGHT.finditer(t):
        if _CLASS_AFTER.match(t[m.end():]):
            continue
        if m.group(1):
            out.append({"error": WEIGHT_SIGN_ERR})
            continue
        v = float(m.group(2).replace(",", ""))
        out.append({"kg": v if m.group(3).lower() == "kg" else v * 1000})
    if _SCI_WEIGHT.search(t):
        out.append({"error": WEIGHT_SCI_ERR})
    return out


def parse_weight(text: str) -> dict[str, Any] | None:
    """文の中から重さを 1 つ取り出す（最初のもの）。返す dict: {kg} か {error}。重さが無ければ None。"""
    ws = parse_weights(text)
    return ws[0] if ws else None


def parse_time(tok: str) -> str | None:
    """「530」「5:30」「05:30」「5時30分」「5時」「5時半」を HH:MM に。「8時半ごろ」のようなものは None。"""
    t = unicodedata.normalize("NFKC", tok or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t) or re.fullmatch(r"(\d{1,2})時(\d{1,2})分", t) or re.fullmatch(r"(\d{1,2})(\d{2})", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None
    m = re.fullmatch(r"(\d{1,2})時半", t)
    if m and int(m.group(1)) < 24:
        return f"{int(m.group(1)):02d}:30"
    m = re.fullmatch(r"(\d{1,2})時", t)
    if m and int(m.group(1)) < 24:
        return f"{int(m.group(1)):02d}:00"
    return None


# 空車の印（横の形のセルも縦の形の「区分」の列も、同じ表で読む。web/own_calc.js の EMPTY_WORDS と同じ）。前後が仮名・漢字の中の「空」（空港、空調）は印にしない
# 「回送」「空コン」（空のコンテナ。半角カナの「空ｺﾝ回送」は NFKC で直る）は、前後に何があっても空車の印にする
EMPTY_WORDS = ("戻り空車", "戻り空", "帰り空車", "帰り空", "空車", "空荷", "荷なし", "荷無し", "荷無", "帰荷なし", "帰荷無し", "帰り荷なし", "帰り荷無し", "空回送", "回送", "カラ", "空")
# 「戻り空」「荷なし」は前が仮名でも印にする（「戻り」「荷」の後ろに続く）。「帰り」「戻り」だけの語は印として弱いので、重さが同じ区間に無いときだけ空車にする（_WEAK_EMPTY）
# 「帰り荷さがし中」「帰り荷探し」「帰り荷待ち」「求荷中」は、帰りの荷を探している（今は積んでいない）ので空車の印にする。「帰り荷あり」は印にしない
_EMPTY_MARK = re.compile(r"回送|空コン|戻り空車|戻り空|荷なし|荷無し|(?:帰り|戻り|帰)荷\s*(?:さがし|探し|待ち|探索|募集|未定)|荷さがし|荷探し|求荷(?:中)?|(?<![ぁ-んァ-ヶ一-鿿々])(?:帰り空車|帰り空|空車|空荷|カラ|空)(?![ぁ-んァ-ヶ一-鿿々])")
def has_empty_mark(s: str) -> bool:
    """備考・荷主・摘要の列のセルに空車の印があるか（「空車回送」「空」「回送」「帰荷なし」）。「空港運輸」のように前後が仮名・漢字の「空」は印にしない。"""
    t = unicodedata.normalize("NFKC", s or "").strip()
    return bool(t) and (is_empty_word(t) or bool(_EMPTY_MARK.search(t)))


_WEAK_EMPTY = re.compile(r"(?<![ぁ-んァ-ヶ一-鿿々])(?:帰り|戻り)(?![ぁ-んァ-ヶ一-鿿々])")
# 区間の前に付く空車の印（「回送 江東 → 加古川」「（回送）江東→加古川」「回送：江東→加古川」「空 江東→加古川」）。外して印として覚え、残りを区間として読む
_EMPTY_PREFIX = re.compile(r"^(?:[(\[【]\s*(?:戻り空車|戻り空|帰り空車|帰り空|空車|空荷|荷なし|荷無し|空回送|回送|空コン|カラ|空)\s*[)\]】]\s*[:：、,]?"
                           r"|(?:空回送|回送|空コン)\s*[:：、,]?|(?:戻り空車|戻り空|帰り空車|帰り空|空車|空荷|荷なし|荷無し|カラ|空)(?:\s*[:：、,]\s*|\s+))\s*")
_RETURN_PREFIX = re.compile(r"^(?:帰り|戻り|復路|復)\s*[:：、,]?\s*(?=[^\s→⇒>~〜=\-])")
_HAS_ARROW = re.compile(r"->|=>|[→⇒>~〜]")
# 実車の印。車庫へ戻る区間で、重さの代わりに「実車」と書いておけば、実車として数える（印も重さも無いと不明になる）
LOADED_WORDS = ("実車", "積載", "積み", "実")
_LOADED_MARK = re.compile(r"(?<![ぁ-んァ-ヶ一-鿿々])(?:実車|積載|積み|実)(?![ぁ-んァ-ヶ一-鿿々])")
_ROUNDS = re.compile(r"(?<![0-9:：])([2-9])\s*(?:回転|往復|回(?![転数]))")
_LOCAL_WORD = re.compile(r"市内|区内|町内|構内|場内|ルート|巡回|地場|集配")
TIME_DROPPED = "時刻が読めません"


def _arrival(rest: str) -> tuple[str | None, bool]:
    """「着」の前後の時刻を読む。「着530」「着 5:30」「5:30着」。「着」の後ろが重さ（9.2t）のときは、前の時刻を読む。
    戻り値は (時刻か None, 時刻を捨てたか)。「8時半ごろ着」「頃」のような概算や読めない時刻は、時刻だけ捨てる（区間は残す）。"""
    if "着" not in rest:
        return None, False
    after = re.search(r"着\s*([^\s（(]+)", rest)
    before = re.search(r"([^\s（(着]+)\s*着", rest)
    bad = False
    for m in (after, before):
        if not m:
            continue
        tok = m.group(1)
        t = parse_time(tok)
        if t:
            return t, False
        if not parse_weights(tok):
            bad = True
    return None, bad


def is_empty_word(s: str) -> bool:
    """区分や印の文字が空車の印か（「（空）」の括弧は外して見る）。"""
    t = unicodedata.normalize("NFKC", s or "").strip().strip("()").strip()
    return t in EMPTY_WORDS or "回送" in t or t.startswith("空コン")


def split_cell_prefix(part: str) -> tuple[bool, str]:
    """区間の前に付いた空車の印と、都道府県の名前（「東京 江東区 → 加古川」の「東京」）を外す。戻り値は (空車の印があったか, 残りの文字)。
    印は、残りに矢印があるときだけ外す（矢印の無いセルの「回送」「空」は、区間の無い空車として別に読む）。"""
    t = part.strip()
    pre = False
    m = _EMPTY_PREFIX.match(t)
    if m and _HAS_ARROW.search(t[m.end():]):
        pre, t = True, t[m.end():].strip()
    else:
        mb = _RETURN_PREFIX.match(t)  # 「帰り 江東→加古川 荷なし」の「帰り」は、区間の名前ではない。外して、空車かどうかは後ろの印で決める
        if mb and _HAS_ARROW.search(t[mb.end():]):
            t = t[mb.end():].strip()
    m2 = re.match(r"^(\S+)\s+(?![→⇒>~〜=\-])(\S+)", t)
    if m2 and pref_name(m2.group(1)) and re.search(r"[市区町村]", m2.group(2).split("→")[0]):
        t = t[m2.start(2):]  # 積地の前の都道府県（「東京 江東区 → …」「東京都 江東区 → …」）は読み飛ばす
    return pre, t


def _local_place(t: str) -> str | None:
    """「加古川市内 ルート配送」の地名（加古川市）。無ければ None。"""
    m = re.match(r"^\s*([^\s→>/]+?[市区町村])内", t)
    if m:
        return m.group(1)
    m = re.match(r"^\s*([^\s→>/]+?)(?:ルート|巡回|地場|集配|構内|場内)", t)
    return m.group(1) if m else None


# 都道府県の名前。「東京 江東区」「東京都江東区」の都道府県は読み飛ばし、市区町村を地点にする（web/own_calc.js の PREFECTURES と同じ）
PREFECTURES = (
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県",
    "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
)
_PREF_SHORT = {p[:-1] if p != "北海道" else p: p for p in PREFECTURES}  # 東京 → 東京都


def pref_name(tok: str) -> str | None:
    """文字が都道府県の名前（東京・東京都）だけなら、正式な名前（東京都）。そうでなければ None。"""
    t = unicodedata.normalize("NFKC", tok or "").strip()
    return t if t in PREFECTURES else _PREF_SHORT.get(t)


def pref_split(text: str) -> tuple[str | None, str]:
    """文字の先頭の都道府県を外す。戻り値は (都道府県の正式な名前か None, 残りの文字)。
    「東京都江東区」「兵庫県加古川市」は正式な名前の直後から外す。「東京 江東区」のように短い名前は、後ろが空白で区切られているときだけ外す
    （「大阪狭山市」「京都市」の先頭を都道府県と読まないため）。残りが空になるとき（「東京」だけ）は外さない。"""
    t = unicodedata.normalize("NFKC", text or "").strip()
    for full in sorted(PREFECTURES, key=len, reverse=True):
        if t.startswith(full) and t[len(full):].strip():
            return full, t[len(full):].strip()
    m = re.match(r"^(\S+)\s+(\S.*)$", t)
    if m and m.group(1) in _PREF_SHORT:
        return _PREF_SHORT[m.group(1)], m.group(2).strip()
    return None, t


# 降ろし地が書かれていないセル（「加古川 → 4.6t」「江東 → 着5:30」）の矢印の後ろ。重さ・時刻の形で始まる語は、地名ではない（地名の別名に登録しない）
_NON_PLACE = re.compile(r"(?:[-−‐‑–+]?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:[kK][gG]|[tT]|トン)(?![a-zA-Z])|[0-9]+(?:\.[0-9]+)?[eE][-+]?[0-9]+\s*(?:[kK][gG]|[tT]|トン)"
                        r"|[0-9]{1,2}:[0-9]{2}|[0-9]{1,2}時|[0-9]+(?:\.[0-9]+)?$)")
def is_non_place(name: str) -> bool:
    """重さ・時刻の形の語（「4.6t」「5:30」）か。地名ではないので、地点にも別名にもしない。"""
    return bool(_NON_PLACE.match(unicodedata.normalize("NFKC", name or "").strip()))


# 矢印（→ ⇒ => -> ~ 〜 >。全角の「～」は NFKC で「~」になる）と、1 つのセルの中の区間の区切り（／ / 改行、「AM」「PM」「午前」「午後」の前）
_ARROW_LEG = re.compile(r"^((?:(?!->|=>)[^→⇒>~〜=\s])*)\s*(?:→|⇒|=>|->|~|〜|>)\s*([^\s（(着]*)\s*(.*)$", re.S)
_LEG_SPLIT = re.compile(r"[／/\r\n]+|\s+(?=(?:AM|PM|午前|午後)(?!\s*[:：]?\s*[0-9:：着]))", re.I)
_PERIOD_HEAD = re.compile(r"^(?:AM|PM|午前|午後)(?!\s*[:：]?\s*[0-9:：着])\s*[:：]?\s*", re.I)


# 矢印の前に荷主名や「積」「卸」が挟まるセル（「姫路 ﾄｳﾜ電機 → 春日部」「岩槻 積 → 姫路」「ﾄｳﾜ電機 姫路→春日部」）。web/own_calc.js の SHIPPER_TOKEN と同じ
_SHIPPER_WORD = re.compile(r"^(?:積|卸|積み|卸し|積込|積込み|取卸|取卸し|荷積|荷卸|荷主|積地|卸地|着|発)$")
_SHIPPER_NAME = re.compile(r"(?:電機|電気|電工|商事|商店|商会|運輸|運送|物流|通運|工業|工場|製作所|製造|産業|食品|フーズ|倉庫|ロジ|ロジスティクス|化学|鉄鋼|金属|建材|機械|石油|興業|紙業|セメント|株式会社|有限会社|\(株\)|\(有\)|㈱|㈲)")
_ARROW_ANY = re.compile(r"->|=>|[→⇒>~〜]")


def _origin_only(part: str) -> str:
    """矢印の前に空白で区切った語が 2 つ以上あるとき、積地として最後の地名らしい語だけを残す（荷主名・「積」「卸」・重さ・時刻の語は積地にしない）。
    「姫路 ﾄｳﾜ電機 → 春日部」は姫路、「ﾄｳﾜ電機 姫路→春日部」は姫路。全部が荷主名らしい語のときは、そのまま（最後の語）にする。"""
    am = _ARROW_ANY.search(part)
    if not am:
        return part
    head = part[:am.start()].strip()
    toks = head.split()
    if len(toks) < 2:
        return part

    def shipper(t: str) -> bool:
        return bool(_SHIPPER_WORD.match(t) or _SHIPPER_NAME.search(t) or is_non_place(t) or re.fullmatch(r"[ァ-ヶー]{2,}", t))

    cands = [t for t in toks if not shipper(t)]
    if not cands:
        return part
    if len(cands) == len(toks):
        return part  # 荷主名らしい語が無い（「東京 江東区」など）。これまでの読み方に任せる
    return cands[-1] + " " + part[am.start():]


def parse_cell(text: str) -> dict[str, Any]:
    """配車表の 1 セルから区間を取り出す。「積地 → 降ろし地 着530 6.5t（空）」「積地 → 降ろし地 5:30着 9.2t」。

    矢印は → ⇒ => -> ～ 〜 >。1 つのセルの区間は「／」「/」、セルの中の改行、「AM … PM …」の切れ目で分ける（何区間でも）。
    返す dict: legs=[{frm, to, empty, arr, weight_kg}]（frm・to は名前のまま。空なら None。重さが 2 つ以上あるセルは weight_kg を None にして weights に kg の一覧を足す。
    「2回転」は rounds、「加古川市内 ルート配送」のような地場の配送は local=True で、積地と降ろし地を同じ地名にした実車）、
    nonleg（休みなど）、errors=[理由]（区間を読めない・重さの記号・指数の重さ）、warnings=[理由]（時刻だけ読めない。区間は残し、arr を空にする）
    空車の印は EMPTY_WORDS（空・帰り空・回送・空荷・カラ・空車・（空））と、どこにあっても「回送」「空コン」。
    降ろし地が無いとき（「加古川 → 4.6t」）は to を None にして、後ろの語を重さ・時刻として読む（実車か空車か分からない区間になる）。
    矢印の後ろが「東京 江東区」のように都道府県の名前のあとに市区町村が続くときは、市区町村を降ろし地にする（都道府県だけなら、そのまま「東京」）。
    """
    t = unicodedata.normalize("NFKC", text or "").strip()
    res: dict[str, Any] = {"legs": [], "nonleg": False, "errors": [], "warnings": []}
    if not t:
        return res
    if t in _NONLEG:
        res["nonleg"] = True
        return res
    for part in _LEG_SPLIT.split(t):
        part = _PERIOD_HEAD.sub("", part.strip(), count=1).strip()
        if not part:
            continue
        pre_empty, part = split_cell_prefix(part)
        part = _origin_only(part)
        m = _ARROW_LEG.match(part)
        if not m:
            lone = _lone_cell(part)
            if lone is None:
                res["errors"].append("区間が読めません")
                continue
            leg, warn = lone
            res["legs"].append(leg)
            if warn:
                res["warnings"].append(TIME_DROPPED)
            continue
        frm, to, rest = (m.group(1) or "").strip() or None, (m.group(2) or "").strip() or None, m.group(3)
        chain: list[str] = []  # 「江東→加古川→大阪」: 降ろし地に矢印が続くときは、経由地ごとに区間を分ける（R7A-10）
        if to and re.search(r"->|=>|[→⇒>~〜]", to):
            hops = [h.strip() for h in re.split(r"->|=>|[→⇒>~〜]", to)]
            if len(hops) >= 2 and all(hops):
                chain, to = hops, hops[0]
        tm = re.match(r"^(.*?)(\d{1,2}:\d{2})$", to or "")  # 「江東5:30着」のように降ろし地に時刻が続いて書かれたとき
        if tm and tm.group(1):
            to, rest = tm.group(1), tm.group(2) + rest
        if to is not None and _NON_PLACE.match(to):  # 降ろし地が無く、重さか時刻が続いている
            to, rest = None, (to + " " + rest).strip()
        elif to is not None and pref_name(to):  # 「東京 江東区 9t」: 都道府県のあとに市区町村が続くなら、市区町村を降ろし地にする
            nx = re.match(r"^\s*([^\s（(着]+)", rest)
            if nx and not _NON_PLACE.match(nx.group(1)) and re.search(r"[市区町村]", nx.group(1)):
                to, rest = nx.group(1), rest[nx.end():]
        empty = pre_empty or bool(_EMPTY_MARK.search(rest)) or "(空)" in part
        weak = False
        if not empty and _WEAK_EMPTY.search(rest) and not parse_weights(rest):  # 「江東 → 加古川 帰り」: 重さが無いときだけ空車の印として読む。弱い印なので weak_empty を付け、確認に出す
            empty, weak = True, True
        if to is not None and is_empty_word(to):
            to, empty = None, True
        if to is not None and not pref_name(to):
            to = pref_split(to)[1] or to  # 「東京都江東区」の都道府県は読み飛ばす
        if frm is not None and not pref_name(frm):
            frm = pref_split(frm)[1] or frm
        arr, bad = _arrival(rest)
        if bad:
            res["warnings"].append(TIME_DROPPED)
        ws = parse_weights(rest)
        sign = [w for w in ws if "error" in w]
        if sign:
            res["errors"].append(sign[0]["error"])
        kgs = _unique([w["kg"] for w in ws if "kg" in w])
        leg: dict[str, Any] = {
            "frm": frm, "to": to, "empty": empty,
            "arr": arr, "weight_kg": kgs[0] if len(kgs) == 1 else None,
        }
        if weak:
            leg["weak_empty"] = True
        if len(kgs) > 1:
            leg["weights"] = kgs
        rm = _ROUNDS.search(rest)
        if rm and not empty:
            leg["rounds"] = int(rm.group(1))
        if not empty and _LOADED_MARK.search(rest):
            leg["loaded_mark"] = True
        if not empty:
            note = cell_note(rest)
            if note:
                leg["note"] = note
        if chain:
            seq = [leg["to"]] + [x if pref_name(x) else (pref_split(x)[1] or x) for x in chain[1:]]
            res["legs"].append(dict(leg, to=seq[0], via=True, arr=None))
            for k in range(len(seq) - 1):
                res["legs"].append(dict(leg, frm=seq[k], to=seq[k + 1], via=True, arr=leg["arr"] if k == len(seq) - 2 else None))
            continue
        res["legs"].append(leg)
    return res


_NOTE_STRIP = [
    re.compile(r"[-−‐‑–+]?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:[kK][gG]|[tT]|トン)\S*"),  # 重さと車の大きさ（4.6t、4t車）
    re.compile(r"[0-9]{1,2}\s*[:：]\s*[0-9]{2}|[0-9]{1,2}時(?:半|[0-9]{1,2}分)?|着|[0-9]+\s*(?:回転|往復|回)"),
    re.compile(r"\(空\)|実車|積載|積み|(?<![ぁ-んァ-ヶ一-鿿々])実(?![ぁ-んァ-ヶ一-鿿々])"),
]


_SCI_NOTE = re.compile(r"(?<![0-9A-Za-z.])[-−‐‑–+]?\s*[0-9]+(?:\.[0-9]+)?[eE][-+]?[0-9]+\s*(?:[kK][gG]|[tT]|トン)\S*")


def cell_note(rest: str) -> str:
    """区間の後ろに書かれた、重さ・時刻・空車の印のどれでもない語（「冷凍」「納品」など）。実車として数えた区間に、どんな語が付いていたかを画面に出すために残す。"""
    t = unicodedata.normalize("NFKC", rest or "")
    t = _SCI_NOTE.sub(" ", t)  # 指数の重さ（1e3kg）は、重さとして読めない記号ごと外す
    for rx in _NOTE_STRIP:
        t = rx.sub(" ", t)
    t = re.sub(r"[()\[\]【】:：、,。・\s]+", " ", t).strip()
    t = " ".join(w for w in t.split(" ") if w and not re.fullmatch(r"[0-9.\-]+", w))
    return t[:24]


def _lone_cell(part: str) -> tuple[dict[str, Any], bool] | None:
    """矢印の無いセル。空車の印だけ（「空」「帰り空」「回送」）は積地・降ろし地の無い空車、「加古川市内 ルート配送」は地場の実車。どちらでもなければ None。
    戻り値は (区間, 時刻を捨てたか)。"""
    if is_empty_word(part):
        return {"frm": None, "to": None, "empty": True, "arr": None, "weight_kg": None}, False
    if part.strip() in ("帰り", "戻り"):
        return {"frm": None, "to": None, "empty": True, "arr": None, "weight_kg": None, "weak_empty": True}, False
    if not _LOCAL_WORD.search(part):
        return None
    place = _local_place(part)
    arr, bad = _arrival(part)
    ws = parse_weights(part)
    kgs = _unique([w["kg"] for w in ws if "kg" in w])
    leg: dict[str, Any] = {"frm": place, "to": place, "empty": bool(_EMPTY_MARK.search(part)), "arr": arr, "weight_kg": kgs[0] if len(kgs) == 1 else None, "local": True}
    if len(kgs) > 1:
        leg["weights"] = kgs
    rm = _ROUNDS.search(part)
    if rm:
        leg["rounds"] = int(rm.group(1))
    return leg, bad


# ---------------------------------------------------------------- デジタコ


DIGITACO_DICT = {  # 列名の辞書（全角を直し、大文字にそろえて引く）
    "KBN": "kind", "ST_TIME": "start_time", "ED_TIME": "end_time", "ODO_KM": "distance_km",
    "JK_FLG": "loaded_flag", "POS_NAME": "place_name", "DRV_CD": "driver_code",
}
DIGITACO_EXTRA = {  # よくある別の書き方（日本語の列名）。web/own_calc.js の DIGITACO_EXTRA と同じ
    "区分": "kind", "開始日時": "start_time", "開始時刻": "start_time", "終了日時": "end_time", "終了時刻": "end_time",
    "走行距離": "distance_km", "走行距離(KM)": "distance_km", "実空区分": "loaded_flag", "実車空車": "loaded_flag", "実空": "loaded_flag",
    "地点名": "place_name", "停車地": "place_name", "停車場所": "place_name", "乗務員コード": "driver_code", "運転者コード": "driver_code",
    # SPEC §41: 別の会社のデジタコの明細（運行日・イベント・開始・終了・区間距離・状態・場所）と、日ごとの集計（日付・車番・実車距離・空車距離）
    "運行日": "date", "日付": "date", "年月日": "date", "車番": "plate", "車両番号": "plate", "ナンバー": "plate",
    "イベント": "kind", "種別": "kind", "開始": "start_time", "開始時間": "start_time", "終了": "end_time", "終了時間": "end_time",
    "出庫": "start_time", "出庫時刻": "start_time", "帰庫": "end_time", "帰庫時刻": "end_time",
    "区間距離": "distance_km", "距離": "distance_km", "状態": "loaded_flag", "場所": "place_name", "地点": "place_name",
    "運転者": "driver_code", "乗務員": "driver_code", "運転手": "driver_code",
    "実車距離": "loaded_km", "実車走行距離": "loaded_km", "実車KM": "loaded_km", "空車距離": "empty_km", "空車走行距離": "empty_km", "空車KM": "empty_km",
    # SPEC §42: メーカーのソフトの日ごとの集計（単位つきの見出しは col_key の後で単位を外して引く）
    "車輌番号": "plate", "車輌NO": "plate", "車両NO": "plate", "車番号": "plate",
    "出庫日時": "start_time", "帰庫日時": "end_time", "実車キロ": "loaded_km", "空車キロ": "empty_km",
    "乗務員名": "driver_code", "運転者名": "driver_code", "運転手名": "driver_code", "乗務員氏名": "driver_code",
    # 日ごとの集計の「荷待ち時間」（「1:30」は 1 時間 30 分、数だけなら分）
    "拘束時間": "restraint", "拘束": "restraint", "総拘束時間": "restraint",
    "荷待ち時間": "wait_min", "荷待時間": "wait_min", "荷待ち": "wait_min", "待機時間": "wait_min", "手待ち時間": "wait_min", "手待時間": "wait_min",
}
HAISHA_DICT = {"車番": "plate", "車種": "vehicle_type"}
# 区分の値（全角は NFKC で直してから比べる）。作業の明細（運転・荷積・荷卸・待機・休憩）は、運転を走行、それ以外を停車として読む
KIND_RUN = ("1", "走行", "運転", "運行", "移動")
KIND_STOP = ("2", "停車", "荷積", "荷卸", "積込", "積込み", "取卸", "取卸し", "積み", "卸し", "荷役", "待機", "荷待ち", "手待ち", "休憩", "休息", "仮眠", "給油", "点検")
KIND_WAIT = ("待機", "荷待ち", "手待ち")  # 停車のうち、荷待ちの候補
JK_LOADED, JK_EMPTY = ("1", "実", "実車"), ("0", "空", "空車")


def col_key(name: Any) -> str:
    """列名を辞書や利用者の指定と比べる形にする（NFKC・前後の空白を除く・大文字）。"""
    return unicodedata.normalize("NFKC", "" if name is None else str(name)).strip().upper()


_UNIT_SUFFIX = re.compile(r"\s*[\(\[【]\s*(?:KM|キロ|キロメートル|時間|分|H|HOUR|MIN|M)\s*[\)\]】]\s*$")


def dict_key_variants(k: str) -> list[str]:
    """辞書を引く列名の形。そのまま、末尾の単位「(km)」「[時間]」を外したもの、さらに空白を外したもの。web/own_calc.js の dictKeys と同じ。"""
    out = [k]
    for v in (_UNIT_SUFFIX.sub("", k), re.sub(r"\s+", "", _UNIT_SUFFIX.sub("", k))):
        if v and v not in out:
            out.append(v)
    return out


def role_of(name: Any, roles: dict[str, str | None] | None = None) -> str | None:
    """列名 → 役割。利用者が決めた役割（roles）を、辞書より先に使う。辞書には、末尾の単位（「実車距離(km)」）や空白を外した形でも当てる。"""
    k = col_key(name)
    if roles and k in roles:
        return None if roles[k] in (None, "skip") else roles[k]
    for v in dict_key_variants(k):
        r = DIGITACO_DICT.get(v) or DIGITACO_EXTRA.get(v)
        if r:
            return r
    return None


def column_signature(columns: list[str], roles: dict[str, str | None] | None = None) -> str:
    """列の対応（列名 → 役割）。同じ名前の列が同じ役割に当たるときだけ、2 つのファイルの列の対応が同じ。web/own_calc.js の columnSignature と同じ。"""
    return "|".join(sorted(f"{col_key(c)}={role_of(c, roles) or ''}" for c in columns))


END_BEFORE_START = "終了が開始より前（または同じ）です"

_DT = re.compile(r"^([0-9]{4})[-/]([0-9]{1,2})[-/]([0-9]{1,2})(?:[ T]+([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?)?$")


def parse_dt(s: Any) -> datetime | None:
    """日時の文字を分までの datetime にする。「2026-10-01 05:00」「2026/10/6 20:10」「２０２６/１０/１ ５:００」（全角は NFKC で直す）。
    秒は捨てる。時差つき（+09:00、Z）や 20261001T0500 のような区切りの無い形は読まない（None）。web/own_calc.js の parseDT と同じ。"""
    m = _DT.match(unicodedata.normalize("NFKC", "" if s is None else str(s)).strip())
    if not m:
        return None
    y, mo, d, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0), int(m.group(5) or 0)
    if y < 1900 or h > 23 or mi > 59:
        return None
    try:
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None


def parse_km(s: str) -> float | None:
    """走行距離の文字を km の数にする。全角・カンマ・末尾の km を直し、数字と小数点だけなら数、そうでなければ None（読めない）。"""
    t = re.sub(r"(?i)km$", "", unicodedata.normalize("NFKC", s or "").replace(",", "")).strip()
    return float(t) if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", t) else None


def parse_date_only(s: Any) -> date | None:
    """日付だけの文字（2026/10/6、2026-10-06、2026年10月6日。時刻が続いてもよい）。年が無いものは None。"""
    p = _date_head(unicodedata.normalize("NFKC", "" if s is None else str(s)))
    if p is None or p[0] is None:
        return None
    try:
        return date(p[0], p[1], p[2])
    except ValueError:
        return None


def parse_hm(s: Any) -> int | None:
    """「20:10」「5:30:15」を 0 時からの分にする（秒は捨てる）。読めなければ None。"""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?", unicodedata.normalize("NFKC", "" if s is None else str(s)).strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def parse_wait_min(s: Any) -> int | None:
    """日ごとの集計の「荷待ち時間」を分にする。「1:30」は 90 分、「45」「45分」は 45 分、「1.5時間」は 90 分。読めなければ None。"""
    t = unicodedata.normalize("NFKC", "" if s is None else str(s)).strip().replace(",", "")
    hm = parse_hm(t)
    if hm is not None:
        return hm
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(時間|h|H|分|min|m)?", t)
    if not m:
        return None
    v = float(m.group(1))
    return int(round(v * 60)) if m.group(2) in ("時間", "h", "H") else int(round(v))


def parse_restraint_hours(s: Any) -> float | None:
    """日ごとの集計の「拘束時間」を時間（小数）にする。「13:10」は 13.1667、「13時間10分」「13.5時間」、数だけなら 24 以下は時間・それより大きければ分。読めなければ None。
    web/own_calc.js の parseRestraintHours と同じ。"""
    t = unicodedata.normalize("NFKC", "" if s is None else str(s)).strip().replace(",", "")
    m = re.fullmatch(r"([0-9]{1,3}):([0-9]{2})(?::[0-9]{2})?", t)  # 拘束時間は 24 時間を超えうる（「34:10」）ので、時を 3 桁まで読む
    if m and int(m.group(2)) < 60:
        return round((int(m.group(1)) * 60 + int(m.group(2))) / 60, 4)
    m = re.fullmatch(r"([0-9]+)\s*時間\s*(?:([0-9]+)\s*分?)?", t)
    if m:
        return round(int(m.group(1)) + int(m.group(2) or 0) / 60, 4)
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(時間|h|H|分|min|m)?", t)
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2) in ("分", "min", "m") or (m.group(2) is None and v > 24):
        return round(v / 60, 4)
    return round(v, 4)


def digitaco_columns(rows: list[dict[str, str]], roles: dict[str, str | None] | None = None) -> dict[str, str]:
    """役割 → 列名。"""
    col: dict[str, str] = {}
    if rows:
        for name in rows[0]:
            role = role_of(name, roles)
            if role:
                col[role] = name
    return col


def parse_digitaco(rows: list[dict[str, str]], resolve: Callable[[str], str | None], plate: str | None = None,
                   roles: dict[str, str | None] | None = None, line_offset: int = 0) -> dict[str, Any]:
    """デジタコの CSV（列名は辞書で役割に直す）から、走行（run）と停車（stop）を取り出す。

    rows は csv.DictReader の行。resolve は地点名 → 地点 ID。run の frm は直前の停車の地点、to は次の停車の地点。
    各要素の `line` は CSV の行番号（見出しが 1。見出しの前に題名の行があるときは line_offset 行ぶん足す）。
    roles は利用者が決めた列の役割 {大文字にした列名: 役割か None（読まない）} で、辞書より先に使う。
    区分は 1・2 のほか「走行」「停車」、実空は 1・0 のほか「実」「実車」「空」「空車」も読む（web/own_calc.js と同じ）。
    開始・終了が「20:10」のように時刻だけで、運行日の列があるときは、運行日に足して日時にする（終了が開始より前なら翌日。同じ運行日の続きの行は、前の行の終了以降にする）。
    読めない行は errors に理由を入れて飛ばす（終了が開始以前の行、時刻・区分が読めない行）。距離が読めない行は距離を空にして続ける。
    """
    col = digitaco_columns(rows, roles)

    def get(r: dict[str, str], role: str) -> str:
        return ((r.get(col[role]) if role in col else "") or "").strip()

    runs: list[dict[str, Any]] = []
    stops: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    prev_stop: dict[str, Any] | None = None
    open_run: dict[str, Any] | None = None
    st_d0: date | None = None  # 時刻だけの行のための、直前の行の運行日と終了
    st_end: datetime | None = None
    last_plate: str | None = None
    for i, r in enumerate(rows, start=2 + line_offset):
        if not any((v or "").strip() for v in r.values() if isinstance(v, str)):
            continue
        row_plate = plate
        if "plate" in col:  # 車番の列があれば、ファイル名の車番は要らない
            row_plate = normalize_plate(get(r, "plate")) or plate
            if row_plate is None:
                errors.append({"line": i, "reason": "車番が読めません"})
                continue
        if row_plate != last_plate:
            prev_stop, open_run, st_d0, st_end = None, None, None, None
            last_plate = row_plate
        s_txt, e_txt = get(r, "start_time"), get(r, "end_time")
        st, ed = parse_dt(s_txt), parse_dt(e_txt)
        d0 = parse_date_only(get(r, "date")) if "date" in col else None
        if (st is None or ed is None) and d0 is not None:
            if st is None and parse_hm(s_txt) is not None:
                base = datetime(d0.year, d0.month, d0.day) + timedelta(minutes=parse_hm(s_txt))
                if st_d0 == d0 and st_end is not None:
                    for _ in range(3):
                        if base < st_end:
                            base += timedelta(days=1)
                st = base
            if ed is None and st is not None and parse_hm(e_txt) is not None:
                e = datetime(st.year, st.month, st.day) + timedelta(minutes=parse_hm(e_txt))
                ed = e + timedelta(days=1) if e < st else e
        if st is None or ed is None:
            errors.append({"line": i, "reason": "時刻が読めません"})
            continue
        if ed <= st:
            errors.append({"line": i, "reason": END_BEFORE_START})
            continue
        st_d0, st_end = d0, ed
        kind_raw = unicodedata.normalize("NFKC", get(r, "kind"))
        kind = "1" if kind_raw in KIND_RUN else "2" if kind_raw in KIND_STOP else kind_raw
        driver = get(r, "driver_code") or None
        if kind == "1":
            km_s = get(r, "distance_km")
            jk_s = unicodedata.normalize("NFKC", get(r, "loaded_flag"))
            km = None
            if km_s:
                km = parse_km(km_s)
                if km is None:
                    errors.append({"line": i, "reason": "距離が読めません"})
            run = {
                "plate": row_plate, "start": st.isoformat(timespec="minutes"), "end": ed.isoformat(timespec="minutes"),
                "km": km, "jk": 1 if jk_s in JK_LOADED else 0 if jk_s in JK_EMPTY else None,
                "frm": prev_stop["place"] if prev_stop else None, "to": None, "driver": driver, "line": i,
            }
            if open_run is not None:
                open_run["cont"] = True  # 停車の行をはさまず次の走行が始まった（長距離の途中の休息。link で 1 区間にまとめる）
            runs.append(run)
            open_run = run
        elif kind == "2":
            name = get(r, "place_name")
            place = resolve(name) if name else None
            stop = {
                "plate": row_plate, "place": place, "name": name, "start": st.isoformat(timespec="minutes"), "end": ed.isoformat(timespec="minutes"),
                "minutes": int(round((ed - st).total_seconds() / 60)), "driver": driver, "line": i,
            }
            if kind_raw in KIND_WAIT:
                stop["wait"] = True
            stops.append(stop)
            if open_run is not None:
                open_run["to"] = place
                open_run = None
            prev_stop = stop
        else:
            errors.append({"line": i, "reason": "区分が読めません"})
    return {"runs": runs, "stops": stops, "errors": errors}


def parse_digitaco_summary(rows: list[dict[str, str]], plate: str | None = None, roles: dict[str, str | None] | None = None,
                           line_offset: int = 0) -> dict[str, Any]:
    """日ごとの集計の形（日付・車番・実車距離・空車距離。出庫・帰庫の時刻があれば読む）を、実車の走行と空車の走行に直す。
    1 行から、実車距離が 0 より大きければ実車の走行 1 つ（jk=1）、空車距離が 0 より大きければ空車の走行 1 つ（jk=0）を作る。
    地点は分からないので frm・to は空。車番の列が無いときは plate（ファイル名の 4 桁）を使う。時刻が無いときは、その日の 0:00 にして time_unknown を真にする。"""
    col = digitaco_columns(rows, roles)

    def get(r: dict[str, str], role: str) -> str:
        return ((r.get(col[role]) if role in col else "") or "").strip()

    runs: list[dict[str, Any]] = []
    waits: list[dict[str, Any]] = []
    restraints: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for i, r in enumerate(rows, start=2 + line_offset):
        if not any((v or "").strip() for v in r.values() if isinstance(v, str)):
            continue
        d0 = parse_date_only(get(r, "date"))
        if d0 is None:
            errors.append({"line": i, "reason": "日付が読めません"})
            continue
        pl = normalize_plate(get(r, "plate")) if "plate" in col else plate
        if pl is None:
            errors.append({"line": i, "reason": "車番が読めません"})
            continue
        wtxt = get(r, "wait_min")
        if wtxt:
            wm = parse_wait_min(wtxt)
            if wm is None:
                errors.append({"line": i, "reason": "荷待ち時間が読めません"})
            elif wm > 0:
                waits.append({"plate": pl, "date": d0.isoformat(), "minutes": wm, "line": i})
        rtxt = get(r, "restraint")
        if rtxt:
            rh = parse_restraint_hours(rtxt)
            if rh is None:
                errors.append({"line": i, "reason": "拘束時間が読めません"})
            elif rh > 0:
                restraints.append({"plate": pl, "date": d0.isoformat(), "hours": rh, "line": i, "driver": get(r, "driver_code") or None})
        kms: list[tuple[float, int]] = []
        for role, jk in (("loaded_km", 1), ("empty_km", 0)):
            txt = get(r, role)
            if not txt:
                continue
            v = parse_km(txt)
            if v is None:
                errors.append({"line": i, "reason": "距離が読めません"})
            elif v > 0:
                kms.append((v, jk))
        sm, em = parse_hm(get(r, "start_time")), parse_hm(get(r, "end_time"))
        base = datetime(d0.year, d0.month, d0.day)
        sdt, edt = parse_dt(get(r, "start_time")), parse_dt(get(r, "end_time"))
        if sdt is not None and edt is not None and edt > sdt:  # 「出庫日時」「帰庫日時」のように日付つきの日時
            st, ed, known = sdt, edt, True
        elif sm is not None and em is not None and sm != em:
            st = base + timedelta(minutes=sm)
            ed = base + timedelta(minutes=em) + (timedelta(days=1) if em < sm else timedelta(0))
            known = True
        else:
            st = ed = base
            known = False
        for v, jk in kms:
            runs.append({"plate": pl, "start": st.isoformat(timespec="minutes"), "end": ed.isoformat(timespec="minutes"), "km": v, "jk": jk,
                         "frm": None, "to": None, "driver": get(r, "driver_code") or None, "line": i, "summary": True, "time_unknown": not known})
    return {"runs": runs, "stops": [], "errors": errors, "waits": waits, "restraints": restraints}


def detect_digitaco(rows: list[list[str]], roles: dict[str, str | None] | None = None) -> dict[str, Any] | None:
    """デジタコの形と見出しの行を、先頭の 10 行から探す。detail: 開始・終了の列があり、役割の付いた列が 3 つ以上。
    summary: 日付の列と、実車距離か空車距離の列がある。見つからなければ None。"""
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        rm = {j: role_of(c, roles) for j, c in enumerate(row) if (c or "").strip() and role_of(c, roles)}
        rs = set(rm.values())
        if "date" in rs and ("loaded_km" in rs or "empty_km" in rs):
            return {"kind": "summary", "header_row": i, "roles": rm}
        if "start_time" in rs and "end_time" in rs and len(rs) >= 3:
            return {"kind": "detail", "header_row": i, "roles": rm}
    return None


def dedupe_digitaco(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """同じ走行・停車の行を 1 つにする。parts は [{file, plate, runs, stops}]（ファイルの順）。
    走行は (車番, 開始, 終了, 距離)、停車は (車番, 開始, 終了, 地点名) が同じなら、あとから出たものを捨てる（同じデジタコを 2 回落とした場合など）。
    返す dict: parts（捨てたあとの runs・stops）, dropped: {runs, stops, by_file: {ファイル名: {runs, stops}}}。"""
    seen_r: set[tuple] = set()
    seen_s: set[tuple] = set()
    out = []
    dropped = {"runs": 0, "stops": 0, "by_file": {}}
    for p in parts:
        runs, stops = [], []
        dr = ds = 0
        for r in p["runs"]:
            k = (r["plate"], r["start"], r["end"], r["km"])
            if k in seen_r:
                dr += 1
            else:
                seen_r.add(k)
                runs.append(r)
        for s_ in p["stops"]:
            k = (s_["plate"], s_["start"], s_["end"], s_["name"])
            if k in seen_s:
                ds += 1
            else:
                seen_s.add(k)
                stops.append(s_)
        dropped["runs"] += dr
        dropped["stops"] += ds
        if dr or ds:
            dropped["by_file"][p["file"]] = {"runs": dr, "stops": ds}
        out.append({**p, "runs": runs, "stops": stops})
    return {"parts": out, "dropped": dropped}


# ---------------------------------------------------------------- 列の役割（利用者が選ぶ）とその説明（SPEC §41）

ROLE_LABEL_JA = {  # 画面の選択肢に出す、やさしい日本語の名前
    "date": "日付", "plate": "車番", "type": "車種・トン数", "from": "荷を積む場所（発地）", "to": "荷を降ろす場所（着地）", "weight": "重さ", "time": "着く時刻",
    "mark": "実空の印がある列", "skip": "使わない", "kind": "走行か停車か", "start_time": "開始の日時", "end_time": "終了の日時", "distance_km": "走った距離（km）",
    "loaded_flag": "実車か空車か", "place_name": "停車した場所", "driver_code": "運転者", "loaded_km": "実車で走った距離（km）", "empty_km": "空車で走った距離（km）",
    "wait_min": "荷待ちの時間（分または 時:分）", "restraint": "拘束時間（時:分または 時間）",
}
HAISHA_ROLE_OPTIONS = ["date", "plate", "from", "to", "weight", "time", "mark", "type", "skip"]
DIGITACO_ROLE_OPTIONS = ["date", "plate", "kind", "start_time", "end_time", "distance_km", "loaded_flag", "place_name", "driver_code", "loaded_km", "empty_km", "wait_min", "restraint", "skip"]


def role_options(source: str) -> list[dict[str, str]]:
    """画面に出す選択肢 [{role, label_ja}]。source は '配車表'、'デジタコ'、どちらか分からないファイルのときはそれ以外。"""
    if source == "配車表":
        roles = HAISHA_ROLE_OPTIONS
    elif source == "デジタコ":
        roles = DIGITACO_ROLE_OPTIONS
    else:  # どちらか分からないファイル: 両方の選択肢を、重ならないように並べる
        roles = _unique([r for r in HAISHA_ROLE_OPTIONS if r != "skip"] + DIGITACO_ROLE_OPTIONS)
    return [{"role": r, "label_ja": ROLE_LABEL_JA[r]} for r in roles]


def needing_roles(source: str, file: str, head: list[str], body: list[list[str]], role_fn: Callable[[str], str | None], used: set[int],
                  blocking: bool, header_line: int) -> list[dict[str, Any]]:
    """役割が決まらなかった列（見出しがあり、中身のある列）。利用者が役割を選べるように、見本の値（3 つまで）と選択肢を付けて返す。
    blocking が真のときは、その列を決めないとファイルが読めない（開始・終了の列が無いなど）。"""
    out = []
    taken: set[str] = set()
    for j, h in enumerate(head):
        h = (h or "").strip()
        if not h or j in used or role_fn(h):
            continue
        vals: list[str] = []
        for r in body:
            v = ((r[j] if j < len(r) else "") or "").strip()
            if v and v not in vals:
                vals.append(v)
        if not vals:
            continue
        guess, ratio = best_role_for(vals)
        if guess == "start_time" and "start_time" in taken:
            guess = "end_time"  # 日時の列が 2 つ並ぶときは、2 つ目を終了にする
        ok = ratio >= 0.6 and guess in ROLE_LABEL_JA and blocking
        if ok:
            taken.add(guess)
        out.append({
            "source": source, "file": file, "column": h, "key": col_key(h), "samples": vals[:3], "blocking": blocking, "line": header_line,
            "suggested_role": guess if ok else None, "suggested_label_ja": ROLE_LABEL_JA.get(guess) if ok else None,
            "options": role_options(source),
        })
    return out


def judged_row(rows: list[list[str]]) -> int:
    """見出しとして見た行（先頭の 10 行のうち、空でないセルがいちばん多い行。同数なら上の行）。読めなかったファイルの説明と、列の役割の選択に使う。"""
    best, bi = -1, 0
    for i, r in enumerate(rows[:HEADER_SCAN_ROWS]):
        n = sum(1 for c in r if (c or "").strip())
        if n > best:
            best, bi = n, i
    return bi


def shape_signature(kind: str, head: list[str]) -> str:
    """ファイルの形の目印。種類と見出しの名前から作り、利用者が列の役割を選ぶ前と後で変わらない。日付の見出し（10/1(木) など）は「日付」とみなして月が変わっても同じにする。"""
    keys = sorted({("<日付>" if _date_head(h or "") else col_key(h)) for h in head if (h or "").strip()})
    return kind + "|" + "|".join(keys)


def dedupe_note(dropped: dict[str, Any]) -> str:
    n = dropped["runs"] + dropped["stops"]
    return f"同じ走行・停車の行 {n} 件（走行 {dropped['runs']}、停車 {dropped['stops']}）は 1 回だけ数えました" if n else ""


# ---------------------------------------------------------------- 3 つの記録の結び付け


def _hm(s: str) -> int:
    return int(s[:2]) * 60 + int(s[3:5])


def _time_diff_min(hint: str, actual_iso: str) -> int:
    """配車表の時刻の目安 − デジタコの時刻（時刻だけで比べ、日付をまたぐ差は短いほうに寄せる）。"""
    a = int(actual_iso[11:13]) * 60 + int(actual_iso[14:16])
    return (_hm(hint) - a + 720) % 1440 - 720


REST_JOIN_MIN, REST_JOIN_MAX = SHIFT_GAP_MIN, 16 * 60  # 走行と走行のあいだ（停車の行なし）がこの範囲なら、途中の休息とみなして 1 区間にする


def _join_rest_runs(run: dict[str, Any], nxt: dict[str, dict[str, Any]], used: set[str]) -> dict[str, Any]:
    """停車の行をはさまず 8〜16 時間あいて続く走行（1 人の運転者が途中で休息した長距離）を、1 つの区間にまとめる。
    開始は最初の走行、終了・到着地は最後の走行、距離は足し合わせ。まとめた走行は used に入れる（別の区間にしない）。"""
    cur, parts = run, [run]
    while cur.get("cont") and cur["id"] in nxt and nxt[cur["id"]]["id"] not in used:
        n = nxt[cur["id"]]
        gap = (_dt(n["start"]) - _dt(cur["end"])).total_seconds() / 60
        if not (REST_JOIN_MIN <= gap <= REST_JOIN_MAX):
            break
        used.add(n["id"])
        parts.append(n)
        cur = n
    if len(parts) == 1:
        return run
    kms = [p.get("km") for p in parts]
    return {**run, "end": parts[-1]["end"], "to": parts[-1].get("to"), "km": None if any(k is None for k in kms) else round(sum(kms), 1), "joined": [p["id"] for p in parts]}


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
    nxt = {d["id"]: n for d, n in zip(digitaco, digitaco[1:]) if n["plate"] == d["plate"]}
    for h in sorted(haisha, key=lambda x: (x["date"], x["plate"], x.get("idx", 0))):
        cands = [d for d in by_key.get((h["plate"], h["date"]), []) if d["id"] not in used]
        pick = None
        if cands:
            pick = cands[0]
            if h.get("arr") and len(cands) > 1:
                pick = min(cands, key=lambda d: abs(_time_diff_min(h["arr"], d["end"])))
            used.add(pick["id"])
            pick = _join_rest_runs(pick, nxt, used)
        ends = frozenset(x for x in (h.get("frm") or (pick or {}).get("frm"), h.get("to") or (pick or {}).get("to")) if x)
        est = est_km.get(ends)
        leg: dict[str, Any] = {
            "plate": h["plate"], "date": h["date"], "hid": h["id"], "did": pick["id"] if pick else None,
            "frm": h.get("frm") or (pick or {}).get("frm"), "to": h.get("to") or (pick or {}).get("to"),
            "empty_mark": bool(h.get("empty")), **({"weak_empty": True} if h.get("weak_empty") else {}), "has_ends": bool(h.get("frm") and h.get("to")), "local": bool(h.get("local")), "loaded_mark": bool(h.get("loaded_mark")), "round": h.get("round") or 1, "rounds": h.get("rounds") or 1,
            "weight_kg": h.get("weight_kg"), "weights_multi": h.get("weights") or None, "jk": pick["jk"] if pick else None, "driver": pick["driver"] if pick else None,
            "dep": pick["start"] if pick else None, "arr": pick["end"] if pick else None, "arr_hint": h.get("arr"),
            "flags": [], "sources": {}, "ref": h.get("ref"), "dref": pick.get("ref") if pick else None,
        }
        if pick and pick.get("joined"):
            leg["dids"] = pick["joined"]  # 途中の休息でまとめた走行の ID すべて（停車に曜日を付けるときに使う）
        if h.get("note"):
            leg["note"] = h["note"]  # 配車表のセルに付いていた、重さ・時刻・印のどれでもない語
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
            "dep": d["start"], "arr": d["end"], "arr_hint": None, "flags": [], "ref": None, "dref": d.get("ref"), **({"summary": True} if d.get("summary") else {}),
            "km": d["km"] if d.get("km") is not None else est,
            "sources": {"km": "デジタコ" if d.get("km") is not None else ("OSRM 推定" if est else None), "time": "デジタコ", "kind": None, "note": "配車表なし"},
        })
    return {"legs": legs + only_d, "digitaco_only": len(only_d), "haisha_only": sum(1 for x in legs if x["did"] is None)}


# ---------------------------------------------------------------- 実車・空車


RETURN_UNMARKED_SRC = "車庫へ戻る区間で、空の印も重さも無い"
WEAK_EMPTY_SRC = "「帰り」「戻り」だけで、空車とも実車とも書かれていない"


def classify_legs(legs: list[dict[str, Any]], base: str | None = None) -> dict[str, Any]:
    """実車・空車・不明を、筋書き §10.2 の順で決める。

    1 デジタコの実空区分（jk: 1 実車、0 空車）> 2 配車表（「空」などの印は空車、積地と降ろし地の両方があれば実車）
    > 3 請求書（invoiced が真なら実車）> 4 業務記録（log_loaded: 真なら実車、偽なら空車）> 5 不明。
    配車表だけの車は 2 で決まる。ただし base（自社の車庫）に戻る配車表の区間で、空車の印も重さも無いものは実車にせず不明にする
    （return_unmarked を真にして、確認に出す）。地場の配送（local）と、デジタコと結び付いた区間は対象外。
    返す dict: legs（kind と kind_src を足した写し）, counts, unknown_ratio。
    """
    out = []
    counts = {"loaded": 0, "empty": 0, "unknown": 0}
    for l in legs:
        l = dict(l)
        kind, src = "unknown", None
        if l.get("jk") in (0, 1):
            kind, src = ("loaded" if l["jk"] == 1 else "empty"), "デジタコの実空区分"
        elif l.get("empty_mark") and l.get("weak_empty"):  # 「帰り」「戻り」だけの印は、空車と決めずに確認に出す
            kind, src = "unknown", WEAK_EMPTY_SRC
            l["return_unmarked"] = True
        elif l.get("empty_mark"):
            kind, src = "empty", "配車表の（空）の印"
        elif l.get("has_ends"):
            if (base and l.get("to") == base and l.get("frm") != base and l.get("hid") is not None and l.get("weight_kg") is None
                    and not l.get("weights_multi") and not l.get("local") and not l.get("loaded_mark")):
                kind, src = "unknown", RETURN_UNMARKED_SRC
                l["return_unmarked"] = True
            else:
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


LADEN_NOTE_MAX = 8  # 画面に出す語の数


def laden_words(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """配車表の積地・降ろし地があるので実車として数えた区間のうち、セルに空車の印でも重さでも時刻でもない語が付いていたものを、語ごとに数える。
    戻り値は [{word, count, example}]（多い順。word は空白で区切った 1 語、example はその語が付いたセルの文字）。印にならなかった語が空車を意味していたときに、利用者が気づくための一覧。"""
    cnt: dict[str, dict[str, Any]] = {}
    for l in legs:
        if l.get("kind") != "loaded" or l.get("kind_src") != "配車表の積地・降ろし地" or not l.get("note"):
            continue
        for w in str(l["note"]).split(" "):
            if not w:
                continue
            e = cnt.setdefault(w, {"word": w, "count": 0, "example": ((l.get("ref") or {}).get("text") or "").strip()[:40]})
            e["count"] += 1
    return sorted(cnt.values(), key=lambda e: (-e["count"], e["word"]))[:LADEN_NOTE_MAX]


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
VALUE_SHAPES: dict[str, tuple[str, Callable[[str], bool]]] = {  # 日時・数・区分などは NFKC で全角を直してから当てる（NFKC_SHAPES。web/own_calc.js の VALUE_SHAPES と同じ）
    "datetime": ("YYYY-MM-DD HH:MM の日時", lambda v: bool(re.fullmatch(r"[0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}[ T][0-9]{1,2}:[0-9]{2}(:[0-9]{2})?", v))),
    "date_header": ("月/日 の日付の見出し", lambda v: bool(re.fullmatch(r"[0-9]{1,2}/[0-9]{1,2}(\(.\))?", v) or re.fullmatch(r"[0-9]{1,2}月[0-9]{1,2}日(\(.\))?", v)
                                                          or re.fullmatch(r"[0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}(\(.\))?", v))),
    "flag01": ("0 か 1", lambda v: v in ("0", "1") + JK_LOADED + JK_EMPTY),
    "code12": ("1 か 2", lambda v: v in ("1", "2") + KIND_RUN + KIND_STOP),
    "driver_code": ("英大文字 1 字と数字 2 桁", lambda v: bool(re.fullmatch(r"[A-Z][0-9]{2}", v))),
    "plate": ("4 桁の番号をふくむ車番", lambda v: normalize_plate(v) is not None),
    "vehicle_type": ("「大型」か「数字 + t」", lambda v: bool(re.fullmatch(r"大型|[0-9]+t", v))),
    "decimal": ("小数まじりの数字", lambda v: bool(re.fullmatch(r"[0-9]+(\.[0-9]+)?", v))),
    "place_kanji": ("漢字・かなだけの名称", lambda v: bool(_KANA_KANJI.match(v))),
    "wait_min": ("分か 時:分の数", lambda v: parse_wait_min(v) is not None),
    "restraint": ("時:分か時間の数", lambda v: parse_restraint_hours(v) is not None),
}
ROLE_SHAPE = {  # 役割 → 期待する値の形
    "kind": "code12", "start_time": "datetime", "end_time": "datetime", "distance_km": "decimal", "loaded_flag": "flag01",
    "place_name": "place_kanji", "driver_code": "driver_code", "plate": "plate", "vehicle_type": "vehicle_type", "date": "date_header",
    "loaded_km": "decimal", "empty_km": "decimal", "wait_min": "wait_min", "restraint": "restraint",
}
ROLE_LABEL = {
    "kind": "区分（走行・停車）", "start_time": "開始の日時", "end_time": "終了の日時", "distance_km": "走行距離（km）",
    "loaded_flag": "実車・空車の区分", "place_name": "停車の地点名", "driver_code": "運転者コード", "plate": "車番",
    "vehicle_type": "車種", "date": "日付", "loaded_km": "実車で走った距離（km）", "empty_km": "空車で走った距離（km）",
    "wait_min": "荷待ちの時間", "restraint": "拘束時間",
}


def column_reason_ja(source: str, column: str, role: str | None, hit: bool, ratio: float, exp: str) -> str:
    """列の対応づけの理由を、辞書・形・確かさといった言葉を使わずに書く。web/own_calc.js の columnReasonJa と同じ。"""
    pct = round(ratio * 100)
    label = ROLE_LABEL.get(role or "", "")
    if hit and source == "配車表" and column.startswith("日付の見出し"):
        return "見出しが「10/1(木)」のように月と日で書かれている列を、日付の列として読みます。"
    if hit:
        t = f"列の名前が「{column}」なので、{label}として読みます。"
        if ratio < 0.9:
            t += f" ただし、中身のうち「{exp}」に見えるのは {pct}% だけなので、念のため確かめてください。"
        return t
    if label and ratio > 0:
        return f"列の名前からは分かりません。中身の {pct}% が「{exp}」に見えるので、{label}かもしれません。合っていれば確定してください。"
    return "列の名前からも中身からも分からないので、読まずに進めます。"


NFKC_SHAPES = ("datetime", "date_header", "flag01", "code12", "decimal", "driver_code")  # 全角を直してから当てる形（車種と地点名は、サンプルの黄色の例のため元の文字のまま）


def _norm_vals(values: list[str], shape_key: str | None = None) -> list[str]:
    vs = [(unicodedata.normalize("NFKC", v or "") if shape_key in NFKC_SHAPES else (v or "")).strip() for v in values]
    return [v for v in vs if v]


def shape_ratio(values: list[str], shape_key: str) -> float:
    vs = _norm_vals(values, shape_key)
    if not vs:
        return 0.0
    ok = VALUE_SHAPES[shape_key][1]
    return sum(1 for v in vs if ok(v)) / len(vs)


def shape_summary(values: list[str]) -> dict[str, float]:
    """値そのものを含まない、形の説明の割合（Claude に送ってよい形）。"""
    vs = _norm_vals(values)
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
        if role in ("wait_min", "restraint"):
            continue  # 値の形だけでは、ほかの時刻の列と区別できないので、列の名前が辞書に当たったときだけ使う
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


def over_max_legs(legs: list[dict[str, Any]], vehicles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """積んだ重さが、その車の最大積載量を超える区間。積載率・積載効率の平均には入れず、確認のカードと気づきに出す。"""
    cap = {v["plate"]: v.get("max_load_kg") for v in vehicles}
    return [l for l in legs if l.get("weight_kg") and cap.get(l["plate"]) and l["weight_kg"] > cap[l["plate"]]]


def _tons(kg: float) -> str:
    return f"{round(kg / 1000, 1):.1f}"


def tons_pair(a_kg: float, b_kg: float) -> tuple[str, str]:
    """2 つの重さを、丸めても違いが見える桁数で文字にする（「2,700kg が 2,700kg を超えます」にならないように）。
    t の小数 1 桁から 3 桁まで増やし、それでも同じなら kg の小数 2 桁。単位をふくむ文字を返す。web/own_calc.js の tonsPair と同じ。"""
    for nd in (1, 2, 3):
        x, y = f"{round(a_kg / 1000, nd):.{nd}f}", f"{round(b_kg / 1000, nd):.{nd}f}"
        if x != y:
            return x + "t", y + "t"
    return f"{a_kg:,.2f}kg", f"{b_kg:,.2f}kg"


def zero_weight_legs(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """配車表に「0t」と書いてある実車の区間（重さが空欄の区間は入れない）。積載率の平均から外れるので、確認のカードに出す。"""
    return [l for l in legs if l.get("kind") == "loaded" and l.get("weight_kg") == 0 and l.get("weight_kg") is not None]


def multi_weight_legs(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1 つのセルに重さが 2 つ以上書いてある区間（どちらか分からないので、重さなしで数える）。"""
    return [l for l in legs if l.get("weights_multi")]


def weight_confirm_items(legs: list[dict[str, Any]], vehicles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """重さの確認のカード（id は呼び出し側が付ける）。区間ごとに 1 枚。1 セルに重さが複数、実車で 0t、最大積載量を超える重さ、の順に当てはめる。"""
    cap = {v["plate"]: v.get("max_load_kg") for v in vehicles}
    over = {id(l) for l in over_max_legs(legs, vehicles)}
    zero = {id(l) for l in zero_weight_legs(legs)}
    multi = {id(l) for l in multi_weight_legs(legs)}
    out = []
    for l in legs:
        if id(l) not in over and id(l) not in zero and id(l) not in multi:
            continue
        item = {"id": "", "kind": "weight", "source": "配車表", "title": f"{_md(l['date'])} 車番 {l['plate']} の積んだ重さ", "confidence": None, "color": "yellow", "leg": l["id"],
                "claude": None}
        if id(l) in multi:
            ws = "、".join(f"{_tons(k)}t" for k in l["weights_multi"])
            item.update(original=f"1 つのセルに重さが {len(l['weights_multi'])} つ書いてあります（{ws}）",
                        proposal="どれも使わず、重さなしで数える（正しい重さを「修正」で入れると、積載率に入れます）",
                        formula="重さが 2 つ以上あるセルは、どれが積荷の重さか分からないので、率に入れません")
        elif id(l) in zero:
            item.update(original="配車表の重さが 0t の実車の区間です",
                        proposal="積載率・積載効率に入れない（積んでいないか、重さの書き忘れかもしれません）",
                        formula="0t の実車の区間は、率に入れません")
        else:
            a, b = tons_pair(l["weight_kg"], cap[l["plate"]])
            vv = next((x for x in vehicles if x["plate"] == l["plate"]), {})
            item.update(over_max=True, plate=l["plate"], cls=vv.get("cls"), cap_kg=cap[l["plate"]], cap_assumed=bool(vv.get("max_load_assumed")),
                        cap_src=vv.get("max_load_src") or ("assumed" if vv.get("max_load_assumed") else None))
            item.update(original=f"配車表の重さ {a} が、最大積載量 {b} を超えています",
                        proposal="積載率・積載効率に入れない（t と kg の取り違えや、桁の間違いかもしれません）",
                        formula="積んだ重さが最大積載量を超える区間は、率に入れません")
        out.append(item)
    return out


def kpis(legs: list[dict[str, Any]], vehicles: list[dict[str, Any]]) -> dict[str, Any]:
    """実車率、大型の積載率、大型の積載効率。割合には、計算できた区間の割合を添える。legs は classify_legs の結果。

    積んだ重さが最大積載量を超える区間は積載率に入れない（over_max にその件数）。"""
    cls = {v["plate"]: v for v in vehicles}
    over_ids = {id(l) for l in over_max_legs(legs, vehicles)}
    n = len(legs)
    judged = [l for l in legs if l["kind"] in ("loaded", "empty")]  # 実車か空車かが分かった区間。距離の有無とは関係ない（率には距離のある区間だけが入る）

    def rate(ls: list[dict[str, Any]]) -> float | None:
        tot = sum(l.get("km") or 0 for l in ls)
        return (sum(l.get("km") or 0 for l in ls if l["kind"] == "loaded") / tot) if tot else None

    large = [l for l in legs if cls.get(l["plate"], {}).get("cls") == "大型"]
    large_judged = [l for l in large if l["kind"] in ("loaded", "empty")]
    with_w = [l for l in large if l["kind"] == "loaded" and l.get("weight_kg") and cls[l["plate"]].get("max_load_kg") and id(l) not in over_ids]
    load_rate = (sum(l["weight_kg"] / cls[l["plate"]]["max_load_kg"] for l in with_w) / len(with_w)) if with_w else None
    large_rate = rate(large_judged)
    # 実車か空車か分からない区間（不明）を空車とした場合の率。不明の区間は km があるものだけが入る（km が無い区間は、どちらにしても率に入らない）
    with_km = [l for l in legs if l["kind"] in ("loaded", "empty", "unknown") and l.get("km")]
    tot_km = sum(l["km"] for l in with_km)
    unknown_km = [l for l in with_km if l["kind"] == "unknown"]
    return {
        "loaded_rate_if_unknown_empty": (sum(l["km"] for l in with_km if l["kind"] == "loaded") / tot_km) if tot_km else None,
        "empty_rate_if_unknown_empty": (sum(l["km"] for l in with_km if l["kind"] != "loaded") / tot_km) if tot_km else None,
        "unknown_as_empty": {"legs": len(with_km), "unknown_legs": len(unknown_km), "unknown_legs_without_km": sum(1 for l in legs if l["kind"] == "unknown" and not l.get("km")),
                             "formula": "不明の区間を空車として数えた場合の率（km が分かる区間だけ）"},
        "actual_rate": {"value": rate(judged), "legs": len(judged), "legs_with_km": sum(1 for l in judged if l.get("km")), "of": n, "share": (len(judged) / n) if n else 0.0,
                        "km_loaded": round(sum(l.get("km") or 0 for l in judged if l["kind"] == "loaded"), 1), "km_empty": round(sum(l.get("km") or 0 for l in judged if l["kind"] == "empty"), 1),
                        "formula": "実車の km ÷ 走った km（実車か空車かが判定できた区間だけ）",
                        **({"basis": "距離の合計", "formula": "距離の合計（日ごとの集計の実車距離の合計 ÷ 走行距離の合計）"} if judged and all(l.get("summary") for l in judged) else {})},
        "load_rate": {"value": load_rate, "legs": len(with_w), "of_large": len(large), "of": n, "share": (len(with_w) / len(large)) if large else 0.0,  # 割合の分母は、画面の文が書く「大型の区間」
                      "over_max": len(over_ids), "zero_weight": len(zero_weight_legs(legs)), "multi_weight": len(multi_weight_legs(legs)),
                      "formula": "積んだ重さ ÷ 最大積載量（大型の実車の区間のうち、重さが分かる区間の平均。最大積載量を超える重さの区間は入れません）"},
        "large_actual_rate": {"value": large_rate, "legs": len(large_judged), "of_large": len(large)},
        "load_efficiency": {"value": (load_rate * large_rate) if (load_rate is not None and large_rate is not None) else None,
                            "formula": "大型の積載率 × 大型の実車率"},
    }


def weekday_table(legs: list[dict[str, Any]], stops: list[dict[str, Any]], dock: str | None = None) -> list[dict[str, Any]]:
    """区間 × 向き × 曜日の表。曜日（wd）は、行きも帰りも、その便自身が出た日の曜日（帰りは実出発の日、無ければ記録日）。
    帰りを前の日の行きに結び付けない。cycle_wd の決め方は web/own_calc.js の analyze と同じ。

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


def leg_pair(l: dict[str, Any], base: str | None = None, dock: str | None = None) -> tuple[str | None, str | None]:
    """区間の (発地, 着地)。両方の地点が分かっていればそのまま。どちらかが無く dir だけあるときは、dir（out: 自社 → 主な行き先、back: 主な行き先 → 自社）から。
    「着地が車庫か」で向きを決めると、大井・名古屋・横浜・福岡からの帰りがすべて同じ区間に入るので、実際の発地と着地の組で数える。"""
    frm, to = l.get("frm"), l.get("to")
    if frm and to:
        return frm, to
    if base and dock:
        if l.get("dir") == "out":
            return base, dock
        if l.get("dir") == "back":
            return dock, base
    return None, None


def pair_role(frm: str, to: str, base: str | None) -> str | None:
    """区間が自社の車庫から出る（out）か、車庫へ戻る（back）か。どちらでもなければ None。"""
    if base and to == base and frm != base:
        return "back"
    if base and frm == base and to != base:
        return "out"
    return None


def leg_wd(l: dict[str, Any]) -> int:
    """区間 × 曜日の表と気づきが数える曜日（cycle_wd。その便自身が出た日の曜日）。無い区間は、その便自身の日付の曜日。web/own_calc.js の legWd と同じ。"""
    wd = l.get("cycle_wd")
    return wd if isinstance(wd, int) and not isinstance(wd, bool) else date.fromisoformat(l["date"]).weekday()


def pair_tables(legs: list[dict[str, Any]], places: dict[str, dict[str, Any]], vehicles: list[dict[str, Any]], base: str | None = None, dock: str | None = None) -> dict[str, Any]:
    """区間を、実際の発地と着地の組ごとに数える（曜日の表・気づき・地図・求荷求車の文に使う）。web/own_calc.js の pairTables と同じ。

    pairs: [{id, frm, to, frm_label, to_label, label, role, trips, loaded, empty, unknown, empty_km, km, classes, empty_classes, plates, by_weekday, leg_ids}]
      classes は {車格: 便数}（全部の便）、empty_classes は空車の便だけ。by_weekday は [{wd, trips, empty, unknown, empty_km, classes, empty_classes}]（曜日は区間の日付の曜日。月 = 0）。
      並びは、空車の便が多い順、便数が多い順、名前の順。発地と着地が同じ区間（地場の配送）は数えない。
    pair_table: 組 × 曜日の平らな表 [{pair, frm, to, label, role, wd, weekday, trips, empty, unknown, empty_km, classes, empty_classes, leg_ids}]（組の並びのまま、曜日は月から）。
    """
    cls_of = {v["plate"]: (v.get("cls") or class_of(v.get("type_raw"))) for v in vehicles}

    def nm(pid: str) -> str:
        p = places.get(pid) or {}
        return p.get("short") or p.get("pseudo") or "?"

    acc: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for l in legs:
        fr, to = leg_pair(l, base, dock)
        if not fr or not to or fr == to:
            continue
        acc.setdefault((fr, to), []).append(l)

    def tally(ls: list[dict[str, Any]]) -> dict[str, Any]:
        cl: dict[str, int] = {}
        ecl: dict[str, int] = {}
        for l in ls:
            c = cls_of.get(l["plate"], "不明")
            cl[c] = cl.get(c, 0) + 1
            if l["kind"] == "empty":
                ecl[c] = ecl.get(c, 0) + 1
        return {
            "trips": len(ls), "empty": sum(1 for l in ls if l["kind"] == "empty"), "unknown": sum(1 for l in ls if l["kind"] == "unknown"),
            "empty_km": round(sum(l.get("km") or 0 for l in ls if l["kind"] == "empty"), 1),
            "classes": dict(sorted(cl.items())), "empty_classes": dict(sorted(ecl.items())),
        }

    items = []
    sorted_legs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (fr, to), raw in acc.items():
        ls = sorted(raw, key=lambda x: (x["date"], x["plate"]))
        sorted_legs[(fr, to)] = ls
        t = tally(ls)
        by_wd = []
        for wd in range(7):
            wl = [l for l in ls if leg_wd(l) == wd]
            if wl:
                by_wd.append({"wd": wd, **tally(wl), "leg_ids": [l.get("id") for l in wl]})
        items.append({
            "frm": fr, "to": to, "frm_label": nm(fr), "to_label": nm(to), "label": f"{nm(fr)} → {nm(to)}", "role": pair_role(fr, to, base),
            "trips": t["trips"], "loaded": sum(1 for l in ls if l["kind"] == "loaded"), "empty": t["empty"], "unknown": t["unknown"], "empty_km": t["empty_km"],
            "km": round(sum(l.get("km") or 0 for l in ls), 1), "classes": t["classes"], "empty_classes": t["empty_classes"],
            "plates": sorted({l["plate"] for l in ls}), "by_weekday": by_wd, "leg_ids": [l.get("id") for l in ls],
        })
    items.sort(key=lambda x: (-x["empty"], -x["trips"], x["label"], x["frm"], x["to"]))
    table = []
    for n, it in enumerate(items, start=1):
        it["id"] = f"P{n}"
        for w in it["by_weekday"]:
            table.append({"pair": it["id"], "frm": it["frm"], "to": it["to"], "label": it["label"], "role": it["role"], "wd": w["wd"], "weekday": WEEKDAYS[w["wd"]],
                          "trips": w["trips"], "empty": w["empty"], "unknown": w["unknown"], "empty_km": w["empty_km"],
                          "classes": w["classes"], "empty_classes": w["empty_classes"], "leg_ids": w["leg_ids"]})
    return {"pairs": items, "pair_table": table}


# ---------------------------------------------------------------- 復路の根拠（SPEC §44。復路の候補の母数と、区間を確認できた便を分ける）
# web/own_calc.js の returnWhen / returnTrips / returnTarget / returnPairs と同じ結果になる（試験 tests/test_v44_negotiate.py で突き合わせる）。
# 曜日は「復路の便そのものが出発した日時」から求める。前日の往路の曜日（cycle_wd）は使わない。実出発時刻が無いときは便の記録日を使い、根拠を date にする。

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_ISO_DT = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")


def _iso_date_ok(s: Any) -> date | None:
    if not isinstance(s, str):
        return None
    m = _ISO_DATE.match(s)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(0))
    except ValueError:
        return None


def src_cat(s: Any) -> str:
    """値の出どころの文字を、digitaco / haisha / estimate / user / unknown のどれかにする。出どころが無い・読み取れないものは unknown。"""
    if s is None or str(s) == "":
        return "unknown"
    t = str(s)
    if "デジタコ" in t:
        return "digitaco"
    if "配車表" in t:
        return "haisha"
    if "推定" in t or "OSRM" in t or "直線" in t:
        return "estimate"
    if "利用者" in t:
        return "user"
    return "unknown"


def return_when(l: dict[str, Any]) -> tuple[str | None, str, int | None]:
    """復路の便の曜日を決める日時。(使った日時, 根拠 'dep'|'date'|'none', 曜日 月=0)。dep は復路の便自身の実出発。無ければ記録日（時刻は不明）。"""
    dep = l.get("dep")
    if isinstance(dep, str):
        m = _ISO_DT.match(dep)
        d = _iso_date_ok(dep)
        if m and d:
            return f"{m.group(1)}T{m.group(2)}", "dep", d.weekday()
    d = _iso_date_ok(l.get("date"))
    if d:
        return d.isoformat(), "date", d.weekday()
    return None, "none", None


def _km_of(l: dict[str, Any]) -> float | None:
    v = l.get("km")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v >= 0 else None


def _src_of(l: dict[str, Any], key: str) -> Any:
    s = l.get(key + "_src")
    if s is None:
        s = (l.get("sources") or {}).get(key)
    return s


def _ret_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    e = [r for r in rows if r["kind"] == "empty"]
    kms = [r["km"] for r in e if r["km"] is not None]
    n_e, n_l = len(e), sum(1 for r in rows if r["kind"] == "loaded")
    return {"trips": len(rows), "empty": n_e, "loaded": n_l, "unknown": len(rows) - n_e - n_l, "judged": n_e + n_l,
            "empty_km": round(sum(kms), 1), "km_missing": n_e - len(kms)}


def _ret_basis(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {b: sum(1 for r in rows if r["dt_basis"] == b) for b in ("dep", "date", "none")}


def _dt_src(l: dict[str, Any], basis: str) -> str:
    if basis == "dep":
        return src_cat(_src_of(l, "time"))
    if basis == "date":
        return "haisha" if l.get("ref") else ("digitaco" if l.get("dref") else "unknown")
    return "unknown"


def return_trips(legs: list[dict[str, Any]], fr: str, to: str, base: str | None = None, dock: str | None = None, pair_id: str | None = None) -> dict[str, Any]:
    """発地 fr の復路の候補（fr → to の区間に入る復路の便）と、そのうち着地を確認できた便（to が記録にある便）を数え、便ごとの根拠の行を返す。
    to が無い便（着地が未確認）は候補には入るが、区間確定には入れない。完全に同じ行（車番・日時・区間・距離・区分・周回が同じ）は 1 便に数える。"""
    pid = pair_id or f"{fr}>{to}"
    rows, seen, dup = [], set(), 0
    for l in legs:
        if l.get("dir") != "back" or leg_pair(l, base, dock) != (fr, to):
            continue
        key = (l.get("plate"), l.get("date"), l.get("dep"), l.get("arr"), l.get("frm"), l.get("to"), l.get("km"), l.get("kind"), l.get("round"))
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        dt, basis, wd = return_when(l)
        ref, dref = l.get("ref") or None, l.get("dref") or None
        kind = l.get("kind") if l.get("kind") in ("empty", "loaded") else "unknown"
        km = _km_of(l)
        rows.append({
            "leg_id": l.get("id"), "row_id": f"{ref.get('file')}:{ref.get('where')}" if ref else (l.get("id") or ""), "pair_id": pid,
            "ref": ref, "dref": dref, "plate": l.get("plate"), "driver": l.get("driver"),
            "dt": dt, "dt_basis": basis, "dt_src": _dt_src(l, basis), "arr": l.get("arr"), "wd": wd,
            "kind": kind, "kind_src": l.get("kind_src") if kind != "unknown" else None,
            "kind_src_cat": src_cat(l.get("kind_src")) if kind != "unknown" else "unknown",
            "km": km, "km_src_cat": src_cat(_src_of(l, "km")) if km is not None else "unknown",
            "dest_confirmed": bool(l.get("frm") == fr and l.get("to") == to),
        })
    rows.sort(key=lambda r: (r["dt"] or "~", str(r["leg_id"])))
    conf = [r for r in rows if r["dest_confirmed"]]
    unc = [r for r in rows if not r["dest_confirmed"]]
    days = sorted((r["dt"] or "")[:10] for r in rows if r["dt"])
    by_wd = []
    for wd in range(7):
        w = [r for r in conf if r["wd"] == wd]
        if w:
            by_wd.append({"wd": wd, "weekday": WEEKDAYS[wd], **_ret_counts(w), "basis": _ret_basis(w)})
    return {
        "fr": fr, "to": to, "pair_id": pid, "candidates": _ret_counts(rows), "confirmed": _ret_counts(conf), "unconfirmed": _ret_counts(unc),
        "duplicates_dropped": dup, "period": {"first": days[0], "last": days[-1]} if days else None,
        "basis": _ret_basis(conf), "no_weekday": sum(1 for r in conf if r["wd"] is None), "by_weekday": by_wd, "rows": rows,
    }


def return_target(model: dict[str, Any], wd: int | None) -> dict[str, Any]:
    """選んだ曜日の見方。その曜日に出た区間確定の復路（confirmed）と、それ以外の候補（others。着地が未確認の便も含む）を分けて数える。"""
    rows = model["rows"]
    idx = {i for i, r in enumerate(rows) if wd is not None and r["dest_confirmed"] and r["wd"] == wd}
    tgt = [r for i, r in enumerate(rows) if i in idx]
    oth = [r for i, r in enumerate(rows) if i not in idx]
    c = _ret_counts(tgt)
    return {
        "wd": wd, "weekday": WEEKDAYS[wd] if wd is not None else None, "confirmed": c, "basis": _ret_basis(tgt),
        "unconfirmed_dest_on_wd": sum(1 for r in rows if wd is not None and not r["dest_confirmed"] and r["wd"] == wd),
        "all_empty": c["trips"] > 0 and c["empty"] == c["trips"], "has_empty": c["empty"] > 0, "others": _ret_counts(oth),
        "dates": [r["dt"] for r in tgt], "rows": tgt,
    }


def return_pairs(legs: list[dict[str, Any]], places: dict[str, dict[str, Any]] | None = None, base: str | None = None, dock: str | None = None) -> list[dict[str, Any]]:
    """復路（dir が back）の区間ごとに、確認できた空車の曜日の候補を並べる。担当者が区間と曜日を選ぶための一覧。空車の多い区間が先。"""
    places = places or {}

    def nm(pid: str) -> str:
        p = places.get(pid) or {}
        return p.get("short") or p.get("pseudo") or "?"

    keys: list[tuple[str, str]] = []
    for l in legs:
        if l.get("dir") != "back":
            continue
        p = leg_pair(l, base, dock)
        if p[0] and p[1] and p[0] != p[1] and p not in keys:
            keys.append(p)
    out = []
    for fr, to in keys:
        m = return_trips(legs, fr, to, base, dock)
        wds = [{"wd": w["wd"], "weekday": w["weekday"], "trips": w["trips"], "empty": w["empty"]} for w in m["by_weekday"] if w["empty"] > 0]
        best = sorted(wds, key=lambda w: (-w["empty"], -(w["empty"] / w["trips"]), w["wd"]))
        out.append({"fr": fr, "to": to, "key": f"{fr}>{to}", "fr_label": nm(fr), "to_label": nm(to), "label": f"{nm(fr)} → {nm(to)}",
                    "candidates": m["candidates"]["trips"], "confirmed": m["confirmed"]["trips"], "confirmed_empty": m["confirmed"]["empty"],
                    "confirmed_empty_km": m["confirmed"]["empty_km"], "empty_km": m["candidates"]["empty_km"],
                    "weekdays": wds, "default_wd": best[0]["wd"] if best else None})
    # 空車で走る距離（km）の多い区間を先に並べる（便の数が多い区間ではなく、手を打つ価値の大きい区間を最初の画面に出す。SPEC §49）
    out.sort(key=lambda x: (-x["confirmed_empty_km"], -x["empty_km"], -x["confirmed_empty"], -x["candidates"], x["label"], x["fr"], x["to"]))
    return out


def apply_max_payload(vehicles: list[dict[str, Any]], by_plate: dict[str, Any] | None = None, by_class: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """車ごと・車格ごとの最大積載量（kg）を車両に当てた写しを返す。by_plate が by_class より先。値が数でない・0 以下なら使わない。
    当てた車は max_load_kg を変え、max_load_assumed を偽、max_load_src を 'plate' か 'class' にする。web/own_calc.js の applyMaxPayload と同じ。"""
    out = []
    bp, bc = by_plate or {}, by_class or {}
    for v in vehicles:
        v = dict(v)
        for src, key, tbl in (("plate", v["plate"], bp), ("class", v.get("cls"), bc)):
            val = tbl.get(key) if key is not None else None
            if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                v["max_load_kg"], v["max_load_assumed"], v["max_load_src"] = float(val), False, src
                break
        out.append(v)
    return out


LARGE_ASSUMED_KG = 10000   # 大型の最大積載量の仮定（登録が無いとき）
LARGE_RAISE_CEIL_KG = 20000  # 仮定を上げる上限。これを超える重さは、t と kg の取り違えや桁の間違いかもしれないので、上げずに「超える重さ」のカードに出す


def raise_assumed_max(vehicles: list[dict[str, Any]], legs: list[dict[str, Any]], ceiling_kg: float = LARGE_RAISE_CEIL_KG) -> dict[str, Any]:
    """登録が無く仮定した最大積載量（max_load_assumed が真）の車に、仮定を超える重さの記録があるとき、その車格の仮定を、記録の最大（ceiling_kg まで）に上げる。
    黙って外さないための処理で、利用者が車・車格ごとに決めた値（assumed が偽）は変えない。戻り値は {vehicles: 写し, note: None か {cls, from_kg, to_kg, legs, vehicles}}。
    note.legs は、元の仮定を超えていた区間の数。web/own_calc.js の raiseAssumedMax と同じ。"""
    out = [dict(v) for v in vehicles]
    by = {v["plate"]: v for v in out if v.get("max_load_assumed") and v.get("max_load_kg")}
    top: dict[str, float] = {}
    n_over: dict[str, int] = {}
    for l in legs:
        v = by.get(l.get("plate"))
        w = l.get("weight_kg")
        if not v or not w or w <= v["max_load_kg"]:
            continue
        c = v.get("cls") or ""
        n_over[c] = n_over.get(c, 0) + 1
        if w <= ceiling_kg and w > top.get(c, 0):
            top[c] = w
    notes = []
    for c, mx in top.items():
        frm = None
        n_v = 0
        for v in by.values():
            if (v.get("cls") or "") == c and v["max_load_kg"] < mx:
                frm = v["max_load_kg"] if frm is None else min(frm, v["max_load_kg"])
                v["max_load_kg"] = float(mx)
                n_v += 1
        if n_v:
            notes.append({"cls": c, "from_kg": frm, "to_kg": float(mx), "legs": n_over.get(c, 0), "vehicles": n_v})
    return {"vehicles": out, "note": notes[0] if notes else None}


# ---------------------------------------------------------------- 気づき（筋書き §11.2、6 規則）


def _md(iso_date: str) -> str:
    return f"{int(iso_date[5:7])}/{int(iso_date[8:10])}"


RULE_LABEL_JA = {  # 気づきの名前（「規則 1」ではなく、中身が分かる言い方）
    1: "同じ曜日の帰りがいつも空", 2: "空で走った区間", 3: "同じ場所での長い待ち", 4: "拘束時間が長い日", 5: "積載の記録の対象なのに重さが空",
    6: "実車か空車か分からない区間が多い", 7: "最大積載量を超える重さ",
}


def place_phrase(p: dict[str, Any], pid: str, own_names: Any = False) -> str:
    """気づきの文に出す地点の言い方。サンプルは「江東区の地点 07」。自社のファイル（own_names）は、ファイルに書いてあった地名のまま（「江東区辰巳」）。
    web/own_calc.js の placePhrase と同じ。"""
    if own_names:
        label = (p.get("label") or p.get("muni") or pid)[:16]
        muni = p.get("muni") or ""
        return f"{label}（{muni}）" if muni and muni not in label else label
    muni = p.get("muni") or ""
    return f"{muni}の{p.get('pseudo', pid)}" if muni else str(p.get("pseudo", pid))


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
    out: list[dict[str, Any]] = []

    def add(rule: int, text: str, numbers: list[dict[str, str]], nxt: dict[str, Any], evidence: list[dict[str, Any]], label: str | None = None) -> None:
        out.append({"id": f"I{len(out) + 1}", "rule": rule, "rule_label_ja": label or RULE_LABEL_JA[rule], "text": text, "numbers": numbers, "next": nxt, "evidence": evidence})

    known = [l for l in legs if l["kind"] in ("loaded", "empty")]

    # 規則 1: ある区間（実際の発地と着地の組）・曜日で、空車の便が 4 便以上のうち 3/4 以上
    def pr(l: dict[str, Any]) -> tuple[str | None, str | None]:
        return leg_pair(l, base, dock)

    def kind_of(fr: str, to: str) -> str:
        return pair_role(fr, to, base) or "pair"

    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for l in known:
        fr, to = pr(l)
        if l.get("cycle_wd") is not None and fr and to and fr != to:
            groups.setdefault((fr, to, l["cycle_wd"]), []).append(l)
    hits = []
    for (fr, to, wd), ls in groups.items():
        n, e = len(ls), sum(1 for l in ls if l["kind"] == "empty")
        if n >= R1_MIN_TRIPS and e * R1_RATIO[1] >= n * R1_RATIO[0]:
            hits.append((e, n, fr, to, wd, ls))
    rank = {"back": 0, "out": 1, "pair": 2}
    pair_all_empty: dict[tuple[str, str], list[int]] = {}  # F10: ある区間の、便のある曜日がどれも全部空なら、曜日ごとのカードを出さず 1 枚にまとめる
    for (fr, to, wd), ls in groups.items():
        pair_all_empty.setdefault((fr, to), []).append(wd)
    merged_done: set[tuple[str, str]] = set()
    for e, n, fr, to, wd, ls in sorted(hits, key=lambda h: (-h[0], h[1], rank[kind_of(h[2], h[3])], f"{short(h[2])} → {short(h[3])}", h[4])):
        d = kind_of(fr, to)
        all_wds = sorted(pair_all_empty.get((fr, to), []))
        all_legs = [l for (f2, t2, w2), g in groups.items() if f2 == fr and t2 == to for l in g]
        if len(all_wds) >= 2 and all(l["kind"] == "empty" for l in all_legs) and sum(1 for h in hits if h[2] == fr and h[3] == to) >= 2:
            if (fr, to) in merged_done:
                continue
            merged_done.add((fr, to))
            lb = f"{short(fr)} → {short(to)}"
            word = {"back": "帰り", "out": "行き", "pair": "区間"}[d]
            wds = "・".join(WEEKDAYS[w] for w in all_wds)
            text = (f"{lb}の{word}は、{len(all_legs)} 便がすべて空でした（{wds}曜のどの曜日も空で、曜日による違いはありません。実車か空車かが分かった便だけ）。")
            payload = {"dir": d, "wd": wd, "weekday": WEEKDAYS[wd], "trips": len(all_legs), "empty": len(all_legs), "night": False, "month": data.get("month"),
                       "frm": fr, "to": to, "frm_label": short(fr), "to_label": short(to), "label": lb, "all_weekdays": True, "weekdays": [WEEKDAYS[w] for w in all_wds]}
            add(1, text, [{"k": lb, "v": f"{len(all_legs)} 便中 {len(all_legs)} 便が空"}, {"k": "空だった曜日", "v": f"{wds}（どの曜日も空）"}],
                {"label": "帰り便の下書きにする", "action": "draft_backhaul", "payload": payload},
                [_ev_leg(l) for l in sorted(all_legs, key=lambda x: x["date"])],
                {"back": "毎回の帰りが空（曜日の違いなし）", "out": "毎回の行きが空（曜日の違いなし）", "pair": "毎回空の区間（曜日の違いなし）"}[d])
            continue
        others = [l for g_key, g in groups.items() if g_key[0] == fr and g_key[1] == to and g_key[2] != wd for l in g]
        oe = sum(1 for l in others if l["kind"] == "empty")
        night = all((l.get("cycle_dep") or "")[11:13].isdigit() and int(l["cycle_dep"][11:13]) >= 18 for l in ls)
        when = f"{WEEKDAYS[wd]}曜" + ("の夜" if night else "")
        who = f"{when}に出た "  # 帰りも、帰りの便自身が出た日
        body = f"{n} 便の帰りがすべて空でした" if (e == n and d == "back") else (f"{n} 便がすべて空でした" if e == n else f"{n} 便のうち {e} 便が空でした")
        lb = f"{short(fr)} → {short(to)}"
        word = {"back": "帰り", "out": "行き", "pair": "区間"}[d]
        tail1 = f"ほかの曜日の同じ向きは {len(others)} 便中 {oe} 便が空です（実車か空車かが分かった便だけ）。" if others else "ほかの曜日の同じ向きの便は、実車か空車かが分かった中にはありません。"
        text = f"{lb}の{word}は、{who}{body}。{tail1}"
        payload = {"dir": d, "wd": wd, "weekday": WEEKDAYS[wd], "trips": n, "empty": e, "night": night, "month": data.get("month"),
                   "frm": fr, "to": to, "frm_label": short(fr), "to_label": short(to), "label": lb}
        add(1, text, [{"k": f"{WEEKDAYS[wd]}曜発の{lb}", "v": f"{n} 便中 {e} 便が空"}, {"k": "ほかの曜日", "v": f"{len(others)} 便中 {oe} 便が空"}],
            {"label": "帰り便の下書きにする", "action": "draft_backhaul", "payload": payload},
            [_ev_leg(l) for l in sorted(ls, key=lambda x: x["date"])],
            {"back": "同じ曜日の帰りがいつも空", "out": "同じ曜日の行きがいつも空", "pair": "同じ曜日の同じ区間がいつも空"}[d])

    # 規則 2: 空で走った区間
    empties = [l for l in legs if l["kind"] == "empty"]
    if empties:
        km = sum(l["km"] or 0 for l in empties)
        eb, eo = sum(1 for l in empties if l.get("dir") == "back"), sum(1 for l in empties if l.get("dir") == "out")
        by_pair: dict[tuple[str, str], int] = {}
        for l in empties:
            fr, to = pr(l)
            if fr and to and fr != to:
                by_pair[(fr, to)] = by_pair.get((fr, to), 0) + 1
        simple = bool(base and dock) and all(k in ((dock, base), (base, dock)) for k in by_pair)  # 自社と主な行き先の往復だけなら、これまでの言い方
        if simple:
            tail = f"向きは {short(dock)} → {short(base)} が {eb} 便、{short(base)} → {short(dock)} が {eo} 便です。" if base and dock else ""
        else:
            top = sorted(by_pair.items(), key=lambda kv: (-kv[1], f"{short(kv[0][0])} → {short(kv[0][1])}"))
            shown = "、".join(f"{short(k[0])} → {short(k[1])} が {c} 便" for k, c in top[:3])
            tail = (f"向きは {shown} です" + (f"（ほか {len(top) - 3} 区間）" if len(top) > 3 else "") + "。") if top else ""
        meter = any((l.get("sources") or {}).get("km") == "デジタコ" for l in legs)  # デジタコを入れていないときは、メーターと書かない
        km_n = sum(1 for l in empties if l.get("km"))
        km_miss = len(empties) - km_n  # 距離が分からない便は、km の合計に入れない（0 km として足さない）
        km_src = f"距離は{'デジタコのメーターと OSRM の推定' if meter else 'OSRM の推定'}"
        if not km_n:
            km_head = f"空で走った区間は {len(empties)} 便でした（距離が分からないので、km は出していません）。"
            km_val = "距離が分からない"
        elif km_miss:
            km_head = f"空で走った区間は {len(empties)} 便、距離の分かる {km_n} 便で約 {round(km):,} km でした（{km_src}）。距離が分からない {km_miss} 便は km に入れていません。"
            km_val = f"約 {round(km):,} km（距離が分からない {km_miss} 便を除く）"
        else:
            km_head = f"空で走った区間は {len(empties)} 便・約 {round(km):,} km でした（{km_src}）。"
            km_val = f"約 {round(km):,} km"
        add(2, km_head + tail,
            [{"k": "空で走った便", "v": f"{len(empties)} 便"}, {"k": "空で走った距離", "v": km_val}],
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
            add(3, f"{place_phrase(p, pid, data.get('own_names'))} で、30 分以上待った記録が {len(ss)} 回、合わせて {round(tot / 60, 1)} 時間でした"
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
        if (v.get("gvw_kg") or 0) >= R5_GVW_KG or (v.get("max_load_kg") or 0) >= R5_LOAD_KG:
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

    # 規則 7: 積んだ重さが最大積載量を超える記録（積載率に入れていない。無いときは出さない）
    over = over_max_legs(legs, vehicles)
    if over:
        cap = {v["plate"]: v.get("max_load_kg") for v in vehicles}
        add(7, f"積んだ重さが最大積載量を超えている記録が {len(over)} 件ありました。積載率と積載効率の平均には入れていません。"
               "重さの単位（t と kg）や桁を、元の配車表で確かめてください。",
            [{"k": "最大積載量を超える記録", "v": f"{len(over)} 件"}],
            {"label": "元の行を見る", "action": "show_evidence", "payload": {}},
            [{**_ev_leg(l), "text": "重さ {} > 最大積載量 {}".format(*tons_pair(l["weight_kg"], cap[l["plate"]]))} for l in sorted(over, key=lambda x: (x["date"], x["plate"]))][:40])
    return out
