"""SPEC §42 道具の採点 r4・実務の効き目 r2 のうち、比べるページ・空きを探す・起動・部品まわりの修正。

- r4a P1-1: 比べるページの帰り便の判定は床と重さを整数で足し、地図の照合（Decimal）と同じ表で同じ結果になる。
- r4a P1-2: 空きを探すで画面の中だけ変えた値は、荷主のカードに出さない。カード・照合・仮予約・書面が同じ値を使う。
- r4b P1-3: スクリプトの読み込みが 1 本失敗しても、ほかの見方は起動する。
- r4a P2-1・r2b #3 #15 #16: 契約単価は 0・負の数を断り、運賃表を貼り付けられ、荷物の表が残る。
Edge が無い環境だけ画面の試験を飛ばす。Edge は同時に 1 つだけ（conftest の edge_page）。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest
from conftest import edge_page

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

from shipcompare import spaceb  # noqa: E402

from test_v21_backhaul import s6_body  # noqa: E402
from test_v3_web import call, srv  # noqa: E402,F401  (srv は fixture)
from test_v39_vehicle_fixes import cargo, trips_by_id  # noqa: E402

PAGE = ROOT / "web" / "compare_lite.html"
ARTIFACT = ROOT / "web" / "publish" / "compare_lite_artifact.html"

# ---------------------------------------------------------------- P1-1 共有の表
# 荷（行ごとに (個数, 縦, 横, 高さ cm, 1 個の kg)）と、デモの便 T7（重さの余り 2,000kg・床の空き 6.0m²）に載るか。
# JS の小数で足すと 6.000000000000001 になる 2 つ（0.4m² を 15 行、0.2 + 4.9 + 0.9m²）を含む。
HOME_CASES = [
    ("0.4m² を 15 行（床 6.0m² ちょうど）", [(1, 100, 40, 50, 10)] * 15, True),
    ("0.2 + 4.9 + 0.9m²（床 6.0m² ちょうど）", [(1, 100, 20, 50, 10), (1, 100, 490, 50, 10), (1, 100, 90, 50, 10)], True),
    ("床 6.01m²", [(1, 100, 40, 50, 10)] * 15 + [(1, 10, 10, 50, 10)], False),
    ("0.1m² を 60 行（床 6.0m²）", [(1, 100, 10, 50, 10)] * 60, True),
    ("2,000kg ちょうど・床 6.0m²", [(10, 100, 60, 50, 200)], True),
    ("2,000.1kg", [(10, 100, 60, 50, 200.01)], False),
    ("0.1kg を 20,000 行ぶんの 2,000kg（10 行 × 200kg）", [(1, 50, 50, 50, 200)] * 10, True),
    ("0.1kg 超過（2,000.1kg）", [(1, 50, 50, 50, 200)] * 10 + [(1, 10, 10, 10, 0.1)], False),
]


def _api_body(rows):
    body = s6_body()
    body["shipment"] = {"items": [{"count": c, "length_cm": l, "width_cm": w, "height_cm": h, "weight_kg": kg} for c, l, w, h, kg in rows]}
    return body


def _py_ok(rows):
    sh = spaceb.parse_shipment(_api_body(rows))
    return spaceb.match(sh, trips_by_id()["T7"])[0]


@pytest.mark.parametrize("name,rows,expect", HOME_CASES, ids=[c[0] for c in HOME_CASES])
def test_home_table_python_and_api_agree(srv, name, rows, expect):  # noqa: F811
    _, port = srv
    assert _py_ok(rows) is expect, name
    st, m = call(port, "POST", "/api/b/match", _api_body(rows))
    assert st == 200
    assert {x["trip_id"]: x["ok"] for x in m["results"]}["T7"] is expect, name


def test_home_table_js_agrees_with_python():
    want = [bool(_py_ok(rows)) for _, rows, _ in HOME_CASES]
    assert want == [c[2] for c in HOME_CASES]
    js_rows = [[dict(n=c, l=l, w=w, h=h, kg=kg) for c, l, w, h, kg in rows] for _, rows, _ in HOME_CASES]
    for path in (PAGE, ARTIFACT):
        with edge_page(1200, 800, webgl=False) as page:
            page.goto(path.as_uri() + "?example=1")
            page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
            got = page.js("(" + json.dumps(js_rows) + ").map((rows) => { const r = evaluate({rows, from: '東京都', to: '鹿児島県', km: 492, recv: 'business', flex: 4})"
                          ".options.find((o) => o.id === 'home'); return {s: r.status, why: r.reasons.join(' / ')}; })")
        assert [g["s"] == "check" for g in got] == want, (path.name, got)
        # 理由の文に、丸め誤差の桁（e-16 など）を出さない
        assert not any("e-" in g["why"] for g in got), got


def test_compare_lite_selftest_passes_on_both_builds():
    for path in (PAGE, ARTIFACT):
        with edge_page(1200, 800, webgl=False) as page:
            page.goto(path.as_uri() + "?selftest=1")
            page.wait(lambda: "SELFTEST" in page.js("document.title"), 30, "自己確認")
            title = page.js("document.title")
        assert title.startswith("SELFTEST PASS"), (path.name, title)


# ---------------------------------------------------------------- 比べるページ: 契約単価・運賃表・荷物の保存
def _compare_page(page, extra=""):
    page.goto(PAGE.as_uri() + "?example=1" + extra)
    page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")


SET_JS = """function set(sel, v){const el=document.querySelector(sel);el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));}"""
TAB = chr(9)
NL = chr(10)


def test_contract_rates_reject_zero_and_negative_and_do_not_restore_them():
    with edge_page(1200, 900, webgl=False) as page:
        _compare_page(page)
        page.js("(function(){" + SET_JS + "set('#c-t-100','0');set('#c-t-120','-5');set('#c-r-rate','15');})()")
        errs = page.js("document.querySelector('#c-errs').textContent")
        assert "0 円では入れられません" in errs and "負の数では入れられません" in errs
        # 0 は額に使わない（0 円の宅配が安い順の先頭に出ない）
        assert page.js("readContract().tk[100]") is None and page.js("readContract().tk[120]") is None
        assert "0 円" not in page.js("document.querySelector('#cmp').textContent").split("いちばん安い")[0]
        # 保存から戻すときも、欄に入れない
        page.js("localStorage.setItem('compare_lite_contract_v1', JSON.stringify({tk: {'100': '-5', '120': '0', '140': '1800'}, mode: 'kg', rate: '-1', min: '', rtab: '', ttab: ''}))")
        _compare_page(page)
        assert page.js("document.querySelector('#c-t-100').value") == "" and page.js("document.querySelector('#c-t-120').value") == ""
        assert page.js("document.querySelector('#c-t-140').value") == "1800" and page.js("document.querySelector('#c-r-rate').value") == ""
        assert "戻しませんでした" in page.js("document.querySelector('#c-msg').textContent")


ROSEN_TABLE = NL.join([TAB.join(["", "〜50kg", "〜100kg", "〜200kg", "〜500kg", "500kg超"]),
                       TAB.join(["〜300km", "3,500", "5,200", "8,800", "14,000", "21,000"]),
                       TAB.join(["〜600km", "4,200", "6,400", "11,000", "17,000", "26,000"]),
                       TAB.join(["601km〜", "5,000", "7,600", "13,000", "20,000", "30,000"])])
TAKUHAI_TABLE = NL.join([TAB.join(["", "60サイズ", "80サイズ", "100サイズ", "120サイズ", "140サイズ"]),
                         TAB.join(["関東", "900", "1,100", "1,300", "1,500", "1,700"]),
                         TAB.join(["関西", "1,000", "1,200", "1,400", "1,600", "1,800"]),
                         TAB.join(["九州・沖縄", "1,400", "1,700", "2,000", "2,300", "2,600"])])


def test_pasted_contract_tables_from_excel_are_used_for_the_amounts():
    with edge_page(1920, 1080, webgl=False) as page:
        _compare_page(page)
        # 小さな荷 4 個（30×20×10cm = 3 辺 60cm、20kg）。埼玉 → 大阪 450km
        page.js("(function(){" + SET_JS + "document.querySelector('#b-clear').click();"
                "set('input.pin[data-i=\"0\"][data-k=\"n\"]','4');set('input.pin[data-i=\"0\"][data-k=\"l\"]','30');set('input.pin[data-i=\"0\"][data-k=\"w\"]','20');"
                "set('input.pin[data-i=\"0\"][data-k=\"h\"]','10');set('input.pin[data-i=\"0\"][data-k=\"kg\"]','20');"
                "const f=document.querySelector('#from');f.value='埼玉県';f.dispatchEvent(new Event('change',{bubbles:true}));"
                "const t=document.querySelector('#to');t.value='大阪府';t.dispatchEvent(new Event('change',{bubbles:true}));set('#km','450');"
                "set('#c-rtab'," + json.dumps(ROSEN_TABLE) + ");set('#c-ttab'," + json.dumps(TAKUHAI_TABLE) + ");})()")
        msgs = page.js("[document.querySelector('#c-rtab-msg').textContent, document.querySelector('#c-ttab-msg').textContent]")
        assert "3 行 × 5 列を読みました" in msgs[0] and "距離帯" in msgs[0], msgs
        assert "3 行 × 5 列を読みました" in msgs[1], msgs
        # 宅配: 1 個 20kg は重さで 140 サイズ（3 辺合計は 60cm でも）。関西 × 140 サイズ 1,800 円 × 4 個 = 7,200 円。路線便: 実重量 80kg → 450km（〜600km）× 〜100kg = 6,400 円
        a = json.loads(page.js("(function(){const a=amountsOf(S.res, readContract(), inputs());return JSON.stringify({t:a.takuhai, r:a.rosen});})()"))
        assert a["t"]["amount"] == 7200 and "関西" in a["t"]["note"] and "140サイズ" in a["t"]["note"] and "重さ" in a["t"]["note"], a
        assert a["r"]["amount"] == 6400 and "〜600km" in a["r"]["note"] and "〜100kg" in a["r"]["note"], a
        text = page.js("document.querySelector('#cmp').textContent")
        assert "7,200" in text and "6,400" in text and "貼り付けた運賃表" in text
        # 保存され、再読み込みしても戻る（荷物の表も残る）
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        assert "3 行 × 5 列" in page.js("document.querySelector('#c-rtab-msg').textContent")
        # 表にない届け先は、理由つきで額なし
        page.js("(function(){const t=document.querySelector('#to');t.value='北海道';t.dispatchEvent(new Event('change',{bubbles:true}));})()")
        why = page.js("amountsOf(S.res, readContract(), inputs()).takuhai.why")
        assert "北海道" in why and "行がありません" in why, why
        # 壊れた表は理由つきで断る（0、負の数、見出しが読めない、降順）
        bad = {
            "zero": TAB.join(["", "〜50kg"]) + NL + TAB.join(["〜300km", "0"]),
            "neg": TAB.join(["", "〜50kg"]) + NL + TAB.join(["〜300km", "-100"]),
            "head": TAB.join(["", "軽い"]) + NL + TAB.join(["〜300km", "100"]),
            "desc": TAB.join(["", "〜100kg", "〜50kg"]) + NL + TAB.join(["〜300km", "100", "200"]),
        }
        gap = TAB.join(["", "〜200kg", "500kg超"]) + NL + TAB.join(["〜600km", "100", "200"])
        g = json.loads(page.js("(function(){const r=parseRateTable(" + json.dumps(gap) + ", 'rosen');return JSON.stringify([lookupRosenTable(r.table, 235, 450, '').amount, lookupRosenTable(r.table, 600, 450, '').amount, lookupRosenTable(r.table, 150, 450, '').amount]);})()"))
        assert g == [None, 200, 100], g   # 200kg 超〜500kg の帯が無い表は、その重さを引かない
        for k, txt in bad.items():
            r = json.loads(page.js("JSON.stringify(parseRateTable(" + json.dumps(txt) + ", 'rosen'))"))
            assert r["errs"] and "table" not in r, (k, r)


def test_cargo_table_stays_across_reload_and_works_without_storage():
    with edge_page(1200, 900, webgl=False) as page:
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        page.js("document.querySelector('#b-clear').click()")
        page.js("(function(){" + SET_JS + "set('input.pin[data-i=\"0\"][data-k=\"n\"]','7');set('input.pin[data-i=\"0\"][data-k=\"l\"]','55');set('input.pin[data-i=\"0\"][data-k=\"w\"]','44');"
                "set('input.pin[data-i=\"0\"][data-k=\"h\"]','33');set('input.pin[data-i=\"0\"][data-k=\"kg\"]','9');set('#km','321');})()")
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        vals = page.js("['n','l','w','h','kg'].map((k) => document.querySelector('input.pin[data-i=\"0\"][data-k=\"' + k + '\"]').value).join(',')")
        assert vals == "7,55,44,33,9" and page.js("document.querySelector('#km').value") == "321"
        assert page.js("document.querySelector('#example-note').hidden") is True
        # 保存が使えないときも動く（例の荷物を出す）
        page.js("Object.defineProperty(window, 'localStorage', {get() { throw new Error('blocked'); }}); document.querySelector('#b-example').click()")
        assert page.js("!!document.querySelector('table.res tbody tr')") is True


# ---------------------------------------------------------------- 空きを探す: 画面の中だけの変更が荷主のカード・照合・仮予約・書面と食い違わない（r4a P1-2）
def _find_js(page, expr):
    return page.js(expr)


def test_find_screen_only_edits_never_reach_the_shipper_card_match_hold_or_document():
    import conftest
    port = conftest.server_port("local")
    call(port, "POST", "/api/b/reset", {})
    try:
        with edge_page(1920, 1080) as page:
            conftest.open_app(page, f"http://127.0.0.1:{port}/index.html#v=find")
            card = "(function(){var c=document.querySelector('.f-card[data-t=T7]');return c?c.textContent:''})()"
            before = page.js(card)
            assert "積める重さの残り 2,000 / 2,000 kg" in before and "95,000" in before
            # 運送会社の役で、T7 の重さの余りを 1kg、料金を 99,999 円に変える（画面の中だけ）
            page.js("__app.Find.setRole('carrier')")
            page.js("(function(){var c=document.querySelector('#f-carrier-sel');var t=__app.Find.state.trips.find(function(t){return t.id==='T7'});c.value=t.carrier_id;c.dispatchEvent(new Event('change',{bubbles:true}));})()")
            page.js("(function(){var s=document.querySelector('#f-trip-sel');s.value='T7';s.dispatchEvent(new Event('change',{bubbles:true}));})()")
            page.js("(function(){document.querySelector('#f-slack-in').value='1';document.querySelector('#f-price-in').value='99999';document.querySelector('#f-apply').click();})()")
            carrier = page.js(card)
            assert "保存していない変更" in carrier and "99,999" in carrier and "積める重さの残り 1 / 1 kg" in carrier
            assert "保存した値は" in carrier  # 文言の直し 2026-11-07 and "95,000" in carrier   # 運送会社のカードには、盤の値も並べる
            # 荷主の役: カードは盤の値だけ。変えた値は出ない
            page.js("__app.Find.setRole('shipper')")
            shipper = page.js(card)
            assert "積める重さの残り 2,000 / 2,000 kg" in shipper and "95,000" in shipper
            assert "99,999" not in shipper and "未登録" not in shipper and "1 / 1" not in shipper
            assert "○ 載ります" in shipper
            # 照合（サーバー）の結果は、カードの「載ります」と同じ
            assert page.js("__app.Find.state.results.T7.ok") is True
            # 仮予約 → 承諾 → 書面。書面の対価はカードと同じ 95,000 円
            page.js("document.querySelector('.f-card[data-t=T7] [data-act=hold]').click()")
            page.wait(lambda: page.js("__app.Find.state.holds.some(function(h){return h.trip_id==='T7'})"), 30, "仮予約")
            held = page.js("__app.Find.state.holds.find(function(h){return h.trip_id==='T7'}).weight_kg")
            assert held == 120   # 荷 120kg（既定の荷。着地の既定が鹿児島市なので T7 に載り、例の荷には切り替わらない）を、盤の重さの余り 2,000kg から確保した（画面の 1kg ではない）
            page.js("__app.Find.setRole('carrier')")
            page.js("document.querySelector('.f-card[data-t=T7] [data-act=accept]').click()")
            page.wait(lambda: page.js("__app.Find.state.holds.some(function(h){return h.state==='accepted'})"), 30, "承諾")
            page.js("document.querySelector('.f-card[data-t=T7] [data-act=contract]').click()")
            page.wait(lambda: page.js("!document.querySelector('#f-contract').hidden"), 30, "書面")
            doc = page.js("document.querySelector('#f-contract').textContent")
            assert "対価: 95,000円" in doc and "99,999" not in doc
            # カードと書面の対価が同じ
            assert re.search(r"95,000", shipper) and "対価: 95,000円" in doc
    finally:
        call(port, "POST", "/api/b/reset", {})


def test_find_chip_names_the_one_piece_weight_when_only_that_fails():
    import conftest
    port = conftest.server_port("local")
    call(port, "POST", "/api/b/reset", {})
    # S6（合計 400kg、1 個 140kg）。T6（福岡市 → 鹿児島市）は重さの余り 800kg に収まるが、1 個の上限が 40kg。T6 の集荷地（福岡市博多区）から出す荷で確かめる
    st, m = call(port, "POST", "/api/b/match", dict(s6_body(), origin_code="40132", dest_code="46201", window_from="2026-11-22", window_to="2026-11-22"))
    r = next(x for x in m["results"] if x["trip_id"] == "T6")
    assert not r["ok"] and r["reasons"][0].startswith("1個の重さ"), r
    assert "1 個の重さが上限を超えます" in (ROOT / "web" / "find.js").read_text(encoding="utf-8")  # 画面のチップの文言
    assert "合計の重さが合いません" in (ROOT / "web" / "find.js").read_text(encoding="utf-8")   # 合計が足りないときは別の文


# ---------------------------------------------------------------- 起動: スクリプトが 1 本読めなくても全体は止まらない（r4b P1-3）
class _FlakyProxy:
    """手元の配信サーバーの前に立ち、指定のスクリプトを最初の n 回だけ（n が大きければずっと）404 で返す。それ以外はそのまま中継する。"""

    def __init__(self, target_port: int, fail_name: str, fail_times: int):
        import http.client
        import http.server
        import threading

        self.hits: list[str] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _go(self):
                path = self.path
                name = path.split("?")[0].rsplit("/", 1)[-1]
                if name == fail_name:
                    outer.hits.append(path)
                    if len(outer.hits) <= fail_times:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n) if n else None
                c = http.client.HTTPConnection("127.0.0.1", target_port, timeout=60)
                c.request(self.command, path, body=data, headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")})
                r = c.getresponse()
                body = r.read()
                self.send_response(r.status)
                for k, v in r.getheaders():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                c.close()

            do_GET = do_POST = _go

            def log_message(self, *a):  # noqa: D401
                pass

        http.server.ThreadingHTTPServer.request_queue_size = 128
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_a_script_that_fails_once_is_loaded_again_and_the_app_starts():
    import conftest
    with _FlakyProxy(conftest.server_port("local"), "own_calc.js", 1) as px, edge_page(1920, 1080) as page:
        conftest.open_app(page, f"http://127.0.0.1:{px.port}/index.html#v=market")
        info = json.loads(page.js("JSON.stringify({retried: __scriptRetry.retried, failed: __scriptRetry.failed, own: typeof OwnCalc, missing: __app.S.missingViews})"))
        assert info["retried"] == ["own_calc.js"] and info["failed"] == [] and info["own"] == "object" and info["missing"] == {}, info
        assert len(px.hits) == 2 and "retry=" in px.hits[1]


def test_a_script_that_never_loads_stops_only_its_own_view():
    import conftest
    with _FlakyProxy(conftest.server_port("local"), "own_calc.js", 99) as px, edge_page(1920, 1080) as page:
        conftest.open_app(page, f"http://127.0.0.1:{px.port}/index.html#v=market")
        info = json.loads(page.js("JSON.stringify({failed: __scriptRetry.failed, missing: Object.keys(__app.S.missingViews), view: __app.S.view, loading: !!document.querySelector('#loading:not(.done)') && document.querySelector('#loading').textContent})"))
        assert info["failed"] == ["own_calc.js"] and info["missing"] == ["own"] and info["view"] == "market", info
        assert not (info["loading"] and "読み込みに失敗" in info["loading"]), info
        # 市場を見る・空きを探す・送り方を比べるは使える
        page.js("__app.setView('find')")
        page.wait(lambda: page.js("__app.Find.state.loaded"), 60, "空きを探す")
        assert page.js("!document.querySelector('#find-view .view-missing') && !!document.querySelector('.f-card')") is True
        page.js("__app.setView('compare')")
        assert page.js("__app.S.view") == "compare"
        # 自社の運行を開いたときだけ、理由を出す
        page.js("__app.setView('own')")
        msg = page.js("(document.querySelector('#own-view .view-missing') || {}).textContent || ''")
        assert "この画面を開けませんでした" in msg and "読み直す" in msg, msg   # 文言の直し 2026-11-07
        assert page.js("!document.querySelector('#m-left .view-missing')") is True


# ---------------------------------------------------------------- 地図の画面「送り方を比べる」と見積の作業
def _open_compare(page, hash_):
    import conftest
    conftest.open_app(page, f"http://127.0.0.1:{conftest.server_port('public')}/index.html{hash_}", ready_js="!!window.__app && !document.querySelector('#loading')", wait_loading=False, start_timeout=180)


def _set_cargo(page, n, l, w, h, kg):
    if not page.js("document.querySelector('#cargo-toggle').getAttribute('aria-expanded') === 'true'"):
        page.js("document.querySelector('#cargo-toggle').click()")
    page.wait(lambda: page.js("!!document.querySelector('#cargo-editor .it-row')"), 30, "編集欄")
    page.js("while (document.querySelectorAll('#cargo-editor .it-row').length > 1) document.querySelectorAll('#cargo-editor .it-row')[1].querySelector('[data-del]').click();")   # 1 行目だけにする
    page.js("(function(){const set=(k,v)=>{const e=document.querySelector('#cargo-editor .it-row[data-i=\"0\"] [data-k=\"'+k+'\"]');e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));};"
            f"set('count',{n});set('length_cm',{l});set('width_cm',{w});set('height_cm',{h});set('weight_kg',{kg});}})()")
    page.js("document.querySelector('#ed-confirm').click()")


def test_map_compare_shows_cargo_ratio_when_only_charter_amounts_appear():
    with edge_page(1920, 1080) as page:
        _open_compare(page, "#v=compare&o=13104&l=km&d=23106")
        # 関東発の運賃表を大阪府・宮城県のどちらにも使うようになり、宅配にも額が出るので、表のない愛知県（名古屋市）を着地にして貸切だけに額が出る場面にする
        _set_cargo(page, 12, 50, 40, 35, 12)   # 12 箱、144kg、床 2.4m²（2t の床の 47%）。重さは小さいが床を使うので「過大」とは言わず、割合だけ出す
        page.wait(lambda: "床の 47%" in page.js("(document.querySelector('#cards .charter-only') || {}).textContent || ''"), 90, "貸切だけの注記")
        txt = page.js("document.querySelector('#cards .charter-only').textContent")
        assert "大きすぎ" not in txt and "金額が出るのは貸切だけです" in txt and "重さの 7%" in txt and "床の 47%" in txt, txt   # 文言の直し 2026-11-07
        # 小さな荷（4 箱、48kg、床 0.8m²）は、重さも床も 25% 未満なので「この荷に貸切は過大」
        _set_cargo(page, 4, 50, 40, 35, 12)
        page.wait(lambda: "床の 16%" in page.js("(document.querySelector('#cards .charter-only') || {}).textContent || ''"), 90, "小さな荷")
        txt = page.js("document.querySelector('#cards .charter-only').textContent")
        assert "この荷物に貸切は大きすぎます" in txt and "床の 16%" in txt, txt   # 文言の直し 2026-11-07
        # 床を使う荷（既定の場面の荷: 400kg・床 4.1m²）では「過大」と言わず、割合だけを出す
        _set_cargo(page, 10, 100, 100, 60, 200)   # 床 10m² は 2t の床を超える。重さ 2,000kg
        page.wait(lambda: "重さの 100%" in page.js("(document.querySelector('#cards .charter-only') || {}).textContent || ''") or not page.js("!!document.querySelector('#cards .charter-only')"), 90, "大きな荷")
        assert "大きすぎ" not in page.js("(document.querySelector('#cards .charter-only') || {}).textContent || ''")   # 文言の直し 2026-11-07


def test_charter_class_constants_match_vehicle_decks():
    decks = json.loads((ROOT / "data" / "vehicle_decks.json").read_text(encoding="utf-8"))
    by = {d["id"]: d for d in decks["decks"]}
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const CHARTER_CLASS = (\{.*?\});", js)
    cls = json.loads(re.sub(r"(\w+):", r'"\1":', m.group(1)).replace("'", '"'))
    for k, deck_id in decks["tariff_class_deck"].items():
        d = by[deck_id]
        assert abs(cls[k]["floor"] - d["length_mm"] * d["width_mm"] / 1e6) < 0.005, k


def test_new_input_clears_cargo_name_and_dates():
    with edge_page(1920, 1080) as page:
        _open_compare(page, "#v=compare&o=13104&l=km&d=27205")
        page.js("document.querySelector('#cargo-toggle').click()")
        page.wait(lambda: page.js("!!document.querySelector('#cargo-editor .it-row')"), 30, "編集欄")
        page.js("(function(){const s=document.querySelector('#sc-select');s.value='S6';s.dispatchEvent(new Event('change',{bubbles:true}));})()")
        filled = json.loads(page.js("JSON.stringify({name: document.querySelector('[data-f=cargo_name]').value, ship: document.querySelector('[data-f=ship_date]').value, w: document.querySelector('#cargo-editor .it-row [data-k=weight_kg]').value})"))
        assert filled["name"] and filled["w"], filled   # 場面 S6 の値が入っている
        page.js("(function(){const s=document.querySelector('#sc-select');s.value='';s.dispatchEvent(new Event('change',{bubbles:true}));})()")
        cleared = json.loads(page.js("JSON.stringify({name: document.querySelector('[data-f=cargo_name]').value, ship: document.querySelector('[data-f=ship_date]').value, desired: document.querySelector('[data-f=desired_date]').value, rows: document.querySelectorAll('#cargo-editor .it-row').length, w: document.querySelector('#cargo-editor .it-row [data-k=weight_kg]').value, c: document.querySelector('#cargo-editor .it-row [data-k=count]').value})"))
        assert cleared == {"name": "", "ship": "", "desired": "", "rows": 1, "w": "", "c": ""}, cleared


def test_total_only_quotes_rank_with_the_unknown_breakdown_mark():
    port = None
    import conftest
    port = conftest.server_port("public")
    lines = {k: {"kind": "included", "amount": None} for k in ("pickup", "trunk", "delivery", "handling", "fuel", "highway")}
    def quote(i, carrier, total):
        return {"id": f"Q{i}", "version": 1, "carrier": carrier, "product": "", "tax": "税抜", "received_on": "2026-11-06", "valid_until": "2026-11-20", "is_demo": False,
                "lines": {**lines, "other": {"kind": "amount", "amount": total}}}
    st, r = call(port, "POST", "/api/compare", {"quotes": [quote(1, "A社", 120000), quote(2, "B社", 95000)], "version": 1, "today": "2026-11-07"})
    assert st == 200, r
    ev = {e["quote"]["carrier"]: e for e in r["evaluations"]}
    assert r["ranking_shown"] and ev["B社"]["rank"] == 1 and ev["A社"]["rank"] == 2 and ev["B社"]["state"] == "complete", r
    js = (ROOT / "web" / "work.js").read_text(encoding="utf-8")
    # 2026-11-08 総点検の直しに合わせて 名前のそばの「（内訳不明）」をやめ、状態は「合計だけの回答」の1つにそろえた
    assert "isTotalOnly" in js and "合計だけの回答" in js and "（内訳不明）" not in js and 'name="total"' in js


# ---------------------------------------------------------------- 部品: 終了が開始より前の区間、400px の名札と出典
def _kit_page(page, port):
    import conftest
    page.send("Page.addScriptToEvaluateOnNewDocument", source=conftest.RESOURCE_HOOK)
    page.goto(f"http://127.0.0.1:{port}/examples/kit/index.html")
    try:
        page.wait(lambda: page.js("!!window.__kit && __kit.ready && __kit.mounted"), 120, "部品の例")
    except Exception as e:  # noqa: BLE001
        conftest.startup_failed(page, "部品の例が起動しません", e, "kit_v42")
    time.sleep(2.5)


def test_kit_legs_do_not_draw_reversed_legs_and_count_them(examples_server):
    with edge_page(1920, 1080) as page:
        _kit_page(page, examples_server)
        res = json.loads(page.js("""(function(){
          const base = __kit.data.legs.legs, a = base[0];
          const bad = Object.assign({}, a, {id: 'R1', start: '2026-10-05T09:00', end: '2026-10-05T08:00'});
          __kit.parts.legs.destroy();
          const host = document.getElementById('d-legs');
          const part = Kit.legs.mount(__kit.maps.legs, {legs: base.concat([bad])}, {legend: {container: host}});
          return JSON.stringify({n: base.length, text: host.textContent});
        })()"""))
        assert "終了が開始より前の 1 件" in res["text"], res["text"][-300:]
        assert "座標か判定が足りない" not in res["text"]
    assert "reversedLeg" in (ROOT / "web" / "kit" / "legs.js").read_text(encoding="utf-8")


def test_kit_example_at_400px_hides_overlapping_labels_and_collapses_attribution(examples_server):
    with edge_page(400, 860, webgl=True) as page:
        _kit_page(page, examples_server)
        info = json.loads(page.js("""(function(){
          const out = {};
          for (const id of ['m-flows', 'm-legs', 'm-timeline']) {
            const root = document.getElementById(id);
            const tags = Array.from(root.querySelectorAll('[data-kit="tag"]')).filter((e) => e.style.visibility !== 'hidden').map((e) => e.getBoundingClientRect());
            let overlap = 0;
            for (let i = 0; i < tags.length; i++) for (let j = i + 1; j < tags.length; j++) { const a = tags[i], b = tags[j]; if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) overlap += 1; }
            out[id] = {total: root.querySelectorAll('[data-kit="tag"]').length, visible: tags.length, overlap};
          }
          out.compact = Array.from(document.querySelectorAll('.maplibregl-ctrl-attrib')).map((e) => e.classList.contains('maplibregl-compact'));
          out.shown = Array.from(document.querySelectorAll('.maplibregl-ctrl-attrib')).map((e) => e.classList.contains('maplibregl-compact-show'));
          out.sw = document.documentElement.scrollWidth;
          return JSON.stringify(out);
        })()"""))
        for k in ("m-flows", "m-legs", "m-timeline"):
            assert info[k]["overlap"] == 0 and info[k]["visible"] >= 1, (k, info[k])
        assert info["compact"] and all(info["compact"]) and not any(info["shown"]), info
        assert info["sw"] <= 400, info
