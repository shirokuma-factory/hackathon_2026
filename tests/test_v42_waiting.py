"""SPEC §42 実務の効き目 r2 を受けた、荷待ちのページとドライバーの 1 枚の直し。

- 貼り付けは表の後ろに足す（同じ行は 1 つにする、足した行数を知らせる、元に戻せる）。
- 見出しの別名（呼ばれ・呼出・呼び出し → 荷役開始、行き先・納品先・積地・届け先 → 場所）。Python と JS の両方（試験表 waiting_vectors.json）。
- 荷主として使う: 受付の分析、荷主がすること、運送会社の書面との照合（書面は出さない）。
- 受付を到着として読んだ回の早着の文、日付の無い逆転の時刻（12 時間を超えると誤りの行）。
- driver.html: 荷待ちのページへのリンクの高さ、会社から配る手順。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from shipcompare import waiting

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "web" / "waiting.html"
DRIVER = ROOT / "web" / "driver.html"
DEFAULTS = {"vehicle_class": "中型", "method": "手積み"}

SHIPPER_LOG = ("日付\t運送会社\t施設\t車番\t受付\t呼出\t作業開始\n"
               "10/5\tA運輸\t江東区 第1倉庫\t1234\t7:50\t8:30\t8:50\n"
               "10/5\tB急送\t江東区 第1倉庫\t5678\t8:10\t9:00\t9:40\n"
               "10/6\tA運輸\t江東区 第1倉庫\t1234\t8:20\t8:25\t8:40\n"
               "10/6\tB急送\t川崎市 第2倉庫\t1111\t13:00\t13:10\t13:30\n")


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _calc(text: str):
    d = waiting.parse_paste_detail(text)
    return d, waiting.calc(d["rows"], 2026, defaults=DEFAULTS)


# ---------------------------------------------------------------- 見出しの別名（Python）
@pytest.mark.parametrize("head,key", [("呼ばれ", "start"), ("呼出", "start"), ("呼び出し", "start"), ("行き先", "place"), ("納品先", "place"), ("積地", "place"), ("届け先", "place")])
def test_memo_heading_aliases_are_read(head, key):
    assert waiting._HEADER_ALIASES[head] == key


def test_driver_memo_with_yobare_and_ikisaki_is_calculated():
    text = "日付\t行き先\t荷主\t着\t受付\t呼ばれ\t積み終わり\n11/10\t江東区 ○○倉庫\t○○\t5時10分\t6時\t8時15分\t9時半\n"
    d, res = _calc(text)
    assert d["rows"][0]["place"] == "江東区 ○○倉庫" and d["rows"][0]["start"] == "8時15分"
    assert res["errors"] == [] and res["rows"][0]["wait_raw_min"] == 185


def test_call_column_is_kept_apart_when_another_start_column_exists():
    d, res = _calc(SHIPPER_LOG)
    r = d["rows"][0]
    assert r["start"] == "8:50" and r["call"] == "8:30" and r["arrive"] == "7:50" and r["arrive_src"] == "reception"
    assert d["shipper_log"] is True and res["errors"] == []
    assert res["rows"][0]["call"] == "08:30" and res["rows"][0]["arrive_from_reception"] is True


def test_call_column_alone_is_the_start():
    d, res = _calc("日付\t運送会社\t施設\t受付\t呼出\n10/5\tA運輸\t江東区\t7:50\t8:30\n")
    assert d["rows"][0]["start"] == "8:30" and "call" not in d["rows"][0] and res["rows"][0]["wait_raw_min"] == 40


def test_carrier_tables_are_not_taken_for_a_shipper_log():
    text = "日付\t契約の相手\t場所\t到着\t受付\t荷役開始\t荷主等の都合\n10/5\tA\tX\t8:00\t8:10\t9:00\tはい\n"
    assert waiting.parse_paste_detail(text)["shipper_log"] is False
    memo = "日付\t荷主\t場所\t着\t受付\t呼ばれ\t積み終わり\n11/10\t○○\t江東\t5時10分\t6時\t8時15分\t9時半\n"
    assert waiting.parse_paste_detail(memo)["shipper_log"] is False      # 着の列があるメモは、ドライバーの記録


# ---------------------------------------------------------------- 日付の無い逆転の時刻
def _one(**kw):
    row = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A", "arrive": "09:00", "start": "10:00", "shipper_fault": "はい"}
    row.update(kw)
    return waiting.calc([row], 2026)


def test_undated_reverse_over_12h_is_an_error_row_not_in_the_total():
    res = _one(arrive="08:00", start="09:00", end="08:30", handler="運転者", method="手積み")
    assert res["rows"] == [] and len(res["errors"]) == 1
    m = res["errors"][0]["message"]
    assert "荷役終了（08:30）が荷役開始（09:00）より前です" in m and "23 時間 30 分" in m and "日付つきの時刻" in m
    res = _one(arrive="10:00", start="09:00")
    assert res["rows"] == [] and "荷役開始（09:00）が到着（10:00）より前です" in res["errors"][0]["message"]
    res = _one(arrive="08:00", start="09:00", end="09:30", depart="09:10", handler="荷主側")
    assert res["rows"] == [] and "出発（09:10）が荷役終了（09:30）より前です" in res["errors"][0]["message"]


def test_overnight_wait_under_12h_and_dated_times_are_unchanged():
    ok = _one(arrive="23:30", start="00:40")
    assert ok["errors"] == [] and ok["rows"][0]["wait_min"] == 70
    dated = _one(arrive="2026/10/05 23:00", start="2026/10/06 11:30")      # 日付があれば 12 時間を超えても誤りにしない
    assert dated["errors"] == [] and dated["rows"][0]["wait_raw_min"] == 750


# ---------------------------------------------------------------- 受付を到着として読んだ回の早着の文（Edge）
def test_page_append_paste_undo_and_reception_wording_in_edge():
    _edge_or_skip()
    import capture_web as cw
    two = ("日付\t契約の相手\t場所\t車種\t到着\t荷役開始\t車番\t荷主等の都合\n"
           "10/5\tA運輸\t江東\t中型\t09:00\t10:00\t1234\tはい\n"
           "10/6\tA運輸\t江東\t中型\t09:10\t10:20\t1234\tはい\n")
    again = ("日付\t契約の相手\t場所\t車種\t到着\t荷役開始\t荷役終了\t車番\t荷役をした人\t荷役の方法\t荷主等の都合\n"
             "2026-10-05\tA運輸\t江東\t中型\t9:00\t10:00\t10:30\t1234\t運転者\t手積み\tはい\n"          # 1 行目と同じ行（日付の書き方・時刻の桁が違う）。荷役終了などは空欄を埋める
             "10/7\tA運輸\t江東\t中型\t09:20\t10:10\t\t1234\t\t\tはい\n")
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri() + "?x=append")
        page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "ページ")
        page.js("localStorage.clear()")
        page.goto(PAGE.as_uri() + "?x=append2")
        page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "ページ")

        def paste(text):
            page.js("document.querySelector('#paste').value = " + json.dumps(text, ensure_ascii=False))
            page.js("document.querySelector('#b-paste').click()")

        paste(two)
        page.wait(lambda: page.js("document.querySelectorAll('#tbody tr').length === 2"), 10, "最初の貼り付け")     # 例の記録は入れ替わる
        paste(again)
        page.wait(lambda: page.js("document.querySelectorAll('#tbody tr').length === 3"), 10, "2 回目の貼り付け")
        info = page.js("""({msg: document.querySelector('#paste-msg').textContent, undo: !document.querySelector('#undo-note').hidden && document.querySelector('#undo-note').textContent,
                           end: S.rows[0].end, handler: S.rows[0].handler, stored: JSON.parse(localStorage.getItem('waiting_page_rows_v1')).rows.length,
                           keep: document.querySelector('#keep-msg').textContent})""")
        assert "1 行を足しました" in info["msg"] and "同じ回の 1 行" in info["msg"]   # 文言の直し 2026-11-07
        assert True and "これまでの記録 2 行は消えていません" in info["msg"], info
        assert "元に戻せます" in info["undo"] and info["end"] == "10:30" and info["handler"] == "運転者" and info["stored"] == 3
        # 元に戻す
        page.js("document.querySelector('#b-undo').click()")
        page.wait(lambda: page.js("document.querySelectorAll('#tbody tr').length === 2"), 10, "元に戻す")
        assert page.js("S.rows[0].end") == "" and page.js("document.querySelector('#undo-note').hidden") is True
        # 開き直しても、保存した行は残っている
        page.goto(PAGE.as_uri() + "?x=reload")
        page.wait(lambda: page.js("document.querySelectorAll('#tbody tr').length === 2"), 10, "保存した行")

        # 受付の時刻を到着として読んだ回: 荷主として使うに自動で切り替わり、運送会社として使うに戻すと早着の文が受付の言葉になる
        paste("日付\t運送会社\t施設\t車番\t受付\t作業開始\n10/8\tA運輸\t江東\t1234\t6:30\t9:00\n")
        page.wait(lambda: page.js("document.body.classList.contains('shipper-mode')"), 10, "荷主として使う")
        page.js("document.querySelector('#b-mode').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 10, "書面")
        doc = page.js("document.querySelector('#doc-attach').textContent")
        assert "荷主の受付の前に着いた可能性" not in doc
        assert "早い時刻の受付" not in doc and "受付の前に着いて待った時間は入っていません" not in doc   # 荷主に渡る書面には社内の見方を入れない  # 日本語の直し 2026-11-08


# ---------------------------------------------------------------- 荷主として使う、運送会社の書面との照合（Edge）
def test_shipper_mode_shows_analysis_actions_and_cross_check_in_edge():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri() + "?x=shipper0")
        page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "ページ")
        page.js("localStorage.clear()")
        page.goto(PAGE.as_uri() + "?x=shipper")
        page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "ページ")
        page.js("document.querySelector('#paste').value = " + json.dumps(SHIPPER_LOG, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        page.wait(lambda: page.js("!!document.querySelector('#shipper-hours')"), 10, "受付の分析")
        got = page.js("""({invoice: !!document.querySelector('#wait-doc'), note: document.querySelector('#mode-note').textContent, btn: document.querySelector('#b-mode').textContent,
                           head: [...document.querySelectorAll('#shipper-hours thead th')].map((t) => t.textContent),
                           hours: document.querySelector('#shipper-hours tbody').textContent, sum: document.querySelector('#shipper-sum').textContent,
                           places: document.querySelector('#shipper-places').textContent, actions: document.querySelector('#shipper-actions').textContent,
                           notice: document.querySelector('#notice-ta').value, basisHidden: document.querySelector('#basis').hidden,
                           pm: !!document.querySelector('#placemonth'), saved: localStorage.getItem('waiting_page_mode_v1')})""")
        assert got["invoice"] is False and "受付簿として読みました" in got["note"] and "運送会社の画面に戻す" in got["btn"] and got["basisHidden"] and got["saved"] == "shipper"
        assert got["head"][:3] == ["施設", "7 時台", "8 時台"] and "13 時台" in got["head"]
        assert "2 件" in got["hours"] and "平均" in got["hours"]                  # 江東区は 8 時台に 2 件（太字の山）
        assert "朝 8 時前の受付は 1 件（25%）" in got["sum"]  # 文言の直し 2026-11-07
        assert "呼出から作業開始まで" in got["places"] and "最長 40 分" in got["places"]
        for need in ("受付開始の時刻を決めて知らせる", "予約の枠を決める", "呼出から作業開始までを短くする", "早く着いた車の扱いを知らせる"):
            assert need in got["actions"], need
        assert "【早着の扱いについてのお願い】" in got["notice"] and "（受付開始の時刻）" in got["notice"]
        page.js("var i = document.querySelector('#notice-open'); i.value = '8:00'; i.dispatchEvent(new Event('input', {bubbles: true}))")
        assert "の受付は、8:00からです" in page.js("document.querySelector('#notice-ta').value")

        # 運送会社の書面の CSV（このページの書き出しと同じ形）を作って貼る: 1234 の 10/5 は到着が 20 分早い、5678 は同じ、9999 は運送会社だけ。自社だけの行は 10/6 の 2 件
        carrier_csv = page.js("""(function(){
          const mk = (d, v, a, s) => ({date: d, vehicle_no: v, vehicle_class: '中型', partner: 'A運輸', place: '江東区 第1倉庫', arrive: a, start: s, end: '', depart: '', handler: '', method: '', shipper_fault: 'はい',
                                        appointed: '', side_work: '', side_min: '', post_wait: ''});
          const rows = [mk('2026-10-05', '1234', '07:30', '08:50'), mk('2026-10-05', '5678', '08:10', '09:40'), mk('2026-10-07', '9999', '08:00', '09:00')];
          return csvTextOf(calc(rows, 2026, TARIFF, S.defaults).rows);
        })()""")
        page.js("document.querySelector('#cross-ta').value = " + json.dumps(carrier_csv, ensure_ascii=False))
        page.js("document.querySelector('#cross-ta').dispatchEvent(new Event('input', {bubbles: true}))")
        page.js("document.querySelector('#b-cross').click()")
        page.wait(lambda: page.js("!!document.querySelector('#cross-result')"), 10, "照合")
        cross = page.js("""({text: document.querySelector('#cross-result').textContent, differ: document.querySelector('#cross-differ') ? document.querySelector('#cross-differ').textContent : '',
                             only: document.querySelector('#cross-only').textContent, ta: document.querySelector('#cross-ta').value.length})""")
        assert "2 回のうち、時刻が 15 分以上違うのは 1 回です" in cross["text"]  # 文言の直し 2026-11-07, cross["text"][:300]
        assert "書面だけにある回は 1 回、受付簿だけにある回は 2 回" in cross["text"]  # 文言の直し 2026-11-07
        assert "1234" in cross["differ"] and "+20" in cross["differ"] and "5678" not in cross["differ"]
        assert "9999" in cross["only"] and "1111" in cross["only"] and cross["ta"] > 100
        # 画面を作り直しても（行を直しても）、貼った CSV と結果は残る
        page.js("renderResult()")
        assert page.js("!!document.querySelector('#cross-result') && document.querySelector('#cross-ta').value.length > 100")
        # 運送会社として使うに戻すと、書面が出る
        page.js("document.querySelector('#b-mode').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc')"), 10, "書面")


def test_header_definitions_are_readable_without_hover_in_edge():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 900) as page:
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#head-defs')"), 30, "ページ")
        d = page.js("({tag: document.querySelector('#head-defs').tagName, sum: document.querySelector('#head-defs summary').textContent, text: document.querySelector('#head-defs').textContent})")
        assert d["tag"] == "DETAILS" and "表の見出しの意味" in d["sum"] and "荷主側の荷役の時間を含む待機" in d["text"] and "2 時間以上だった回数" in d["text"]


# ---------------------------------------------------------------- ドライバーの 1 枚
def test_driver_page_has_distribution_steps_and_a_tall_enough_link():
    html = DRIVER.read_text(encoding="utf-8")
    assert "事務の方へ" in html and "ホーム画面に追加" in html and "LINE" in html   # 文言の直し 2026-11-07
    assert not re.search(r"qrcode|QRCode|<canvas", html), "QR コードの部品は入れない"
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon"):
        assert bad not in html
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(400, 860) as page:
        page.goto(DRIVER.as_uri())
        page.wait(lambda: page.js("!!document.querySelector('[data-stamp=arrive]')"), 30, "ドライバーの 1 枚")
        lay = page.js("""(function(){
          const links = [...document.querySelectorAll('a')].filter((a) => a.getBoundingClientRect().width > 0);
          const sec = document.querySelector('#distribute');
          return {links: links.map((a) => [a.textContent, a.getBoundingClientRect().height]), wide: document.documentElement.scrollWidth,
                  sec: sec ? sec.textContent : '', url: document.querySelector('#own-url') ? document.querySelector('#own-url').value : null,
                  tall: [...document.querySelectorAll('button, input, select')].filter((b) => b.getBoundingClientRect().width > 0 && b.getBoundingClientRect().height < 43.5).map((b) => b.id || b.textContent)};
        })()""")
    assert lay["links"] and all(h >= 24 for _t, h in lay["links"]), lay["links"]
    assert lay["wide"] <= 400 and lay["tall"] == []
    assert "URL を LINE" in lay["sec"] and "ファイルで送る" in lay["sec"] and "ホーム画面に追加" in lay["sec"]  # 文言の直し 2026-11-07
    assert lay["url"] and lay["url"].startswith("file:")
