"""SPEC §43 実務の効き目 r3 と道具の採点 r5 を受けた、荷待ち・ドライバー・送り方比べ・地図の画面の修正。

- 荷待ち: 受付の時刻があるときは受付から数えた待機を主な額にし、「除くと 0 円」を出さない。「荷待ち記録」で始まらないスマホのメモ、
  荷主の受付記録の見出し（受付日・受付時刻・入場・退場）、紙・PDF からコピーした表での照合。
- ドライバー: 日を選んで、前の日や夜中をまたいだ回もコピーできる。
- 送り方比べ: 重さを 10 進で足す（200.0001kg × 10）、2t の判定に荷の高さ、指数の書き方を断る、契約単価を 3 社まで並べる。
- 地図の画面: データの読み込みを読み直し、最後は日本語の文と「読み直す」。比べの要約を経路より上に置く。印が重ならない。
- /api/b/price_formula は shipment の中の dest_code を読む。
Edge の確認は 1 つずつ直列に動かす。Edge が無い環境では Edge の確認を飛ばす。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from shipcompare import waiting
from test_v3_web import srv  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

WAITING = ROOT / "web" / "waiting.html"
DRIVER = ROOT / "web" / "driver.html"
COMPARE = ROOT / "web" / "compare_lite.html"
DEFAULTS = {"vehicle_class": "中型", "method": "手積み"}


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _row(**kw):
    base = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A運輸", "arrive": "06:10", "start": "08:45", "shipper_fault": "はい"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 荷待ち（Python）
def test_reception_time_gives_a_wait_counted_from_reception_and_it_is_the_main_figure():
    res = waiting.calc([_row(reception="06:20"), _row(date="2026-10-06", arrive="09:00", start="10:00")], 2026)
    a, b = res["rows"]
    assert a["wait_min"] == 155 and a["reception_wait_min"] == 145 and a["reception_amount"] < a["amount"]
    assert "reception_amount" not in b                      # 受付の時刻が無い行は、到着から数えるだけ
    p = res["by_partner"][0]
    assert p["reception_count"] == 1 and p["reception_arrive_amount"] == a["amount"] and p["reception_amount"] == a["reception_amount"]
    assert p["main_amount"] == p["amount"] - a["amount"] + a["reception_amount"]
    # 早着の疑いの行に受付の時刻があれば、「受付の時刻が無い早着の疑い」には数えない（除くと 0 円にならない）
    assert p["early_assumed_count"] == 1 and p["early_assumed_norec_count"] == 0 and p["early_assumed_norec_amount"] == 0


def test_reception_before_arrival_or_after_start_is_not_used():
    res = waiting.calc([_row(arrive="09:30", reception="09:00", start="10:30"), _row(date="2026-10-06", arrive="06:00", reception="11:00", start="10:30")], 2026)
    assert all("reception_amount" not in r for r in res["rows"]) and "main_amount" not in res["by_partner"][0]


def test_no_reception_keeps_the_old_aggregate_shape():
    res = waiting.calc([_row()], 2026)
    assert not any(k.startswith("reception") or k == "main_amount" for k in res["by_partner"][0])


# ---------------------------------------------------------------- 荷待ち: 貼り付け
def test_phone_memo_without_the_label_is_read_and_unreadable_lines_are_explained():
    text = "11/10 江東 着7:50 呼ばれ9:40 終わり10:30\n10/5 尼崎 ○○物流DC 着6:10 受付6:20 呼ばれ8:45 相手 ○○\nあいう\n11/12 船橋 着8:00\n13/45 x 着8:00 呼ばれ9:00\n"
    d = waiting.parse_paste_detail(text)
    assert [r["date"] for r in d["rows"]] == ["11/10", "10/5"]
    assert d["rows"][0]["place"] == "江東" and d["rows"][0]["arrive"] == "7:50" and d["rows"][0]["start"] == "9:40" and d["rows"][0]["end"] == "10:30"
    assert d["rows"][1]["reception"] == "6:20" and d["rows"][1]["partner"] == "○○" and d["rows"][1]["place"] == "尼崎 ○○物流DC"
    assert d["rows"][0]["partner"] == waiting.MEMO_NO_PARTNER
    bad = next(n for n in d["notes"] if n.startswith("読めなかった行は表に入れていません"))
    assert "（3 行）" in bad and "「あいう」は日付" in bad and "「11/12 船橋 着8:00」は呼ばれ" in bad and "13/45" in bad
    res = waiting.calc(d["rows"], 2026, defaults=DEFAULTS)
    assert res["errors"] == [] and len(res["rows"]) == 2         # 壊れた行は表に残らない


def test_text_that_is_neither_a_table_nor_a_memo_says_what_shapes_are_read():
    d = waiting.parse_paste_detail("あいう\nえお\n")
    assert d["rows"] == [] and len(d["notes"]) == 1 and "driver.html" in d["notes"][0] and "「あいう」" in d["notes"][0]


def test_shipper_reception_headers_ukeke_nyujo_taijo_are_read():
    d = waiting.parse_paste_detail("受付日\t運送会社\t施設\t車番\t受付時刻\t入場\t退場\n10/5\tA運輸\t江東区\t1234\t7:50\t8:30\t10:40\n")
    assert d["shipper_log"] is True and d["rows"][0]["date"] == "10/5" and d["rows"][0]["arrive"] == "7:50"
    assert d["rows"][0]["start"] == "8:30" and d["rows"][0]["depart"] == "10:40"
    res = waiting.calc(d["rows"], 2026, defaults=DEFAULTS)
    assert res["errors"] == [] and res["rows"][0]["wait_raw_min"] == 40


# ---------------------------------------------------------------- 荷待ち（Edge）
def _wait_for(page, js, what):
    page.wait(lambda: page.js(js), 30, what)


def test_waiting_page_memo_paste_document_main_figure_and_pdf_table_cross_check():
    _edge_or_skip()
    import capture_web as cw
    memo = "10/5 尼崎 ○○物流DC 着6:10 受付6:20 呼ばれ8:45 相手 ○○\n10/6 尼崎 ○○物流DC 着7:50 受付8:05 呼ばれ9:40 相手 ○○\nあいう\n"
    with cw.edge_page(1280, 900) as page:
        page.goto(WAITING.as_uri() + "?x=v43")
        _wait_for(page, "!!document.querySelector('#b-paste')", "荷待ちのページ")
        page.js("document.querySelector('#paste').value = " + json.dumps(memo, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        _wait_for(page, "!!document.querySelector('#wait-doc table.rec')", "書面")
        got = page.js("""({doc: document.querySelector('#wait-doc').textContent, rows: document.querySelectorAll('#tbody tr').length,
                           bad: document.querySelectorAll('#tbody tr.bad').length, note: document.querySelector('#paste-note').textContent,
                           errs: document.querySelector('#errs').textContent})""")
        assert got["bad"] == 0 and got["errs"] == "", got
        assert "「あいう」は日付" in got["note"] and "表に入れていません" in got["note"]
        doc = got["doc"]
        assert "受付から数えた数字" in doc and "受付の時刻がある 2 回" in doc and "受付の時刻から数えると" in doc
        assert "を除くと 0 円" not in doc and "を除くと" not in doc
        assert "参考額の合計" in doc and "受付の時刻がある回は受付から数えた額" in doc
        # 荷主として使う: 受付記録を貼り、紙・PDF の表（空白で区切った列）を照合に貼る
        page.js("document.querySelector('#paste').value = " + json.dumps(
            "受付日\t運送会社\t施設\t車番\t受付時刻\t入場\n2026/10/5\tA運輸\t江東区\t1234\t7:50\t8:30\n2026/10/6\tA運輸\t江東区\t5678\t8:10\t9:00\n", ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        _wait_for(page, "!!document.querySelector('#cross-ta')", "照合の欄")
        pdf = "日付  車番  到着  荷役開始  荷役終了\n2026/10/5  1234  7:30  8:30  9:00\n2026/10/6  5678  8:10  9:00  9:30\n2026/10/7  9999  8:10  9:00\n"
        page.js("document.querySelector('#cross-ta').value = " + json.dumps(pdf, ensure_ascii=False))
        page.js("document.querySelector('#b-cross').click()")
        _wait_for(page, "!!document.querySelector('#cross-result') || !!document.querySelector('#cross-out .errs')", "照合の結果")
        cross = page.js("document.querySelector('#cross-out').textContent")
        assert "日付と車番が同じ組 2 組" in cross, cross
        assert "列の数が見出しと違うので読まなかった行が 1 行" in cross
        assert "参考額の合計の目安" in cross


# ---------------------------------------------------------------- ドライバー（Edge）
def test_driver_page_copies_any_day_and_keeps_a_night_crossing_record_in_its_day():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(400, 860) as page:
        page.goto(DRIVER.as_uri())
        _wait_for(page, "!!document.querySelector('[data-stamp=arrive]')", "ドライバーの 1 枚")
        page.js("""(function(){
          const mk = (id, date, place) => ({id, date, place, arrive: '7:50', start: '9:40', end: '10:30', vno: '1234', partner: '○○'});
          localStorage.setItem('driver_records_v1', JSON.stringify([mk('a', '10/5', '尼崎'), mk('b', '10/5', '尼崎 B'), mk('c', '11/1', '江東')]));
        })()""")
        page.goto(DRIVER.as_uri() + "?x=2")
        _wait_for(page, "!!document.querySelector('#day')", "日の選択")
        opts = page.js("[...document.querySelectorAll('#day option')].map((o) => o.textContent)")
        assert any("10/5（2 件）" in o for o in opts) and any("11/1（1 件）" in o for o in opts) and any("全部の日（3 件）" in o for o in opts), opts
        page.js("(function(){const s=document.querySelector('#day'); s.value='10/5'; s.dispatchEvent(new Event('input',{bubbles:true}));})()")
        info = page.js("""(function(){document.getElementById('b-copy').click();
          return {list: [...document.querySelectorAll('#recs li span:first-child')].map((e) => e.textContent), ta: document.getElementById('ta').value, title: document.getElementById('list-title').textContent};})()""")
        assert len(info["list"]) == 2 and all("荷待ち記録 10/5" in x for x in info["list"]) and "10/5の記録（2 件）" in info["title"], info
        # 夜中をまたいで入力中の回は、その回の日付（着いた日）を最初に選び、日をまたいでも同じ日にまとまる
        page.js("localStorage.setItem('driver_current_v1', JSON.stringify({date: '10/5', arrive: '23:40', reception: '', start: '', end: '', depart: ''}))")
        page.goto(DRIVER.as_uri() + "?x=3")
        _wait_for(page, "!!document.querySelector('#day')", "日の選択")
        sel = page.js("document.querySelector('#day').value")
        lines = page.js("[...document.querySelectorAll('#recs li span:first-child')].map((e) => e.textContent)")
        assert sel == "10/5" and len(lines) == 3 and any("入力中" in x for x in lines), (sel, lines)


# ---------------------------------------------------------------- 送り方比べ（Edge）
def test_compare_lite_decimal_weight_height_exponent_and_three_carriers():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        page.goto(COMPARE.as_uri() + "?example=1")
        _wait_for(page, "!!document.querySelector('table.res')", "判定")
        # 200.0001kg × 10 個（2,000.001kg）は、帰り便にも 2t にも載らない（地図の照合と同じ）。ちょうど 2,000kg は載る
        out = json.loads(page.js("""(function(){
          const by = (kg) => Object.fromEntries(evaluate({rows: [{n: 10, l: 100, w: 60, h: 50, kg}], from: '東京都', to: '大阪府', km: 492, recv: 'business', flex: 4}).options.map((o) => [o.id, o.status]));
          const ex = evaluate({rows: [{n: 2, l: 240, w: 60, h: 200, kg: 140}, {n: 2, l: 100, w: 60, h: 80, kg: 60}], from: '東京都', to: '大阪府', km: 492, recv: 'business', flex: 4}).options.find((o) => o.id === 'kashikiri_2t');
          return JSON.stringify({over: by('200.0001'), exact: by('200'), zen: by('２００．０００１'), ex2t: ex.status, exWhy: ex.reasons.join('/'), e3: readNum('1e3'), inf: readNum('Infinity')});})()"""))
        assert out["over"]["home"] == "ng" and out["over"]["kashikiri_2t"] == "ng" and out["zen"]["home"] == "ng"
        assert out["exact"]["home"] == "check" and out["exact"]["kashikiri_2t"] != "ng"
        assert out["ex2t"] == "check" and "2t ショート" in out["exWhy"] and "ロング" in out["exWhy"]    # 例の荷（高さ 200cm）は 2t ショートに入らない（地図の △ と同じ）
        assert out["e3"] is None and out["inf"] is None
        # 契約単価を 3 社まで、会社名つきで並べる（小さな荷にして、宅配と路線便の額が出るようにする）
        page.js("S.rows = [{n: 3, l: 40, w: 30, h: 20, kg: 5}]; renderRows(); render();")
        page.js("""(function(){
          const set = (sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); };
          set('#c-name', 'A社'); set('#c-t-100', '1500'); set('#c-r-rate', '12');
          set('#c-name-2', 'B社'); set('#c-t-100-2', '1200'); set('#c-r-rate-2', '15');
          set('#c-name-3', 'C社'); set('#c-t-100-3', '1800'); set('#c-r-rate-3', '9');
        })()""")
        page.wait(lambda: page.js("!!document.querySelector('#cmp-side')"), 10, "会社ごとの比べ")
        side = page.js("document.querySelector('#cmp-side').textContent")
        assert "A社" in side and "B社" in side and "C社" in side, side
        order = page.js("[...document.querySelectorAll('table.cmpt:not(#cmp-side) tbody tr')].map((tr) => tr.children[2].textContent.replace(/[^0-9]/g, '')).map(Number)")
        assert order == sorted(order) and len(order) >= 8, order
        names = page.js("document.querySelector('#cmp').textContent")
        assert "（A社）" in names and "（B社）" in names and "（C社）" in names
        saved = page.js("JSON.parse(localStorage.getItem('compare_lite_contract_v1')).more.map((m) => m.name)")
        assert saved == ["B社", "C社"]
        # 指数の書き方は、荷物の欄でも距離でも誤りにする
        page.js("(function(){const e=document.querySelector('#km'); e.value='1e3'; e.dispatchEvent(new Event('input',{bubbles:true}));})()")
        assert "1e3" in page.js("document.querySelector('#errs').textContent")


# ---------------------------------------------------------------- 地図の画面（Edge と配信サーバー）
def test_map_app_getjson_retries_then_shows_japanese_message_with_a_retry_button():
    _edge_or_skip()
    from test_v31_market_routes import _browser
    with _browser(1280, 900) as (page, open_):
        open_("#v=market")
        out = json.loads(page.js("""(async function(){
          const real = window.fetch; let calls = 0;
          window.fetch = (u) => { calls += 1; return calls < 3 ? Promise.reject(new TypeError('Failed to fetch')) : real(u); };
          const ok = await getJSON('data/meta.json').then(() => 'ok', (e) => e.message);
          const afterOk = calls;
          window.fetch = () => { calls += 1; return Promise.reject(new TypeError('Failed to fetch')); };
          const t0 = Date.now(); calls = 0;
          const msg = await getJSON('data/nothing.json').then(() => 'ok', (e) => e.message);
          const ms = Date.now() - t0;
          window.fetch = real;
          showLoadNotice(jpLoadMessage(new TypeError('Failed to fetch')), null);
          const n = document.querySelector('#load-notice');
          const r = n.getBoundingClientRect();
          return JSON.stringify({ok, afterOk, msg, ms, calls, notice: n.textContent, btn: !!n.querySelector('button.retry'), onScreen: r.width > 0 && r.bottom <= innerHeight && r.right <= innerWidth});})()"""))
        assert out["ok"] == "ok" and out["afterOk"] == 3                  # 2 回失敗して 3 回目に読めた
        assert out["calls"] == 3 and out["ms"] >= 1400                   # 3 回まで、間をあけて（500ms + 1,000ms）読み直す
        assert "Failed to fetch" not in out["msg"] and "読み込めませんでした" in out["msg"]
        assert "Failed to fetch" not in out["notice"] and "読み直す" in out["notice"] and out["btn"] and out["onScreen"]


def test_map_app_compare_summary_is_above_the_route_card_and_badges_do_not_overlap_days():
    _edge_or_skip()
    from test_v31_market_routes import _browser
    cards = ("[{label: '貸切', sub: '2t車', amount: 109750, status: '要確認', why: '積めるかどうかは判定していません', days: '900km まで翌日、以後 1 日ごとに加算'},"
             " {label: 'カゴ台車', amount: 98000, status: '公開条件に合う', est: true, days: '900km まで翌日、以後 1 日ごとに加算'},"
             " {label: '宅配', amount: 150000, status: '公開条件に合う', est: true, days: 'のみ 1 日'}]")
    # 印の位置は、実際の画面の幅（1920 の右のパネル、400 の下から出るパネル）と同じ幅の箱に図を入れて測る。パネルが閉じていても測れる
    badge = """(function(width){
      const host = document.createElement('div'); host.style.cssText = 'position:fixed;left:0;top:0;z-index:9999;background:#101826;width:' + width + 'px;padding:0 16px;box-sizing:border-box';
      host.innerHTML = CompareChart.html(%s); document.body.appendChild(host);
      const rows = [...host.querySelectorAll('.cc-row')].map((r) => {
        const d = r.querySelector('.cc-days'), marks = [...r.querySelectorAll('.cc-mark')], tr = r.querySelector('.cc-track').getBoundingClientRect(), dr = d ? d.getBoundingClientRect() : null;
        const cut = marks.filter((m) => m.getBoundingClientRect().right > tr.right + 0.5).length;
        const over = marks.filter((m) => { const mr = m.getBoundingClientRect(); return dr && dr.width > 0 && mr.right > dr.left + 0.5 && mr.left < dr.right - 0.5 && mr.bottom > dr.top + 0.5 && mr.top < dr.bottom - 0.5; }).length;
        return {cut, over, marks: marks.length, w: tr.width};
      });
      const out = JSON.stringify({rows, scrollW: host.scrollWidth, boxW: host.clientWidth}); host.remove(); return out;})""" % cards
    with _browser(1920, 1080) as (page, open_):
        open_("#v=compare&o=13104&l=km")
        res = json.loads(page.js(badge + "(460)"))
        assert sum(r["marks"] for r in res["rows"]) >= 2 and all(r["cut"] == 0 and r["over"] == 0 for r in res["rows"]), res
        # 比べの要約は、経路のカード（#rt-modes）より上に出る
        order = json.loads(page.js("""(function(){
          const host = document.querySelector('#cards'); host.innerHTML = CompareChart.html(%s) + '<div class="card" id="fake-card">経路のカード</div>';
          const rm = document.querySelector('#rt-modes'); rm.innerHTML = '<div id="fake-mode" style="height:90px">経路の切り替え</div>';
          const cc = host.querySelector('.cc').getBoundingClientRect(), fm = document.querySelector('#fake-mode').getBoundingClientRect(), fk = host.querySelector('#fake-card').getBoundingClientRect();
          return JSON.stringify({cc: cc.top, fm: fm.top, fk: fk.top, ord: getComputedStyle(host.querySelector('.cc')).order + '<' + getComputedStyle(rm).order});})()""" % cards))
        assert order["cc"] < order["fm"] < order["fk"] and order["ord"] == "1<3", order
    with _browser(400, 860) as (page, open_):
        open_("#v=compare&o=13104&l=km")
        res = json.loads(page.js(badge + "(400)"))
        assert sum(r["marks"] for r in res["rows"]) >= 2 and all(r["cut"] == 0 and r["over"] == 0 for r in res["rows"]), res
        assert res["scrollW"] <= res["boxW"], res


# ---------------------------------------------------------------- /api/b/price_formula
def test_price_formula_reads_dest_code_inside_shipment_like_match_or_refuses(srv):
    from test_v21_backhaul import s6_body
    from test_v3_web import post
    _, port = srv
    body = s6_body()
    dest = body.pop("dest_code")
    top = post(port, "/api/b/price_formula", {**body, "dest_code": dest, "trip_id": "T7"})
    inside = post(port, "/api/b/price_formula", {**body, "shipment": {**body["shipment"], "dest_code": dest}, "trip_id": "T7"})
    assert top[0] == 200 and inside[0] == 200 and top[1]["reference"]["reference_yen"] == inside[1]["reference"]["reference_yen"]
    none = post(port, "/api/b/price_formula", {**body, "trip_id": "T7"})
    assert none[0] == 400 and "dest_code" in json.dumps(none[1], ensure_ascii=False)
