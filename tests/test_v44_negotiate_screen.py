"""SPEC §44 帰り便の提案の画面（#v=own の右の欄。ヘッドレス Edge を 1 つだけ使う）。

最初の選択、ヘッダー、最初の結果（分母と不明が閉じていても見える）、集計の根拠、条件、保留・見送り、金額、文面の編集と保護、作り直しの確認、コピー（編集後の文）、
コピーできないときの手動の案内、社内の情報の混入、自社の CSV（OwnCalc.analyze を通す）と、読み込めなかったときにサンプルへ切り替えないこと、空車が無いときの無効化。
画面の写真は、環境変数 V44_SHOT_DIR があればそこへ、無ければ一時フォルダへ書く。
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "own"
EX_FILES = [EXAMPLES / "haisha_2026-10.csv", EXAMPLES / "digitaco_1234.csv", EXAMPLES / "digitaco_5678.csv"]


@pytest.fixture(scope="module")
def app(local_server):
    import capture_web as cw
    from conftest import RESOURCE_HOOK
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
        page.send("Page.navigate", url=f"http://localhost:{local_server}/index.html#v=own")
        try:
            page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
            page.wait(lambda: page.js("!!document.querySelector('#neg-root')"), 60, "帰り便の提案の欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面が起動しません", e, "neg_fail")
        time.sleep(1.5)
        yield page


def T(page, sel):
    return page.js(f"(document.querySelector({json.dumps(sel)}) || {{innerText: ''}}).innerText")


def click(page, sel):
    page.js(f"document.querySelector({json.dumps(sel)}).click()")
    time.sleep(0.15)


def setv(page, sel, v, ev="input"):
    page.js("(function(s, v, ev){const e = document.querySelector(s); e.value = v; e.dispatchEvent(new Event(ev, {bubbles: true}));})(%s, %s, %s)" % (json.dumps(sel), json.dumps(v), json.dumps(ev)))


def radio(page, name, value):
    page.js("(function(){const e = document.querySelector('input[name=%s][value=%s]'); e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true}));})()" % (name, value))
    time.sleep(0.1)


def shot(page, name, quality=60):
    d = Path(os.environ.get("V44_SHOT_DIR") or os.environ.get("TEMP", "."))
    d.mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=quality)
    (d / name).write_bytes(base64.b64decode(r["data"]))


def to_sample(page):
    page.js("App.setView('own')")
    page.js("Own.state.pendingRoles = null; Negotiate.state.entry = null; Negotiate.state.view = 'result'")
    if page.js("Own.state.mode") != "sample":
        page.js("document.querySelector('#own-src [data-act=reset]').click()")
        page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")
    page.js("Negotiate.render()")


def fresh_flow(page, to="result"):
    """サンプルを選び、結果の画面にする。to='cond' なら条件、'draft' なら文面まで進める。状態は初期化する。"""
    to_sample(page)
    page.js("""(function(){const s = Negotiate.state; s.type = 'commodity'; s.load = {state:'unconfirmed', note:''}; s.recv = {state:'unconfirmed', note:''};
      s.hold = {action:'hold', picks:[]}; s.econ = Negotiate.emptyEcon(); s.draft = Negotiate.newDraft(); s.regenAsk = false; s.copyMsg = ''; s.pairKey = null; s.wd = null; s.lastCopied = null;})()""")
    click(page, "#neg-entry-sample")
    assert page.js("Negotiate.state.entry") == "sample"
    if to in ("cond", "draft"):
        click(page, "#neg-go-cond")
    if to == "draft":
        click(page, "#neg-go-draft")


def test_entry_choice_and_first_result_with_denominators(app):
    page = app
    to_sample(page)
    t = T(page, "#neg-root")
    assert "荷主への相談文を作る" in t and page.js("!!document.querySelector('#neg-entry-sample') && !!document.querySelector('#neg-entry-file')") and "#neg-head" and page.js("!document.querySelector('#neg-result')")
    shot(page, "2026-11-06_v44_neg_entry_1920.jpg")
    click(page, "#neg-entry-sample")
    head = T(page, "#neg-head")
    assert "架空のサンプル" in head and "2026-10-03〜2026-10-31" in head and "江東 → 鹿屋" in head  # 2026-11-07 見本の鹿児島化
    assert page.js("document.querySelector('#neg-ev').open") is False  # 根拠は閉じたまま
    base = T(page, "#neg-base")
    assert "53 本" in base and "45 本" in base and "空車 20・実車 25" in base, base  # 文言の直し 2026-11-07
    page.js("document.querySelector('#neg-more').open = true")  # 数え方・距離・注意は閉じた「詳しく」に入れた（SPEC §45）
    res = T(page, "#neg-result")
    assert "曜日ごとの帰りの空車" in res  # 文言の直し 2026-11-07
    assert "5 / 5 便が空車" in res and "15 / 40 便が空車" in res and "空車か実車か分かった便で割っています" in res
    assert "6,931.9 km" in res and "32 本" in T(page, "#neg-basis")  # 2026-11-07 見本の鹿児島化
    assert "今後は未定" in res  # 文言の直し 2026-11-07
    assert page.js("document.querySelector('#neg-wd').selectedOptions[0].textContent").startswith("土曜")  # 2026-11-07 見本の鹿児島化
    # 便ごとの根拠（開くと 53 行）
    page.js("document.querySelector('#neg-ev').open = true")
    assert page.js("document.querySelectorAll('#neg-ev tbody tr').length") == 7 + 53
    assert page.js("document.querySelectorAll('#neg-ev tr.neg-hit').length") == 5
    ev = T(page, "#neg-ev")
    assert "デジタコ" in ev and "配車表" in ev and "推定" in ev and "記録日（時刻不明）" in ev and "未確認" in ev
    shot(page, "2026-11-06_v44_neg_analysis_1920.jpg")
    page.js("document.querySelector('#neg-ev').open = false")
    # 鹿屋着として確定した 8 便（to なし）は「確認済み」にならない
    unconf = page.js("[...document.querySelectorAll('#neg-ev tbody tr')].filter(r => r.textContent.includes('未確認')).length")
    assert unconf == 8


def test_conditions_default_unconfirmed_and_hold_skip(app):
    page = app
    fresh_flow(page, "cond")
    assert page.js("document.querySelector('input[name=neg-load][value=unconfirmed]').checked") and page.js("document.querySelector('input[name=neg-recv][value=unconfirmed]').checked")
    t = T(page, "#neg-cond")
    assert "自社の記録" in t and "確認した結果" in t and "方面へ商品を出す荷主" in t and "関西向け" not in t and "空の通い箱・パレットを返したい荷主" in t
    assert "未確認の項目は、相手への質問にしています" in T(page, "#neg-status")
    radio(page, "neg-load", "ng")
    assert "保留" in T(page, "#neg-status") and "見送る" in T(page, "#neg-status")
    click(page, "#neg-go-draft")
    d = T(page, "#neg-draft")
    assert "保留" in d and "お見積りを比べていただく条件" not in page.js("document.querySelector('#neg-text').value")
    assert page.js("document.querySelector('#neg-copy').disabled") is True  # 再確認の内容を選ぶまでコピーできない
    click(page, "[data-act=to-cond]")
    page.js("(function(){const e = document.querySelector('[data-pick=load_time]'); e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true}));})()")
    click(page, "#neg-go-draft")
    assert "積込の日時を変えることは可能でしょうか" in page.js("document.querySelector('#neg-text').value")
    assert page.js("document.querySelector('#neg-copy').disabled") is False
    click(page, "[data-act=to-cond]")
    page.js("(function(){const e = document.querySelector('[data-hold=skip]'); e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true}));})()")
    click(page, "#neg-go-draft")
    assert page.js("!document.querySelector('#neg-text')") and "文面は作りません" in T(page, "#neg-none") and page.js("document.querySelector('#neg-copy').disabled") is True


def test_money_blank_zero_and_loss_skip(app):
    page = app
    fresh_flow(page, "cond")
    page.js("document.querySelector('#neg-econ').open = true")
    assert "見積額と費用を入れると、差額を出します" in T(page, "#neg-econ-out")
    setv(page, "[data-e=quote]", "10000")
    out = T(page, "#neg-econ-out")
    assert "差額を出すには" in out and "追加費用が未入力" in out and "増えるもうけ" not in out  # 文言の直し 2026-11-07
    setv(page, "[data-e=cost]", "0")
    for k, v in (("qUnit", "1件"), ("cUnit", "1件"), ("qTax", "税抜"), ("cTax", "税抜")):
        setv(page, f"[data-e={k}]", v, "change")
    assert "範囲" in T(page, "#neg-econ-out") and "増えるもうけ" not in T(page, "#neg-econ-out")
    page.js("(function(){const e = document.querySelector('[data-scope=fuel]'); e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true}));})()")
    page.js("(function(){const e = document.querySelector('[data-e=scopeOk]'); e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true}));})()")
    assert "増えるもうけ 10,000 円" in T(page, "#neg-econ-out")  # 文言の直し 2026-11-07
    setv(page, "[data-e=cost]", "10000")
    assert "0 円以下なので、見送り" in T(page, "#neg-econ-out") and "見送り" in T(page, "#neg-status")
    click(page, "#neg-go-draft")
    assert page.js("!document.querySelector('#neg-text')") and page.js("document.querySelector('#neg-copy').disabled") is True
    # 金額は文面に入らない（0 円以下でなくなったあと）
    click(page, "[data-act=to-cond]")
    setv(page, "[data-e=quote]", "52000")
    click(page, "#neg-go-draft")
    v = page.js("document.querySelector('#neg-text').value")
    assert "52000" not in v and "52,000" not in v and "円" not in v.replace("【", "")


def test_draft_edit_protection_regenerate_confirm_and_copy(app):
    page = app
    fresh_flow(page, "draft")
    v0 = page.js("document.querySelector('#neg-text').value")
    # 社外の文に入れないもの
    for bad in ("5678", "3344", "haisha", "digitaco", ".csv", "5/5", "2,790", "2790", "6,931", "6931", "D01", "L0", "さつま運輸", "円"):
        assert bad not in v0, bad
    assert "【宛先" in v0 and "江東区周辺" in v0 and "土曜日" in v0 and "積込を希望する日時" in v0 and "お受けできるかとお見積りをお返しします" in v0
    assert "社内の根拠" in T(page, "#neg-internal") or page.js("!!document.querySelector('#neg-internal')")
    page.js("document.querySelector('#neg-internal').open = true")
    assert "5 本中 空車 5 本" in T(page, "#neg-internal") and "6,931.9 km" in T(page, "#neg-internal")  # 2026-11-07 見本の鹿児島化（文言の直し 2026-11-07 で「便」を「本」に統一）
    assert "5 便中" not in v0 and "5 本中" not in v0 and page.js("!document.querySelector('#neg-text').closest('#neg-internal')")
    # 手で直す
    edited = v0.replace("【宛先（会社名・ご担当者名）】", "山田商事 ご担当者") + "\n追記: 手で書いた一行"
    setv(page, "#neg-text", edited)
    page.js("document.querySelector('#neg-text').dispatchEvent(new Event('input', {bubbles: true}))")
    assert page.js("!document.querySelector('#neg-stale')")  # 条件は変えていない
    # 条件を変える: 手で直した文は残り、「条件を変えました。手で直した文はそのままです」が出る
    click(page, "[data-act=to-cond]")
    assert page.js("Negotiate.state.draft.text") == edited
    radio(page, "neg-load", "ok")
    setv(page, "[data-note=load]", "金曜夕方に準備可と、荷主の担当者に電話で確認")
    page.js("(function(){const e = document.querySelector('[data-type=returnable]'); e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true}));})()") if False else None
    click(page, "#neg-go-draft")
    assert page.js("document.querySelector('#neg-text').value") == edited
    assert "条件を変えました。手で直した文はそのままです" in T(page, "#neg-stale")
    # 画面の再描画でも消えない
    page.js("Own.state.copyMsg = 'x'; Negotiate.update({}); Negotiate.render()")
    assert page.js("document.querySelector('#neg-text').value") == edited
    shot(page, "2026-11-06_v44_neg_draft_stale_1920.jpg")
    # 作り直す: 手で直した内容が消えるので、画面の中で確認する（window.confirm は使わない）
    page.js("window.__confirmCalled = false; window.confirm = () => { window.__confirmCalled = true; return true; }")
    click(page, "#neg-regen")
    assert "直した内容は消えます" in T(page, "#neg-draft") and page.js("document.querySelector('#neg-text').value") == edited
    click(page, "#neg-regen-no")
    assert page.js("document.querySelector('#neg-text').value") == edited and "条件を変えました。手で直した文はそのままです" in T(page, "#neg-stale")
    click(page, "#neg-regen")
    click(page, "#neg-regen-yes")
    nv = page.js("document.querySelector('#neg-text').value")
    assert nv != edited and "積込の条件: 金曜夕方に準備可と、荷主の担当者に電話で確認" in nv and "積込を希望する日時" not in nv and "納品先（受取側）が受け取れる日時" in nv
    assert page.js("!document.querySelector('#neg-stale')") and page.js("window.__confirmCalled") is False
    # 編集していない文は、条件を変えると黙って作り直される（守る編集が無い）
    click(page, "[data-act=to-cond]")
    radio(page, "neg-recv", "ok")
    setv(page, "[data-note=recv]", "土曜の午前に受取可と確認")
    click(page, "#neg-go-draft")
    assert "受取の条件: 土曜の午前に受取可と確認" in page.js("document.querySelector('#neg-text').value") and page.js("!document.querySelector('#neg-stale')")
    assert "確認の状況: " not in page.js("document.querySelector('#neg-text').value")


def test_copy_copies_the_edited_text_and_falls_back_to_manual(app):
    page = app
    fresh_flow(page, "draft")
    page.js("window.__clip = null; Object.defineProperty(navigator, 'clipboard', {value: {writeText: (t) => { window.__clip = t; return Promise.resolve(); }}, configurable: true})")
    v = page.js("document.querySelector('#neg-text').value") + "\n手で足した行 ABC"
    setv(page, "#neg-text", v)
    click(page, "#neg-copy")
    page.wait(lambda: page.js("window.__clip") is not None, 5, "コピー")
    assert page.js("window.__clip") == v and "コピーしました" in T(page, "#neg-copymsg")
    # 社内の語を入れると、コピーを止める
    setv(page, "#neg-text", v + " 車番 5678")
    assert page.js("document.querySelector('#neg-copy').disabled") is True and "社内の情報" in T(page, "#neg-leak") and page.js("!document.querySelector('#neg-leak').hidden")
    page.js("window.__clip = null")
    page.js("document.querySelector('#neg-copy').disabled = false; document.querySelector('#neg-copy').click()")
    time.sleep(0.4)
    assert page.js("window.__clip") is None and "コピーしていません" in T(page, "#neg-copymsg")
    # ブラウザがコピーを許可しないとき: 文面を選択し、手動の案内を出す
    setv(page, "#neg-text", v)
    page.js("Object.defineProperty(navigator, 'clipboard', {value: {writeText: () => Promise.reject(new Error('denied'))}, configurable: true}); document.execCommand = () => false;")
    click(page, "#neg-copy")
    page.wait(lambda: "選択しました" in T(page, "#neg-copymsg"), 5, "手動コピーの案内")
    sel = page.js("(function(){const t = document.querySelector('#neg-text'); return [t.selectionStart, t.selectionEnd, t.value.length, document.activeElement === t];})()")
    assert sel[0] == 0 and sel[1] == sel[2] and sel[3] is True
    assert "Ctrl+C" in T(page, "#neg-copymsg")


def test_navigation_buttons_are_distinct_and_keep_text(app):
    page = app
    fresh_flow(page, "draft")
    setv(page, "#neg-text", "編集した文面 XYZ")
    click(page, "[data-act=to-cond]")
    assert page.js("Negotiate.state.view") == "cond" and page.js("Negotiate.state.entry") == "sample" and page.js("Negotiate.state.draft.text") == "編集した文面 XYZ"
    click(page, "[data-act=to-entry]")
    assert page.js("Negotiate.state.entry") is None and "架空のサンプルで試す" in T(page, "#neg-root")
    click(page, "#neg-entry-sample")
    click(page, "#neg-go-cond")
    click(page, "#neg-go-draft")
    assert page.js("document.querySelector('#neg-text').value") == "編集した文面 XYZ"


def test_returnable_questions_differ(app):
    page = app
    fresh_flow(page, "cond")
    page.js("(function(){const e = document.querySelector('[data-type=returnable]'); e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true}));})()")
    click(page, "#neg-go-draft")
    v = page.js("document.querySelector('#neg-text').value")
    assert "通い箱・パレットの種類" in v and "積み重ねの可否" in v and "所有者と返却先" in v and "回収・受け渡しの方法" in v and "品名・荷姿" not in v


def test_csv_import_flow_failure_and_no_empty(app, tmp_path):
    page = app
    to_sample(page)

    def set_files(paths):
        doc = page.send("DOM.getDocument", depth=1)
        nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#own-file")["nodeId"]
        page.send("DOM.setFileInputFiles", files=[str(p) for p in paths], nodeId=nid)

    page.js("Own.setTab('import')")
    # 読み込めないファイル: サンプルへ切り替えず、理由を出す
    bad = tmp_path / "memo.xlsx"
    bad.write_bytes(b"PK\x03\x04")
    click(page, "#neg-entry-file")
    assert page.js("Negotiate.state.entry") == "file" and "CSV を選ぶ" in T(page, "#neg-root") and page.js("!document.querySelector('#neg-result')")
    set_files([bad])
    page.wait(lambda: "読めるファイルがありません" in T(page, "#neg-root"), 20, "読み込みの失敗の表示")
    assert page.js("Own.state.mode") == "sample" and page.js("!document.querySelector('#neg-result') && !document.querySelector('#neg-head')")
    shot(page, "2026-11-06_v44_neg_loadfail_1920.jpg")
    # 実際の CSV 3 本（OwnCalc.analyze を通る）
    set_files(EX_FILES)
    page.wait(lambda: page.js("Own.state.mode") == "import" and not page.js("Own.state.busy"), 120, "取り込み")
    page.wait(lambda: page.js("!!document.querySelector('#neg-result')"), 20, "結果")
    assert "自社のファイル" in T(page, "#neg-head") and "架空のサンプル" not in T(page, "#neg-head")
    base = T(page, "#neg-base")
    assert "53 本" in base and "45 本" in base, base  # 文言の直し 2026-11-07
    page.js("document.querySelector('#neg-more').open = true")  # 数え方・距離・注意は閉じた「詳しく」に入れた（SPEC §45）
    res = T(page, "#neg-result")
    assert "5 / 5 便が空車" in res and "6,931.9 km" in res and "15 / 40 便が空車" in res and "自社" not in T(page, "#neg-basis") + "x"
    assert "32 本" in T(page, "#neg-basis")  # 2026-11-07 見本の鹿児島化
    # 空車が 1 件も確認できない自社のファイル（（空）の印を消した配車表だけ）: 提案の作成は無効
    txt = (EXAMPLES / "haisha_2026-10.csv").read_text(encoding="utf-8").replace("（空）", "")
    nf = tmp_path / "haisha_noempty.csv"
    nf.write_text(txt, encoding="utf-8")
    page.js("document.querySelector('#own-src [data-act=reset]').click()")
    page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")
    page.js("Negotiate.state.entry = 'file'; Negotiate.render()")
    set_files([nf])
    page.wait(lambda: page.js("Own.state.mode") == "import" and not page.js("Own.state.busy"), 120, "取り込み")
    page.wait(lambda: page.js("!!document.querySelector('#neg-result')"), 20, "結果")
    assert page.js("Negotiate.analysisOf(Own.state.data, null, null).model.confirmed.empty") == 0
    assert page.js("document.querySelector('#neg-go-cond').disabled") is True and "相談文は作れません" in T(page, "#neg-noempty") and "配車表" in T(page, "#neg-noempty")  # 文言の直し 2026-11-07
    page.js("document.querySelector('#own-src [data-act=reset]').click()")
    page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")


def _shots_at(page, w, h, tag):
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
    time.sleep(0.8)
    return tag


def test_narrow_screenshots(app):
    page = app
    fresh_flow(page, "result")
    _shots_at(page, 360, 800, "n")
    try:
        page.js("Own.state.tab = 'import'")
        page.js("document.querySelector('#neg-root').scrollIntoView()")
        time.sleep(0.4)
        assert page.js("document.documentElement.scrollWidth") <= 360 + 2, page.js("document.documentElement.scrollWidth")
        page.js("document.querySelector('.neg-row').scrollIntoView({block: 'center'})")
        time.sleep(0.3)
        shot(page, "2026-11-06_v44_neg_analysis_360.jpg")
        click(page, "#neg-go-cond")
        page.js("document.querySelector('#neg-root').scrollIntoView()")
        time.sleep(0.3)
        shot(page, "2026-11-06_v44_neg_cond_360.jpg")
        click(page, "#neg-go-draft")
        v = page.js("document.querySelector('#neg-text').value")
        setv(page, "#neg-text", v + "\n手で足した行")
        click(page, "[data-act=to-cond]")
        radio(page, "neg-load", "ok")
        click(page, "#neg-go-draft")
        page.js("document.querySelector('#neg-root').scrollIntoView()")
        time.sleep(0.3)
        assert "条件を変えました。手で直した文はそのままです" in T(page, "#neg-stale")
        page.js("document.querySelector('#neg-stale').scrollIntoView({block: 'center'})")
        time.sleep(0.3)
        shot(page, "2026-11-06_v44_neg_draft_stale_360.jpg")
        over = page.js("(function(){const r = document.querySelector('#neg-root').getBoundingClientRect(); return [r.left, r.right, window.innerWidth];})()")
        assert over[0] >= 0 and over[1] <= over[2] + 1, over
    finally:
        page.send("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
        time.sleep(0.5)
    fresh_flow(page, "cond")
    shot(page, "2026-11-06_v44_neg_cond_1920.jpg")


def test_single_file_page_has_the_same_flow(app, tmp_path):
    """web/own_lite.html（サーバーなし）にも同じ流れがある。読み込めないファイルでサンプルに切り替わらない。"""
    page = app
    page.goto((ROOT / "web" / "own_lite.html").as_uri() + "#g=all")  # 全部を見る画面（手順の画面は test_v45_own_guided.py）
    page.wait(lambda: page.js("!!document.querySelector('#neg-root') && typeof Negotiate === 'object'"), 30, "1 枚版の起動")
    assert "架空のサンプルで試す" in T(page, "#neg-root") and "自社の運行記録を読み込む" in T(page, "#neg-root")
    bad = tmp_path / "memo.xlsx"
    bad.write_bytes(b"PK\x03\x04")
    page.js("document.querySelector('#file-in').click = () => {}")  # ファイル選択の窓は開かない
    click(page, "#neg-entry-file")
    doc = page.send("DOM.getDocument", depth=1)
    nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#file-in")["nodeId"]
    page.send("DOM.setFileInputFiles", files=[str(bad)], nodeId=nid)
    page.wait(lambda: "読めるファイルがありません" in T(page, "#neg-root"), 20, "失敗の表示")
    assert page.js("!document.querySelector('#neg-result') && !document.querySelector('#neg-head')")
    click(page, "#neg-entry-sample")
    page.wait(lambda: page.js("!!document.querySelector('#neg-result')"), 30, "サンプル")
    page.js("document.querySelector('#neg-more').open = true")  # 数え方・距離・注意は閉じた「詳しく」に入れた（SPEC §45）
    res = T(page, "#neg-result")
    assert "5 / 5 便が空車" in res and "15 / 40 便が空車" in res and "6,931.9 km" in res and "架空のサンプル" in T(page, "#neg-head")
    click(page, "#neg-go-cond")
    click(page, "#neg-go-draft")
    v = page.js("document.querySelector('#neg-text').value")
    assert "5678" not in v and "積込を希望する日時" in v
    setv(page, "#neg-text", v + "\n手で足した行")
    click(page, "[data-act=to-cond]")
    page.js("(function(){const e = document.querySelector('input[name=neg-load][value=ok]'); e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true}));})()")
    click(page, "#neg-go-draft")
    assert "条件を変えました。手で直した文はそのままです" in T(page, "#neg-stale") and page.js("document.querySelector('#neg-text').value") == v + "\n手で足した行"
    assert page.js("document.querySelector('#neg-leak').hidden") is True and page.js("document.querySelector('#neg-copy').disabled") is False  # 区間の地名は社外に出してよい語
    page.js("document.querySelector('#neg-stale').scrollIntoView({block: 'center'})")
    shot(page, "2026-11-06_v44_neg_lite_1920.jpg")
