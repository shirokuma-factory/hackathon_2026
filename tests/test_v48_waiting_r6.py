"""実務の効き目 r6a/r6b・画面の出来 r3a・道具の採点 r8b を受けた、荷待ちのページとドライバーの 1 枚の修正（SPEC §48）。

- 手書きメモ: 時刻が先の書き方（5:10着 6:40開始 7:35終）、コロンの無い 3〜4 桁の時刻（着520・520着）。
- 表の見出し: 「終了」「終了時刻」「作業終了」を荷役終了として読む。
- 照合: 書面の運送会社 1 社と、同じ運送会社の自社の受付記録を並べる。決められないときは自社側の額を出さない。
- 予約の枠: 平均の待ちが最も長い時間帯から、最も短い時間帯へ移す。
- 同じ注記が何行も並ぶときは 1 行にまとめる。書面の表の待機・荷役の列が 1 文字ずつ折れない。
- ドライバーの 1 枚: 最初の画面は時刻のボタン 5 つと主な操作 1 つ。
Edge の確認は 1 つずつ直列に動かす。Edge が無い環境では Edge の確認を飛ばす。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from shipcompare import waiting

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

WAITING = ROOT / "web" / "waiting.html"
DRIVER = ROOT / "web" / "driver.html"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _wait_for(page, js, what, timeout=30):
    page.wait(lambda: page.js(js), timeout, what)


# ---------------------------------------------------------------- 読み取り（Python）
def test_memo_time_first_order_and_compact_times_are_read():
    r = waiting.parse_paste_detail("11/6 ﾄｳﾜ春日部 5:10着 6:40開始 7:35終")["rows"][0]
    assert (r["place"], r["arrive"], r["start"], r["end"]) == ("トウワ春日部", "5:10", "6:40", "7:35")
    res = waiting.calc([{**r, "vehicle_class": "中型", "partner": "A", "shipper_fault": "はい"}], 2026)
    assert res["rows"][0]["wait_min"] == 90
    assert waiting.parse_paste_detail("11/6 x 5:10着 6:40開始 7:35終了")["rows"][0]["end"] == "7:35"
    a = waiting.parse_paste_detail("11/6 江東 着520 受付540 呼ばれ755 終830")["rows"][0]
    b = waiting.parse_paste_detail("11/6 江東 520着 540受付 755呼ばれ 830終了")["rows"][0]
    for r2 in (a, b):
        assert (r2["arrive"], r2["reception"], r2["start"], r2["end"]) == ("5:20", "5:40", "7:55", "8:30")
    c = waiting.parse_paste_detail("11/8 江東 車1234 着1530 開始1630")["rows"][0]
    assert (c["vehicle_no"], c["arrive"], c["start"]) == ("1234", "15:30", "16:30")      # 車番の数字は時刻にしない


def test_table_end_headers_are_read():
    for head in ("終了", "終了時刻", "終了時間", "作業終了", "荷役終了"):
        t = f"日付\t車番\t車種\t契約の相手\t場所\t到着\t荷役開始\t{head}\t荷役をした人\t荷役の方法\n10/5\t1234\t中型\tA社\t江東\t8:00\t9:00\t9:40\t運転者\t手積み\n"
        d = waiting.parse_paste_detail(t)
        assert d["rows"][0]["end"] == "9:40", head
        assert waiting.calc(d["rows"], 2026)["rows"][0]["handling_min"] == 40, head


# ---------------------------------------------------------------- 画面（Edge）
def test_slot_suggestion_moves_from_the_longest_wait_hour_to_the_shortest():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 900) as page:
        page.goto(WAITING.as_uri() + "?x=slot48")
        _wait_for(page, "typeof WaitingShared !== 'undefined'", "共有の計算")
        out = page.js("""(function(){
          const m = (t) => Number(t.split(':')[0]) * 60 + Number(t.split(':')[1]);
          const mk = (date, a, s) => ({date, place: '江東', arrive: a, start: s, report_wait_min: m(s) - m(a), call: ''});
          // 7 時台は件数が多いが待ちは 60 分、8 時台は待ちが 90 分で最長、10 時台は待ちが 10 分で最短
          const rows = [mk('10/1','07:00','08:00'), mk('10/1','07:10','08:10'), mk('10/2','07:00','08:00'), mk('10/3','07:00','08:00'),
                        mk('10/1','08:00','09:30'), mk('10/2','08:00','09:30'), mk('10/1','10:00','10:10'), mk('10/2','10:00','10:10'), mk('10/3','10:00','10:10')];
          const sum = WaitingShared.shipperSummary(rows);
          return WaitingShared.shipperActions(sum).find((a) => a.id === 'slot').body;
        })()""")
    assert "8 時台の平均の待ちが最も長く" in out and "10 時台が最も短い" in out, out
    assert "7 時台" not in out, out                                 # 件数の多い 7 時台を選ばない
    assert "枠の提案はありません" in out or "10 時台に移します" in out, out


def test_cross_amount_is_for_the_same_carrier_and_unknown_carrier_shows_no_own_amount():
    _edge_or_skip()
    import capture_web as cw
    log = ("受付日\t運送会社\t施設\t車番\t受付時刻\t入場\n2026/10/5\tA運輸\t江東区 第1倉庫\t1234\t8:00\t10:35\n"
           "2026/10/6\tB急送\t江東区 第1倉庫\t9999\t8:10\t9:00\n")
    doc_a = "日付 車番 場所 到着 荷役開始 荷役終了 参考額（円）\n2026/10/5 1234 江東区 第1倉庫 7:30 11:20 11:50 20000\n"
    doc_x = "日付 車番 場所 到着 荷役開始 荷役終了 参考額（円）\n2026/10/5 7777 江東区 第1倉庫 7:30 11:20 11:50 20000\n"
    with cw.edge_page(1280, 1000) as page:
        page.goto(WAITING.as_uri() + "?x=cross48")
        _wait_for(page, "!!document.querySelector('#b-paste')", "荷待ちのページ")
        page.js("document.querySelector('#b-clear').click()")
        page.js("document.querySelector('#paste').value = " + json.dumps(log, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        _wait_for(page, "!!document.querySelector('#cross-ta')", "照合の欄")
        page.js("document.querySelector('#cross-ta').value = " + json.dumps(doc_a, ensure_ascii=False))
        page.js("document.querySelector('#b-cross').click()")
        _wait_for(page, "!!document.querySelector('#cross-amount')", "額")
        a = page.js("({f: S.cross.carrier_filter, auto: S.cross.carrier_auto, n: S.cross.shipper_n, t: document.querySelector('#cross-amount').textContent})")
        assert a["f"] == "A運輸" and a["auto"] and a["n"] == 1 and "A運輸の行だけ" in a["t"] and "書面の車番から決めました" in a["t"], a
        page.js("document.querySelector('#cross-ta').value = " + json.dumps(doc_x, ensure_ascii=False))
        page.js("document.querySelector('#b-cross').click()")
        _wait_for(page, "S.cross && S.cross.carrier_ambiguous === true", "決められない")
        x = page.js("document.querySelector('#cross-amount').textContent")
        assert "並べません" in x and "決められなかった" in x and "円" in x, x


def test_repeated_row_notes_are_collapsed_to_one_summary_line():
    _edge_or_skip()
    import capture_web as cw
    rows = "".join(f"2026/10/{1 + i % 28}\tA運輸\t江東区 第1倉庫\t{1000 + i}\t中型\t8:00\t9:00\t9:30\n" for i in range(12))
    text = "日付\t契約の相手\t場所\t車番\t車種\t到着\t荷役開始\t荷役終了\n" + rows
    with cw.edge_page(1280, 900) as page:
        page.goto(WAITING.as_uri() + "?x=notes48")
        _wait_for(page, "!!document.querySelector('#b-paste')", "荷待ちのページ")
        page.js("document.querySelector('#b-clear').click()")
        page.js("document.querySelector('#paste').value = " + json.dumps(text, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        _wait_for(page, "document.querySelectorAll('#tbody tr').length >= 12", "表")
        info = page.js("""({groups: document.querySelectorAll('details.note-group').length,
          lines: [...document.querySelectorAll('#errs .note div')].filter((d) => d.textContent.includes('荷役をした人が分からない')).length,
          sum: [...document.querySelectorAll('details.note-group summary')].map((s) => s.textContent)})""")
    assert info["lines"] == 0, info
    assert info["groups"] >= 1 and any("12 行" in x for x in info["sum"]), info


def test_document_table_columns_do_not_break_per_character_at_1920():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(WAITING.as_uri() + "?x=cols48")
        _wait_for(page, "!!document.querySelector('#b-example')", "荷待ちのページ")
        page.js("document.querySelector('#b-example').click()")
        _wait_for(page, "!!document.querySelector('#wait-doc table.rec')", "書面")
        info = page.js("""(function(){
          const th = [...document.querySelectorAll('#wait-doc table.rec')[0].querySelectorAll('th')];
          const pick = (t) => th.find((x) => x.textContent.startsWith(t));
          return {hs: ['待機（分）', '荷役（分）'].map((t) => pick(t).getBoundingClientRect().height), notes: !!document.querySelector('#rec-notes')};
        })()""")
    assert all(h < 40 for h in info["hs"]), info          # 1 文字ずつ折れると見出しの高さが 100px を超える
    assert info["notes"], "注記は表の下に番号つきで出す"


def test_driver_first_view_has_five_time_buttons_and_one_primary():
    _edge_or_skip()
    import capture_web as cw
    for w, h in ((1920, 1080), (360, 700)):
        with cw.edge_page(w, h) as page:
            page.goto(DRIVER.as_uri() + f"?x=first48_{w}")
            _wait_for(page, "!!document.querySelector('[data-stamp=arrive]')", "ドライバーの 1 枚")
            info = page.js("""(function(){
              const vis = [...document.querySelectorAll('button, input, select, textarea, summary, a')].filter((e) => { const r = e.getBoundingClientRect(); const d = e.closest('details:not([open])'); return r.width > 0 && r.height > 0 && r.top < window.innerHeight && (!d || (e.tagName === 'SUMMARY' && e.parentElement === d)); });   // 閉じた details の中身は見えない
              return {n: vis.length, stamps: vis.filter((e) => e.dataset.stamp).length, primary: vis.filter((e) => e.classList.contains('primary')).length,
                      closed: ['setup', 'fix'].every((id) => !document.getElementById(id).open), names: vis.map((e) => e.id || e.dataset.stamp || e.textContent.slice(0, 8))};
            })()""")
            assert info["stamps"] == 5 and info["primary"] == 1 and info["closed"], info
            assert info["n"] <= 12, info
