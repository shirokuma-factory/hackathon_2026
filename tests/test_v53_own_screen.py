"""SPEC §53.1 自社の運行記録の取り込み画面（#v=own）。ヘッドレス Edge を 1 つだけ使い、AI の API はページの中で差し替える（本物の API は呼ばない）。

- 取り込みの案内（読めるファイル・最低限の列）と「見本の CSV を保存」。見本は OwnCalc で読める
- 列の役割が決まらないとき、AI が使えれば「AI に列を整理してもらう」が出る。押しても「送る」を押すまでは何も送らない
- 送る内容の一覧に値そのもの（車番・地点名）が出ない。AI の案は「AI の案」と出て、「この案で決める」を押すまで計算は変わらない
- 見出しを変えた Shift_JIS の CSV を、帰り便の「自社の運行記録を読み込む」からも、取り込みの欄からも読み込むと、帰り便の欄が再描画される
画面の写真は、環境変数 V53_SHOT_DIR があればそこへ、無ければ一時フォルダへ書く。
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

# ページの中の fetch を差し替える。/api/ai/status は使える、/api/ai/own_columns は呼ばれた内容を記録して window.__aiReply を返す
MOCK_AI = """(function(){
  window.__aiCalls = []; window.__aiReply = {ok: true, suggestions: []}; window.__dl = [];
  const of = window.fetch.bind(window);
  const json = (o, s) => new Response(JSON.stringify(o), {status: s || 200, headers: {'Content-Type': 'application/json'}});
  window.fetch = function(u, o){
    const url = String(u);
    if (url.indexOf('/api/ai/status') >= 0) return Promise.resolve(json({available: true, reason: '', limits: {}}));
    if (url.indexOf('/api/ai/own_columns') >= 0) { window.__aiCalls.push(o && o.body ? o.body : ''); return Promise.resolve(json(window.__aiReply)); }
    return of(u, o);
  };
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function(){ if (this.download) { window.__dl.push(this.download); return; } return click.apply(this, arguments); };
})();"""

PLATE = "鹿児島 100 か 9001"
HEAD = "運行日,車番,発地,着地,出庫,着時刻,実績値,実空"


def odd_csv() -> str:
    """見出しを変えた配車表（縦の形）。出庫・実績値 は列の名前だけでは役割が決まらない。実空の印で帰りは空車。"""
    rows = [HEAD]
    for d in range(1, 13):
        if d % 2:
            rows.append(f"2026/10/{d:02d},{PLATE},鹿屋,江東,21:10,05:30,681.9,")
        else:
            rows.append(f"2026/10/{d:02d},{PLATE},江東,鹿屋,19:05,04:30,681.8,空")
    return "\r\n".join(rows) + "\r\n"


@pytest.fixture(scope="module")
def app(local_server):
    import capture_web as cw
    from conftest import RESOURCE_HOOK
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
        page.send("Page.addScriptToEvaluateOnNewDocument", source=MOCK_AI)
        page.send("Page.navigate", url=f"http://localhost:{local_server}/index.html#v=own")
        try:
            page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
            page.wait(lambda: page.js("!!document.querySelector('#own-file') && !!document.querySelector('#neg-root')"), 60, "取り込みの欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面が起動しません", e, "own_v53_fail")
        time.sleep(1.5)
        yield page


def T(page, sel):
    return page.js(f"(document.querySelector({json.dumps(sel)}) || {{innerText: ''}}).innerText")


def _set_files(page, paths):
    doc = page.send("DOM.getDocument", depth=1)
    nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#own-file")["nodeId"]
    page.send("DOM.setFileInputFiles", files=[str(p) for p in paths], nodeId=nid)


def _to_sample(page):
    page.js("App.setView('own')")
    page.js("Own.setGuided(false, ''); Own.state.pendingRoles = null; Own.state.aiOwn.sugg = {}; Own.state.aiOwn.confirm = null; Own.state.aiOwn.msg = ''")
    if page.js("Own.state.mode") != "sample":
        page.js("document.querySelector('#own-src [data-act=reset]').click()")
    page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")
    page.js("Own.setTab('import')")


def _odd_file(tmp_path) -> Path:
    p = tmp_path / "運行実績_他社式.csv"
    p.write_bytes(odd_csv().encode("cp932"))  # Shift_JIS
    return p


def _import_odd(page, tmp_path):
    _to_sample(page)
    _set_files(page, [_odd_file(tmp_path)])
    page.wait(lambda: page.js("Own.state.mode") == "import" and not page.js("Own.state.busy"), 60, "取り込み")
    page.wait(lambda: page.js("!!document.querySelector('#own-roles')"), 20, "役割を選ぶ欄")


def _shot(page, name, tmp_path, quality=70):
    d = Path(os.environ.get("V53_SHOT_DIR") or tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=quality)
    (d / name).write_bytes(base64.b64decode(r["data"]))


# ---------------------------------------------------------------- 1. 案内と見本の CSV
def test_import_screen_shows_format_guidance_and_the_sample_csv_is_readable(app):
    page = app
    _to_sample(app)
    page.js("document.querySelector('#own-fmt details').open = true")  # 2026-11-08 の最終評価に合わせて: 列の説明は閉じた「詳しく」に入れた
    g = T(page, "#own-fmt")
    for s in ("配車表・デジタコ・運行日報の CSV", "UTF-8", "Shift_JIS", "日付・車番・発地か着地", "開始と終了の日時", "見本の CSV を保存"):
        assert s in g, s
    assert len([x for x in g.splitlines() if x.strip()]) == 4  # 短く: 詳しくの見出し、2 段落の案内、ボタン
    csv = page.js("Own.sampleCsv()")
    assert csv.splitlines()[0] == "日付,車番,車種,発地,着地,着時刻,重さ,実空"
    page.js("document.querySelector('#own-fmt [data-act=sample-csv]').click()")
    assert page.js("window.__dl") == ["見本_配車表.csv"]
    # 見本は OwnCalc で最後まで読める（架空の車番と地名）
    r = json.loads(page.js("""JSON.stringify((function(){const d = OwnCalc.analyze([{name: '見本_配車表.csv', text: Own.sampleCsv()}], {});
      return {read: d.files_read, failed: d.failed || '', need: (d.column_roles_needed || []).length, unread: d.unreadable.length, legs: (d.legs || d.trips || []).length};})())"""))
    assert r["read"] == 1 and not r["failed"] and r["need"] == 0 and r["unread"] == 0, r


# ---------------------------------------------------------------- 2. AI に列を整理してもらう
def test_ai_button_needs_confirmation_and_sends_no_raw_values(app, tmp_path):
    page = app
    _import_odd(page, tmp_path)
    need = json.loads(page.js("JSON.stringify(Own.state.data.column_roles_needed.map(x => x.column))"))
    assert "出庫" in need and "実績値" in need, need
    page.js("window.__aiCalls.length = 0")
    page.wait(lambda: page.js("!!document.querySelector('#own-aibox [data-act=ai-cols]')"), 10, "AI のボタン")
    assert T(page, "#own-aibox [data-act=ai-cols]") == "AI に列を整理してもらう"
    # 押しても確認が出るだけで、何も送らない
    page.js("document.querySelector('#own-aibox [data-act=ai-cols]').click()")
    time.sleep(0.5)
    conf = T(page, ".o-aiconf")
    assert "この内容を Anthropic（Claude の提供元）のサーバーに送ります" in conf and "値そのものは送りません" in conf
    assert page.js("!!document.querySelector('.o-aiconf [data-act=ai-cols-send]') && !!document.querySelector('.o-aiconf [data-act=ai-cols-cancel]')")
    assert "出庫" in conf and "実績値" in conf and "HH:MM の時刻" in conf
    for raw in ("鹿児島", "9001", "鹿屋", "江東", "681.9", "21:10"):
        assert raw not in conf, raw
    assert page.js("window.__aiCalls.length") == 0
    _shot(page, "own_ai_confirm_1920.jpg", tmp_path)
    page.js("document.querySelector('.o-aiconf [data-act=ai-cols-cancel]').click()")
    time.sleep(0.3)
    assert page.js("window.__aiCalls.length") == 0 and page.js("!document.querySelector('.o-aiconf')")
    # 送る: 送った内容に値が入っていない
    page.js("document.querySelector('#own-aibox [data-act=ai-cols]').click()")
    time.sleep(0.3)
    cols = json.loads(page.js("JSON.stringify(Own.state.aiOwn.confirm.payload.columns)"))
    reply = {"ok": True, "suggestions": [{"index": i, "role": "start_time" if c["header"] == "出庫" else "ignore", "confidence": "medium", "reason_ja": "見出しと時刻の形が合います"} for i, c in enumerate(cols)]}
    # 出庫の列に、配車表の選択肢に無い役割（start_time）を混ぜても、画面は選択肢にある役割だけ受ける。ignore は「使わない」になる
    reply["suggestions"].append({"index": 99, "role": "date", "confidence": "high", "reason_ja": "知らない列"})
    page.js("window.__aiReply = %s" % json.dumps(reply))
    page.js("document.querySelector('.o-aiconf [data-act=ai-cols-send]').click()")
    page.wait(lambda: page.js("window.__aiCalls.length") == 1, 10, "送信")
    sent = page.js("window.__aiCalls[0]")
    for raw in ("鹿児島", "9001", "鹿屋", "江東", "681.9", "21:10", "05:30", "2026/10"):
        assert raw not in sent, raw
    body = json.loads(sent)
    assert [c["header"] for c in body["columns"]] == [c["column"] for c in json.loads(page.js("JSON.stringify(Own.state.data.column_roles_needed)"))]
    assert all(set(c) == {"index", "header", "options", "shape"} for c in body["columns"])
    assert "time_hm" in json.dumps(body["columns"])
    # 案は「AI の案」と出て、選択に入るが、「この案で決める」までは計算も記録も変わらない
    page.wait(lambda: page.js("!!document.querySelector('.o-aitag')"), 10, "AI の案")
    assert "AI の案" in T(page, "#own-roles")
    assert page.js("document.querySelector('#own-roles select[data-role-col=\"出庫\"]').value") == ""  # 選択肢に無い案は付かない
    assert page.js("document.querySelector('#own-roles select[data-role-col=\"実績値\"]').value") == "skip"
    assert "実績値" not in page.js("JSON.stringify(Own.state.roles)")  # 「この案で決める」までは記録も計算も変えない
    _shot(page, "own_ai_suggest_1920.jpg", tmp_path)
    page.js("document.querySelector('.o-aitag [data-act=ai-apply]').click()")
    page.wait(lambda: "実績値" in page.js("JSON.stringify(Own.state.roles)"), 20, "役割の記録")
    assert json.loads(page.js("JSON.stringify(Own.state.roles)"))["haisha"]["実績値"] == "skip"
    page.wait(lambda: not page.js("Own.state.busy"), 20, "再計算")


def test_ai_button_hidden_when_unavailable(app, tmp_path):
    page = app
    page.js("Own.state.aiOwn.avail = false; Own.state.aiOwn.msg = ''")
    _import_odd(page, tmp_path)
    assert page.js("!document.querySelector('#own-aibox')")
    assert page.js("!!document.querySelector('#own-roles select')")  # 手で選ぶ欄は残る
    page.js("Own.state.aiOwn.avail = true")


# ---------------------------------------------------------------- 3. 見出しを変えた Shift_JIS の CSV で、帰り便の欄が更新される
def _neg_to_start(page, guided: bool):
    page.js("App.setView('own')")
    page.js("Own.state.pendingRoles = null; Own.state.pendingYear = null")
    if page.js("Own.state.mode") != "sample":
        page.js("document.querySelector('[data-act=reset]').click()")
        page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")
    page.js("Negotiate.state.entry = null; Negotiate.state.view = 'result'; Negotiate.state.wd = null; Negotiate.state.picking = false")
    page.js("Own.setGuided(false, ''); " + ("Own.setGuided(true, 'empty')" if guided else "Own.setTab('import')"))
    time.sleep(0.4)


@pytest.mark.parametrize("guided", [False, True])
def test_return_trip_panel_rerenders_with_the_imported_file(app, tmp_path, guided):
    page = app
    _neg_to_start(page, guided)
    page.js("document.querySelector('#neg-entry-sample').click()")
    time.sleep(0.5)
    before = T(page, "#neg-head")
    before_res = T(page, "#neg-result")
    assert "架空のサンプル" in before and "自社のファイル" not in before, before
    page.js("Negotiate.state.entry = null; Negotiate.state.picking = false; Negotiate.render()")  # 「別のデータを選ぶ」と同じ
    page.js("document.querySelector('#neg-entry-file').click()")  # 「自社の運行記録を読み込む」（ctx.pickFile → own.js の取り込み）
    _set_files(page, [_odd_file(tmp_path)])
    page.wait(lambda: page.js("Own.state.mode") == "import" and not page.js("Own.state.busy"), 60, "取り込み")
    page.wait(lambda: "自社のファイル" in T(page, "#neg-head"), 20, "帰り便の欄の更新")  # 再読み込みなしで描き直される
    head = T(page, "#neg-head")
    assert "架空のサンプル" not in head and "鹿屋" in head and "江東" in head, head
    if guided:
        assert page.js("Negotiate.step()") == 2 and "空いた帰りを見る" in T(page, "#neg-root")
    res = T(page, "#neg-result")  # 帰りの便は 2026-10 の偶数日（金・日・火・木・土・月）。水曜が無く、サンプルの数え方とは違う
    assert res != before_res and "金曜（空車 1 / 1 便）" in res and "水曜" not in res, res
    assert page.js("Own.state.files.length") == 1 and page.js("Own.state.files[0].encoding") == "Shift_JIS"
    _shot(page, f"own_return_trip_after_import_{'guided' if guided else 'full'}_1920.jpg", tmp_path)


def test_import_tab_with_the_same_file_updates_the_return_trip_panel_too(app, tmp_path):
    page = app
    _to_sample(page)
    page.js("Negotiate.state.entry = 'sample'; Negotiate.state.view = 'result'; Negotiate.render()")
    assert "架空のサンプル" in T(page, "#neg-head")
    _set_files(page, [_odd_file(tmp_path)])  # 取り込みの欄から直接
    page.wait(lambda: page.js("Own.state.mode") == "import" and not page.js("Own.state.busy"), 60, "取り込み")
    page.wait(lambda: "自社のファイル" in T(page, "#neg-head"), 20, "帰り便の欄の更新")
    assert "鹿屋" in T(page, "#neg-head") and "江東" in T(page, "#neg-head")


# ---------------------------------------------------------------- 4. 画面の写真（案内・AI のボタン・確認・案）と、狭い画面で横にはみ出さないこと
@pytest.mark.parametrize("w,h", [(1920, 1080), (390, 844)])
def test_import_screen_shots_and_no_horizontal_overflow(app, tmp_path, w, h):
    page = app
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
    try:
        time.sleep(0.7)
        _import_odd(page, tmp_path)
        page.js("document.querySelector('#own-aibox [data-act=ai-cols]').scrollIntoView({block: 'center'})")
        _shot(page, f"own_ai_button_{w}.jpg", tmp_path)
        page.js("document.querySelector('#own-aibox [data-act=ai-cols]').click()")
        time.sleep(0.4)
        page.js("document.querySelector('.o-aiconf').scrollIntoView({block: 'center'})")
        _shot(page, f"own_ai_confirm_{w}.jpg", tmp_path)
        over = page.js("(function(){const r = document.querySelector('#own-body'); return r.scrollWidth - r.clientWidth;})()")
        assert over <= 1, f"取り込みの欄が横にはみ出しています（{over}px）"
        page.js("document.querySelector('.o-aiconf [data-act=ai-cols-cancel]').click()")
        _to_sample(page)
        page.js("document.querySelector('#own-fmt').scrollIntoView({block: 'center'})")
        _shot(page, f"own_import_guidance_{w}.jpg", tmp_path)
    finally:
        page.send("Emulation.clearDeviceMetricsOverride")
        time.sleep(0.5)
