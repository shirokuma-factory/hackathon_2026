"""荷待ち・荷役の記録から、標準的運賃の表に当てた参考額を計算する（SPEC §23）。

純粋な関数だけで、ファイルや通信には触らない（記録は呼び出しの間だけメモリにある）。
表は data/tariffs/mlit_standard_fare_waiting.json（標準的運賃 別紙、2024 年 3 月 22 日告示）。

数え方（根拠は docs/plan/2026-11-04_顧客の今のやり方の調査.md §G・§I-6）:
- 待機の分 W = 荷役開始 − 到着。「荷主等の都合」が いいえ なら 0。荷役を荷主側がしたら、その時間も W に含める。
- 待機時間料: 2 時間の境は到着 + 120 分。W から最初の 30 分を差し引き（Q29）、
  到着 + 30 分〜到着 + 120 分を 30 分までごとに通常の額、到着 + 120 分を超える部分を 30 分までごとに 2 時間超の額。端数は切り上げ。
- 指定の到着時刻（任意、SPEC §23.7）: 指定があり実際の到着がそれより早いときは、指定の時刻から数える（待機の始まり = max(到着, 指定)）。
  2 時間の境も待機の始まりから数える。附帯業務（内容・分）は記録して一覧に出すだけで、参考額は付けない（告示で実費として収受）。
- 荷役のあとの待ち（任意、SPEC §25）: 行ごとに「荷役のあとの待ちを荷主等の都合として数える」を はい にしたときだけ、
  (出発 − 荷役終了) を待機の分に足す。30 分の差し引きは 1 か所ごとに 1 回、2 時間の境は待機の始まりから。
- 読み取り（SPEC §25）: 全角・半角、前後と途中の空白は NFKC で正規化してから読む。日付は 2026/10/05・10月5日・R8.10.5・令和8年10月5日 など、
  時刻は 9:00:00・9時00分・0900 など、車種は 4t車・4トン・中型車 など、はい/いいえ は ○・有・荷主都合 / ×・無・渋滞 などを受ける。
- 積込料・取卸料: 運転者が荷役をした行だけ。荷役の時間を到着 + 120 分の前後に分け、30 分までごとに額を付ける。
  最初の 30 分は差し引かない（告示の文言からの読み取り。Q&A の例で確かめたのは待機時間料だけ）。
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from .tariffs import load_waiting_tariff

MAX_ROWS = 5000

# 入力の列（貼り付けのときの並び順）
COLUMNS = ("date", "vehicle_no", "vehicle_class", "partner", "place", "arrive", "start", "end", "depart",
           "handler", "method", "shipper_fault", "appointed", "side_work", "side_min", "post_wait")

_HEADER_ALIASES = {
    "日付": "date", "車番": "vehicle_no", "車種": "vehicle_class", "契約の相手": "partner", "場所": "place",
    "到着": "arrive", "荷役開始": "start", "荷役終了": "end", "出発": "depart", "荷役をした人": "handler",
    "荷役の方法": "method", "荷主等の都合の待機か": "shipper_fault", "荷主等の都合": "shipper_fault",
    "指定の到着時刻": "appointed", "附帯業務の内容": "side_work", "附帯業務の分": "side_min",
    "運行日": "date", "年月日": "date", "月日": "date", "車両番号": "vehicle_no", "車格": "vehicle_class",
    "荷主": "partner", "元請": "partner", "契約先": "partner", "到着時刻": "arrive", "着時刻": "arrive",
    "積込開始": "start", "積込開始時刻": "start", "荷役開始時刻": "start", "積込終了": "end", "積込終了時刻": "end", "荷役終了時刻": "end",
    "出発時刻": "depart", "発時刻": "depart", "指定時刻": "appointed", "指定到着": "appointed",
    "作業日": "date", "配送日": "date", "納品日": "date", "トン数": "vehicle_class", "車型": "vehicle_class",
    "取引先": "partner", "得意先": "partner", "荷主名": "partner", "納品先": "place", "届け先": "place", "配送先": "place",
    "到着時間": "arrive", "着": "arrive", "積込開始時間": "start", "荷役開始時間": "start", "積込終了時間": "end", "荷役終了時間": "end",
    "出発時間": "depart", "発": "depart", "指定時間": "appointed",
    # 貨物自動車運送事業輸送安全規則 第 8 条第 1 項の記録項目の言葉と、よくある様式の言葉（SPEC §23.8）
    "集荷地点等": "place", "集貨地点等": "place", "集荷地点": "place", "集貨地点": "place", "荷待ち地点": "place", "荷待地点": "place",
    "到着日時": "arrive", "到着の日時": "arrive", "指定到着日時": "appointed", "指定の到着日時": "appointed",
    "荷役作業の開始日時": "start", "荷役開始日時": "start", "荷役作業開始日時": "start",
    "荷役作業の終了日時": "end", "荷役終了日時": "end", "荷役作業終了日時": "end",
    "出発日時": "depart", "出発の日時": "depart", "荷役作業等の内容": "work_content", "荷役作業の内容": "work_content",
    "車両登録番号": "vehicle_no", "自動車登録番号": "vehicle_no", "登録番号": "vehicle_no",
    "荷主都合": "shipper_fault", "荷主都合か": "shipper_fault", "荷主都合の有無": "shipper_fault", "荷主起因": "shipper_fault",
    # 読まない列（見出しとしては知っている。名前の無い列と同じく、黙って読み飛ばす）
    "乗務員氏名": "_ignore", "運転者氏名": "_ignore", "乗務員名": "_ignore", "運転者名": "_ignore", "氏名": "_ignore",
    "荷主の確認の有無": "_ignore", "荷主の確認": "_ignore", "確認の有無": "_ignore", "備考": "_ignore",
    "附帯業務開始日時": "_ignore", "附帯業務終了日時": "_ignore", "附帯業務の開始日時": "_ignore", "附帯業務の終了日時": "_ignore",
    "荷役のあとの待ちを荷主等の都合として数える": "post_wait", "荷役のあとの待ちを数える": "post_wait", "荷役のあとの待ち": "post_wait", "荷役後の待ち": "post_wait",
}

_CLASS_ALIASES = {
    "小型": "小型", "小型車": "小型", "中型": "中型", "中型車": "中型", "大型": "大型", "大型車": "大型",
    "トレーラー": "トレーラー", "トレーラ": "トレーラー", "トレーラ車": "トレーラー", "トレーラー車": "トレーラー",
    "セミトレーラ": "トレーラー", "セミトレーラー": "トレーラー",
}
_TON_CLASS = {"2": "小型", "4": "中型", "10": "大型"}   # 「4t車」「4トン」「4tウイング」の先頭の数字で見る
_YES = {"はい", "yes", "y", "true", "1", "○", "〇", "◯", "有", "あり", "荷主都合", "荷主の都合", "荷主等の都合"}
_NO = {"いいえ", "no", "n", "false", "0", "×", "✕", "✗", "x", "無", "なし", "渋滞", "都合外"}
_ERA_BASE = {"令和": 2018, "r": 2018, "平成": 1988, "h": 1988, "昭和": 1925, "s": 1925}


# 日時の欄（「2026/10/05 9:00」「10/5 9:00」「R8.10.5 9時00分」）の、時刻の前の日付の部分
_DT_PREFIX = r"(?:[0-9令和平成昭和元RHSrhs/\-年月日.]+(?: ?\([月火水木金土日祝]+\))? )?"
_DT_SPLIT = r"([0-9令和平成昭和元RHSrhs/\-年月日.]+(?: ?\([月火水木金土日祝]+\))?) ((?:[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)|(?:[0-9]{1,2}時(?:[0-9]{1,2}分?)?))"
_POS_NOTE = ("見出しの行が無いので、列の順（日付、車番、車種、契約の相手、場所、到着、荷役開始、荷役終了、出発、荷役をした人、荷役の方法、荷主等の都合）で読みました")
UNKNOWN_HANDLER = "不明"
UNKNOWN_HANDLER_NOTE = "荷役をした人が分からないので、荷役の時間は待機にも積込料にも入れていません"


def _nz(s: Any) -> str:
    """全角・半角をそろえ（NFKC）、空白（全角空白を含む）を 1 つにして前後を切る。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s if s is not None else ""))).strip()


def _norm(s: Any) -> str:
    return str(s if s is not None else "").strip()


def _normalize_class(raw: str) -> str | None:
    key = re.sub(r"[\s（）()]", "", _nz(raw)).lower()
    if key in _CLASS_ALIASES:
        return _CLASS_ALIASES[key]
    m = re.match(r"(10|2|4)(?:t|ton|トン)", key)
    if m:
        return _TON_CLASS[m.group(1)]
    # 「小型（2t）」のような書き方は先頭の語で見る
    for name in ("トレーラー", "トレーラ", "小型", "中型", "大型"):
        if key.startswith(name.lower()):
            return "トレーラー" if name.startswith("トレーラ") else name
    return None


def parse_time(text: str) -> int | None:
    """時刻を分にする。9:00 / 9:00:00 / 09時00分 / 9時 / 0900 / 900（全角も可、日付つきも可）。読めなければ None。"""
    t = _nz(text)
    m = re.fullmatch(_DT_PREFIX + r"([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?", t)
    if m:
        h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    else:
        m = re.fullmatch(_DT_PREFIX + r"([0-9]{1,2})時(?:([0-9]{1,2})分?)?", t)
        if m:
            h, mi, sec = int(m.group(1)), int(m.group(2) or 0), 0
        else:
            m = re.fullmatch(r"([0-9]{1,2})([0-9]{2})", t)
            if not m:
                return None
            h, mi, sec = int(m.group(1)), int(m.group(2)), 0
    if h > 23 or mi > 59 or sec > 59:
        return None
    return h * 60 + mi


def fmt_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_date(text: str, year: int | None) -> date | None:
    """日付を読む。2026-10-05 / 2026/10/5 / 2026年10月5日 / R8.10.5 / 令和8年10月5日（元年も可）と、年を渡したときの 10/5 / 10月5日。
    曜日の（月）や時刻が後ろに付いていてもよい。読めない日付は None。"""
    t = _nz(text)
    t = re.sub(r" [0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?$", "", t)
    t = re.sub(r" ?\([月火水木金土日祝]+\)$", "", t)
    try:
        m = re.fullmatch(r"([0-9]{4})(?:[-/.]|年)([0-9]{1,2})(?:[-/.]|月)([0-9]{1,2})日?", t)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.fullmatch(r"(令和|平成|昭和|r|h|s)(元|[0-9]{1,2})(?:[-/.]|年)([0-9]{1,2})(?:[-/.]|月)([0-9]{1,2})日?", t, re.I)
        if m:
            n = 1 if m.group(2) == "元" else int(m.group(2))
            return date(_ERA_BASE[m.group(1).lower()] + n, int(m.group(3)), int(m.group(4)))
        m = re.fullmatch(r"([0-9]{1,2})(?:/|月)([0-9]{1,2})日?", t)
        if m and year is not None:
            return date(year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    return None


def _bool(raw: str) -> bool | None:
    k = _nz(raw).lower()
    if k in _YES:
        return True
    if k in _NO:
        return False
    return None


def _header_key(cell: str) -> str:
    return re.sub(r"\s+", "", _nz(cell))


def _date_part(text: str) -> str:
    """日時の欄（「2026/10/05 9:00」「10/5 9:00」）から日付の部分を取り出す。無ければ空。"""
    m = re.fullmatch(_DT_SPLIT, _nz(text))
    if not m or parse_time(m.group(2)) is None:
        return ""
    return m.group(1)


def parse_paste_ex(text: str) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """Excel から貼り付けたタブ区切りを (行の辞書, defaulted, notes) にする（SPEC §25・§23.8）。
    1 行目が見出しなら見出しの名前で読み（列の順は問わない）、読まなかった見出しは notes に名前を書く。
    見出しの行が無いときだけ、列の順で読み、そのことを notes に書く。
    見出しがあって「荷主等の都合」の欄が無い、または見出しが無くてどの行にも欄が無いときは、全部の行を はい にして defaulted に入れる。
    見出しに欄があるのに読めない値の行は、はい にせず計算側が誤りにする。"""
    lines = [ln for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return [], [], []
    notes: list[str] = []
    cols = list(COLUMNS)
    header = False
    first = [_header_key(c) for c in lines[0].split("\t")]
    if first and (first[0] in ("日付", "date") or sum(1 for c in first if c in _HEADER_ALIASES) >= 3):
        header = True
        mapped = [_HEADER_ALIASES.get(c) for c in first]
        cols = [c if c and c != "_ignore" else "" for c in mapped]   # 読まない列・知らない列は "" にして、行に入れない（乗務員氏名などを残さない）
        seen: set[str] = set()
        for raw, c in zip(lines[0].split("\t"), mapped):
            name = _nz(raw)
            if c is None and name and name not in seen:
                seen.add(name)
                notes.append(f"見出し『{name}』は読みませんでした")
        lines = lines[1:]
    else:
        notes.append(_POS_NOTE)
    rows = []
    for ln in lines:
        cells = ln.split("\t")
        rows.append({cols[i]: cells[i].strip() for i in range(min(len(cols), len(cells))) if cols[i]})
    # 日付の列が無い（または空の）行は、到着日時などの日時の欄の日付の部分を日付にする
    for r in rows:
        if not r.get("date"):
            for k in ("arrive", "start", "end", "depart"):
                d = _date_part(r.get(k, ""))
                if d:
                    r["date"] = d
                    break
    # 荷役をした人の列が無いとき: 荷役作業等の内容があれば運転者（法定の記録は運転者自身の荷役）、無ければ不明
    if header and "handler" not in cols:
        derived = False
        for r in rows:
            if r.get("end"):
                if r.get("work_content"):
                    r["handler"] = "運転者"
                    if not r.get("method"):
                        wc = _nz(r["work_content"])
                        r["method"] = "フォークリフト等" if ("フォーク" in wc or "機械" in wc) else "手積み"
                    derived = True
                else:
                    r["handler"] = UNKNOWN_HANDLER
        if derived:
            notes.append("荷役をした人の列が無いので、荷役作業等の内容がある行は運転者が荷役をしたとして読みました"
                         "（荷役の方法が書かれていない行は、荷役作業等の内容にフォークリフトの語が無ければ手積み）。違う行は表で直してください")
    defaulted: list[str] = []
    if rows and (("shipper_fault" not in cols) if header else all("shipper_fault" not in r for r in rows)):
        for r in rows:
            r["shipper_fault"] = "はい"
        defaulted.append("shipper_fault")
    return rows, defaulted, notes


def parse_paste_full(text: str) -> tuple[list[dict[str, str]], list[str]]:
    rows, defaulted, _notes = parse_paste_ex(text)
    return rows, defaulted


def parse_paste(text: str) -> list[dict[str, str]]:
    return parse_paste_ex(text)[0]


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
            if year is None and parse_date(g["date"], 2000) is not None:
                errs.append(f"日付「{g['date']}」の年が分かりません（年を選んでください）")
            else:
                errs.append(f"日付「{g['date']}」を読めません（2026-10-05、2026/10/5、10/5、10月5日、R8.10.5、令和8年10月5日 のように入れてください）")

    vclass = None
    if not g["vehicle_class"]:
        errs.append("車種がありません")
    else:
        vclass = _normalize_class(g["vehicle_class"])
        if vclass is None:
            errs.append(f"車種「{g['vehicle_class']}」を読めません（小型（2t）/ 中型（4t）/ 大型（10t）/ トレーラー）")

    partner = _nz(g["partner"])   # 全角・半角の違いで相手が分かれないようにそろえる
    if not partner:
        errs.append("契約の相手がありません")

    def need_time(key: str, label: str, required: bool) -> int | None:
        if not g[key]:
            if required:
                errs.append(f"{label}がありません")
            return None
        v = parse_time(g[key])
        if v is None:
            errs.append(f"{label}「{g[key]}」を読めません（9:00、09時00分、0900 のように入れてください）")
        return v

    arrive = need_time("arrive", "到着", True)
    start = need_time("start", "荷役開始", True)
    end = need_time("end", "荷役終了", False)
    depart = need_time("depart", "出発", False)
    appointed = need_time("appointed", "指定の到着時刻", False)
    side_min = 0
    if g["side_min"]:
        if re.fullmatch(r"[0-9]{1,4}", _nz(g["side_min"])):
            side_min = int(_nz(g["side_min"]))
        else:
            errs.append(f"附帯業務の分「{g['side_min']}」を読めません（分を整数で入れてください）")

    handler = _nz(g["handler"])
    if handler in ("荷主", "荷主側"):
        handler = "荷主側"
    elif handler in ("運転者", "ドライバー", "運転手"):
        handler = "運転者"
    elif handler == UNKNOWN_HANDLER:
        pass   # 荷役をした人の列が無い貼り付け。行は残し、荷役の時間は数えない（下で注記）
    elif handler:
        errs.append(f"荷役をした人「{handler}」を読めません（運転者 / 荷主側）")
        handler = ""
    has_handling = bool(g["end"])
    if not handler and (has_handling or g["method"]):
        if not any("荷役をした人" in e for e in errs):
            errs.append("荷役をした人がありません（運転者 / 荷主側）")
    if handler and handler != UNKNOWN_HANDLER and not has_handling:
        errs.append("荷役をした人を入れたときは、荷役終了も入れてください")

    method = _nz(g["method"])
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

    post_on = False
    if g["post_wait"]:
        pw = _bool(g["post_wait"])
        if pw is None:
            errs.append(f"荷役のあとの待ちを数えるか「{g['post_wait']}」を読めません（はい / 空欄）")
        else:
            post_on = pw
    if post_on and (not g["end"] or not g["depart"]):
        errs.append("荷役のあとの待ちを数えるときは、荷役終了と出発を入れてください")

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
    post_min = 0   # 荷役終了 → 出発（荷役のあとの待ち）
    if depart is not None:
        dep_rel = depart - arrive
        floor = e_rel if e_rel is not None else s_rel
        while dep_rel < floor:
            dep_rel += 1440
            crosses = True
        if post_on and e_rel is not None:
            post_min = dep_rel - e_rel
    handling_min = (e_rel - s_rel) if e_rel is not None else 0

    unit = int(tariff["unit_minutes"])
    free = int(tariff["free_minutes"])
    thr = int(tariff["threshold_minutes"])

    # 待機の始まり = max(到着, 指定の到着時刻)。指定が到着より早い側にあれば到着から数える（SPEC §23.7）。
    # 指定と到着の差は 12 時間以内の近い側に取る（23:50 到着で指定 00:10 は 20 分後、翌日の指定）
    base = 0
    if appointed is not None:
        diff = appointed - arrive
        if diff > 720:
            diff -= 1440
        elif diff < -720:
            diff += 1440
        base = max(0, diff)
    boundary = base + thr  # 2 時間の境（到着からの分）

    # 待機
    if handler == "荷主側":
        wait_raw = max(0, e_rel - max(s_rel, base))  # 指定の時刻より後の荷役だけ
        wait_raw += max(0, s_rel - base)
    else:
        wait_raw = max(0, s_rel - base)
    wait_raw += post_min   # 30 分の差し引きは 1 か所ごとに 1 回、2 時間の境は待機の始まりから（SPEC §25）
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
        before = max(0, min(e_rel, boundary) - min(s_rel, boundary))
        after = max(0, e_rel - max(s_rel, boundary))
        hn_units, ho_units = _ceil_div(before, unit), _ceil_div(after, unit)
        hn_rate = tariff["handling"][method]["normal"][vclass]
        ho_rate = tariff["handling"][method]["over_2h"][vclass]
    handling_fee = hn_units * hn_rate + ho_units * ho_rate

    row_notes = [UNKNOWN_HANDLER_NOTE] if handler == UNKNOWN_HANDLER and end is not None else []
    out = {
        "row": n,
        "date": d.isoformat(),
        "month": d.strftime("%Y-%m"),
        "vehicle_no": g["vehicle_no"],
        "vehicle_class": vclass,
        "partner": partner,
        "place": g["place"],
        "arrive": fmt_time(arrive),
        "start": fmt_time(start),
        "end": fmt_time(end) if end is not None else "",
        "depart": fmt_time(depart) if depart is not None else "",
        "appointed": fmt_time(appointed) if appointed is not None else "",
        "wait_from": "指定の到着時刻" if base > 0 else "到着",   # 待機の数え始め
        "wait_base_min": base,                    # 待機の数え始め（到着からの分）
        "side_work": g["side_work"], "side_min": side_min,   # 附帯業務（記録だけ。参考額は付けない）
        "handler": handler,
        "method": method,
        "shipper_fault": fault,
        "crosses_midnight": crosses,
        "wait_raw_min": s_rel,                    # 荷役開始 − 到着（責任の有無によらない）
        "wait_min": w,                            # 待機として数える分（荷主側の荷役と、数えるとした荷役のあとの待ちを含む。責任なしは 0）
        "post_wait": post_on,                     # 荷役のあとの待ちを荷主等の都合として数える行か
        "post_wait_min": post_min if fault else 0,  # そのうち、荷役のあとの待ちの分
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
        "has_side_work": bool(g["side_work"]) or side_min > 0,
    }
    if row_notes:
        out["notes"] = row_notes
    return out, []


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
        "side_work_count": sum(1 for r in rows if r["has_side_work"]),
        "side_work_minutes": sum(r["side_min"] for r in rows),
    }


def _half_up(num: int, den: int) -> int:
    """num ÷ den を四捨五入（0.5 は切り上げ）した整数。JS 側と同じ結果にするため、浮動小数点を使わない。"""
    return (2 * num + den) // (2 * den)


def _pct(num: int, den: int) -> int:
    return _half_up(100 * num, den) if den else 0


NO_PLACE = "（場所の記入なし）"


def hotspots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """どこで・いつ待っているか（SPEC §27.1）。（契約の相手、場所、実際の到着の時間帯）ごとの集計。
    荷待ちの合計の分が大きい順、同じなら相手・場所・時間帯の文字の順。合計 0 の組も返す（画面と書面で除く）。"""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        key = (r["partner"], r["place"] or NO_PLACE, f"{int(r['arrive'][:2])}時台")
        groups.setdefault(key, []).append(r)
    out = []
    for (partner, place, band), rs in groups.items():
        total = sum(r["wait_min"] for r in rs)
        out.append({"partner": partner, "place": place, "band": band, "count": len(rs), "wait_minutes": total,
                    "avg_minutes": _half_up(total, len(rs)), "max_minutes": max(r["wait_min"] for r in rs),
                    "over_1h_count": sum(1 for r in rs if r["over_1h"]), "amount": sum(r["amount"] for r in rs)})
    out.sort(key=lambda h: (-h["wait_minutes"], h["partner"], h["place"], h["band"]))
    return out


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
        out.append({"partner": name, **_sum(rs), "over_1h_share": _pct(sum(1 for r in rs if r["over_1h"]), len(rs)), "date_from": min(r["date"] for r in rs),
                    "date_to": max(r["date"] for r in rs), "months": months})
    out.sort(key=lambda p: (-p["amount"], p["partner"]))
    return out


def calc(rows: list[Any], year: int | None = None, tariff: dict[str, Any] | None = None) -> dict[str, Any]:
    """記録の行を計算する。返り値は {rows, by_partner, hotspots, errors, tariff}。

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
        "hotspots": hotspots(done),
        "errors": errors,
        "tariff": {"source_url": t["source_url"], "applies_from": t["applies_from"], "checked_on": t["checked_on"],
                   "tax": t["tax"], "unit_minutes": t["unit_minutes"], "free_minutes": t["free_minutes"],
                   "threshold_minutes": t["threshold_minutes"], "class_labels": t.get("class_labels", {}),
                   "waiting": t["waiting"], "handling": t["handling"]},
    }
