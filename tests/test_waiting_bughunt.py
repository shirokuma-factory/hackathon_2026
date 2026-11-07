"""荷待ちのページの不具合探し（docs/reviews/2026-11-08_不具合探し_荷待ち.md）B1〜B11 の回帰試験。入力はレビューに書かれた文のとおり。

計算の双子（src/shipcompare/waiting.py）は Python で、ページ（web/waiting.html）は ヘッドレス Edge で確かめる。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
WAITING = ROOT / "web" / "waiting.html"

HDR_PLATE = "日付\t車番\t車種\t契約の相手\t場所\t到着\t荷役開始\t荷主等の都合"
HDR_NOPLATE = "日付\t車種\t契約の相手\t場所\t到着\t荷役開始\t荷主等の都合"
SHIP_LOG = "受付日\t運送会社\t車番\t施設\t受付\t作業開始\n10/5\t運送Y\t1234\t第1センター\t7:35\t9:40\n10/6\t運送Y\t1234\t第1センター\t8:05\t8:40"
CARRIER_TABLE = ("日付\t車番\t車種\t契約の相手\t場所\t到着\t荷役開始\t荷役終了\t出発\t荷役をした人\t荷役の方法\t荷主等の都合\n"
                 "10/5\t1\t大型\t元請A\t江東\t8:00\t9:30\t10:00\t10:10\t運転者\t手積み\tはい")


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _open(page):
    page.goto(WAITING.as_uri() + "?selftest=0")
    page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "ページ")
    page.js("document.querySelector('#b-clear').click()")
    page.js("S.year = 2026; document.querySelector('#year').value = '2026'")


def _paste(page, text, wait_doc=True):
    page.js("document.querySelector('#paste').value = " + json.dumps(text, ensure_ascii=False))
    page.js("document.querySelector('#b-paste').click()")
    if wait_doc:
        page.wait(lambda: page.js("!!S.res && S.res.rows.length > 0"), 30, "計算")


def _filled_rows(page):
    return page.js("S.rows.filter((r) => !isBlank(r)).length")


# ---------------------------------------------------------------- Python の双子（B4・B5）

def test_b4_python_infers_next_year_for_month_only_dates_crossing_new_year():
    from shipcompare import waiting as w
    rows = [{"date": d, "partner": "元請A", "vehicle_class": "中型", "arrive": "08:00", "start": "09:30", "shipper_fault": "はい"} for d in ("12/27", "12/28", "1/5", "1/6")]
    r = w.calc(rows, 2026)
    assert [x["date"] for x in r["rows"]] == ["2026-12-27", "2026-12-28", "2027-01-05", "2027-01-06"]
    assert r["year_crossed"] == {"from": 2026, "to": 2027, "rows": 2}
    # 同じ年の中だけなら推測しない
    r2 = w.calc([{**rows[0], "date": "1/5"}, {**rows[0], "date": "1/6"}], 2026)
    assert "year_crossed" not in r2 and r2["rows"][0]["date"] == "2026-01-05"
    # 年が書いてある日付は動かさない
    r3 = w.calc([{**rows[0], "date": "2026-12-27"}, {**rows[0], "date": "2026-01-05"}], 2026)
    assert "year_crossed" not in r3 and r3["rows"][1]["date"] == "2026-01-05"


def test_b5_python_finds_header_below_a_title_row_without_false_notes():
    from shipcompare import waiting as w
    text = ("10月分 荷待ち記録表\t\t\t\t\t\t\n日付\t車種\t契約の相手\t場所\t到着\t荷役開始\t荷主等の都合\n"
            "10/5\t中型\t元請A\t江東\t8:00\t9:30\tはい\n\t\t\t\t\t\t\n10/6\t中型\t元請A\t江東\t8:00\t9:40\tはい")
    d = w.parse_paste_detail(text)
    assert len(d["rows"]) == 2 and d["rows"][0]["partner"] == "元請A"
    assert d["defaulted"] == []   # 荷主の都合の列があるので「全部はい」の注記は付かない
    assert any("表題" in n for n in d["notes"])
    assert not any("列の順" in n for n in d["notes"])
    res = w.calc(d["rows"], 2026)
    assert len(res["rows"]) == 2 and res["errors"] == []


# ---------------------------------------------------------------- B1・B2

def test_b1_rows_without_plate_or_with_other_partner_are_kept_and_exact_duplicates_are_merged():
    _edge_or_skip()
    import capture_web as cw
    text = "\n".join([HDR_NOPLATE, "10/5\t中型\t元請A\t江東倉庫\t8:00\t9:30\tはい", "10/5\t中型\t元請A\t江東倉庫\t8:00\t9:40\tはい", "10/5\t大型\t元請B\t江東倉庫\t8:00\t10:00\tはい"])
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, text)
        assert _filled_rows(page) == 3
        assert page.js("S.res.by_partner.map((p) => p.partner).sort().join(',')") == "元請A,元請B"
        # 車番があって相手だけが違う行は別の行
        _open(page)
        _paste(page, "\n".join([HDR_PLATE, "10/5\t1\t中型\t元請A\t江東倉庫\t8:00\t9:30\tはい", "10/5\t1\t中型\t元請B\t江東倉庫\t8:00\t9:30\tはい"]))
        assert _filled_rows(page) == 2
        # 荷役開始だけが違う行も別の行
        _open(page)
        _paste(page, "\n".join([HDR_PLATE, "10/5\t1\t中型\t元請A\t江東倉庫\t8:00\t9:30\tはい", "10/5\t1\t中型\t元請A\t江東倉庫\t8:00\t9:40\tはい"]))
        assert _filled_rows(page) == 2
        # すべて同じ（車番あり）なら 1 行にまとめ、まとめた数を知らせる
        _open(page)
        _paste(page, "\n".join([HDR_PLATE, "10/5\t1\t中型\t元請A\t江東倉庫\t8:00\t9:30\tはい", "10/5\t1\t中型\t元請A\t江東倉庫\t8:00\t9:30\tはい"]))
        assert _filled_rows(page) == 1
        assert "1 行" in page.js("document.querySelector('#paste-msg').textContent") and "足さずに" in page.js("document.querySelector('#paste-msg').textContent")


def test_b2_reimporting_exported_csv_does_not_double_rows():
    _edge_or_skip()
    import capture_web as cw
    text = "\n".join([HDR_PLATE] + [f"10/{d}\t{d}\t中型\t元請A\t江東倉庫\t8:00\t9:{d}0\tはい" for d in range(1, 5)])
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, text)
        assert _filled_rows(page) == 4
        before = page.js("S.res.by_partner[0].amount")
        page.js("document.querySelector('#csvpaste').value = csvText()")
        page.js("document.querySelector('#b-csvpaste').click()")
        assert _filled_rows(page) == 4
        assert page.js("S.res.by_partner[0].amount") == before
        assert "同じ行を 4 行まとめました" in page.js("document.querySelector('#csvpaste-msg').textContent")


# ---------------------------------------------------------------- B3・B7・B8・文言

def test_b3_body_average_uses_actual_wait_for_no_rows_too():
    _edge_or_skip()
    import capture_web as cw
    text = "\n".join([HDR_PLATE, "10/5\t1\t中型\t元請A\t江東倉庫\t8:00\t9:30\tはい", "10/6\t2\t中型\t元請A\t江東倉庫\t8:00\t9:30\tいいえ", "10/7\t3\t中型\t元請A\t江東倉庫\t8:10\t9:40\tいいえ"])
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, text)
        t = page.js("draftText(S.res.by_partner[0])")
        assert "平均90分、最長90分" in t and "到着から荷役開始まで" in t
        assert "1時間以上お待ちした回が3回" in t
        assert "平均30分" not in t
        # 別紙の月ごとの表も 3 回・平均 90 分で同じ
        assert page.js("monthTable('元請A')[0].avg_minutes") == 90


def test_b7_early_arrival_text_is_not_in_copied_body_and_wording_items():
    _edge_or_skip()
    import capture_web as cw
    text = "\n".join([HDR_PLATE, "10/5\t1\t中型\t元請A\t江東\t7:00\t9:30\tはい", "10/6\t2\t中型\t元請A\t江東\t9:00\t10:30\tはい"])
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, text)
        t = page.js("draftText(S.res.by_partner[0])")
        assert "早着の疑い" not in t and "荷主の受付の前に着いた可能性" not in t
        # 荷役をした人の列が無い（荷役を数えない）ときは「荷待ちを」と書く
        assert "荷待ちと荷役を国の標準的運賃" not in t and "荷待ちを国の標準的運賃に当てはめた参考額" in t


def test_b8_calc_text_follows_actual_basis_and_wording_note_once():
    _edge_or_skip()
    import capture_web as cw
    # 荷主側の荷役が 1 行も無い表では「貴社側で荷役をしていただいた時間」と書かない
    rows = [HDR_PLATE] + [f"10/{d}\t{d}\t中型\t元請A\t江東倉庫\t{7 + d % 2}:00\t{8 + d % 2}:40\tはい" for d in range(1, 6)]
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, "\n".join(rows))
        t = page.js("draftText(S.res.by_partner[0])")
        assert "貴社側で荷役をしていただいた時間も、待ち時間に含めています" not in t
        # 「指定の到着時刻のない到着が多く含まれます。」は 1 回だけ（箇条書きごとに繰り返さない）
        assert t.count("指定の到着時刻のない到着が多く含まれます。") <= 1
        for s in page.js("consultSentences('元請A')"):
            assert "指定の到着時刻のない到着が多く含まれます" not in s


# ---------------------------------------------------------------- B4・B5（ページ）

def test_b4_year_crossing_page_subject_and_warning():
    _edge_or_skip()
    import capture_web as cw
    text = "\n".join([HDR_PLATE, "12/27\t1\t中型\t元請A\t江東\t8:00\t9:30\tはい", "12/28\t2\t中型\t元請A\t江東\t8:00\t9:30\tはい", "1/5\t3\t中型\t元請A\t江東\t8:00\t9:30\tはい", "1/6\t4\t中型\t元請A\t江東\t8:00\t9:30\tはい"])
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, text)
        assert "12月27日〜1月6日" in page.js("draftSubject(S.res.by_partner[0])")
        assert page.js("S.res.by_partner[0].date_to") == "2027-01-06"
        assert "2027 年として読みました" in page.js("document.querySelector('#year-note').textContent")


def test_b5_title_row_above_header_in_page():
    _edge_or_skip()
    import capture_web as cw
    text = ("10月分 荷待ち記録表\t\t\t\t\t\t\n" + HDR_NOPLATE + "\n10/5\t中型\t元請A\t江東\t8:00\t9:30\tはい\n\n\t\t\t\t\t\t\n10/6\t中型\t元請A\t江東\t8:00\t9:40\tはい")
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, text)
        assert _filled_rows(page) == 2
        assert page.js("S.res.errors.length") == 0
        note = page.js("document.querySelector('#paste-note').textContent")
        assert "荷主の都合の列が無い" not in note and "全部" not in note.replace("全部の", "")


# ---------------------------------------------------------------- B6

def test_b6_mode_follows_each_paste_and_wrong_mode_is_not_kept():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, SHIP_LOG, wait_doc=False)
        page.wait(lambda: page.js("S.mode === 'shipper'"), 10, "荷主の画面")
        page.js("document.querySelector('#b-clear').click()")
        assert page.js("S.mode") == "carrier"   # 自動で切り替えた分は、表を空にすると戻る
        _paste(page, SHIP_LOG, wait_doc=False)
        page.wait(lambda: page.js("S.mode === 'shipper'"), 10, "荷主の画面")
        _paste(page, CARRIER_TABLE, wait_doc=False)   # 空にせずに運送会社の表を貼る
        page.wait(lambda: page.js("S.mode === 'carrier'"), 10, "運送会社の画面に戻る")
        assert "運送会社の画面" in page.js("document.querySelector('#mode-note').textContent")
        assert page.js("localStorage.getItem('waiting_page_mode_v1')") == "carrier"
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 15, "メールの下書き")


# ---------------------------------------------------------------- B9・B10・B11

def test_b9_cross_check_counts_exactly_15_minutes_as_different():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        c = page.js("""(function(){
          const car = [{date: '2026-10-05', vehicle_no: '1234', place: '第1センター', arrive: '08:00', start: '08:55'}];
          const shp = [{date: '2026-10-05', vehicle_no: '1234', place: '第1センター', arrive: '08:00', start: '08:40'}];
          const r = WaitingShared.crossCheck(car, shp); return {differ: r.differ.length, limit: r.limit};
        })()""")
        assert c == {"differ": 1, "limit": 15}


def test_b10_place_and_partner_spelling_kept_for_display():
    _edge_or_skip()
    import capture_web as cw
    rows = [HDR_NOPLATE] + [f"10/{d}\t中型\t㈱元請A\t株式会社 山田 第2倉庫\t8:00\t9:30\tはい" for d in range(5, 9)]
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, "\n".join(rows))
        t = page.js("draftText(S.res.by_partner[0])")
        assert "株式会社の山田" not in t and "株式会社 山田 第2倉庫" in t
        assert t.startswith("㈱元請A")
        assert page.js("placeJa('江東区 地点07')") == "江東区の地点07"


def test_b11_long_partner_name_does_not_scroll_horizontally_at_390():
    _edge_or_skip()
    import capture_web as cw
    name = "株式会社とても長い名前の元請運送ホールディングス東日本"
    rows = [HDR_NOPLATE] + [f"10/{d}\t中型\t{name}\t江東\t8:00\t9:30\tはい" for d in range(5, 9)]
    with cw.edge_page(390, 844) as page:
        _open(page)
        _paste(page, "\n".join(rows))
        w = page.js("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert w[0] <= w[1], w


# ---------------------------------------------------------------- 追加分（grader r3b・本線）

LINE_LOG = ("2026/10/05(月)\t08:12\t山田\t荷待ち記録 10/5 江東区 ○○倉庫 着 7:50 荷役開始 9:40 車番 1234 相手 元請A 車種 中型\n"
            "2026/10/05(月)\t08:13\t山田\tおつかれさま\n"
            "2026/10/06(火)\t08:30\t山田\t荷待ち記録 10/6 江東区 ○○倉庫 着 8:00 荷役開始 10:00 車番 1234 相手 元請A 車種 中型")


def test_line_talk_history_with_tabs_is_read_as_driver_text_python():
    from shipcompare import waiting as w
    d = w.parse_paste_detail(LINE_LOG)
    assert len(d["rows"]) == 2 and d["rows"][0]["partner"] == "元請A" and d["rows"][0]["vehicle_no"] == "1234"
    res = w.calc(d["rows"], 2026, defaults={"vehicle_class": "中型", "method": "手積み"})
    assert res["errors"] == [] and len(res["rows"]) == 2
    # 見出しのある表は、表題に「荷待ち記録」とあっても表として読む
    t = "荷待ち記録表\t\t\n日付\t車種\t契約の相手\t場所\t到着\t荷役開始\t荷主等の都合\n10/5\t中型\t元請A\t江東\t8:00\t9:30\tはい"
    assert len(w.parse_paste_detail(t)["rows"]) == 1


def test_line_talk_history_with_tabs_in_page():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, LINE_LOG)
        assert _filled_rows(page) == 2
        assert page.js("S.res.errors.length") == 0


def test_mixed_handler_rows_keep_handling_fee_and_say_how_many_are_unknown():
    _edge_or_skip()
    import capture_web as cw
    text = ("日付\t車番\t到着\t荷役開始\t荷役終了\n" + "\n".join(f"2026/10/{d}\t{d}\t8:00\t9:10\t9:50" for d in range(5, 10)))
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, text)
        page.js("S.rows.forEach((r, i) => { if (i < 2) { r.handler = '運転者'; r.method = '手積み'; } }); doCalc()")
        p = page.js("S.res.by_partner[0]")
        assert p["handling_fee"] > 0
        t = page.js("draftText(S.res.by_partner[0])")
        assert "荷役をした人が分からない3回は、荷役の時間と積込料・取卸料に入れていません" in t
        assert "荷役をした人の記録がないため" not in t
        assert page.js("handlerUnknown(S.res.by_partner[0])") is False


def test_mail_total_and_over_1h_count_use_same_basis_and_no_stale_shipper_handling_note():
    _edge_or_skip()
    import capture_web as cw
    text = "\n".join([HDR_PLATE, "10/5\t1\t中型\t元請A\t江東倉庫\t8:00\t9:30\tはい", "10/6\t2\t中型\t元請A\t江東倉庫\t8:00\t9:30\tいいえ", "10/7\t3\t中型\t元請A\t江東倉庫\t8:10\t9:40\tいいえ"])
    with cw.edge_page(1920, 1080) as page:
        _open(page)
        _paste(page, text)
        t = page.js("draftText(S.res.by_partner[0])")
        assert "合計で4時間30分" in t and "1時間以上お待ちした回が3回" in t   # 90 分 × 3 回。全部 1 時間以上なので合計と回数が食い違わない
        assert "貴社側で荷役をしていただいた時間も含まれています" not in t
