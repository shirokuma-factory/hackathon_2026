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
  時刻は 9:00:00・9時00分・5時半・0900 など、車種は 4t車・4トン・中型車 など、はい/いいえ は ○・有・荷主都合 / ×・無・渋滞 などを受ける。
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
    # 現場のメモの言葉（SPEC §41）。積み始め・積み終わり・積んだ人、受付の時刻、荷主が自社の受付記録を貼るときの運送会社・施設
    "積み始め": "start", "積始め": "start", "積み始め時刻": "start", "積み終わり": "end", "積み終り": "end", "積終わり": "end", "積み終わり時刻": "end",
    "積んだ人": "handler", "荷役者": "handler", "作業者": "handler", "荷役した人": "handler", "積んだ人名": "handler",
    "受付": "reception", "受付時刻": "reception", "受付時間": "reception", "受付日時": "reception",
    "運送会社": "partner", "運送事業者": "partner", "運送業者": "partner", "施設": "place", "施設名": "place", "拠点": "place",
    # ドライバーの手書きメモ・荷主の受付記録の言葉（SPEC §42）。driver.html のボタンは「呼ばれた（荷役開始）」
    "呼ばれ": "start", "呼ばれた": "start", "呼出": "start", "呼出し": "start", "呼び出し": "start", "呼出時刻": "start", "呼出時間": "start", "呼び出し時刻": "start",
    "作業開始": "start", "作業開始時刻": "start", "作業終了": "end", "作業終了時刻": "end",
    "行き先": "place", "行先": "place", "積地": "place", "積み地": "place", "積込地": "place",
    # 荷主の受付記録の言葉（SPEC §43）。入場は呼ばれて構内に入った時刻（呼出と同じ扱い）、退場は出発
    "受付日": "date", "入場日": "date", "到着日": "date", "日にち": "date", "来場日": "date",
    "入場": "start", "入場時刻": "start", "入場時間": "start", "入構": "start", "入構時刻": "start",
    "退場": "depart", "退場時刻": "depart", "退場時間": "depart", "退構": "depart", "退構時刻": "depart",
}
# 呼び出しの言葉の列。荷役開始の別の列（作業開始など）が同じ表にあるときは、荷役開始ではなく呼出（call）として読む（荷主の受付記録: 受付 → 呼出 → 作業開始）
_CALL_HEADERS = {"呼ばれ", "呼ばれた", "呼出", "呼出し", "呼び出し", "呼出時刻", "呼出時間", "呼び出し時刻", "入場", "入場時刻", "入場時間", "入構", "入構時刻"}
# これらの列があるときは、運送会社の請求の書面の側の表（荷主の受付記録ではない）
_CARRIER_CONTEXT_COLS = {"shipper_fault", "side_work", "side_min", "post_wait", "handler", "method", "work_content", "appointed"}
# 日付の無い時刻が前の時刻より小さいとき翌日と読むが、その差がこれを超えるなら誤りにする（9:00 → 8:30 を 23.5 時間と読まない）
REVERSE_MAX_MIN = 12 * 60
# 契約の相手の列として黙って読んではいけない見出し（荷主は着荷主のことがある）。読んだときは asks に入れて、画面で確かめる
_ASK_PARTNER_HEADERS = {"荷主", "荷主名"}
ASK_PARTNER = "partner_from_shipper"
# 早着: 指定の到着時刻が無く、到着がこの範囲（5:00〜7:59）のとき「早着の疑い」。指定があって実際の到着が指定より前のときは、指定の時刻から数える
EARLY_FROM_MIN, EARLY_UNTIL_MIN = 5 * 60, 8 * 60
DRIVER_LABEL = "荷待ち記録"
# ドライバーの 1 枚（web/driver.html）が LINE に貼る文の言葉 → 入力の列
_DRIVER_KEYS = {"着": "arrive", "到着": "arrive", "受付": "reception", "荷役開始": "start", "積込開始": "start", "開始": "start",
                "終了": "end", "荷役終了": "end", "積込終了": "end", "出発": "depart", "車番": "vehicle_no", "相手": "partner", "荷主": "partner",
                "車種": "vehicle_class", "指定": "appointed", "指定時刻": "appointed", "荷役": "handler"}

_CLASS_ALIASES = {
    "小型": "小型", "小型車": "小型", "中型": "中型", "中型車": "中型", "大型": "大型", "大型車": "大型",
    "トレーラー": "トレーラー", "トレーラ": "トレーラー", "トレーラ車": "トレーラー", "トレーラー車": "トレーラー",
    "セミトレーラ": "トレーラー", "セミトレーラー": "トレーラー",
}
_TON_CLASS = {"2": "小型", "4": "中型", "10": "大型"}   # 「4t車」「4トン」「4tウイング」の先頭の数字で見る
_YES = {"はい", "yes", "y", "true", "1", "○", "〇", "◯", "有", "あり", "荷主都合", "荷主の都合", "荷主等の都合"}
_NO = {"いいえ", "no", "n", "false", "0", "×", "✕", "✗", "x", "無", "なし", "渋滞", "都合外"}
_ERA_BASE = {"令和": 2018, "r": 2018, "平成": 1988, "h": 1988, "昭和": 1925, "s": 1925}
# 元号の始まりの日。これより前の日付は読めない日付にする（令和元年 1 月 5 日は平成 31 年で、打ち間違い）
_ERA_START = {"令和": date(2019, 5, 1), "r": date(2019, 5, 1), "平成": date(1989, 1, 8), "h": date(1989, 1, 8),
              "昭和": date(1926, 12, 25), "s": date(1926, 12, 25)}


# 日時の欄（「2026/10/05 9:00」「10/5 9:00」「R8.10.5 9時00分」）の、時刻の前の日付の部分
_DT_PREFIX = r"(?:[0-9令和平成昭和元RHSrhs/\-年月日.]+(?: ?\([月火水木金土日祝]+\))? )?"
_DT_SPLIT = r"([0-9令和平成昭和元RHSrhs/\-年月日.]+(?: ?\([月火水木金土日祝]+\))?) ((?:[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)|(?:[0-9]{1,2}時(?:[0-9]{1,2}分?|半)?))"
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
        m = re.fullmatch(_DT_PREFIX + r"([0-9]{1,2})時(?:([0-9]{1,2})分?|半)?", t)
        if m:
            h, mi, sec = int(m.group(1)), (30 if t.endswith("半") else int(m.group(2) or 0)), 0
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
            key = m.group(1).lower()
            dt = date(_ERA_BASE[key] + n, int(m.group(3)), int(m.group(4)))
            return dt if dt >= _ERA_START[key] else None
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


_YEARLESS = re.compile(r"[0-9]{1,2}(?:/|月)[0-9]{1,2}日?(?: ?\([月火水木金土日祝]+\))?")
LONG_WAIT_NOTE = "待ちが 1 日（24 時間）を超えています。日付と時刻を確かめてください"


_DRIVER_DATE = re.compile(r"(?:[0-9]{4}[/.\-])?[0-9]{1,2}(?:/|月)[0-9]{1,2}日?(?:\([月火水木金土日祝]+\))?")
_ASK_NOTE_DRIVER = "ドライバーの記録（荷待ち記録）を読みました"


def parse_driver_text(text: str) -> list[dict[str, str]]:
    """ドライバーの 1 枚（web/driver.html）が LINE に貼る文を行にする（SPEC §41）。
    1 件 = 「荷待ち記録 11/10 江東区 ○○倉庫 着 7:50 受付 8:05 荷役開始 9:40 終了 10:30 出発 10:40 車番 1234 相手 ○○」の形（1 行）。
    日付の次から最初の言葉（着・受付・荷役開始・終了・出発・車番・相手・車種・指定・荷役）までが場所。言葉の順は問わない。
    1 件は行の終わりか次の「荷待ち記録」で終わる（LINE の送信者や時刻の行を値に混ぜない）。「荷待ち記録」の前の文字は読まない。"""
    rows: list[dict[str, str]] = []
    for line in _nz_lines(text):
        for seg in line.split(DRIVER_LABEL)[1:]:
            toks = seg.split()
            if not toks:
                continue
            r: dict[str, str] = {}
            i = 0
            if _DRIVER_DATE.fullmatch(toks[0]):
                r["date"] = re.sub(r"\([月火水木金土日祝]+\)$", "", toks[0])
                i = 1
            place: list[str] = []
            while i < len(toks) and toks[i] not in _DRIVER_KEYS:
                place.append(toks[i])
                i += 1
            if place:
                r["place"] = " ".join(place)
            while i < len(toks):
                key = _DRIVER_KEYS[toks[i]]
                i += 1
                val: list[str] = []
                while i < len(toks) and toks[i] not in _DRIVER_KEYS:
                    val.append(toks[i])
                    i += 1
                v = " ".join(val)
                if v and v not in ("-", "ー", "なし"):
                    r[key] = v
            if r.get("end") and not r.get("handler"):
                r["handler"] = UNKNOWN_HANDLER
            rows.append(r)
    return rows


def _nz_lines(text: str) -> list[str]:
    return [_nz(ln) for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]


# ---- 「荷待ち記録」で始まらないスマホのメモ（SPEC §43）。1 行 = 1 件: 「11/10 江東 着7:50 呼ばれ9:40 終わり10:30」
MEMO_NO_PARTNER = "（相手未記入）"
MEMO_UNREADABLE = ("読める形ではありませんでした（{shown}）。次のどれかの形で貼ってください: Excel の表（1 行目が見出し、列はタブ区切り）、"
                   "ドライバーの 1 枚（driver.html）の文（荷待ち記録 11/10 …）、1 行 1 件のメモ（11/10 江東 着7:50 呼ばれ9:40 終わり10:30）")
_MEMO_KEYS = {"到着": "arrive", "着": "arrive", "受付": "reception", "呼ばれた": "call", "呼ばれ": "call", "呼び出し": "call", "呼出し": "call", "呼出": "call",
              "荷役開始": "start", "積込開始": "start", "積み始め": "start", "積始め": "start", "開始": "start",
              "荷役終了": "end", "積込終了": "end", "積み終わり": "end", "積み終り": "end", "積終わり": "end", "終わり": "end", "終了": "end",
              "出発": "depart", "発": "depart", "指定": "appointed"}
_MEMO_TIME = r"(?:[0-9]{1,2}:[0-9]{2}|[0-9]{1,2}時(?:[0-9]{1,2}分?|半)?)"
_MEMO_RE = re.compile("(" + "|".join(sorted(_MEMO_KEYS, key=len, reverse=True)) + ")[ :]?(" + _MEMO_TIME + ")")
_MEMO_TEXT_RE = re.compile(r"(車番|相手|荷主|車種) ?([^ ]+)")
_MEMO_DATE_RE = re.compile(r"((?:[0-9]{4}[/.\-])?[0-9]{1,2}[/\-月][0-9]{1,2}日?(?:\([月火水木金土日祝]+\))?)(?= |$)")
_MEMO_TEXT_COLS = {"車番": "vehicle_no", "相手": "partner", "荷主": "partner", "車種": "vehicle_class"}


def _memo_clip(line: str) -> str:
    return line if len(line) <= 36 else line[:36] + "…"


def parse_memo_text(text: str) -> dict[str, Any] | None:
    """日付で始まり、「着7:50」「呼ばれ9:40」のような言葉つきの時刻がある行を、1 行 1 件として読む。
    そういう行が 1 つも無いときは None（メモではない）。読めない行は rows に入れず bad に（行, 理由）で返す。"""
    lines = _nz_lines(text)
    if not any((m := _MEMO_DATE_RE.match(ln)) and _MEMO_RE.search(ln[m.end():]) for ln in lines):
        return None
    rows: list[dict[str, str]] = []
    bad: list[tuple[str, str]] = []
    for ln in lines:
        m = _MEMO_DATE_RE.match(ln)
        if not m:
            bad.append((ln, "日付（11/10 のような形）で始まっていません"))
            continue
        date_s = re.sub(r"\([月火水木金土日祝]+\)$", "", m.group(1))
        if parse_date(date_s, 2000) is None:
            bad.append((ln, f"日付「{date_s}」を読めません"))
            continue
        rest = ln[m.end():]
        found: dict[str, str] = {}
        for k in _MEMO_RE.finditer(rest):
            col = _MEMO_KEYS[k.group(1)]
            if col not in found:
                found[col] = k.group(2)
        rest2 = _MEMO_RE.sub(" ", rest)
        r: dict[str, str] = {"date": date_s}
        for k in _MEMO_TEXT_RE.finditer(rest2):
            r.setdefault(_MEMO_TEXT_COLS[k.group(1)], k.group(2))
        place = re.sub(r"\s+", " ", _MEMO_TEXT_RE.sub(" ", rest2)).strip()
        if place:
            r["place"] = place
        if "arrive" not in found:
            bad.append((ln, "着（到着）の時刻がありません"))
            continue
        if "start" not in found and "call" not in found:
            bad.append((ln, "呼ばれ（荷役開始・積み始め）の時刻がありません"))
            continue
        r["arrive"] = found["arrive"]
        if "reception" in found:
            r["reception"] = found["reception"]
        if "start" in found:
            r["start"] = found["start"]
            if "call" in found:
                r["call"] = found["call"]
        else:
            r["start"] = found["call"]
        for col in ("end", "depart", "appointed"):
            if col in found:
                r[col] = found[col]
        if r.get("end"):
            r["handler"] = UNKNOWN_HANDLER
        rows.append(r)
    return {"rows": rows, "bad": bad}


def parse_paste_detail(text: str) -> dict[str, Any]:
    """Excel から貼り付けたタブ区切り、またはドライバーの LINE の文を {rows, defaulted, notes, asks} にする（SPEC §25・§23.8・§41）。
    1 行目が見出しなら見出しの名前で読み（列の順は問わない）、読まなかった見出しは notes に名前を書く。
    見出しの行が無いときだけ、列の順で読み、そのことを notes に書く。
    見出しがあって「荷主等の都合」の欄が無い、または見出しが無くてどの行にも欄が無いときは、全部の行を はい にして defaulted に入れる。
    見出しに欄があるのに読めない値の行は、はい にせず計算側が誤りにする。
    asks: 画面で確かめてもらう読み方（「荷主」の列を契約の相手として読んだとき ASK_PARTNER）。"""
    asks: list[str] = []
    lines = [ln for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return {"rows": [], "defaulted": [], "notes": [], "asks": [], "shipper_log": False}
    if DRIVER_LABEL in _nz(text) and not any(chr(9) in ln for ln in lines):   # タブのある貼り付けは Excel の表（表題に「荷待ち記録」とあっても表として読む）
        rows = parse_driver_text(text)
        notes = [f"{_ASK_NOTE_DRIVER}（{len(rows)} 件）。『相手』を契約の相手として読んでいます" if rows else ""]
        notes = [n for n in notes if n]
        if any(r.get("partner") for r in rows):
            asks.append(ASK_PARTNER)
        defaulted: list[str] = []
        if rows:
            for r in rows:
                r["shipper_fault"] = "はい"
            defaulted.append("shipper_fault")
        return {"rows": rows, "defaulted": defaulted, "notes": notes, "asks": asks, "shipper_log": False}
    if not any(chr(9) in ln for ln in lines):   # タブが無く、日付で始まるメモの行（「11/10 江東 着7:50 呼ばれ9:40 終わり10:30」）
        memo = parse_memo_text(text)
        if memo is not None:
            rows = memo["rows"]
            notes = []
            defaulted = []
            if rows:
                notes.append(f"スマホのメモの書き方で読みました（{len(rows)} 件）。『着』『受付』『呼ばれ』『積み始め』『終わり』『出発』の言葉の次の時刻を読んでいます")
                if any(not r.get("partner") for r in rows):
                    for r in rows:
                        if not r.get("partner"):
                            r["partner"] = MEMO_NO_PARTNER
                    notes.append(f"契約の相手が書かれていない行は『{MEMO_NO_PARTNER}』としました。表の『契約の相手』を書き換えてください（メモに『相手 ○○』と書き足しても読みます）")
                if any(r["partner"] != MEMO_NO_PARTNER for r in rows):
                    asks.append(ASK_PARTNER)
                for r in rows:
                    r["shipper_fault"] = "はい"
                defaulted.append("shipper_fault")
            if memo["bad"]:
                items = "、".join(f"「{_memo_clip(ln)}」は{why}" for ln, why in memo["bad"])
                notes.append(f"読めなかった行は表に入れていません（{len(memo['bad'])} 行）: {items}。1 行に『日付 場所 着7:50 呼ばれ9:40 終わり10:30』の形で書くか、ドライバーの 1 枚（driver.html）の文を使ってください")
            return {"rows": rows, "defaulted": defaulted, "notes": notes, "asks": asks, "shipper_log": False}
        shown = "、".join(f"「{_memo_clip(ln)}」" for ln in _nz_lines(text)[:3])
        return {"rows": [], "defaulted": [], "notes": [MEMO_UNREADABLE.format(shown=shown)], "asks": asks, "shipper_log": False}
    notes: list[str] = []
    cols = list(COLUMNS)
    header = False
    first = [_header_key(c) for c in lines[0].split("\t")]
    if first and (first[0] in ("日付", "date") or sum(1 for c in first if c in _HEADER_ALIASES) >= 3):
        header = True
        mapped = [_HEADER_ALIASES.get(c) for c in first]
        start_idx = [i for i, c in enumerate(mapped) if c == "start"]
        call_idx = [i for i in start_idx if first[i] in _CALL_HEADERS]
        if call_idx and len(call_idx) < len(start_idx):   # 作業開始などの別の列があるとき、呼出は荷役開始にしない
            for i in call_idx:
                mapped[i] = "call"
        cols = [c if c and c != "_ignore" else "" for c in mapped]   # 読まない列・知らない列は "" にして、行に入れない（乗務員氏名などを残さない）
        if any(c == "partner" and h in _ASK_PARTNER_HEADERS for c, h in zip(mapped, first)):
            asks.append(ASK_PARTNER)
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
    # 荷主の自社の受付記録: 受付の列があり、運送会社の書面の側の列（荷主等の都合、荷役をした人、附帯業務など）が無い
    shipper_log = bool(header and "reception" in cols and "arrive" not in cols and not (_CARRIER_CONTEXT_COLS & set(cols)))
    # 到着の列が無く受付の列があるとき（荷主が自社の受付記録を貼る使い方）は、受付の時刻を到着として読む
    if header and "arrive" not in cols and "reception" in cols:
        for r in rows:
            if r.get("reception"):
                r["arrive"] = r.pop("reception")
                r["arrive_src"] = "reception"   # 到着として読んだ印。早着の文を受付の言葉にする
        notes.append("到着の列が無いので、受付の時刻を到着として読みました（荷主の受付記録の使い方。受付の前に着いて待った時間は入りません）")
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
    defaulted = []
    if rows and (("shipper_fault" not in cols) if header else all("shipper_fault" not in r for r in rows)):
        for r in rows:
            r["shipper_fault"] = "はい"
        defaulted.append("shipper_fault")
    return {"rows": rows, "defaulted": defaulted, "notes": notes, "asks": asks, "shipper_log": shipper_log}


def parse_paste_ex(text: str) -> tuple[list[dict[str, str]], list[str], list[str]]:
    d = parse_paste_detail(text)
    return d["rows"], d["defaulted"], d["notes"]


def parse_paste_full(text: str) -> tuple[list[dict[str, str]], list[str]]:
    rows, defaulted, _notes = parse_paste_ex(text)
    return rows, defaulted


def parse_paste(text: str) -> list[dict[str, str]]:
    return parse_paste_ex(text)[0]


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b) if a > 0 else 0


def _reverse_msg(label: str, t: int, prev_label: str, pt: int, gap: int) -> str:
    """日付の無い時刻が前の時刻より小さく、翌日と読むと 12 時間を超えてあく場合の誤り（SPEC §42）。"""
    return (f"{label}（{fmt_time(t)}）が{prev_label}（{fmt_time(pt)}）より前です。日付が無いので翌日と読むと {gap // 60} 時間 {gap % 60} 分あいたことになります。"
            "日付つきの時刻（10/5 09:00 のように）で入れるか、時刻を確かめてください")


def _calc_row(n: int, raw: dict[str, Any], year: int | None, tariff: dict[str, Any], defaults: dict[str, str] | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """1 行を読んで計算する。読めなければ (None, 理由のリスト)。"""
    errs: list[str] = []
    g = {k: _norm(raw.get(k)) for k in COLUMNS}
    defaults = defaults or {}
    used: list[str] = []   # 空欄を既定の値で計算した欄（SPEC §41）

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
    if not g["vehicle_class"] and defaults.get("vehicle_class"):
        vclass = defaults["vehicle_class"]
        used.append("vehicle_class")
    elif not g["vehicle_class"]:
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
    reception = None   # 受付の時刻（記録の一覧に出すだけ。計算には使わない。読めないときは行を落とさず注記する）
    reception_raw = _norm(raw.get("reception"))
    if reception_raw:
        reception = parse_time(reception_raw)
    call = None   # 呼出の時刻（荷主の受付記録。記録の一覧に出すだけ。計算には使わない）
    call_raw = _norm(raw.get("call"))
    if call_raw:
        call = parse_time(call_raw)
    side_min = 0
    if g["side_min"]:
        if re.fullmatch(r"[0-9]{1,4}", _nz(g["side_min"])):
            side_min = int(_nz(g["side_min"]))
        else:
            errs.append(f"附帯業務の分「{g['side_min']}」を読めません（分を整数で入れてください）")

    handler = _nz(g["handler"])
    if handler in ("荷主", "荷主側", "先方", "客先", "倉庫側"):
        handler = "荷主側"
    elif handler in ("運転者", "ドライバー", "運転手", "自分", "自社"):
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
        if not method and defaults.get("method"):
            method = defaults["method"]
            used.append("method")
        elif not method:
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

    # 日時の欄に日付が書いてあるときは、日付と時刻から実際の差を出す（SPEC §38）。日付が無い欄だけ、
    # 到着を基準に、前の時刻より小さければ +24 時間（日付をまたぐ）として扱う
    yr = d.year

    def field_day(key: str, ref: int | None = None) -> int | None:
        part = _date_part(g[key]) if g[key] else ""
        if not part:
            return None
        dd = parse_date(part, yr)
        if dd is not None and ref is not None and _YEARLESS.fullmatch(_nz(part)) and dd.toordinal() < ref - 180:
            dd = parse_date(part, yr + 1)   # 年の無い 1/1 が 12/31 の翌日になる場合
        return dd.toordinal() if dd is not None else None

    a_day = field_day("arrive")
    if a_day is None:
        a_day = d.toordinal()
    a_abs = a_day * 1440 + arrive

    s_day = field_day("start", a_day)
    if s_day is not None:
        s_rel = s_day * 1440 + start - a_abs
        if s_rel < 0:
            errs.append("荷役開始が到着より前です（日付と時刻を確かめてください）")
    else:
        s_rel = start - arrive
        if s_rel < 0:
            s_rel += 1440
            if s_rel > REVERSE_MAX_MIN:
                errs.append(_reverse_msg("荷役開始", start, "到着", arrive, s_rel))
    e_rel = None
    if end is not None:
        e_day = field_day("end", a_day)
        if e_day is not None:
            e_rel = e_day * 1440 + end - a_abs
            if e_rel < s_rel:
                errs.append("荷役終了が荷役開始より前です（日付と時刻を確かめてください）")
        else:
            e_rel = end - arrive
            if e_rel < s_rel:
                while e_rel < s_rel:
                    e_rel += 1440
                if e_rel - s_rel > REVERSE_MAX_MIN:
                    errs.append(_reverse_msg("荷役終了", end, "荷役開始", start, e_rel - s_rel))
    post_min = 0   # 荷役終了 → 出発（荷役のあとの待ち）
    dep_rel = None
    if depart is not None:
        floor = e_rel if e_rel is not None else s_rel
        p_day = field_day("depart", a_day)
        if p_day is not None:
            dep_rel = p_day * 1440 + depart - a_abs
            if dep_rel < floor:
                errs.append("出発が" + ("荷役終了" if e_rel is not None else "荷役開始") + "より前です（日付と時刻を確かめてください）")
        else:
            dep_rel = depart - arrive
            if dep_rel < floor:
                while dep_rel < floor:
                    dep_rel += 1440
                if dep_rel - floor > REVERSE_MAX_MIN:
                    errs.append(_reverse_msg("出発", depart, "荷役終了" if e_rel is not None else "荷役開始", end if e_rel is not None else start, dep_rel - floor))
        if post_on and e_rel is not None:
            post_min = dep_rel - e_rel
    if errs:
        return None, errs
    crosses = arrive + max(x for x in (s_rel, e_rel, dep_rel) if x is not None) >= 1440
    handling_min = (e_rel - s_rel) if e_rel is not None else 0

    unit = int(tariff["unit_minutes"])
    free = int(tariff["free_minutes"])
    thr = int(tariff["threshold_minutes"])

    # 待機の始まり = max(到着, 指定の到着時刻)。指定が到着より早い側にあれば到着から数える（SPEC §23.7）。
    # 指定と到着の差は 12 時間以内の近い側に取る（23:50 到着で指定 00:10 は 20 分後、翌日の指定）
    base = 0
    if appointed is not None:
        ap_day = field_day("appointed", a_day)
        if ap_day is not None:
            diff = ap_day * 1440 + appointed - a_abs   # 日付があるときは実際の差
        else:
            diff = appointed - arrive
            if diff > 720:
                diff -= 1440
            elif diff < -720:
                diff += 1440
        base = max(0, diff)
    boundary = base + thr  # 2 時間の境（到着からの分）

    # 待機。時計の上の区間ごとに、待機の始まり + 120 分の境の前と後に分ける（SPEC §25.6 の訂正、告示 別紙 V）。
    # 荷役のあとの待ちも、荷役の時間を含めた時計の境で分ける（運転者の荷役の間に境をまたぐ場合を含む）
    if handler == "荷主側":
        hi = max(base, e_rel)   # 荷主側の荷役は待機に含む。到着（または指定）から荷役終了まで続く
    else:
        hi = max(base, s_rel)
    n_main = max(0, min(hi, boundary) - base)
    o_main = max(0, hi - boundary)
    post_over = max(0, dep_rel - max(e_rel, boundary)) if post_min > 0 else 0
    wait_raw = n_main + o_main + post_min   # 30 分の差し引きは 1 か所ごとに 1 回（SPEC §25）
    w = wait_raw if fault else 0
    wn_total = (n_main + post_min - post_over) if fault else 0
    wo_total = (o_main + post_over) if fault else 0
    wn_min = max(0, wn_total - free)
    wo_min = wo_total
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
    if reception_raw and reception is None:
        row_notes.append(f"受付「{reception_raw}」を読めなかったので、受付の時刻は書面に出していません")
    # 早着（SPEC §41）。指定の到着時刻があって到着が指定より前: 指定の時刻から数えている。指定が無く 5:00〜7:59 の到着: 早着の疑い（別に数える）
    early = ""
    if appointed is not None and base > 0:
        early = "designated"
    elif appointed is None and EARLY_FROM_MIN <= arrive < EARLY_UNTIL_MIN:
        early = "assumed"
    if call_raw and call is None:
        row_notes.append(f"呼出「{call_raw}」を読めなかったので、呼出の時刻は使っていません")
    if wait_raw >= 1440:
        row_notes.append(LONG_WAIT_NOTE)
    out = {
        "row": n,
        "date": d.isoformat(),
        "month": d.strftime("%Y-%m"),
        "vehicle_no": g["vehicle_no"],
        "vehicle_class": vclass,
        "partner": partner,
        "place": _nz(g["place"]),   # 全角・半角の違いで場所が分かれないようにそろえる
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
        "report_wait_min": max(0, s_rel - base),  # SPEC §29.1: 定期報告の定義（到着か指定の時刻の遅い方 → 荷役開始。荷主等の都合に関わらず）
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
    if reception is not None:
        out["reception"] = fmt_time(reception)
    if call is not None:
        out["call"] = fmt_time(call)
    if _norm(raw.get("arrive_src")) == "reception":
        out["arrive_from_reception"] = True
    if early:
        out["early_arrival"] = early
    # 受付の時刻があるとき: 受付から数えた待機も出す（SPEC §43）。到着が受付より前でも、荷主の受付から数える額が書面の主な額
    if reception is not None and _norm(raw.get("arrive_src")) != "reception" and 0 <= reception - arrive <= REVERSE_MAX_MIN:
        part = _date_part(g["arrive"]) if g["arrive"] else ""
        v = dict(raw)
        v["arrive"] = (part + " " if part else "") + fmt_time(reception)
        v["reception"] = ""
        v.pop("arrive_src", None)
        o2, _e2 = _calc_row(n, v, year, tariff, defaults)
        if o2 is not None:
            out["reception_wait_min"] = o2["wait_min"]
            out["reception_amount"] = o2["amount"]
    if used:
        out["defaults_used"] = used
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
                    "over_1h_count": sum(1 for r in rs if r["over_1h"]), "amount": sum(r["amount"] for r in rs),
                    # SPEC §28.1: 注記の条件になる回数（指定の到着時刻が入っている回数、荷役をした人が荷主側の回数）
                    "designated_count": sum(1 for r in rs if r["appointed"]),
                    "shipper_handling_count": sum(1 for r in rs if r["handler"] == "荷主側")})
    out.sort(key=lambda h: (-h["wait_minutes"], h["partner"], h["place"], h["band"]))
    return out


def by_place_month(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """場所ごと・月ごとの平均の荷待ち（SPEC §28.2、§29.1）。鍵は（場所、月 YYYY-MM）。場所が空なら「（場所の記入なし）」。
    数えるのは report_wait_min（特定荷主の定期報告の算定方法）。並びは場所の文字の順、月の順。"""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["place"] or NO_PLACE, r["month"]), []).append(r)
    out = []
    for (place, month), rs in groups.items():
        total = sum(r["report_wait_min"] for r in rs)
        out.append({"place": place, "month": month, "count": len(rs), "wait_minutes": total, "avg_minutes": _half_up(total, len(rs)),
                    "over_1h_count": sum(1 for r in rs if r["report_wait_min"] >= 60)})
    out.sort(key=lambda x: (x["place"], x["month"]))
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
        early = [r for r in rs if r.get("early_arrival") == "assumed"]
        rec = [r for r in rs if "reception_amount" in r]   # 受付の時刻があって、受付から数えた額を出せた行
        early_norec = [r for r in early if "reception_amount" not in r]
        item = {"partner": name, **_sum(rs), "early_assumed_count": len(early), "early_assumed_wait_minutes": sum(r["wait_min"] for r in early),
                "early_assumed_amount": sum(r["amount"] for r in early), "early_designated_count": sum(1 for r in rs if r.get("early_arrival") == "designated"),
                "over_1h_share": _pct(sum(1 for r in rs if r["over_1h"]), len(rs)), "date_from": min(r["date"] for r in rs),
                "date_to": max(r["date"] for r in rs), "months": months}
        if rec:
            item["reception_count"] = len(rec)
            item["reception_wait_minutes"] = sum(r["reception_wait_min"] for r in rec)
            item["reception_amount"] = sum(r["reception_amount"] for r in rec)
            item["reception_arrive_wait_minutes"] = sum(r["wait_min"] for r in rec)
            item["reception_arrive_amount"] = sum(r["amount"] for r in rec)
            item["main_amount"] = item["amount"] - item["reception_arrive_amount"] + item["reception_amount"]
            item["early_assumed_norec_count"] = len(early_norec)
            item["early_assumed_norec_wait_minutes"] = sum(r["wait_min"] for r in early_norec)
            item["early_assumed_norec_amount"] = sum(r["amount"] for r in early_norec)
        out.append(item)
    out.sort(key=lambda p: (-p["amount"], p["partner"]))
    return out


def calc(rows: list[Any], year: int | None = None, tariff: dict[str, Any] | None = None, defaults: dict[str, str] | None = None) -> dict[str, Any]:
    """記録の行を計算する。返り値は {rows, by_partner, hotspots, by_place_month, errors, tariff}。

    errors は {row, message}（row は 1 始まりの行番号）。読めない行は計算から外す。
    defaults（任意）: {"vehicle_class": "中型", "method": "手積み"}。車種が空欄の行、運転者が荷役をして荷役の方法が空欄の行を、行を落とさずこの値で計算し、
    行に defaults_used（使った欄の名前）を付ける。渡さないときは、空欄の行は誤りになる。"""
    if not isinstance(rows, list):
        raise ValueError("rows は配列で渡してください")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"行が多すぎます（{MAX_ROWS:,} 行まで）")
    if year is not None and (isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 2100):
        raise ValueError("year は 2000〜2100 の整数で渡してください")
    t = tariff or load_waiting_tariff()
    dfl: dict[str, str] = {}
    if defaults:
        if defaults.get("vehicle_class") in t["waiting"]["normal"]:
            dfl["vehicle_class"] = defaults["vehicle_class"]
        if defaults.get("method") in t["handling"]:
            dfl["method"] = defaults["method"]
    done: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for i, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            errors.append({"row": i, "message": "行の形が正しくありません"})
            continue
        res, errs = _calc_row(i, raw, year, t, dfl)
        if res is None:
            errors.extend({"row": i, "message": e} for e in errs)
        else:
            done.append(res)
    return {
        "rows": done,
        "by_partner": aggregate(done),
        "hotspots": hotspots(done),
        "by_place_month": by_place_month(done),
        "errors": errors,
        "tariff": {"source_url": t["source_url"], "applies_from": t["applies_from"], "checked_on": t["checked_on"],
                   "tax": t["tax"], "unit_minutes": t["unit_minutes"], "free_minutes": t["free_minutes"],
                   "threshold_minutes": t["threshold_minutes"], "class_labels": t.get("class_labels", {}),
                   "waiting": t["waiting"], "handling": t["handling"]},
    }
