"""SPEC §44 最初の画面と操作: 役割の選択、起動の遅延、保存したアドレスの優先、戻る・進む、荷主の結果の並び。

- 通常のアドレスでは、荷主・運送会社の 2 つのカードだけを出し、地図と統計のデータは読まない。
- 地図用データの取得が失敗しても、役割は選べる（失敗は選んだあとに日本語で出る。サンプルへ自動では切り替えない）。
- #v=... と旧い比較のアドレス（v なし）は、これまでどおり指定先を条件ごと開く。
- 「最初の画面に戻る」と「条件に戻る」は別。ブラウザーの戻る・進むで最初の画面と各画面を行き来できる。
- 荷主の結果は 送り方 / この荷物に使えるか / 費用の目安 / 日数 の順で、経路と内訳は閉じており、送り方を選ぶと見積依頼文へ進める。
ヘッドレス Edge は同時に 1 つだけ（conftest の edge_page）。Edge が無いときだけ飛ばす。
"""

from __future__ import annotations

import http.client
import http.server
import json
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

READY = "!!window.__app && !document.querySelector('#loading')"


def _open_home(page, port):
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 30, "最初の画面")


def _open_saved(page, port, hash_):
    open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}", ready_js=READY, wait_loading=False, start_timeout=180, tag="v44_home_fail")


def _view(page):
    return page.js("window.__app ? window.__app.S.view : null")


def _wait_view(page, v, what=None):
    page.wait(lambda: _view(page) == v, 90, what or f"画面が {v} になる")


# ---------------------------------------------------------------- 最初の画面
@pytest.mark.parametrize("w,h", [(360, 800), (1280, 720)])
def test_home_shows_both_roles_and_buttons_without_scrolling(w, h):
    port = server_port("public")
    with edge_page(w, h) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
        _open_home(page, port)
        info = json.loads(page.js("""JSON.stringify({
          rects: [...document.querySelectorAll('#home button, #home h2')].map(e => { const r = e.getBoundingClientRect(); return [e.textContent.trim(), r.top, r.bottom, r.left, r.right]; }),
          cards: [...document.querySelectorAll('.home-card')].map(e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; }),
          scroll: document.scrollingElement.scrollHeight, h: innerHeight, sw: document.scrollingElement.scrollWidth, w: innerWidth,
          text: document.getElementById('home').innerText, app: !!window.__app })"""))
        texts = [r[0] for r in info["rects"]]
        for t in ["荷主の方", "運送会社の方", "荷物の条件を入れる", "運行記録を見る", "全国の物流傾向を見る", "すべての画面を開く"]:
            assert any(t in x for x in texts), (t, texts)
        for t, top, bottom, left, right in info["rects"]:
            assert top >= 0 and bottom <= info["h"] and left >= 0 and right <= info["w"], (t, top, bottom, info["h"])
        assert info["scroll"] <= info["h"] and info["sw"] <= info["w"], info
        assert abs(info["cards"][0][0] - info["cards"][1][0]) < 2, info["cards"]   # 2 つは同じ幅
        for bad in ["積載効率", "実車率", "順位", "凡例", "%"]:
            assert bad not in info["text"], bad
        assert info["app"] is False   # 地図の起動はまだ
        assert "荷物に合う送り方と費用の目安を比べられます。" in info["text"]
        assert "自社の運行記録から、帰りが空車になりやすい行き先と曜日が分かります。" in info["text"]


class _DataFailProxy:
    """/data/ と /api/ の取得を 503 で断る配信（地図用データの失敗を作る）。取得の回数を数える。"""

    def __init__(self, target_port: int):
        self.hits: list[str] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _go(self):
                path = self.path
                if path.split("?")[0].startswith(("/data/", "/api/")):
                    outer.hits.append(path)
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                c = http.client.HTTPConnection("127.0.0.1", target_port, timeout=60)
                c.request(self.command, path, headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")})
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

            do_GET = _go

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


@pytest.mark.parametrize("steps,hash_,cls", [(["#role-shipper", "#p-ship-compare"], "#v=compare&g=1", "view-compare"), (["#role-carrier", "#p-car-empty"], "#v=own&g=empty", "view-own"), (["#role-national"], "#v=market&g=1", "view-market")])
def test_roles_can_be_chosen_when_map_data_requests_fail(steps, hash_, cls):
    with _DataFailProxy(server_port("public")) as px, edge_page(1280, 720) as page:
        _open_home(page, px.port)
        time.sleep(1.5)
        assert px.hits == [], px.hits   # 最初の画面は、地図のデータを取りに行かない
        for b in steps:
            page.js(f"document.querySelector('{b}').click()")
        assert page.js("location.hash").startswith(hash_), page.js("location.hash")
        page.wait(lambda: page.js("!!document.querySelector('#load-notice')"), 90, "読み込み失敗の案内")
        msg = page.js("document.querySelector('#load-notice').textContent")
        assert "読み込" in msg and "読み直す" in msg, msg   # 文言の直し 2026-11-07
        assert px.hits, "選んだあとは取得しにいく"
        assert page.js("document.body.classList.contains('" + cls + "')") is True
        assert page.js("!!window.__app") is False   # サンプルなどへは自動で切り替えない


# ---------------------------------------------------------------- 保存したアドレス
SAVED = [
    ("#v=market&ot=confirm&m=truck&n=30&km=200", "market"),
    ("#v=find", "find"),
    ("#v=compare", "compare"),
    ("#v=own", "own"),
    ("#o=13104&l=days&d=27205", "compare"),   # v が無い旧い比較のアドレス
]


@pytest.mark.parametrize("hash_,view", SAVED, ids=[s[0] for s in SAVED])
def test_saved_urls_open_their_target_with_conditions(hash_, view):
    port = server_port("public")
    with edge_page(1280, 720) as page:
        _open_saved(page, port, hash_)
        _wait_view(page, view)
        assert page.js("getComputedStyle(document.getElementById('home')).display") == "none"
        assert page.js(f"document.body.classList.contains('view-{view}')") is True
        if view == "market":
            st = json.loads(page.js("JSON.stringify({n: __app.Market.state.n, km: __app.Market.state.minKm, m: __app.Market.state.mode})"))
            assert st == {"n": 30, "km": 200, "m": "truck"}, st
            h = page.js("location.hash")
            assert "m=truck" in h and "n=30" in h and "km=200" in h, h
            # 全国の指標と順位は選んだときだけ開く
            assert page.js("getComputedStyle(document.getElementById('m-summary')).display") == "none"
            assert page.js("document.getElementById('open-sum').getAttribute('aria-expanded')") == "false"
            page.js("document.getElementById('open-sum').click()")
            assert page.js("getComputedStyle(document.getElementById('m-summary')).display") != "none"
            assert page.js("document.getElementById('open-sum').getAttribute('aria-expanded')") == "true"
            page.js("document.getElementById('open-rank').click()")
            assert page.js("document.getElementById('open-rank').getAttribute('aria-expanded')") == "true"
            assert page.js("getComputedStyle(document.getElementById('m-table-wrap')).display") != "none"
            cond = page.js("document.getElementById('m-now-cond').textContent")
            assert "営業用トラック" in cond and "30" in cond and "200" in cond, cond
        if hash_.startswith("#o="):
            st = json.loads(page.js("JSON.stringify({o: __app.S.origin, l: __app.S.layer, sel: __app.S.sel})"))
            assert st["o"] == "13104" and st["l"] == "days" and st["sel"] >= 0, st


# ---------------------------------------------------------------- 戻る・進む
def test_home_and_condition_back_are_different_and_browser_history_works():
    port = server_port("public")
    with edge_page(1280, 720) as page:
        _open_home(page, port)
        page.js("document.querySelector('#role-national').click()")
        page.wait(lambda: page.js(READY), 180, "地図の起動")
        _wait_view(page, "market")
        assert page.js("location.hash").startswith("#v=market&g=1")
        # アプリ内の「最初の画面に戻る」
        page.js("document.querySelector('#guide-bar [data-home]').click()")
        _wait_view(page, "home")
        assert page.js("location.hash") == "" and page.js("getComputedStyle(document.getElementById('home')).display") != "none"
        assert page.js("document.activeElement.id") == "home-h"
        # ブラウザーの戻る・進む
        page.js("history.back()")
        _wait_view(page, "market")
        assert page.js("getComputedStyle(document.getElementById('home')).display") == "none"
        assert page.js("location.hash").startswith("#v=market")
        page.js("history.forward()")
        _wait_view(page, "home")
        # 荷主を選ぶ。条件に戻るは最初の画面へ戻らず、比べる画面に残る
        page.js("document.querySelector('#role-shipper').click()")
        page.js("document.querySelector('#p-ship-compare').click()")
        _wait_view(page, "compare")
        page.js("(() => { const A = window.__app; A.select(A.S.idx.codes.indexOf('27128')); })()")
        page.wait(lambda: page.js("document.body.classList.contains('has-detail')"), 60, "結果が開く")
        time.sleep(0.8)
        page.js("document.querySelector('#close').click()")
        time.sleep(0.5)
        assert _view(page) == "compare" and page.js("document.body.classList.contains('has-detail')") is False
        assert page.js("document.activeElement.id") == "q"   # 条件の欄へフォーカスが戻る
        page.js("document.querySelector('#panel [data-home]').click()")
        _wait_view(page, "home")
        page.js("history.back()")
        _wait_view(page, "compare")


def test_deep_link_can_return_to_home_and_back():
    port = server_port("public")
    with edge_page(1280, 720) as page:
        _open_saved(page, port, "#v=market&m=truck&n=30&km=200")
        page.js("document.querySelector('#m-now [data-home]').click()")
        _wait_view(page, "home")
        page.js("history.back()")
        _wait_view(page, "market")
        assert page.js("document.body.classList.contains('guided')") is False   # 保存したアドレスは、これまでの全部の画面
        assert "km=200" in page.js("location.hash")


# ---------------------------------------------------------------- キーボード
def test_roles_can_be_chosen_with_the_keyboard():
    port = server_port("public")
    with edge_page(1280, 720) as page:
        _open_home(page, port)
        ids = []
        for _ in range(2):
            page.key("Tab")
            ids.append(page.js("document.activeElement.id"))
        assert ids == ["role-shipper", "role-carrier"], ids
        page.key("Tab")
        assert page.js("document.activeElement.id") == "role-national"
        page.key("Enter")
        page.wait(lambda: page.js("location.hash").startswith("#v=market"), 30, "全国の画面へ")
        page.wait(lambda: page.js(READY), 180, "地図の起動")
        _wait_view(page, "market")


# ---------------------------------------------------------------- 荷主の結果
def test_shipper_result_order_collapsed_details_and_request_text():
    port = server_port("public")
    with edge_page(1280, 720) as page:
        _open_home(page, port)
        page.js("document.querySelector('#role-shipper').click()")
        page.js("document.querySelector('#p-ship-compare').click()")
        page.wait(lambda: page.js(READY), 180, "地図の起動")
        # 入力が先: 集荷地・届け先・荷物
        labs = page.js("[...document.querySelectorAll('#panel .lbl')].map(e => e.textContent.trim().slice(0, 3)).join(',')")
        assert labs.startswith("集荷地,届け先,荷物"), labs
        page.js("(() => { const A = window.__app; A.select(A.S.idx.codes.indexOf('27128')); })()")
        page.wait(lambda: page.js("!!document.querySelector('#cards .cc-row')"), 90, "結果の表")
        head = page.js("[...document.querySelectorAll('#cards .cc-head span')].map(e => e.textContent.replace(/\\s+/g, '')).join('|')")
        assert head == "送り方|使えるか|費用の目安|日数", head   # 文言の直し 2026-11-07
        rows = json.loads(page.js("""JSON.stringify([...document.querySelectorAll('#cards .cc-row')].map(r => ({
          name: r.querySelector('.cc-name').textContent.trim(), ok: r.querySelector('.cc-ok').textContent.trim(),
          amt: r.querySelector('.cc-track').textContent.trim(), days: r.querySelector('.cc-days').textContent.trim(),
          kids: [...r.children].map(c => c.className.split(' ')[0]).slice(0, 4) })))"""))
        assert rows, rows
        for r in rows:
            assert r["kids"] == ["cc-name", "cc-ok", "cc-track", "cc-days"], r
            assert r["ok"] in ("使える", "要確認", "使えない", "未確認"), r
            assert not r["amt"].startswith("0円") and r["amt"] != "", r
            assert r["days"] != "", r
        # 額の無い手段は 0 円にせず「要見積」か対象外の印にする
        # 鉄道・船・飛行機の行を足したので、鉄道は「日数と額は見積で確認」も許す
        for r in rows:
            if "円" not in r["amt"]:
                assert r["amt"] in ("要見積", "—", "日数と額は見積で確認", "航路の表に入っていません") or r["amt"].startswith("なし"), r
        # 経路の説明・内訳は閉じている
        assert page.js("document.getElementById('rt-details').open") is False
        assert page.js("document.querySelector('#cards .cards-more').open") is False
        assert page.js("document.querySelector('.route-note').open") is False
        # 結果欄の先頭（表）が画面の中に見える
        r0 = json.loads(page.js("JSON.stringify(document.querySelector('#cards .cc-row').getBoundingClientRect())"))
        assert 0 < r0["top"] < 560, r0
        # 案内の表示では一番安い行を初めから選び、見積依頼文を作るボタンが主ボタンとして出る（SPEC §47、画面の出来 r2 F9）
        assert page.js("!!document.querySelector('#cards input[name=cc-pick]:checked')") is True
        assert page.js("document.getElementById('make-request').hidden") is False
        page.js("document.querySelector('#cards input[name=cc-pick]').click()")
        assert page.js("document.getElementById('make-request').hidden") is False
        assert page.js("document.getElementById('make-request').textContent.trim()") == "見積の依頼文を作る"
        assert "選んだ送り方" in page.js("document.getElementById('pick-hint').textContent")
        page.js("document.getElementById('make-request').click()")
        page.wait(lambda: page.js("document.body.classList.contains('has-work')"), 60, "見積依頼文の作業が開く")


# ---------------------------------------------------------------- 架空のデモの表示
def test_find_demo_is_labelled_before_entering_and_inside():
    port = server_port("public")
    with edge_page(1280, 720) as page:
        _open_saved(page, port, "#v=compare")
        assert "架空のデモ" in page.js("document.querySelector('#views [data-view=find]').textContent")
        page.js("document.querySelector('#views [data-view=find]').click()")
        _wait_view(page, "find")
        assert "架空のデモ" in page.js("document.querySelector('#find-view .p-title').textContent")
        txt = page.js("document.getElementById('find-view').innerText + document.getElementById('find-notes').innerText")
        assert "現在募集中" not in txt and "入札" not in txt


# ---------------------------------------------------------------- 軽量版の直開き
def test_lite_page_opens_directly_and_shows_the_result_columns():
    page_file = ROOT / "web" / "compare_lite.html"
    with edge_page(1280, 800) as page:
        page.goto(page_file.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定の表")
        head = page.js("[...document.querySelectorAll('table.res th')].map(e => e.textContent).join('|')")
        assert head == "送り方|この荷物に使えるか|費用の目安（税抜）と理由", head


# ---------------------------------------------------------------- 目的から入る案内（SPEC §44 追記）
def _purposes(page):
    return page.js("[...document.querySelectorAll('.home-purposes')].filter(e => !e.hidden).map(e => [...e.children].map(c => c.textContent.trim()))")


def test_purpose_step_routes_and_keeps_g_in_the_address():
    port = server_port("public")
    with edge_page(1280, 720) as page:
        _open_home(page, port)
        page.js("document.querySelector('#role-carrier').click()")
        assert page.js("location.hash") == "#r=carrier"
        vis = _purposes(page)
        assert vis == [["どの曜日の帰りが空いているか知りたい", "帰りの空車を荷主に案内する文を作りたい", "荷待ちの時間を荷主に示す書面を作りたい", "ドライバーに荷待ちの時刻を記録してもらいたい"]], vis
        assert page.js("document.getElementById('p-car-wait').getAttribute('href')") == "waiting.html#g"
        assert page.js("document.getElementById('p-car-driver').getAttribute('href')") == "driver.html"
        page.js("document.getElementById('p-car-propose').click()")
        page.wait(lambda: page.js(READY), 180, "起動")
        _wait_view(page, "own")
        assert "g=propose" in page.js("location.hash") and page.js("__app.S.guide") == "propose"
        page.js("history.back()")
        page.wait(lambda: page.js("location.hash") == "#r=carrier" and _view(page) == "home", 30, "目的の選択に戻る")
        page.js("history.back()")
        page.wait(lambda: page.js("location.hash") == "" and page.js("!document.getElementById('home-step1').hidden"), 30, "役割の選択に戻る")
        page.js("document.querySelector('#role-shipper').click()")
        vis = _purposes(page)
        assert vis[0][0] == "この荷物の送り方と費用の目安を知りたい" and "荷待ちの記録を確かめたい" in vis[0][1], vis


BUDGETS = {1920: 450, 360: 300}


@pytest.mark.parametrize("view,w,h", [(v, w, h) for v in ("compare", "market", "find") for w, h in ((1920, 1080), (360, 800))])
def test_guided_screens_stay_within_the_reading_budget(view, w, h):
    from viewport_text import COUNT_JS
    port = server_port("public")
    with edge_page(w, h) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
        _open_saved(page, port, f"#v={view}&g=1")
        time.sleep(2.5)
        assert page.js("document.body.classList.contains('guided')") is True
        r = json.loads(page.js(COUNT_JS))
        assert r["chars"] <= BUDGETS[w] and r["primary"] <= 1 and r["controls"] <= 8, {k: v for k, v in r.items() if k != "samples"}
        if view == "compare":   # 結果を出した状態も同じ予算
            page.js("(() => { const A = window.__app; A.select(A.S.idx.codes.indexOf('27128')); })()")
            page.wait(lambda: page.js("!!document.querySelector('#cards .cc-row')"), 90, "結果の表")
            time.sleep(2.5)
            page.js("document.querySelector('#cards input[name=cc-pick]').click()")
            r = json.loads(page.js(COUNT_JS))
            assert r["chars"] <= BUDGETS[w] and r["primary"] <= 1 and r["controls"] <= 8, {k: v for k, v in r.items() if k != "samples"}


def test_all_view_toggle_restores_the_playground():
    port = server_port("public")
    with edge_page(1280, 720) as page:
        _open_saved(page, port, "#v=market&g=1")
        assert page.js("getComputedStyle(document.getElementById('views')).display") == "none"
        page.js("document.getElementById('g-toggle').click()")
        assert page.js("document.body.classList.contains('guided')") is False
        assert "g=" not in page.js("location.hash") and "v=market" in page.js("location.hash")
        assert page.js("getComputedStyle(document.getElementById('views')).display") != "none"


# ---------------------------------------------------------------- 地図を入口にする
def test_home_map_row_is_a_small_static_image_and_opens_the_map_in_one_click():
    port = server_port("public")
    assert (ROOT / "web" / "img" / "home_map.jpg").stat().st_size < 60_000
    with edge_page(1280, 720) as page:
        _open_home(page, port)
        info = json.loads(page.js("""JSON.stringify({img: document.querySelector('#role-national img').getAttribute('src'), lazy: document.querySelector('#role-national img').loading,
          text: document.getElementById('role-national').textContent, ml: typeof maplibregl, app: !!window.__app})"""))
        assert info["img"] == "img/home_map.jpg" and info["lazy"] == "lazy" and "日本地図で見る" in info["text"] and info["app"] is False, info
        page.js("document.getElementById('role-national').click()")   # 1 回の選択で地図の案内へ
        page.wait(lambda: page.js(READY), 180, "起動")
        _wait_view(page, "market")
        assert page.js("document.querySelectorAll('#mg-rows .mg-row').length") == 5
        assert page.js("__app.Market.state.rows.length") == 5   # 地図に出すのは上位 5 区間
        page.js("document.querySelectorAll('#mg-rows .mg-row')[1].click()")
        page.wait(lambda: "三重県から東京都へ帰る荷" in page.js("document.getElementById('mg-sel').textContent"), 10, "選んだ区間の数字")
        t = page.js("document.getElementById('mg-sel').textContent")
        assert "t" in t and "3 日間" not in t and page.js("document.getElementById('mg-rows').children[1].firstElementChild.getAttribute('aria-pressed')") == "true", t
