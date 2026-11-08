"""地図の画面の目視確認（docs/reviews/2026-11-08_画面の目視_地図.md）の X2・X4 と、部品の読み込み失敗の試験。

- X2: 空きを探すの書面の下書きで、荷主の名称・住所・法人番号を入れて保存でき、確定できる（法人番号は空欄＝個人事業主、入れるなら 13 桁）。
- X4: 狭い画面で「全国の指標を見る」を押すと、下のパネルに全国の数字が出る。
- 荷待ちの計算（waiting_calc.js）などが構文エラーで配られても、地図の画面は起動し、自社の運行だけが「開けない」になる。
ヘッドレス Edge は同時に 1 つだけ（conftest の edge_page）。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import edge_page, open_app, server_port

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_v21_backhaul import s6_body  # noqa: E402

READY = "!!window.__app && !document.querySelector('#loading')"


def _post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _accepted_hold(port):
    """運送会社が承諾した仮予約（荷主の情報なし）を API で作る。"""
    _post(port, "/api/b/reset", {})
    b = dict(s6_body())
    b.pop("shipper", None)
    st, m = _post(port, "/api/b/match", b)
    assert st == 200, m
    t = next(r for r in m["results"] if r["ok"])
    st, h = _post(port, "/api/b/hold", dict(b, trip_id=t["trip_id"], hold_minutes=30))
    assert st == 200, h
    hid = h["hold"]["hold_id"]
    assert _post(port, "/api/b/decide", {"hold_id": hid, "decision": "accept"})[0] == 200
    return hid


# ---------------------------------------------------------------- X2 サーバー側
def test_contract_accepts_shipper_and_becomes_ready():
    port = server_port("local")
    try:
        hid = _accepted_hold(port)
        st, c = _post(port, "/api/b/contract", {"hold_id": hid})
        assert st == 200 and "荷主の法人番号（架空の番号）" in c["contract"]["missing"], c
        opts = next(i for i in c["contract"]["items"] if i["key"] == "payment")["options"]
        pay = opts[0] if opts else None
        sh = {"name": "架空商事株式会社", "address": "東京都江東区（架空の住所）", "corporate_number": ""}
        st, c = _post(port, "/api/b/contract", {"hold_id": hid, "fuel_surcharge": "なし", "payment_method": pay, "lift_equipment": "あり（架空）", "shipper": sh})
        assert st == 200, c
        d = c["contract"]
        assert d["ready"] and d["missing"] == [], d
        assert next(i for i in d["items"] if i["key"] == "corp_no")["value"] == "未記入"
        # 13 桁を入れたら、その番号が書面に出る
        sh["corporate_number"] = "1234567890123"
        st, c = _post(port, "/api/b/contract", {"hold_id": hid, "fuel_surcharge": "なし", "payment_method": pay, "lift_equipment": "あり（架空）", "shipper": sh})
        assert next(i for i in c["contract"]["items"] if i["key"] == "corp_no")["value"] == "1234567890123"
    finally:
        _post(port, "/api/b/reset", {})


@pytest.mark.parametrize("bad", [
    {"name": "", "address": "東京都", "corporate_number": ""},
    {"name": "架空商事", "address": "", "corporate_number": ""},
    {"name": "架空商事", "address": "東京都", "corporate_number": "12345"},
    {"name": "架空商事", "address": "東京都", "corporate_number": "12345678901ab"},
    {"name": "x" * 101, "address": "東京都", "corporate_number": ""},
    {"name": "架空商事", "address": "東京都", "corporate_number": 1234567890123},
])
def test_contract_rejects_bad_shipper(bad):
    port = server_port("local")
    try:
        hid = _accepted_hold(port)
        st, r = _post(port, "/api/b/contract", {"hold_id": hid, "shipper": bad})
        assert st == 400, r
    finally:
        _post(port, "/api/b/reset", {})


# ---------------------------------------------------------------- X2 画面（既定の例 S1 の荷物。荷主の情報は画面で入れる）
@pytest.mark.parametrize("w,h", [(1920, 1080), (390, 844)])
def test_contract_form_lets_the_shipper_confirm(w, h):
    port = server_port("local")
    with edge_page(w, h) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
        open_app(page, f"http://127.0.0.1:{port}/index.html#v=find", ready_js=READY, wait_loading=False, start_timeout=180, tag="map_visual_x2")
        try:
            page.wait(lambda: page.js("__app.Find.state.loaded"), 60, "空きを探す")
            page.wait(lambda: page.js("!!document.querySelector('#f-list .f-card.ok [data-act=hold]')"), 60, "載る便")
            page.js("document.querySelector('#f-list .f-card.ok [data-act=hold]').click()")
            page.wait(lambda: page.js("document.querySelectorAll('#f-list .f-card.hold-mine').length") == 1, 60, "仮予約")
            page.js("__app.Find.setRole('carrier')")
            page.wait(lambda: page.js("!!document.querySelector('#find-right [data-act=accept]')"), 30, "承諾のボタン")
            page.js("document.querySelector('#find-right [data-act=accept]').click()")
            page.wait(lambda: page.js("!!document.querySelector('#find-right [data-act=contract]')"), 30, "書面のボタン")
            page.js("document.querySelector('#find-right [data-act=contract]').click()")
            page.wait(lambda: page.js("!document.getElementById('f-contract').hidden && !!document.getElementById('fc-shipper')"), 30, "荷主の欄")
            assert page.js("document.getElementById('fc-confirm').disabled") is True
            assert page.js("(function(){const i=document.querySelector('#fc-shipper input[data-sh=name]'); const r=i.getBoundingClientRect(); return r.width>40 && r.height>10;})()") is True
            fill = """(function(k,v){const f=document.getElementById('fc-shipper'); const i=f.querySelector('input[data-sh='+k+']'); i.value=v; i.dispatchEvent(new Event('input',{bubbles:true}));})"""
            page.js(f"({fill})('name','架空商事株式会社')")
            page.js(f"({fill})('address','東京都江東区（架空の住所）')")
            page.js(f"({fill})('corporate_number','12345')")
            page.js("document.getElementById('fc-shipper').requestSubmit()")
            page.wait(lambda: "13 桁" in page.js("document.querySelector('.fc-sherr').textContent"), 10, "法人番号の誤り")
            page.js(f"({fill})('corporate_number','')")   # 個人事業主
            page.js("document.getElementById('fc-shipper').requestSubmit()")
            page.wait(lambda: "未記入" in page.js("document.getElementById('fc-body').innerText"), 30, "保存")
            page.js("""(function(){const s=document.querySelectorAll('#fc-body select[data-ct]'); s.forEach((e)=>{e.value=e.options[1].value; e.dispatchEvent(new Event('change',{bubbles:true}));});})()""")
            page.wait(lambda: page.js("document.getElementById('fc-confirm').disabled") is False, 30, "確定できる")
            page.js("document.getElementById('fc-confirm').click()")
            assert "確定しました" in page.js("document.getElementById('fc-body').innerText")
        finally:
            _post(port, "/api/b/reset", {})


# ---------------------------------------------------------------- X4
def test_market_summary_opens_in_the_mobile_sheet():
    port = server_port("local")
    with edge_page(390, 844) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        open_app(page, f"http://127.0.0.1:{port}/index.html#v=market", ready_js=READY, wait_loading=False, start_timeout=180, tag="map_visual_x4")
        page.wait(lambda: page.js("document.body.classList.contains('sheet-mode')"), 30, "狭い画面のパネル")
        page.js("document.getElementById('open-sum').click()")
        page.wait(lambda: page.js("getComputedStyle(document.getElementById('m-summary')).display") != "none", 10, "全国の指標")
        r = json.loads(page.js("JSON.stringify((function(){const e=document.getElementById('m-summary'), b=e.getBoundingClientRect(); return {h:b.height, text:e.innerText.length, btn:document.getElementById('open-sum').textContent};})())"))
        assert r["h"] > 40 and r["text"] > 20 and "閉じ" in r["btn"], r
        page.js("document.getElementById('open-sum').click()")
        page.wait(lambda: page.js("getComputedStyle(document.getElementById('m-summary')).display") == "none", 10, "閉じる")


# ---------------------------------------------------------------- 部品の読み込み失敗
class _BrokenProxy:
    """手元の配信サーバーの前に立ち、指定のスクリプトを 200 のまま構文エラーの本文で返す（読み直しの対象にならない誤り）。"""

    def __init__(self, target_port: int, broken: str):
        import http.client
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            def _go(self):
                path = self.path
                if path.split("?")[0].rsplit("/", 1)[-1] == broken:
                    body = b"const x = ;\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/javascript")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
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

            def log_message(self, *a):
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


@pytest.mark.parametrize("broken", ["waiting_calc.js", "own_calc.js"])
def test_broken_optional_module_only_disables_its_view(broken):
    with _BrokenProxy(server_port("local"), broken) as px, edge_page(1920, 1080) as page:
        open_app(page, f"http://127.0.0.1:{px.port}/index.html#v=market", ready_js=READY, wait_loading=False, start_timeout=180, tag="map_visual_broken")
        info = json.loads(page.js("JSON.stringify({missing: Object.keys(__app.S.missingViews), view: __app.S.view})"))
        assert info["missing"] == ["own"] and info["view"] == "market", info
        page.js("__app.setView('find')")
        page.wait(lambda: page.js("__app.Find.state.loaded"), 60, "空きを探す")
        assert page.js("!document.querySelector('#find-view .view-missing') && !!document.querySelector('.f-card')") is True
        page.js("__app.setView('own')")
        msg = page.js("(document.querySelector('#own-view .view-missing') || {}).textContent || ''")
        assert "この画面を開けませんでした" in msg, msg
