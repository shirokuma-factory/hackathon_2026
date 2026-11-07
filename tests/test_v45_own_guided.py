"""SPEC §45 自社の運行の画面を、目的別の手順（guided）と、自由に触る画面（full）に分ける。

- guided（#v=own&g=empty / g=propose、1 枚版の既定）: 「データを選ぶ → 空いた帰りを見る → 荷主に相談する」。1 画面に出す文字・主ボタン・操作できる部品の数に上限を置く。
  数字（5 / 5・15 / 40・53 / 45 / 8・27）は、閉じた「集計の根拠を見る」を開けば出る。編集した文面の保護とコピーは変わらない。
- full（g の無い #v=own）: 機能は全部出す。最初の画面の文字の数に上限を置く。
- 文字の読みやすさ: 本文の大きさ、行間。狭い画面（360 x 800）で手順の中身が地図の下に押し下げられない。
上限の数え方（measure の JS）: 最初の画面（スクロールしない状態）に見える文字。地図・出典・上の切り替え・入力欄の中の文字（文面のテキスト欄、選択肢）は数えない。
操作できる部品: button・a・summary・select・textarea・input（ラジオは同じ name を 1 つ）。
画面の写真は、環境変数 V45_SHOT_DIR があればそこへ、無ければ docs/assets/ へ書く。ヘッドレス Edge は 1 つだけ使う。
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
SHOTS = Path(os.environ.get("V45_SHOT_DIR") or ROOT / "docs" / "assets")
PAGE = ROOT / "web" / "own_lite.html"

MEASURE = r"""(function () {
  const vw = innerWidth, vh = innerHeight;
  const SKIP = 'canvas,.maplibregl-canvas-container,.maplibregl-ctrl,.maplibregl-ctrl-attrib,#attr-toggle,#views,#sheet-tabs,#toast,#loading,textarea,option,script,style,noscript,#m-summary,#layers,.m-sum';
  const skip = (el) => !!el.closest(SKIP);
  function clipped(el, r) {
    let x0 = Math.max(0, r.left), y0 = Math.max(0, r.top), x1 = Math.min(vw, r.right), y1 = Math.min(vh, r.bottom);
    for (let a = el; a && a !== document.documentElement; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (a !== document.body && (cs.overflowX !== 'visible' || cs.overflowY !== 'visible')) {
        const b = a.getBoundingClientRect();
        x0 = Math.max(x0, b.left); y0 = Math.max(y0, b.top); x1 = Math.min(x1, b.right); y1 = Math.min(y1, b.bottom);
      }
    }
    return x1 - x0 > 1 && y1 - y0 > 1;
  }
  const shown = (el) => el.checkVisibility({ checkVisibilityCSS: true, opacityProperty: true }) && getComputedStyle(el).visibility !== 'hidden';
  const out = { chars: 0, blocks: 0, controls: 0, primary: 0, fonts: [], lh: [], texts: [] };
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const blocks = new Set();
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const t = n.nodeValue.replace(/\s+/g, ' ').trim();
    if (!t) continue;
    const el = n.parentElement;
    if (!el || skip(el) || !shown(el)) continue;
    const rg = document.createRange(); rg.selectNodeContents(n);
    const r = rg.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || !clipped(el, r)) continue;
    out.chars += t.length; blocks.add(el.closest('p,li,h1,h2,h3,td,th,label,summary,button,div,span,b,small') || el);
    const cs = getComputedStyle(el);
    out.fonts.push([parseFloat(cs.fontSize), t.length]);
    const lh = cs.lineHeight === 'normal' ? 1.2 : parseFloat(cs.lineHeight) / parseFloat(cs.fontSize);
    out.lh.push([lh, t.length, el.tagName]);
    out.texts.push(t.slice(0, 40));
  }
  out.blocks = blocks.size;
  const seen = new Set();
  for (const el of document.querySelectorAll('button,a[href],summary,select,textarea,input:not([type=hidden])')) {
    if (skip(el) || !shown(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || !clipped(el, r)) continue;
    if (el.type === 'radio' || el.type === 'checkbox') { const k = el.type + ':' + (el.name || el.id || Math.random()); if (seen.has(k)) continue; seen.add(k); }
    out.controls++;
    if (el.matches('.btn-primary, button.primary')) out.primary++;
  }
  return out;
})()"""


@pytest.fixture(scope="module")
def app(local_server):
    import capture_web as cw
    from conftest import RESOURCE_HOOK
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
        page.send("Page.navigate", url=f"http://localhost:{local_server}/index.html#v=own&g=empty")
        try:
            page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
            page.wait(lambda: page.js("!!document.querySelector('#neg-root')"), 60, "手順の欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面が起動しません", e, "v45_fail")
        time.sleep(1.5)
        yield page


def size(page, w, h):
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
    time.sleep(0.7)


def T(page, sel):
    return page.js(f"(document.querySelector({json.dumps(sel)}) || {{innerText: ''}}).innerText")


def click(page, sel):
    page.js(f"document.querySelector({json.dumps(sel)}).click()")
    time.sleep(0.25)


def radio(page, name, value):
    # SPEC §47: 手順の条件の確認は、未確認・適合・不適合を 1 つの選択（select）にした。文面を作る前の画面（radio）も同じ呼び方で選べる
    page.js("(function(){const s = document.querySelector('select[name=%s]'); if (s) { s.value = '%s'; s.dispatchEvent(new Event('change', {bubbles: true})); return; } const e = document.querySelector('input[name=%s][value=%s]'); e.checked = true; e.dispatchEvent(new Event('change', {bubbles: true}));})()" % (name, value, name, value))
    time.sleep(0.15)


def measure(page):
    time.sleep(0.3)
    return page.js(MEASURE)


def shot(page, name, quality=55):
    SHOTS.mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=quality)
    (SHOTS / name).write_bytes(base64.b64decode(r["data"]))


def to_guided(page, goal="empty"):
    """手順の最初（データを選ぶ）に戻す。サンプルのデータは読み込んだままだが、選んだことにはしない。"""
    page.js("App.setView('own')")
    page.js("Own.state.pendingRoles = null; Own.state.pendingYear = null")
    if page.js("Own.state.mode") != "sample":
        page.js("document.querySelector('[data-act=reset]').click()")
        page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")
    page.js("Negotiate.state.entry = null; Negotiate.state.view = 'result'; Negotiate.state.wd = null; Negotiate.state.gsub = 'type'; Negotiate.state.picking = false; Negotiate.state.draft = Negotiate.newDraft()")
    page.js("Own.setGuided(false, ''); Own.setGuided(true, %s)" % json.dumps(goal))
    time.sleep(0.3)


def step_of(page):
    return page.js("Negotiate.step()")


def report(tag, m):
    print(f"[v45] {tag}: 文字 {m['chars']}・ブロック {m['blocks']}・部品 {m['controls']}・主ボタン {m['primary']}")


def body_font(m):
    """文字数で重みをつけた、いちばん多い文字の大きさ（本文の大きさ）。"""
    from collections import Counter
    c = Counter()
    for f, n in m["fonts"]:
        c[round(f, 1)] += n
    return c.most_common(1)[0][0]


def body_lh(m):
    tot = sum(n for _, n, _ in m["lh"])
    return min(lh for lh, n, _ in m["lh"] if n >= tot * 0.1) if m["lh"] else 9


LIMIT = {1920: 450, 360: 300}
DIMS = {1920: (1920, 1080), 360: (360, 800)}


# ---------------------------------------------------------------- 1. 手順の 1 画面ごとの上限

@pytest.mark.parametrize("w", [1920, 360])
def test_guided_steps_stay_within_budget(app, w):
    page = app
    size(page, *DIMS[w])
    to_guided(page)
    # 1 データを選ぶ: サンプルを黙って選ばない
    assert step_of(page) == 1 and page.js("Negotiate.state.entry") is None
    assert page.js("document.querySelector('#neg-steps li.on').textContent.trim()").startswith("1")
    t = T(page, "#neg-root")
    assert "架空のサンプルで試す" in t and "自社の運行記録を読み込む" in t
    assert "最初の画面に戻る" in T(page, "#guide-bar") and "最初の画面に戻る" not in t, "戻る操作は上の案内の帯の 1 つだけ（SPEC §46）"
    assert not page.js("!!document.querySelector('#neg-result')")
    m = measure(page); report(f"手順1 {w}", m)
    assert m["chars"] <= LIMIT[w] and m["primary"] <= 1 and m["controls"] <= 8, m
    shot(page, f"2026-11-07_v45_own_guided1_{w}.jpg")
    # 2 空いた帰りを見る
    click(page, "#neg-entry-sample")
    assert step_of(page) == 2
    res = T(page, "#neg-result")
    assert "5 / 5 便が空車" in res and "15 / 40 便が空車" in res and "金曜" in res and "ほかの曜日" in res
    assert "ほか 8 便は除外" in T(page, "#neg-denom")   # 文言の直し 2026-11-07
    assert page.js("document.querySelector('#neg-ev').open") is False and page.js("document.querySelector('#own-adv').open") is False
    assert "データ" in T(page, "#neg-head") and "架空のサンプル" in T(page, "#neg-head")  # 選んだあとも、どのデータかが見える
    m = measure(page); report(f"手順2 {w}", m)
    assert m["chars"] <= LIMIT[w] and m["primary"] <= 1 and m["controls"] <= 8, m
    shot(page, f"2026-11-07_v45_own_guided2_{w}.jpg")
    # 3a 荷主の種類 → 3b 条件 → 3c 文面
    click(page, "#neg-go-cond")
    assert step_of(page) == 3 and page.js("!!document.querySelector('input[name=neg-type]')")
    m = measure(page); report(f"手順3a {w}", m)
    assert m["chars"] <= LIMIT[w] and m["primary"] <= 1 and m["controls"] <= 8, m
    shot(page, f"2026-11-07_v45_own_guided3a_{w}.jpg")
    click(page, "#neg-g-conds")
    assert page.js("!!document.querySelector('#neg-cond-load')") and page.js("document.querySelector('#neg-int').open") is False  # 社内の 2 つの「詳しく」は 1 つにまとめた
    m = measure(page); report(f"手順3b {w}", m)
    assert m["chars"] <= LIMIT[w] and m["primary"] <= 1 and m["controls"] <= 8, m
    shot(page, f"2026-11-07_v45_own_guided3b_{w}.jpg")
    click(page, "#neg-go-draft")
    assert page.js("!!document.querySelector('#neg-text')")
    m = measure(page); report(f"手順3c {w}", m)
    assert m["chars"] <= LIMIT[w] and m["primary"] <= 1 and m["controls"] <= 8, m
    shot(page, f"2026-11-07_v45_own_guided3c_{w}.jpg")


# ---------------------------------------------------------------- 2. 数字と根拠

def test_key_numbers_reachable_and_evidence_via_the_summary(app):
    page = app
    size(page, 1920, 1080)
    to_guided(page)
    click(page, "#neg-entry-sample")
    ev = page.js("document.querySelector('#neg-ev')")
    assert ev is not None
    # 閉じている間は、根拠の数字は画面に出ない。開けば出る
    closed = T(page, "#neg-result")
    assert "32 便は" not in closed and "53 便" not in closed
    page.js("document.querySelector('#neg-ev > summary').click()")
    time.sleep(0.2)
    opened = T(page, "#neg-ev")
    assert "53 本" in opened and "45 本" in opened and "行き先と積荷が分からない" in opened and "32 本は出発時刻の記録がなく" in opened and "6,931.9 km" in opened   # 文言の直し 2026-11-07
    assert page.js("document.querySelectorAll('#neg-ev tbody tr').length") == 7 + 53 and page.js("document.querySelectorAll('#neg-ev tr.neg-hit').length") == 5
    assert "空車 20・実車 25・不明 8" in opened
    # 詳しく（取り込みの結果・全体の数字・費用）は右の欄の中の閉じた欄にある
    page.js("document.querySelector('#own-adv > summary').click()")
    time.sleep(0.3)
    adv = T(page, "#own-adv")
    assert "取り込みの結果" in adv or "ファイル" in adv
    assert page.js("!!document.querySelector('#own-adv .o-tile') && !!document.querySelector('#own-adv #own-body') && !!document.querySelector('#own-adv #own-cost-out')")
    assert page.js("document.querySelector('#own-view').parentElement.id") == "own-adv-slot"
    # 動く: 取り込みの結果の欄の「表の形の判定」は閉じている
    assert page.js("[...document.querySelectorAll('#own-adv .o-det')].every((d) => !d.open)")
    page.js("document.querySelector('#own-adv').open = false")


# ---------------------------------------------------------------- 3. 文面の保護とコピー

def test_edited_draft_is_protected_and_copy_works(app):
    page = app
    size(page, 1920, 1080)
    to_guided(page)
    click(page, "#neg-entry-sample")
    click(page, "#neg-go-cond")
    click(page, "#neg-g-conds")
    click(page, "#neg-go-draft")
    assert page.js("document.querySelector('#neg-text').value").strip()
    page.js("(function(){const t = document.querySelector('#neg-text'); t.value = '編集した文面 XYZ'; t.dispatchEvent(new Event('input', {bubbles: true}));})()")
    click(page, "[data-act=to-cond]")
    assert page.js("!!document.querySelector('#neg-cond-load')") and page.js("Negotiate.state.draft.text") == "編集した文面 XYZ"
    radio(page, "neg-load", "ng")
    click(page, "#neg-go-draft")
    assert page.js("document.querySelector('#neg-text')") is None or page.js("document.querySelector('#neg-text').value") == "編集した文面 XYZ"
    assert page.js("Negotiate.state.draft.text") == "編集した文面 XYZ"
    assert "未反映" in T(page, "#neg-stale") or "条件を変え" in T(page, "#neg-stale")
    # 戻して適合にもどし、コピーが編集後の文面で動く
    click(page, "[data-act=to-cond]")
    radio(page, "neg-load", "unconfirmed")
    click(page, "#neg-go-draft")
    page.js("window.__clip = null; Object.defineProperty(navigator, 'clipboard', {value: {writeText: (t) => { window.__clip = t; return Promise.resolve(); }}, configurable: true});")
    click(page, "#neg-copy")
    page.wait(lambda: page.js("window.__clip") is not None, 5, "コピー")
    assert page.js("window.__clip") == "編集した文面 XYZ"
    assert "コピーしました" in T(page, "#neg-copymsg")
    # 作り直しは、編集した文面を消す前に確かめる
    click(page, "#neg-regen")
    assert page.js("!!document.querySelector('#neg-regen-yes')") and page.js("Negotiate.state.draft.text") == "編集した文面 XYZ"
    click(page, "#neg-regen-no")


# ---------------------------------------------------------------- 4. 目的の指定（g）と切り替え

def test_goal_in_hash_and_toggle_between_guided_and_full(app):
    page = app
    size(page, 1920, 1080)
    to_guided(page)
    assert page.js("Own.state.guided") is True and page.js("document.querySelector('#own-sheet').classList.contains('own-guided')")
    assert "g=empty" in page.js("location.hash")
    # ほかの目的へ（アドレスの変更）
    page.js("location.hash = '#v=own&g=propose'")
    page.wait(lambda: page.js("Own.state.goal") == "propose", 5, "g=propose")
    assert page.js("Own.state.guided") is True
    click(page, "#neg-entry-sample")
    assert step_of(page) == 3 and page.js("!!document.querySelector('input[name=neg-type]')"), "相談する文を作りたいときは、データを選んだあと荷主の種類から始める"
    click(page, "[data-act=to-result]")
    assert step_of(page) == 2
    # 全部を見る → full。左の欄は元の場所へ戻り、荷待ちのタブも出る
    click(page, "#g-toggle")  # 上の案内の帯の「全部を見る」（手順の欄には同じボタンを置かない）
    assert page.js("Own.state.guided") is False and "g=" not in page.js("location.hash")
    assert page.js("document.querySelector('#own-view').parentElement.id") == "own-sheet"
    assert page.js("!!document.querySelector('#own-tabs [data-tab=wait]') && !!document.querySelector('#own-right .o-tile')")
    # 手順に沿って進める → guided
    click(page, "[data-act=to-guided]")
    assert page.js("Own.state.guided") is True and page.js("document.querySelector('#own-view').parentElement.id") == "own-adv-slot"
    # ハッシュの g を外す → full
    page.js("location.hash = '#v=own'")
    page.wait(lambda: page.js("Own.state.guided") is False, 5, "g なしは full")


# ---------------------------------------------------------------- 5. 全部を見る画面の上限

def test_full_mode_budget_and_collapsed_details(app):
    page = app
    size(page, 1920, 1080)
    to_guided(page)
    page.js("Own.setGuided(false, '')")
    page.js("Negotiate.state.entry = 'sample'; Negotiate.state.view = 'result'; Negotiate.render()")
    time.sleep(0.5)
    m = measure(page); report("全部 1920", m)
    assert m["chars"] <= 1200, m
    assert page.js("[...document.querySelectorAll('#own-body .o-det')].every((d) => !d.open)"), "取り込みの結果の表の形の判定・列の対応づけは閉じておく"
    assert page.js("document.querySelectorAll('#own-right .o-tile').length") == 3
    # 数字の大きな欄は、数字と 1 行だけ。詳しくは閉じた欄に入る
    assert page.js("[...document.querySelectorAll('#own-right .o-tile')].every((t) => !t.querySelector('.o-tmore').open && (!t.querySelector('.o-talt') || !t.querySelector('.o-talt').checkVisibility({checkVisibilityCSS: true})))")
    shot(page, "2026-11-07_v45_own_full_1920.jpg")
    size(page, 360, 800)
    m = measure(page); report("全部 360", m)
    shot(page, "2026-11-07_v45_own_full_360.jpg")
    size(page, 1920, 1080)
    # 全部の画面で、機能は残っている（荷待ちのタブ・年や月の選択・費用の欄）
    assert page.js("!!document.querySelector('#own-tabs [data-tab=wait]') && !!document.querySelector('#own-perkm') && !!document.querySelector('#own-fm')")


# ---------------------------------------------------------------- 6. 読みやすさ

@pytest.mark.parametrize("w", [1920, 360])
def test_readability_of_guided_steps(app, w):
    page = app
    size(page, *DIMS[w])
    to_guided(page)
    click(page, "#neg-entry-sample")
    for name, act in (("2", None), ("3a", "#neg-go-cond"), ("3b", "#neg-g-conds"), ("3c", "#neg-go-draft")):
        if act:
            click(page, act)
        m = measure(page)
        body = body_font(m)
        assert body >= (14 if w == 1920 else 15), (name, body)
        assert min(f for f, n in m["fonts"]) >= 12.5, (name, [t for t, (f, n) in zip(m["texts"], m["fonts"]) if f < 12.5])
        # 本文の行間は 1.6 以上（文字数の多い文）
        assert body_lh(m) >= 1.55, (name, body_lh(m))
    # 1 つの書体
    fams = page.js("[...new Set([...document.querySelectorAll('#own-right, #own-right *')].filter((e) => e.offsetParent && !e.closest('textarea') && e.childNodes.length && [...e.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim())).map((e) => getComputedStyle(e).fontFamily))]")
    assert len(fams) == 1, fams
    # 1 行は 40 字ほどまで（欄の幅と文字の大きさから）
    per = page.js("(function(){const e = document.querySelector('#neg-root .neg-box'); const cs = getComputedStyle(e); return (e.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)) / parseFloat(getComputedStyle(e.querySelector('h3 ~ p, p') || e).fontSize);})()")
    assert per <= 42, per


def test_narrow_screen_steps_start_in_the_first_viewport(app):
    page = app
    size(page, 360, 800)
    to_guided(page)
    for name, act in (("1", None), ("2", "#neg-entry-sample"), ("3a", "#neg-go-cond")):
        if act:
            click(page, act)
        top = page.js("document.querySelector('#neg-steps').getBoundingClientRect().top")
        h3 = page.js("document.querySelector('#neg-root h3').getBoundingClientRect().top")
        assert top < 0.5 * 800 and h3 < 0.62 * 800, (name, top, h3)  # 地図の帯（34%）の下から手順が始まり、最初の画面に入る
        assert page.js("document.documentElement.scrollWidth") <= 361
    # 地図は手順の欄の後ろに隠れる（手順の欄が上から始まる）
    st = page.js("document.querySelector('#own-sheet').getBoundingClientRect().top")
    assert 0.28 * 800 <= st <= 0.4 * 800, st  # 地図は上の細い帯（画面の高さの約 34%）として見える
    shot(page, "2026-11-07_v45_own_map_strip_360.jpg")
    size(page, 1920, 1080)


# ---------------------------------------------------------------- 7. 自社の CSV で手順 2 まで進める

def test_guided_with_own_csv_reaches_step2_and_shows_the_data_kind(app):
    page = app
    size(page, 1920, 1080)
    to_guided(page)
    ex = ROOT / "examples" / "own"
    doc = page.send("DOM.getDocument", depth=1)
    nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#own-file")["nodeId"]
    page.send("DOM.setFileInputFiles", files=[str(ex / n) for n in ("haisha_2026-10.csv", "digitaco_1234.csv", "digitaco_5678.csv")], nodeId=nid)
    page.wait(lambda: page.js("Own.state.mode") == "import", 30, "自社のファイルの読み込み")
    time.sleep(0.8)
    assert step_of(page) == 2 and "自社のファイル" in T(page, "#neg-head") and "架空のサンプル" not in T(page, "#neg-head")
    assert "5 / 5 便が空車" in T(page, "#neg-result")
    # データを選び直す: 手順 1 に戻る
    click(page, "[data-act=to-entry]")
    assert step_of(page) == 1
    click(page, "#neg-entry-sample")
    page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプル")
    assert step_of(page) == 2


# ---------------------------------------------------------------- 8. 1 枚版（file://）

LITE_JS_STEP = "document.body.dataset.step"


@pytest.mark.parametrize("w", [1920, 360])
def test_lite_page_is_guided_by_default_and_full_with_g_all(w):
    with edge_page(DIMS[w][0], DIMS[w][1], webgl=False) as page:
        page.send("Page.navigate", url=PAGE.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 60, "1 枚版の起動")
        time.sleep(0.5)
        assert page.js("OwnLite.state.guided") is True and page.js(LITE_JS_STEP) == "1"
        m = page.js(MEASURE); report(f"1 枚版 手順1 {w}", m)
        assert m["chars"] <= LIMIT[w] and m["primary"] <= 1 and m["controls"] <= 8, m
        assert page.js("document.querySelector('#neg-steps li.on').textContent.trim()").startswith("1")
        assert page.js("OwnLite.state.data") is None, "サンプルを黙って選ばない"
        shot(page, f"2026-11-07_v45_own_lite_step1_{w}.jpg")
        page.js("document.querySelector('#neg-entry-sample').click()")
        page.wait(lambda: page.js(LITE_JS_STEP) == "2", 30, "手順 2")
        time.sleep(0.4)
        res = page.js("document.querySelector('#neg-result').innerText")
        assert "5 / 5 便が空車" in res and "15 / 40 便が空車" in res
        assert page.js("document.querySelector('#adv').open") is False and page.js("document.querySelector('#neg-ev').open") is False
        m = page.js(MEASURE); report(f"1 枚版 手順2 {w}", m)
        assert m["chars"] <= LIMIT[w] and m["primary"] <= 1 and m["controls"] <= 8, m
        shot(page, f"2026-11-07_v45_own_lite_step2_{w}.jpg")
        page.js("document.querySelector('#neg-go-cond').click()")
        time.sleep(0.3)
        assert page.js(LITE_JS_STEP) == "3"
        m = page.js(MEASURE); report(f"1 枚版 手順3 {w}", m)
        assert m["chars"] <= LIMIT[w] and m["primary"] <= 1 and m["controls"] <= 8, m
        # 運行の図（1 枚版には trip_view を埋め込んである）
        page.js("document.querySelector('[data-act=to-result]').click()")
        time.sleep(0.3)
        page.js("document.querySelector('#neg-ev > summary').click()")
        page.js("document.querySelector('#neg-ev tr.neg-trow').click()")
        time.sleep(0.3)
        assert page.js("document.querySelectorAll('#neg-ev [data-tv]').length") == 1
        shot(page, f"2026-11-07_v45_own_lite_trip_{w}.jpg")
        # 全部を見る
        page.js("document.querySelector('[data-act=to-full]').click()")
        time.sleep(0.4)
        assert page.js("OwnLite.state.guided") is False and page.js("!!document.querySelector('#sec-kpi') && !!document.querySelector('#sec-files .drop')")


def test_lite_page_full_mode_by_hash():
    with edge_page(1920, 1080, webgl=False) as page:
        page.send("Page.navigate", url=PAGE.as_uri() + "#g=all")
        page.wait(lambda: page.js("!!window.OwnLite"), 60, "1 枚版の起動")
        assert page.js("OwnLite.state.guided") is False and page.js("!!document.querySelector('#sec-files .drop')")


# ---------------------------------------------------------------- 9. 地図が答えを運ぶ・運行の図

def inject_trip_view(page):
    """web/index.html への追加（link と script）が入るまでの間、試験では同じ 2 つを足して動かす。"""
    page.js("""(function(){ if (window.TripView) return;
      const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = 'trip_view.css'; document.head.appendChild(l);
      return new Promise((res) => { const s = document.createElement('script'); s.src = 'trip_view.js'; s.onload = () => res(true); document.head.appendChild(s); }); })()""")
    page.wait(lambda: page.js("!!window.TripView"), 20, "trip_view.js")


def test_map_carries_the_answer_and_follows_the_weekday(app):
    page = app
    size(page, 1920, 1080)
    to_guided(page)
    assert page.js("Own.state.focus") is None
    click(page, "#neg-entry-sample")
    time.sleep(0.5)
    f = page.js("Own.state.focus && {wd: Own.state.focus.wd, n: Own.state.focus.ids.length}")
    assert f and f["wd"] == 5 and f["n"] == 5
    geo = page.js("Own.state.focusGeo.map((x) => [x.hl, x.col.join(',')])")
    assert any(h for h, _ in geo) and any(not h for h, _ in geo), "選んだ曜日の線と、うすくした線の両方がある"
    assert "実線＝荷物あり、点線＝空車" in page.js("document.querySelector('#neg-lg').textContent")
    assert page.js("typeof Own.state.focusGeo[0].path[0][0]") == "number"
    assert page.js("Own.state.focusGeo.some((x) => x.hl && x.w > 4) && Own.state.focusGeo.filter((x) => !x.hl).every((x) => x.w <= 3)")
    # 曜日を変えると、強調する便が変わる
    page.js("(function(){const e = document.querySelector('#neg-wd'); e.value = '0'; e.dispatchEvent(new Event('change', {bubbles: true}));})()")
    time.sleep(0.4)
    f2 = page.js("({wd: Own.state.focus.wd, n: Own.state.focus.ids.length})")
    assert f2["wd"] == 0 and f2["n"] != f["n"] + 99 and f2["n"] == 7
    # 地図の層は deck に渡っている（手順 2 の間は選んだ区間の層）
    assert page.js("Own.layers().map((l) => l.id)") == ["o-focus", "o-pts"]
    # 手順 1 に戻ると、まだ選んでいないデータの線と点は地図に出さない（SPEC §46。手順 2 に進むと選んだ区間の層に戻る）
    click(page, "[data-act=to-entry]")
    assert page.js("Own.state.focus") is None and page.js("Own.layers().map((l) => l.id)") == []
    click(page, "#neg-entry-sample")
    shot(page, "2026-11-07_v45_own_map_step2_1920.jpg")


def test_far_origin_return_is_drawn_as_a_straight_pair_line(app, tmp_path):
    page = app
    size(page, 1920, 1080)
    from test_v42_own_screen import FAR_HAISHA
    f = tmp_path / "haisha_far.csv"
    f.write_bytes(FAR_HAISHA.encode("utf-8"))
    to_guided(page)
    doc = page.send("DOM.getDocument", depth=1)
    nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#own-file")["nodeId"]
    page.send("DOM.setFileInputFiles", files=[str(f)], nodeId=nid)
    page.wait(lambda: page.js("Own.state.mode") == "import", 60, "取り込み")
    time.sleep(1.0)
    if step_of(page) == 2:
        n = page.js("Own.state.focusGeo ? Own.state.focusGeo.length : -1")
        assert n > 0, n
    to_guided(page)


def test_trip_view_opens_from_an_evidence_row_one_at_a_time(app):
    page = app
    size(page, 1920, 1080)
    inject_trip_view(page)
    to_guided(page)
    click(page, "#neg-entry-sample")
    page.js("document.querySelector('#neg-ev > summary').click()")
    time.sleep(0.3)
    rows = page.js("document.querySelectorAll('#neg-ev tr.neg-trow').length")
    assert rows == 53
    page.js("document.querySelector('#neg-ev tr.neg-trow').scrollIntoView({block: 'center'})")
    click(page, "#neg-ev tr.neg-trow[data-i='0']")
    assert page.js("document.querySelectorAll('#neg-ev [data-tv]').length") == 1
    assert page.js("document.querySelector('#neg-ev tr.neg-trow[data-i=\"0\"]').getAttribute('aria-expanded')") == "true"
    # SPEC §47: 運行の図は表の行の中ではなく、表の下の全幅の箱（#neg-tripbox）に出す
    assert page.js("!document.querySelector('#neg-ev tr.neg-tripr') && !document.querySelector('#neg-tripbox').hidden && !!document.querySelector('#neg-tripbox [data-tv]')")
    txt = T(page, "#neg-tripbox")
    assert "距離" in txt and ("荷物あり" in txt or "空車" in txt)
    shot(page, "2026-11-07_v45_own_trip_1920.jpg")
    click(page, "#neg-ev tr.neg-trow[data-i='3']")  # 別の行: 前の図は消える
    assert page.js("document.querySelectorAll('#neg-ev [data-tv]').length") == 1 and page.js("document.querySelectorAll('#neg-tripbox [data-tv]').length") == 1
    assert page.js("document.querySelector('#neg-ev tr.neg-trow[data-i=\"0\"]').getAttribute('aria-expanded')") == "false"
    click(page, "#neg-ev tr.neg-trow[data-i='3']")  # 同じ行をもう一度: 閉じる
    assert page.js("document.querySelectorAll('#neg-ev [data-tv]').length") == 0 and page.js("document.querySelector('#neg-tripbox').hidden") is True
    # 描き直し（曜日の変更）でも残らない
    click(page, "#neg-ev tr.neg-trow[data-i='1']")
    page.js("(function(){const e = document.querySelector('#neg-wd'); e.value = '0'; e.dispatchEvent(new Event('change', {bubbles: true}));})()")
    time.sleep(0.3)
    assert page.js("document.querySelectorAll('[data-tv]').length") == 0


def test_motion_is_off_under_reduced_motion(app):
    page = app
    page.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
    to_guided(page)
    click(page, "#neg-entry-sample")
    assert page.js("getComputedStyle(document.querySelector('#neg-root .neg-box')).animationName") == "none"
    page.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
    to_guided(page)
    click(page, "#neg-entry-sample")
    assert page.js("getComputedStyle(document.querySelector('#neg-root .neg-box')).animationName") == "neg-in"


def test_default_weekday_comes_from_the_data(app):
    page = app
    size(page, 1920, 1080)
    # 火曜が空車 3/4、金曜が空車 2/2、月曜が空車 1/6 の小さな入力（同数の空車は割合、それも同じなら便数）。火曜が勝つ
    r = page.js("""(function(){
      const mk = (wd, empty, trips) => ({wd: wd, weekday: '月火水木金土日'[wd], empty: empty, trips: trips});
      return [Negotiate.defaultWd({by_weekday: [mk(0, 1, 6), mk(1, 3, 4), mk(4, 2, 2)]}, null),
              Negotiate.defaultWd({by_weekday: [mk(0, 2, 6), mk(1, 2, 4), mk(4, 2, 2)]}, null),
              Negotiate.defaultWd({by_weekday: [mk(2, 2, 4), mk(3, 2, 4)]}, null),
              Negotiate.defaultWd({by_weekday: [mk(2, 0, 4)]}, 9)];})()""")
    assert r == [1, 4, 2, 9], r  # 空車最多は火 / 同数なら割合の高い金 / 全く同じなら小さい曜日 / 空車が無ければ元の既定
    to_guided(page)
    click(page, "#neg-entry-sample")
    assert page.js("Negotiate.state.wd") == 5 and "5 / 5 便が空車" in T(page, "#neg-result") and "15 / 40 便が空車" in T(page, "#neg-result")
    # 曜日を変えたあと、データを選び直すと既定に戻る
    page.js("(function(){const e = document.querySelector('#neg-wd'); e.value = '0'; e.dispatchEvent(new Event('change', {bubbles: true}));})()")
    click(page, "[data-act=to-entry]")
    click(page, "#neg-entry-sample")
    assert page.js("Negotiate.state.wd") == 5
