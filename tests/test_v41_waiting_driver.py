"""SPEC §41 実務の効き目 r1 を受けた、荷待ちの記録から協議の書面まで（ドライバーの 1 枚、貼り付けの読み取り、荷主が読む書面）。

- Python（src/shipcompare/waiting.py）で、ドライバーの LINE の文、現場の見出しと時刻の言い方、既定の車種・方法、早着、「荷主」の列の確認を固定する。
  JS（web/waiting_calc.js）は同じ試験表を ?selftest=1 で通す（tests/test_v237_waiting_page.py）。
- web/driver.html: 通信しない、ボタン 44px 以上、400px で横にはみ出さない、文が荷待ちのページでそのまま読める（ヘッドレス Edge）。
- web/waiting.html の書面: 荷主等の都合の列、欄が無かった断り、早着の別集計、取適法の説明、壊れた保存の知らせ。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from shipcompare import waiting

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

DRIVER = ROOT / "web" / "driver.html"
PAGE = ROOT / "web" / "waiting.html"
DEFAULTS = {"vehicle_class": "中型", "method": "手積み"}
SAMPLE = "荷待ち記録 11/10 江東区 ○○倉庫 着 7:50 受付 8:05 荷役開始 9:40 終了 10:30 出発 10:40 車番 1234 相手 ○○"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


# ---------------------------------------------------------------- ドライバーの文の読み取り
def test_driver_sample_line_is_read_field_by_field():
    rows = waiting.parse_driver_text(SAMPLE)
    assert rows == [{"date": "11/10", "place": "江東区 ○○倉庫", "arrive": "7:50", "reception": "8:05", "start": "9:40", "end": "10:30", "depart": "10:40",
                     "vehicle_no": "1234", "partner": "○○", "handler": "不明"}]


def test_driver_text_many_blocks_from_line_with_chatter_and_one_line_blocks():
    text = ("[LINE] 山田 太郎\n" + SAMPLE + "\n10:41\n"
            "荷待ち記録 11/11 川崎市 着 8:30 指定 8:30 荷役開始 8:50 終了 9:20 荷役 荷主側 相手 ○○ 車種 大型\n"
            "荷待ち記録 11/12 市川市 着 5時半 荷役開始 10時半 相手 □□ 荷待ち記録 11/13 船橋 着 9:00 荷役開始 10:00 相手 □□")
    d = waiting.parse_paste_detail(text)
    assert len(d["rows"]) == 4
    assert d["rows"][0]["partner"] == "○○"                         # 次の行の「10:41」は相手に混ぜない
    assert d["rows"][1]["appointed"] == "8:30" and d["rows"][1]["handler"] == "荷主側" and d["rows"][1]["vehicle_class"] == "大型"
    assert d["rows"][2]["arrive"] == "5時半" and d["rows"][3]["place"] == "船橋"   # 1 行に 2 件あっても分ける
    assert d["defaulted"] == ["shipper_fault"] and d["asks"] == [waiting.ASK_PARTNER]
    res = waiting.calc(d["rows"], 2026, defaults=DEFAULTS)
    assert res["errors"] == [] and len(res["rows"]) == 4
    first = res["rows"][0]
    assert (first["reception"], first["arrive"], first["start"], first["wait_min"]) == ("08:05", "07:50", "09:40", 110)
    assert first["early_arrival"] == "assumed" and first["defaults_used"] == ["vehicle_class"]


def test_driver_text_missing_pieces_give_reasons_not_silence():
    d = waiting.parse_paste_detail("荷待ち記録 11/10 江東区 着 abc 荷役開始 9:00 相手 ○○ 荷待ち記録 江東 着 9:00 荷役開始 10:00 相手 ○○")
    res = waiting.calc(d["rows"], 2026, defaults=DEFAULTS)
    assert len(res["rows"]) == 0
    msgs = [e["message"] for e in res["errors"]]
    assert any("到着「abc」を読めません" in m for m in msgs) and any("日付がありません" in m for m in msgs)


def test_excel_paste_with_the_label_in_a_title_is_still_read_as_a_table():
    text = "荷待ち記録\t\t\n日付\t車種\t契約の相手\t到着\t荷役開始\n10/5\t中型\tA運輸\t09:00\t10:00\n"
    d = waiting.parse_paste_detail(text)
    assert d["notes"] and "ドライバーの記録" not in d["notes"][0]       # 表題の行があっても、LINE の文としては読まない


# ---------------------------------------------------------------- 見出しと時刻
def test_memo_headings_and_times_are_read_and_rows_are_kept_with_defaults():
    text = ("日付\t荷主\t場所\t着\t受付\t積み始め\t積み終わり\t出発\t積んだ人\t備考\n"
            "11/10\t○○\t江東区 ○○倉庫\t5時半\t7時50分\t10時半\t11時20分\t11時半\t運転者\t雨\n")
    d = waiting.parse_paste_detail(text)
    r = d["rows"][0]
    assert (r["arrive"], r["reception"], r["start"], r["end"], r["handler"], r["partner"]) == ("5時半", "7時50分", "10時半", "11時20分", "運転者", "○○")
    assert "雨" not in json.dumps(d["rows"], ensure_ascii=False)       # 読まない列（備考）は行に残さない
    assert d["asks"] == [waiting.ASK_PARTNER]          # 「荷主」の列は契約の相手として黙って使わない
    bare = waiting.calc(d["rows"], 2026)
    assert [e["message"] for e in bare["errors"]] == ["車種がありません", "運転者が荷役をしたときは、荷役の方法（手積み / フォークリフト等）が必要です"]   # 既定を渡さなければ、これまでどおり誤りにする
    res = waiting.calc(d["rows"], 2026, defaults=DEFAULTS)
    row = res["rows"][0]
    assert res["errors"] == [] and row["defaults_used"] == ["vehicle_class", "method"]
    assert (row["arrive"], row["start"], row["end"], row["wait_min"], row["handling_min"]) == ("05:30", "10:30", "11:20", 300, 50)


def test_contract_partner_headings_do_not_ask_but_shipper_headings_do():
    base = "日付\t{h}\t車種\t到着\t荷役開始\n10/5\tA運輸\t中型\t09:00\t10:00\n"
    assert waiting.parse_paste_detail(base.format(h="荷主"))["asks"] == [waiting.ASK_PARTNER]
    assert waiting.parse_paste_detail(base.format(h="荷主名"))["asks"] == [waiting.ASK_PARTNER]
    for h in ("契約の相手", "元請", "運送会社"):
        assert waiting.parse_paste_detail(base.format(h=h))["asks"] == []


@pytest.mark.parametrize("raw,minutes", [("5時半", 330), ("10時半", 630), ("7時50分", 470), ("9時", 540), ("9時 半", None), ("9時半30", None), ("2026/10/5 10時半", 630)])
def test_japanese_times(raw, minutes):
    assert waiting.parse_time(raw) == minutes


def test_shipper_side_reception_records_give_facility_month_averages():
    text = ("日付\t運送会社\t施設\t受付\t荷役開始\n10/5\tX運輸\tA工場\t8:10\t9:00\n10/6\tY運送\tA工場\t8:20\t9:30\n11/2\tX運輸\tB倉庫\t13:00\t13:20\n")
    d = waiting.parse_paste_detail(text)
    assert "受付の時刻を到着として読みました" in d["notes"][0] and all("reception" not in r for r in d["rows"])
    res = waiting.calc(d["rows"], 2026, defaults=DEFAULTS)
    assert res["errors"] == []
    pm = {(m["place"], m["month"]): m for m in res["by_place_month"]}
    assert pm[("A工場", "2026-10")]["count"] == 2 and pm[("A工場", "2026-10")]["avg_minutes"] == 60
    assert {h["band"] for h in res["hotspots"]} == {"8時台", "13時台"}


# ---------------------------------------------------------------- 既定の車種・方法、早着
def test_defaults_only_fill_blank_cells_and_name_the_columns_used():
    rows = [{"date": "2026-10-05", "partner": "A", "arrive": "09:00", "start": "10:00", "shipper_fault": "はい"},
            {"date": "2026-10-05", "partner": "A", "vehicle_class": "大型", "arrive": "09:00", "start": "10:00", "end": "10:30", "handler": "運転者", "shipper_fault": "はい"},
            {"date": "2026-10-05", "partner": "A", "vehicle_class": "軽", "arrive": "09:00", "start": "10:00", "shipper_fault": "はい"}]
    res = waiting.calc(rows, 2026, defaults=DEFAULTS)
    assert [r.get("defaults_used") for r in res["rows"]] == [["vehicle_class"], ["method"]]
    assert [e["message"] for e in res["errors"]] == ["車種「軽」を読めません（小型（2t）/ 中型（4t）/ 大型（10t）/ トレーラー）"]   # 読めない値は既定にしない
    assert res["rows"][0]["wait_fee"] == waiting.calc([{**rows[0], "vehicle_class": "中型"}], 2026)["rows"][0]["wait_fee"]
    assert waiting.calc(rows[:1], 2026, defaults={"vehicle_class": "軽"})["errors"]   # 知らない既定は使わない


def _row(**kw):
    return {"date": "2026-10-05", "vehicle_class": "中型", "partner": "A", "shipper_fault": "はい", **kw}


def test_early_arrival_rule_and_both_totals():
    rows = [_row(arrive="07:50", start="09:00"), _row(arrive="05:00", start="06:00"), _row(arrive="04:59", start="06:00"), _row(arrive="08:00", start="09:30"),
            _row(arrive="07:50", appointed="08:00", start="09:00"), _row(arrive="07:50", appointed="07:30", start="09:00")]
    res = waiting.calc(rows, 2026)
    assert [r.get("early_arrival") for r in res["rows"]] == ["assumed", "assumed", None, None, "designated", None]
    p = res["by_partner"][0]
    assert p["early_assumed_count"] == 2 and p["early_designated_count"] == 1
    assert p["early_assumed_wait_minutes"] == res["rows"][0]["wait_min"] + res["rows"][1]["wait_min"]
    assert p["early_assumed_amount"] == res["rows"][0]["amount"] + res["rows"][1]["amount"]
    assert 0 < p["early_assumed_amount"] < p["amount"]


def test_vectors_have_the_v41_cases():
    names = {c["name"] for c in json.loads((ROOT / "tests" / "data" / "waiting_vectors.json").read_text(encoding="utf-8"))["cases"]}
    for need in ("paste_driver_line_text_many_blocks", "paste_memo_headings_and_japanese_times", "defaults_fill_missing_vehicle_class_and_method", "early_arrival_rules",
                 "paste_reception_only_means_arrival_for_a_shippers_own_records", "times_with_han"):
        assert need in names, need


# ---------------------------------------------------------------- ドライバーの 1 枚（文字列）
def test_driver_page_is_one_offline_file_with_the_required_controls():
    html = DRIVER.read_text(encoding="utf-8")
    assert "\r" not in html
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource", "import(", "navigator.serviceWorker"):
        assert bad not in html, bad
    assert not re.search(r"<script[^>]+src\s*=", html, re.I) and not re.search(r"<link(?![^>]*href\s*=\s*.data:)[^>]+href\s*=", html, re.I)
    assert not re.search(r"@import|url\(\s*['\"]?https?:", html, re.I)
    for label in ("着いた", "受付した", "呼ばれた（荷役開始）", "荷役が終わった", "出発した", "LINE に貼る文をコピー", "場所", "相手（荷主）", "車番"):
        assert label in html, label
    # localStorage は必ず try/catch の中で使う
    assert html.count("localStorage") == 3 and html.count("try {") >= 3
    assert "min-height: 44px" in html and "min-height: 56px" in html
    assert "荷待ち記録" in html and "荷待ち記録 11/10" in html   # 文の形を、ページの中にも書いている
    assert "実在" not in html


# ---------------------------------------------------------------- ヘッドレス Edge（1 つずつ）
def test_driver_page_in_phone_width_makes_a_line_the_waiting_page_reads_directly():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(400, 860) as page:
        page.goto(DRIVER.as_uri())
        page.wait(lambda: page.js("!!document.querySelector('[data-stamp=arrive]')"), 30, "ドライバーの 1 枚")
        layout = page.js("""(function(){
          const bs = [...document.querySelectorAll('button, input, select')].filter((b) => b.getBoundingClientRect().width > 0);
          return {wide: document.documentElement.scrollWidth, small: bs.filter((b) => b.getBoundingClientRect().height < 43.5).map((b) => b.id || b.textContent)};
        })()""")
        assert layout["wide"] <= 400, layout
        assert layout["small"] == [], layout
        page.js("""(function(){
          const set = (id, v) => { const e = document.getElementById(id); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); };
          set('place', '江東区 ○○倉庫'); set('partner', '○○'); set('vno', '1234'); set('vclass', '中型'); set('handler', '運転者');
          for (const k of ['arrive', 'reception', 'start', 'end', 'depart']) document.querySelector('[data-stamp=' + k + ']').click();
          const t = document.querySelector('[data-time=arrive]'); t.value = '07:50'; t.dispatchEvent(new Event('input', {bubbles: true}));
          document.getElementById('b-save').click();
        })()""")
        info = page.js("""(function(){
          const again = document.querySelector('[data-stamp=arrive]'); again.click();   // 押し直しても書き換わらない
          document.getElementById('b-copy').click();
          return {text: todays().map(recordLine).join('\\n'), cur: S.cur.arrive, msg: document.getElementById('msg').textContent, n: todays().length,
                  stored: !!localStorage.getItem('driver_records_v1')};
        })()""")
        text = info["text"]
        assert info["n"] == 1 and info["stored"]
        assert re.fullmatch(r"荷待ち記録 \d{1,2}/\d{1,2} 江東区 ○○倉庫 着 7:50 受付 \d{1,2}:\d{2} 荷役開始 \d{1,2}:\d{2} 終了 \d{1,2}:\d{2} 出発 \d{1,2}:\d{2} 車番 1234 相手 ○○ 車種 中型 荷役 運転者", text), text
        # 荷待ちのページの貼り付け欄に、そのまま貼る
        page.goto(PAGE.as_uri() + "?x=driver")
        page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "荷待ちのページ")
        page.js("document.querySelector('#paste').value = " + json.dumps(text + "\n" + text.replace("着 7:50", "着 8:10"), ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "書面")
        got = page.js("""({rows: document.querySelectorAll('#tbody tr').length, errs: document.querySelector('#errs').textContent,
                          ask: !!document.querySelector('#ask-box #b-ask-ok'), note: document.querySelector('#paste-note').textContent})""")
    assert got["rows"] == 2 and got["errs"] == "" and got["ask"] and "荷待ち記録" in got["note"]


def test_document_for_the_receiving_shipper_shows_fault_column_early_split_and_notes_in_edge():
    _edge_or_skip()
    import capture_web as cw
    memo = ("日付\t荷主\t場所\t着\t受付\t積み始め\t積み終わり\t出発\t積んだ人\n"
            "11/10\t○○\t江東区 ○○倉庫\t7:50\t8:05\t9:40\t10:30\t10:40\t運転者\n"
            "11/11\t○○\t江東区 ○○倉庫\t9:00\t9:10\t10:00\t\t10:10\t\n"
            "11/12\t○○\t江東区 ○○倉庫\t5時半\t7時50分\t10時半\t\t10時40分\t\n")
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri() + "?x=prep")
        page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "荷待ちのページ")
        page.js("localStorage.setItem('waiting_page_rows_v1', '{こわれた')")     # 壊れた保存を作って、開き直す
        page.goto(PAGE.as_uri() + "?x=broken")
        page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "荷待ちのページ")
        first = page.js("""({broken: !document.querySelector('#broken-note').hidden && document.querySelector('#broken-note').textContent,
                             ph: [...document.querySelectorAll('#tbody input[placeholder]')].filter((i) => i.placeholder).map((i) => i.dataset.i + ':' + i.placeholder),
                             shipper: document.querySelector('#shipper-line').textContent,
                             basis: document.querySelector('#basis').textContent})""")
        assert "保存した記録が読めませんでした（消していません）" in first["broken"]
        assert first["ph"] and all(p.startswith("0:例 ") for p in first["ph"]), first["ph"]       # 例の時刻は 1 行目だけ、「例」つき
        assert "受付の記録" in first["shipper"] and "施設ごと・月ごと" in first["shipper"] and "時間帯" in first["shipper"]
        assert "（正式名: 製造委託等に係る中小受託事業者に対する代金の支払の遅延等の防止に関する法律）" in first["basis"]
        assert first["basis"].index("正式名") < first["basis"].index("取適法では、長時間")
        page.js("document.querySelector('#paste').value = " + json.dumps(memo, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "書面")
        doc = page.js("""({text: document.querySelector('#wait-doc').textContent, head: [...document.querySelectorAll('#wait-doc table.rec')[0].querySelectorAll('th')].map((t) => t.textContent),
                           defaults: document.querySelector('#defaults-box').textContent, ask: document.querySelector('#ask-box').textContent,
                           askDoc: !!document.querySelector('#ask-doc'), process: document.querySelector('#process').textContent,
                           sum: document.querySelector('#partners table.par tbody tr').textContent})""")
        assert "荷主等の都合" in doc["head"]
        t = doc["text"]
        assert "荷主等の都合の欄が貼り付けの記録に無かったため" in t and "該当 3 行" in t
        assert "早着の疑いを分けた数字" in t and "うち早着の疑い 2 回" in t and "早着の疑い（指定の到着時刻なし）" in t
        assert "指定の到着時刻が無く、到着が 05:00 から 07:59 までの回" in t
        assert "車種が記入されていない 3 行は『中型』の単価で計算しています" in t
        assert "運転者が荷役をして荷役の方法が記入されていない 1 行は『手積み』の単価" in t
        assert "を除くと" in doc["sum"]
        assert "車種が空欄の 3 行は『中型』" in doc["defaults"] and "荷役の方法が空欄の 1 行" in doc["defaults"]
        assert "契約の相手として読みました" in doc["ask"] and doc["askDoc"]
        assert doc["process"].index("正式名") < doc["process"].index("取適法では、相手")
        # 車種をまとめて選ぶと、空欄の行がすべて埋まって断りが消える
        page.js("var s = document.querySelector('#bulk-class-sel'); s.value = '大型'; s.dispatchEvent(new Event('change', {bubbles: true}))")
        page.wait(lambda: page.js("S.rows.filter((r) => r.vehicle_class === '大型').length === 3"), 10, "車種の一括")
        after = page.js("({defaults: document.querySelector('#defaults-box').textContent, doc: document.querySelector('#wait-doc').textContent})")
        assert "車種が空欄" not in after["defaults"] and "車種が記入されていない" not in after["doc"]
        page.js("document.querySelector('#b-ask-ok').click()")
        assert page.js("!document.querySelector('#ask-doc') && document.querySelector('#ask-box').textContent === ''")
