"""SPEC §53.2 帰り便の案内文の「AI で読みやすく整える」の画面（#v=own の手順 3。ヘッドレス Edge を 1 つだけ使い、AI の API はページの中で差し替える）。

- 使えるとき（status が available）だけボタンが出る。単独の版（own_lite.html）には出ず、通信の記述もない
- 「送る」を押すまで要求を出さない。送る本文は確認の枠に出した本文と同じ。やめたら何も送らない
- 社内の情報が文面に入っているときは送らず、既存の文を出す
- 「この案に置き換える」で文面が置き換わり、手で直した扱いになる。確認が必要な案は理由つきで出る
画面の写真は、環境変数 OFFER_SHOT_DIR があればそこへ、無ければ一時フォルダへ書く。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from test_v45_own_guided import click, size, step_of, to_guided  # noqa: E402

MOCK_AI = """(function(){
  window.__polishCalls = []; window.__polishReply = {ok: true, ready: true, text: '', problems: [], cached: false};
  const of = window.fetch.bind(window);
  const json = (o, s) => new Response(JSON.stringify(o), {status: s || 200, headers: {'Content-Type': 'application/json'}});
  window.fetch = function(u, o){
    const url = String(u);
    if (url.indexOf('/api/ai/status') >= 0) return Promise.resolve(json({available: true, reason: '', limits: {}}));
    if (url.indexOf('/api/ai/offer_polish') >= 0) { window.__polishCalls.push(o && o.body ? o.body : ''); return Promise.resolve(json(window.__polishReply)); }
    return of(u, o);
  };
})();"""

PROPOSAL = ("【宛先（会社名・ご担当者名）】 様\n\n【運送会社名】の【担当者名】です。\n\n"
            "毎週{wd}に、{fr}から{to}へ戻るトラックに空きがあります。\n\n最初に、次の点を教えてください。\n・荷物の品名・荷姿、数量\n・積込を希望する日時\n"
            "詳しい条件は、ご関心があればあらためて伺います。\n\nまずは 1 回、お試しでいかがでしょうか。")


@pytest.fixture(scope="module")
def app(local_server):
    import capture_web as cw
    from conftest import RESOURCE_HOOK
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
        page.send("Page.addScriptToEvaluateOnNewDocument", source=MOCK_AI)
        page.send("Page.navigate", url=f"http://localhost:{local_server}/index.html#v=own&g=empty")
        try:
            page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
            page.wait(lambda: page.js("!!document.querySelector('#neg-root')"), 60, "手順の欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面が起動しません", e, "offer_ai_fail")
        time.sleep(1.5)
        yield page


def shot(page, name, quality=60):
    d = Path(os.environ.get("OFFER_SHOT_DIR") or os.environ.get("TEMP", "."))
    d.mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=quality)
    (d / name).write_bytes(base64.b64decode(r["data"]))


def setv(page, sel, v):
    page.js("(function(s, v){const e = document.querySelector(s); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true}));})(%s, %s)" % (json.dumps(sel), json.dumps(v)))


def T(page, sel):
    return page.js(f"(document.querySelector({json.dumps(sel)}) || {{innerText: ''}}).innerText")


def to_draft(page):
    to_guided(page)
    page.js("Negotiate.state.polish = null; window.__polishCalls.length = 0")
    click(page, "#neg-entry-sample")
    click(page, "#neg-go-cond")
    click(page, "#neg-g-conds")
    click(page, "#neg-go-draft")
    assert step_of(page) == 3 and page.js("!!document.querySelector('#neg-text')")
    page.wait(lambda: page.js("!!document.querySelector('#neg-ai-ask')"), 10, "AI のボタン")


def reply(page, **kw):
    ta = page.js("document.querySelector('#neg-text').value")
    base = {"ok": True, "ready": True, "text": ta.replace("\n■進め方", "\n■進め方") if kw.pop("same", False) else PROPOSAL.format(wd="火曜日", fr="鹿屋市周辺", to="江東区周辺"), "problems": [], "cached": False}
    base.update(kw)
    page.js("window.__polishReply = %s" % json.dumps(base, ensure_ascii=False))


def test_nothing_is_sent_before_send_and_cancel_sends_nothing(app):
    page = app
    size(page, 1920, 1080)
    to_draft(page)
    text = page.js("document.querySelector('#neg-text').value")
    assert T(page, "#neg-ai-ask") == "AI で読みやすく整える"
    click(page, "#neg-ai-ask")
    assert "この文を Anthropic の API に送ります" in T(page, "#neg-ai-confirm")
    assert page.js("document.querySelector('#neg-ai-sent').textContent") == text            # 送る本文を、そのまま見せる
    assert T(page, "#neg-ai-send") == "送る" and T(page, "#neg-ai-cancel") == "やめる"
    assert page.js("window.__polishCalls.length") == 0
    shot(page, "offer_confirm_1920.jpg")
    click(page, "#neg-ai-cancel")
    time.sleep(0.2)
    assert page.js("window.__polishCalls.length") == 0 and page.js("!document.querySelector('#neg-ai-confirm')") and page.js("!!document.querySelector('#neg-ai-ask')")


def test_payload_is_the_confirmed_text_only_then_proposal_and_replace_marks_edited(app):
    page = app
    size(page, 1920, 1080)
    to_draft(page)
    text = page.js("document.querySelector('#neg-text').value")
    reply(page)
    click(page, "#neg-ai-ask")
    click(page, "#neg-ai-send")
    page.wait(lambda: page.js("!!document.querySelector('#neg-ai-result')"), 10, "案の表示")
    calls = page.js("window.__polishCalls")
    assert len(calls) == 1 and json.loads(calls[0]) == {"text": text}                       # 本文だけ
    assert "AI の案" in T(page, "#neg-ai-result") and "確認が必要" not in T(page, "#neg-ai-result")
    assert page.js("document.querySelector('#neg-text').value") == text                      # 置き換えるまで元の文のまま
    assert page.js("Negotiate.isEdited(Negotiate.state.draft)") is False
    shot(page, "offer_proposal_1920.jpg")
    proposal = page.js("document.querySelector('#neg-ai-proposal').textContent")
    click(page, "#neg-ai-replace")
    assert page.js("document.querySelector('#neg-text').value") == proposal
    assert page.js("Negotiate.isEdited(Negotiate.state.draft)") is True and page.js("!document.querySelector('#neg-ai-result')")
    # 手で直した扱いなので、条件を変えても上書きされない
    click(page, "[data-act=to-cond]")
    click(page, "#neg-go-draft")
    assert page.js("document.querySelector('#neg-text').value") == proposal


def test_internal_info_in_the_text_is_not_sent(app):
    page = app
    to_draft(page)
    setv(page, "#neg-text", page.js("document.querySelector('#neg-text').value") + "\n社内の目安 5/5")
    assert page.js("!document.querySelector('#neg-leak').hidden")
    click(page, "#neg-ai-ask")
    time.sleep(0.2)
    assert page.js("window.__polishCalls.length") == 0 and page.js("!document.querySelector('#neg-ai-confirm')")
    assert "社内の情報（5/5）が文面に入っています。消してからコピーしてください。" in T(page, "#neg-ai-msg")


def test_problem_proposal_is_shown_as_needs_check_with_reasons(app):
    page = app
    size(page, 1920, 1080)
    to_draft(page)
    reply(page, ready=False, text=PROPOSAL.format(wd="水曜日", fr="鹿屋市周辺", to="江東区周辺"), problems=["曜日「火曜」が消えています", "元の文にない曜日「水曜」が入っています"])
    click(page, "#neg-ai-ask")
    click(page, "#neg-ai-send")
    page.wait(lambda: page.js("!!document.querySelector('#neg-ai-result')"), 10, "案の表示")
    t = T(page, "#neg-ai-result")
    assert "確認が必要な案" in t and "曜日「火曜」が消えています" in t and "AI の案" not in t.replace("確認が必要な案", "")
    shot(page, "offer_problem_1920.jpg")
    click(page, "#neg-ai-close")
    assert page.js("!document.querySelector('#neg-ai-result')") and page.js("Negotiate.isEdited(Negotiate.state.draft)") is False


def test_server_error_shows_one_line_and_keeps_the_text(app):
    page = app
    to_draft(page)
    text = page.js("document.querySelector('#neg-text').value")
    page.js("""(function(){const of = window.fetch; window.__of = of; window.fetch = function(u, o){ if (String(u).indexOf('/api/ai/offer_polish') >= 0) return Promise.resolve(new Response(JSON.stringify({error: 'AI に整えてもらえませんでした（RuntimeError）。今の文のままお使いください'}), {status: 502, headers: {'Content-Type': 'application/json'}})); return of.apply(this, arguments); };})()""")
    try:
        click(page, "#neg-ai-ask")
        click(page, "#neg-ai-send")
        page.wait(lambda: page.js("!!document.querySelector('#neg-ai-msg')"), 10, "エラーの表示")
        assert "RuntimeError" in T(page, "#neg-ai-msg") and page.js("document.querySelector('#neg-text').value") == text
    finally:
        page.js("window.fetch = window.__of")


def test_narrow_screenshots(app):
    page = app
    size(page, 390, 844)
    try:
        to_draft(page)
        assert page.js("document.documentElement.scrollWidth") <= 392
        reply(page)
        click(page, "#neg-ai-ask")
        page.js("document.querySelector('#neg-ai-confirm').scrollIntoView({block: 'center'})")
        time.sleep(0.3)
        shot(page, "offer_confirm_390.jpg")
        assert page.js("document.documentElement.scrollWidth") <= 392
        click(page, "#neg-ai-send")
        page.wait(lambda: page.js("!!document.querySelector('#neg-ai-result')"), 10, "案の表示")
        page.js("document.querySelector('#neg-ai-result').scrollIntoView({block: 'center'})")
        time.sleep(0.3)
        shot(page, "offer_proposal_390.jpg")
        assert page.js("document.documentElement.scrollWidth") <= 392
    finally:
        size(page, 1920, 1080)


def test_single_file_page_has_no_button_and_no_network_code():
    text = (ROOT / "web" / "own_lite.html").read_text(encoding="utf-8")
    assert "AiExplain" in text and "typeof AiExplain === 'undefined'" in text      # 部品が無いので何も出ない
    assert "/api/ai/" not in text and "offer_polish" not in text
