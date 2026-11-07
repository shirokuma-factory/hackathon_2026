"""SPEC §53.4 荷物の条件を文で書いて AI に表にしてもらう（地図の画面の荷物の欄。ヘッドレス Edge を 1 つだけ使い、AI の API はページの中で差し替える）。

- 使えるとき（status が available）だけ出る。使えないときは何も出ない
- 「送る」を押すまで /api/ai/cargo_rows へ要求を出さない。確認の枠に出した文と、送った本文が同じ。やめたら何も送らない
- 案は「AI の案」の表に出るだけで、「この行を荷物の表に入れる」を押すまで荷物の表は変わらない
画面の写真は、環境変数 CARGO_SHOT_DIR があればそこへ、無ければ一時フォルダへ書く。
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

TEXT = "段ボール 120サイズ 3箱 各40kg、パレット1枚 110×110×150cm 450kg、東京から大阪"
REPLY = {"ok": True, "cached": False, "origin": "東京", "destination": "大阪",
         "rows": [{"kind": "段ボール", "n": 3, "l_cm": None, "w_cm": None, "h_cm": None, "kg": 40, "note": "120サイズ（3辺合計120cm以内）"},
                  {"kind": "パレット", "n": 1, "l_cm": 110, "w_cm": 110, "h_cm": 150, "kg": 450, "note": ""}],
         "unknowns": ["120サイズは3辺合計120cm以内の区分で、縦・横・高さは分かりません", "出荷日"],
         "dropped": ["3 行目（その他）を外しました: 重さの 500 が、文にある数（単位の換算を含む）と合いません"]}

MOCK_AI = """(function(){
  window.__cargoCalls = []; window.__statusCalls = 0; window.__avail = %s; window.__cargoReply = %s;
  const of = window.fetch.bind(window);
  const json = (o, s) => new Response(JSON.stringify(o), {status: s || 200, headers: {'Content-Type': 'application/json'}});
  window.fetch = function(u, o){
    const url = String(u);
    if (url.indexOf('/api/ai/status') >= 0) { window.__statusCalls++; return Promise.resolve(json({available: window.__avail, reason: window.__avail ? '' : 'disabled', remaining: 87, limits: {daily: 100},
      cost: {tokens_per_char: 0.8, usd_jpy: 150, stages: []}, cargo_cost: {in_base: 650, out_typical: 600, out_max: 1000, price_in: 0.1, price_out: 0.5}})); }
    if (url.indexOf('/api/ai/cargo_rows') >= 0) { window.__cargoCalls.push(o && o.body ? o.body : ''); return Promise.resolve(json(window.__cargoReply)); }
    return of(u, o);
  };
})();"""

HASH = "#v=compare&o=13104&l=km&d=27128"


def start(page, port, avail=True):
    import capture_web as cw
    from conftest import RESOURCE_HOOK
    page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
    page.send("Page.addScriptToEvaluateOnNewDocument", source=MOCK_AI % (json.dumps(avail), json.dumps(REPLY, ensure_ascii=False)))
    page.send("Page.navigate", url=f"http://localhost:{port}/index.html{HASH}")
    try:
        page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
        page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
        page.wait(lambda: page.js("!!document.querySelector('#cargo-toggle')"), 60, "荷物の欄")
    except Exception as e:  # noqa: BLE001
        fail_with_evidence(page, "地図の画面が起動しません", e, "cargo_ai_fail")
    page.js("document.querySelector('#cargo-toggle').click()")
    page.wait(lambda: page.js("!!document.querySelector('#cargo-editor .it-row')"), 30, "荷物の編集欄")


@pytest.fixture(scope="module")
def app(local_server):
    import capture_web as cw
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        start(page, local_server)
        yield page


def shot(page, name, quality=60):
    d = Path(os.environ.get("CARGO_SHOT_DIR") or os.environ.get("TEMP", "."))
    d.mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=quality)
    (d / name).write_bytes(base64.b64decode(r["data"]))


def T(page, sel):
    return page.js(f"(document.querySelector({json.dumps(sel)}) || {{innerText: ''}}).innerText")


def click(page, sel):
    page.js(f"document.querySelector({json.dumps(sel)}).click()")


def setv(page, sel, v):
    page.js("(function(s, v){const e = document.querySelector(s); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true}));})(%s, %s)" % (json.dumps(sel), json.dumps(v)))


def items(page):
    return json.loads(page.js("JSON.stringify(W.draft.items)"))


def size(page, w, h):
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
    time.sleep(0.4)


def test_nothing_is_sent_before_send_and_cancel_sends_nothing(app):
    page = app
    size(page, 1920, 1080)
    page.wait(lambda: page.js("!!document.querySelector('#ai-cg-box')"), 20, "AI の欄")
    assert "文で書いて AI に表にしてもらう" in T(page, "#ai-cg-box > summary")
    page.js("document.querySelector('#ai-cg-box').open = true")
    setv(page, "#ai-cg-text", TEXT)
    click(page, "[data-cg=ask]")
    assert "この文を Anthropic の API に送ります" in T(page, "#ai-cg-confirm")
    assert page.js("document.querySelector('#ai-cg-sent').textContent") == TEXT          # 送る文を、そのまま見せる
    assert "今日の残り 87 回" in T(page, "#ai-cg-confirm") and "費用の目安" in T(page, "#ai-cg-confirm")
    assert T(page, "#ai-cg-send") == "送る" and T(page, "#ai-cg-cancel") == "やめる"
    assert page.js("window.__cargoCalls.length") == 0
    click(page, "#ai-cg-cancel")
    time.sleep(0.2)
    assert page.js("window.__cargoCalls.length") == 0 and page.js("!document.querySelector('#ai-cg-confirm')")


def test_payload_is_only_the_text_and_rows_enter_only_when_pressed(app):
    page = app
    size(page, 1920, 1080)
    before = items(page)
    setv(page, "#ai-cg-text", TEXT)
    click(page, "[data-cg=ask]")
    page.js("document.querySelector('#ai-cg-box').scrollIntoView({block: 'start'})")
    shot(page, "cargo_confirm_1920.jpg")
    click(page, "#ai-cg-send")
    page.wait(lambda: page.js("!!document.querySelector('#ai-cg-result')"), 10, "案の表示")
    calls = page.js("window.__cargoCalls")
    assert len(calls) == 1 and json.loads(calls[0]) == {"text": TEXT}                       # 本文だけ
    t = T(page, "#ai-cg-result")
    assert "AI の案" in t and "分からなかったこと" in t and "出荷日" in t and "この行を荷物の表に入れる" in t
    assert page.js("document.querySelectorAll('#ai-cg-result .ai-cg-r1').length") == 2
    assert items(page) == before                                                              # 押すまで荷物の表は変わらない
    page.js("document.querySelector('#ai-cg-box').scrollIntoView({block: 'start'})")
    shot(page, "cargo_result_1920.jpg")
    page.js("document.querySelectorAll('[data-cg=insert]')[1].click()")                       # パレットの行だけ入れる
    page.wait(lambda: len(items(page)) != len(before) or items(page) != before, 10, "行の追加")
    it = items(page)[-1]
    assert (it["count"], it["length_cm"], it["width_cm"], it["height_cm"], it["weight_kg"]) == (1, 110, 110, 150, 450)
    assert len(items(page)) == 1                                                              # 手を付けていない例の行は、入れた最初の AI の行に置き換わる（例の行は残らない）
    assert page.js("!!document.querySelector('#ai-cg-result') && document.querySelectorAll('[data-cg=insert][disabled]').length") == 1   # 入れた行は押せない
    assert "入れました" in T(page, "#ai-cg-result")


def test_blank_first_row_is_replaced_and_dims_stay_empty_for_takuhai(app):
    page = app
    page.js("W.draft.items = blankShipment().items; renderCargoEditor();")
    page.wait(lambda: page.js("!!document.querySelector('#ai-cg-box')"), 10, "AI の欄")
    page.js("document.querySelector('#ai-cg-box').open = true")
    setv(page, "#ai-cg-text", TEXT)
    click(page, "[data-cg=ask]")
    click(page, "#ai-cg-send")
    page.wait(lambda: page.js("!!document.querySelector('#ai-cg-result')"), 10, "案の表示")
    page.js("document.querySelectorAll('[data-cg=insert]')[0].click()")
    page.wait(lambda: len(items(page)) == 1 and items(page)[0]["count"] == 3, 10, "行の入れ替え")
    it = items(page)[0]
    assert (it["length_cm"], it["width_cm"], it["height_cm"], it["weight_kg"]) == (None, None, None, 40)   # 120 サイズは縦・横・高さにしない


def test_server_error_shows_one_line_and_keeps_the_text(app):
    page = app
    page.js("document.querySelector('#ai-cg-box').open = true")
    setv(page, "#ai-cg-text", "木箱 2個")
    page.js("""(function(){const of = window.fetch; window.__of = of; window.fetch = function(u, o){ if (String(u).indexOf('/api/ai/cargo_rows') >= 0) return Promise.resolve(new Response(JSON.stringify({error: 'AI に表にしてもらえませんでした（RuntimeError）。荷物の表は手で入れてください'}), {status: 502, headers: {'Content-Type': 'application/json'}})); return of.apply(this, arguments); };})()""")
    try:
        click(page, "[data-cg=ask]")
        click(page, "#ai-cg-send")
        page.wait(lambda: page.js("!!document.querySelector('#ai-cg-err')"), 10, "エラーの表示")
        assert "RuntimeError" in T(page, "#ai-cg-err") and page.js("document.querySelector('#ai-cg-text').value") == "木箱 2個"
    finally:
        page.js("window.fetch = window.__of")


def test_narrow_screenshots(app):
    page = app
    size(page, 390, 844)
    try:
        page.js("(function(){const b = [...document.querySelectorAll('#sheet-tabs button')].find((x) => x.textContent.trim() === '条件'); if (b) b.click();})()")
        time.sleep(0.5)
        page.js("document.querySelector('#ai-cg-box').open = true")
        setv(page, "#ai-cg-text", TEXT)
        click(page, "[data-cg=ask]")
        page.js("document.querySelector('#ai-cg-confirm').scrollIntoView({block: 'center'})")
        time.sleep(0.3)
        shot(page, "cargo_confirm_390.jpg")
        click(page, "#ai-cg-send")
        page.wait(lambda: page.js("!!document.querySelector('#ai-cg-result')"), 10, "案の表示")
        page.js("document.querySelector('#ai-cg-result').scrollIntoView({block: 'center'})")
        time.sleep(0.3)
        shot(page, "cargo_result_390.jpg")
        assert page.js("document.documentElement.scrollWidth") <= 392
    finally:
        size(page, 1920, 1080)


def test_nothing_shown_when_not_available(local_server):
    import capture_web as cw
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        start(page, local_server, avail=False)
        time.sleep(1.0)
        assert page.js("!document.querySelector('#ai-cg-box')") and page.js("!!document.querySelector('#cargo-ai')")
