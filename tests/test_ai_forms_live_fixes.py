"""AI の 4 つの入口を本物の API で通したときに見つかった問題の修正（2026-11-08）。SDK は差し替える（本物の API は呼ばない）。

- offer_polish: 質問を減らさない指示、AI の案で消えた項目の一覧（removed）
- cargo_rows: 「分かっていること」を unknowns に入れない。例の行は最初の AI の行に置き換える。寸法が空の行は「荷物の入力が足りない」になる
- own_columns: 確認の枠の「送る」「やめる」が 1920x1080 で押せる。費用と残り回数。決められなかった列。足りない役割の説明
- own 取り込み: 1 行 = 1 便の表の「走行距離」はその便の距離（月の合計の表では読まない）
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import edge_page, fail_with_evidence

from shipcompare import ai_explain as ax
from shipcompare import own

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(ax, "LIMITER", ax.Limiter())
    ax.CACHE.clear()
    for k in ("AI_EXPLAIN_ENABLED", "AI_EXPLAIN_PUBLIC", "AI_DAILY_LIMIT", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------- 3. offer_polish
SRC = """■お聞かせいただきたいこと
・荷物の品名・荷姿（箱・パレットなど）、1 個あたりの寸法・重量、数量
・積込を希望する日時と、日程を広げられる範囲
・積込場所の荷役設備（フォークリフト・ドックなど）と、積込にかかる時間
・ほかの荷物との混載の可否と、積み降ろしの順の希望
・納品先（受取側）が受け取れる日時
・現在の送り方と、その条件（比べるため）"""
OUT3 = """最初に、次の点を教えてください。
・荷物の品名・荷姿、1 個あたりの寸法・重量、数量
・積込を希望する日時
・納品先が受け取れる日時
詳しい条件は、ご関心があればあらためて伺います。"""


def test_polish_prompt_keeps_loading_dates_and_receiving_questions():
    s = ax.SYS_POLISH
    assert "消さない" in s and "積み込みの設備" in s and "日程" in s and "受け取りの条件" in s and "混載" in s
    assert "3〜4 件に絞る" not in s


def test_polish_removed_lists_dropped_bullets_and_ignores_rewording():
    gone = ax.polish_removed(SRC, OUT3)
    joined = "\n".join(gone)
    assert "荷役設備" in joined and "混載" in joined and "日程を広げられる範囲" in joined and "現在の送り方" in joined
    assert not any("品名" in g or "受け取れる" in g for g in gone)   # 残っている項目は出さない
    # 記号・句読点・言い回しの小さな違いは消えたことにしない
    assert ax.polish_removed("・積込場所の荷役設備は？", "積込場所の荷役設備を教えてください。") == []
    assert ax.polish_removed("本文だけ。箇条書きなし", "別の文") == []


def test_polish_removed_ignores_headings_and_closing_and_reworded_receiving_item():
    gone = ax.polish_removed(SRC, OUT3)
    assert not any("お聞かせ" in g or g.startswith("■") for g in gone)   # ■ の見出しは対象にしない
    assert not any("納品先" in g for g in gone)   # 「納品先（受取側）が受け取れる日時」→「納品先の受取可能な日時」は消えていない
    assert ax.polish_removed("・納品先（受取側）が受け取れる日時", "・納品先の受取可能な日時") == []
    assert len(gone) == 4, gone   # 積込の日程・荷役設備・混載・現在の送り方


def test_offer_polish_response_has_removed(monkeypatch):
    monkeypatch.setenv("AI_EXPLAIN_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ax, "polish_problems", lambda s, o: [])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(
        content=[SimpleNamespace(type="text", text=OUT3)], stop_reason="end_turn", usage=SimpleNamespace(input_tokens=500, output_tokens=200))))
    monkeypatch.setattr(ax, "make_client", lambda: client)
    r = ax.offer_polish({"text": SRC}, "c1", False)
    assert r["removed"] and any("荷役設備" in x for x in r["removed"])


# ---------------------------------------------------------------- 4. cargo unknowns / empty dims
def test_cargo_unknowns_drop_known_facts_but_keep_unsure_ones():
    text = "段ボール 3箱 各40kg、東京から大阪"
    data = {"rows": [{"kind": "段ボール", "n": 3, "l_cm": None, "w_cm": None, "h_cm": None, "kg": 40, "note": ""}], "origin": "東京", "destination": "大阪",
            "unknowns": ["段ボールの個数の内訳は3箱で確定", "寸法", "重さが確定していません", "出荷日が決まっている"]}
    _rows, _o, _d, unknowns, _dropped = ax.cargo_validate(text, data)
    assert "寸法" in unknowns and "重さが確定していません" in unknowns
    assert not any("3箱で確定" in u or "決まっている" in u for u in unknowns)
    assert "確定していること" in ax.SYS_CARGO and "unknowns に入れない" in ax.SYS_CARGO


def test_size_only_row_says_dimensions_are_missing_not_no_tariff():
    from shipcompare import estimate as est
    from shipcompare.models import Item, Shipment
    sh = Shipment(items=[Item(count=3, weight_kg=40)])
    reasons = " ".join(" ".join(r.reasons) for r in est.estimate_all(sh))
    assert "荷物1行目の縦" in reasons
    assert "料金表がない" not in reasons


def test_status_has_own_cost():
    c = ax.status(False)["own_cost"]
    assert c["in_base"] > 0 and c["out_typical"] > 0 and c["price_in"] > 0


# ---------------------------------------------------------------- 6. 走行距離
def _trip_rows(head: str) -> list[list[str]]:
    rows = [head.split(",")]
    for d in range(1, 7):
        rows.append(f"2026/10/{d:02d},鹿児島 100 か 9001,鹿屋,江東,,{d * 100}".split(","))
    return rows


def test_py_trip_sheet_reads_run_distance_column():
    lay = own.detect_haisha(_trip_rows("日付,車番,発地,着地,実空,走行距離"))
    assert lay and "distance_km" in lay["roles"]


def test_py_user_skip_and_summary_sheet_still_ignore_run_distance():
    lay = own.detect_haisha(_trip_rows("日付,車番,発地,着地,実空,走行距離"), roles={own.col_key("走行距離"): "skip"})
    assert lay and "distance_km" not in lay["roles"]
    lay2 = own.detect_haisha(_trip_rows("日付,車番,発地,着地,実空,合計走行距離"))
    assert lay2 and "distance_km" not in lay2["roles"]
    wide = [["車番", "10/1(木)", "10/2(金)", "10/3(土)", "走行距離"], ["鹿児島 100 か 9001", "鹿屋→江東", "江東→鹿屋", "鹿屋→江東", "4000"]]
    lay3 = own.detect_haisha(wide)
    assert lay3 and "distance_km" not in lay3["roles"]


JS_TRIP = r"""
const fs=require('fs'),vm=require('vm');const ctx={console};vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8')+';this.O=OwnCalc',ctx);
const mk=(h)=>{const r=[h];for(let d=1;d<=6;d++)r.push(`2026/10/0${d},鹿児島 100 か 9001,${d%2?'鹿屋':'江東'},${d%2?'江東':'鹿屋'},${d%2?'':'空'},${d*100}`);return r.join('\n')};
const out={};
for(const h of ['日付,車番,発地,着地,実空,走行距離','日付,車番,発地,着地,実空,合計走行距離']){
  const d=ctx.O.analyze([{name:'a.csv',text:mk(h)}],{});
  out[h]=(d.legs||[]).map(l=>l.km);
}
console.log(JSON.stringify(out));
"""


def test_js_trip_sheet_uses_run_distance_and_monthly_does_not(tmp_path):
    js = tmp_path / "t.js"
    js.write_text(JS_TRIP, encoding="utf-8", newline="\n")
    r = subprocess.run(["node", str(js), str(ROOT / "web" / "own_calc.js")], capture_output=True, text=True, encoding="utf-8", check=True)
    out = json.loads(r.stdout)
    a, b = out["日付,車番,発地,着地,実空,走行距離"], out["日付,車番,発地,着地,実空,合計走行距離"]
    assert sorted(a) == [100, 200, 300, 400, 500, 600], a   # ファイルの走行距離を使う
    assert 100 not in b and 600 not in b, b                        # 見出しが「合計走行距離」なら使わない


# ---------------------------------------------------------------- 画面（1920x1080）
MOCK_AI = """(function(){
  window.__aiCalls = []; window.__aiReply = {ok: true, suggestions: []};
  const of = window.fetch.bind(window);
  const json = (o, s) => new Response(JSON.stringify(o), {status: s || 200, headers: {'Content-Type': 'application/json'}});
  window.fetch = function(u, o){
    const url = String(u);
    if (url.indexOf('/api/ai/status') >= 0) return Promise.resolve(json({available: true, reason: '', remaining: 91, limits: {daily: 100},
      cost: {tokens_per_char: 0.8, usd_jpy: 150, stages: []}, own_cost: {in_base: 450, out_typical: 400, out_max: 2000, price_in: 0.1, price_out: 0.5}}));
    if (url.indexOf('/api/ai/own_columns') >= 0) { window.__aiCalls.push(o && o.body ? o.body : ''); return Promise.resolve(json(window.__aiReply)); }
    return of(u, o);
  };
})();"""
NO_ROUTE_CSV = "出庫,実績値,行先メモ,運行日,車両\r\n" + "".join(f"21:10,68{d}.5,鹿屋行き,2026/10/{d:02d},2t\r\n" for d in range(1, 9))


@pytest.fixture(scope="module")
def own_page(local_server):
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
            fail_with_evidence(page, "地図の画面が起動しません", e, "own_live_fix_fail")
        time.sleep(1.5)
        yield page


def _import(page, tmp_path, text):
    page.js("App.setView('own'); Own.setGuided(false, ''); Own.state.pendingRoles = null; Own.state.aiOwn.sugg = {}; Own.state.aiOwn.confirm = null; Own.state.aiOwn.msg = ''; Own.state.aiOwn.none = {}; Own.state.aiOwn.done = false")
    if page.js("Own.state.mode") != "sample":
        page.js("document.querySelector('#own-src [data-act=reset]').click()")
        page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")
    page.js("Own.setTab('import')")
    p = tmp_path / "unko.csv"
    p.write_bytes(text.encode("cp932"))
    doc = page.send("DOM.getDocument", depth=1)
    nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#own-file")["nodeId"]
    page.send("DOM.setFileInputFiles", files=[str(p)], nodeId=nid)
    page.wait(lambda: page.js("!!document.querySelector('#own-roles, #own-roles-pend') && !Own.state.busy"), 40, "役割を選ぶ欄")


def test_own_confirm_buttons_are_visible_and_clickable_with_cost_and_remaining(own_page, tmp_path):
    page = own_page
    _import(page, tmp_path, NO_ROUTE_CSV)
    page.wait(lambda: page.js("!!document.querySelector('#own-aibox [data-act=ai-cols]')"), 10, "AI のボタン")
    page.js("document.querySelector('#own-aibox [data-act=ai-cols]').click()")
    page.wait(lambda: page.js("!!document.querySelector('#own-ai-left')"), 10, "残り回数")
    t = page.js("document.querySelector('.o-aiconf').innerText")
    assert "今日の残り 91 回" in t and "1 回の費用の目安" in t and "円" in t
    for sel in ("send", "cancel"):
        r = json.loads(page.js("""(function(){const b = document.querySelector('.o-aiconf [data-act=ai-cols-%s]'); b.scrollIntoView({block: 'nearest'});
          const r = b.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, el = document.elementFromPoint(x, y);
          return JSON.stringify({h: r.height, top: r.top, bottom: r.bottom, vh: innerHeight, hit: !!el && (el === b || b.contains(el)), el: el ? el.tagName + '.' + el.className + '#' + el.id : null});})()""" % sel))
        assert r["hit"] and r["h"] > 20 and r["top"] >= 0 and r["bottom"] <= r["vh"], (sel, json.dumps(r))
    page.js("document.querySelector('.o-aiconf [data-act=ai-cols-cancel]').click()")


def test_columns_the_ai_could_not_decide_and_missing_roles_are_explained(own_page, tmp_path):
    page = own_page
    _import(page, tmp_path, NO_ROUTE_CSV)
    page.wait(lambda: page.js("!!document.querySelector('#own-aibox [data-act=ai-cols]')"), 10, "AI のボタン")
    page.js("window.__aiReply = {ok: true, suggestions: []}")
    page.js("document.querySelector('#own-aibox [data-act=ai-cols]').click()")
    page.wait(lambda: page.js("!!document.querySelector('.o-aiconf [data-act=ai-cols-send]')"), 10, "確認")
    page.js("document.querySelector('.o-aiconf [data-act=ai-cols-send]').click()")
    page.wait(lambda: page.js("!!document.querySelector('#own-ai-missing')"), 10, "足りない役割")
    miss = page.js("document.querySelector('#own-ai-missing').innerText")
    assert "発地か着地の列" in miss, miss
    assert page.js("document.querySelectorAll('[data-ai-none]').length") >= 1
    assert "この列は AI でも決められませんでした" in page.js("document.querySelector('[data-ai-none]').innerText")


# ---------------------------------------------------------------- 荷物の欄: 例の行を AI の行に置き換える
CARGO_MOCK = """(function(){
  const of = window.fetch.bind(window);
  const json = (o, s) => new Response(JSON.stringify(o), {status: s || 200, headers: {'Content-Type': 'application/json'}});
  window.fetch = function(u, o){
    const url = String(u);
    if (url.indexOf('/api/ai/status') >= 0) return Promise.resolve(json({available: true, reason: '', remaining: 87, limits: {daily: 100}, cost: {tokens_per_char: 0.8, usd_jpy: 150, stages: []}, cargo_cost: {in_base: 650, out_typical: 600, out_max: 1000, price_in: 0.1, price_out: 0.5}}));
    if (url.indexOf('/api/ai/cargo_rows') >= 0) return Promise.resolve(json({ok: true, cached: false, origin: '', destination: '', rows: [
      {kind: '段ボール', n: 3, l_cm: null, w_cm: null, h_cm: null, kg: 40, note: ''}, {kind: 'パレット', n: 1, l_cm: 110, w_cm: 110, h_cm: 150, kg: 450, note: ''}], unknowns: [], dropped: []}));
    return of(u, o);
  };
})();"""


@pytest.fixture(scope="module")
def cargo_page(local_server):
    import capture_web as cw
    from conftest import RESOURCE_HOOK
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
        page.send("Page.addScriptToEvaluateOnNewDocument", source=CARGO_MOCK)
        page.send("Page.navigate", url=f"http://localhost:{local_server}/index.html#v=compare&o=13104&l=km&d=27128")
        try:
            page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
            page.wait(lambda: page.js("!!document.querySelector('#cargo-toggle')"), 60, "荷物の欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面が起動しません", e, "cargo_live_fix_fail")
        page.js("document.querySelector('#cargo-toggle').click()")
        page.wait(lambda: page.js("!!document.querySelector('#ai-cg-box')"), 30, "AI の欄")
        yield page


def test_untouched_example_row_is_replaced_by_the_first_ai_row(cargo_page):
    page = cargo_page
    # 2026-11-08 の直しに合わせて: 初めの荷は例 S1 ではなく、段ボール 100 サイズ・10 kg・10 個の簡単な荷
    assert page.js("W.draft.items.length") == 1 and page.js("W.draft.items[0].count") == 10 and "100 サイズ" in page.js("document.querySelector('#sc-simple').innerText")
    n0 = page.js("W.draft.items.length")
    page.js("document.querySelector('#ai-cg-box').open = true")
    page.js("(function(){const e = document.querySelector('#ai-cg-text'); e.value = '段ボール 3箱 各40kg、パレット1枚 110×110×150cm 450kg'; e.dispatchEvent(new Event('input', {bubbles: true}));})()")
    page.js("document.querySelector('[data-cg=ask]').click()")
    page.wait(lambda: page.js("!!document.querySelector('#ai-cg-send')"), 10, "確認")
    hit = page.js("""(function(){const b = document.querySelector('#ai-cg-send'); b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(), el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return JSON.stringify({h: r.height, top: r.top, vh: innerHeight, ok: r.height > 20 && !!el && (el === b || b.contains(el)), el: el ? el.tagName + '.' + el.className + '#' + el.id : null});})()""")
    assert json.loads(hit)["ok"], hit   # 「送る」は見えていて押せる（.bar は style.css では 5px の進み具合の棒）
    page.js("document.querySelector('#ai-cg-send').click()")
    page.wait(lambda: page.js("!!document.querySelector('[data-cg=insert]')"), 10, "案")
    page.js("document.querySelector('[data-cg=insert][data-i=\"0\"]').click()")
    items = json.loads(page.js("JSON.stringify(W.draft.items)"))
    assert items == [{"count": 3, "length_cm": None, "width_cm": None, "height_cm": None, "weight_kg": 40}], (n0, items)
    assert "例 S1" not in page.js("document.querySelector('#cargo-editor').innerText")
    page.js("document.querySelector('[data-cg=insert][data-i=\"1\"]').click()")
    assert page.js("W.draft.items.length") == 2   # 2 行目からは、これまでどおり足す


def test_blocked_confirm_marks_right_table_as_stale(cargo_page):
    page = cargo_page
    page.js("document.querySelector('#detail').classList.remove('cargo-stale')")
    page.js("W.draft.items = [{count: 4, length_cm: null, width_cm: null, height_cm: null, weight_kg: 570}]")
    page.js("document.querySelector('#ed-confirm').click()")
    page.wait(lambda: page.js("document.querySelector('#detail').classList.contains('cargo-stale')"), 10, "前の条件の額の表示")
    assert "確定するまで前の条件の額です" in page.js("getComputedStyle(document.querySelector('#detail'), '::before').content")
    assert float(page.js("getComputedStyle(document.querySelector('#detail > header')).opacity")) < 1
