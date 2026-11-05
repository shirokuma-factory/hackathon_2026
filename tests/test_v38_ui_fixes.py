"""版 3.8 の画面の修正（洗い出し r1: F02/F11・F05・F06・F07・F08・F10・F5・F9・N5・TQ-7）。

静的な確認（文字列・明暗比）と、ヘッドレス Edge での画面の確認。Edge が無い環境では Edge の確認を飛ばす。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from test_v31_market_routes import _browser
from test_v33_layout_chart import _watch

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
NOTE_4T = "公表例の 4t ウイング（約 2,700kg）には載りません。同じ 4t 車の区分でも、車によって最大積載量が違います"


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _card(label, amount=None, status="公開条件に合う", **kw):
    return {"label": label, "amount": amount, "status": status, **kw}


def _clean(page) -> None:
    assert not page.js("window.__errs.length") and not page.js("window.__con.length"), (page.js("window.__errs"), page.js("window.__con"))


def _key(page, key, code, vk, shift=False, text=None):
    extra = {"text": text, "unmodifiedText": text} if text else {}
    page.send("Input.dispatchKeyEvent", type="keyDown", key=key, code=code, windowsVirtualKeyCode=vk, modifiers=8 if shift else 0, **extra)
    page.send("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=vk)
    time.sleep(0.15)


# ---------------------------------------------------------------- F02 / F11 横棒の図
CHART_JS = """(function(cards){const d=document.createElement('div');d.innerHTML=CompareChart.html(cards);
  const rows=[...d.querySelectorAll('.cc-row')].map(r=>({n:r.querySelector('.cc-name').textContent,cheap:r.classList.contains('cheap'),
    chk:r.classList.contains('chk'),best:[...r.querySelectorAll('.cc-mark:not(.chk)')].map(e=>e.textContent),
    mark:[...r.querySelectorAll('.cc-mark.chk')].map(e=>e.textContent),why:(r.querySelector('.cc-why')||{}).textContent||''}));
  return JSON.stringify({rows, ratio:(d.querySelector('.cc-ratio')||{}).textContent||'', only:(d.querySelector('.cc-only')||{}).textContent||'',
    title:d.querySelector('.cc-title').firstChild.textContent});})"""


def test_f02_f11_cheapest_and_ratio_only_among_fitting_options():
    mixed = [_card("宅配", 30000, est=True), _card("路線便", 20000, est=True),
             _card("貸切", 10000, "要確認", sub="2t車", why="積めるかどうかは判定していません"),
             _card("カゴ台車", 45000, est=True), _card("鉄道", None, "計算できない")]
    only_check = [_card("貸切", 10000, "要確認", sub="2t車", why="条件の確認が必要です"), _card("宅配", 30000, "要確認")]
    one_fit = [_card("貸切", 52000), _card("宅配", 9000, "要確認", why="サイズの確認")]
    with _browser(1920, 1080) as (page, open_):
        _watch(page)
        open_("#v=compare&o=13104&l=km")
        run = lambda cards: json.loads(page.js(f"{CHART_JS}({json.dumps(cards, ensure_ascii=False)})"))  # noqa: E731
        out = run(mixed)
        by = {r["n"].replace("2t車", ""): r for r in out["rows"]}
        assert by["路線便"]["best"] == ["最安"] and by["路線便"]["cheap"]  # いちばん安いのは要確認の貸切ではなく、条件のそろった路線便
        assert by["貸切"]["best"] == [] and not by["貸切"]["cheap"] and by["貸切"]["mark"] == ["要確認"] and "判定していません" in by["貸切"]["why"]
        assert "約 2.3 倍" in out["ratio"]  # 45,000 / 20,000。要確認の 10,000 円は入れない
        names = [r["n"].replace("2t車", "") for r in out["rows"]]
        assert names[:3] == ["路線便", "宅配", "カゴ台車"] and names[3] == "貸切"
        out = run(only_check)
        assert "確認待ちを含む参考の最安" in out["title"] and "確認待ちを含む参考" in out["ratio"] and "約 3 倍" in out["ratio"]
        assert [r["best"] for r in out["rows"]] == [["参考の最安"], []] and all(r["chk"] and r["mark"] == ["要確認"] for r in out["rows"])
        out = run(one_fit)
        assert out["ratio"] == "" and "条件がそろって額が出るのは貸切だけです" in out["only"]
        assert [r["best"] for r in out["rows"]] == [[], []] and out["rows"][1]["mark"] == ["要確認"]
        _clean(page)


# ---------------------------------------------------------------- F10 名前
def test_f10_market_view_is_renamed_everywhere_on_screen():
    html = read("index.html")
    assert ">全国の物流傾向</button>" in html and "市場を見る" not in html
    assert "公開統計の集計で、いま出ている案件ではありません" in html
    for n in ("app.js", "market.js", "muni.js", "routes.js", "sheet.js", "loadview.js", "work.js"):
        body = re.sub(r"/\*.*?\*/", "", read(n), flags=re.S)  # コメントは除く（画面に出る文言だけ見る）
        body = re.sub(r"(?m)^\s*//.*$|\s//.*$", "", body)
        assert "市場を見る" not in body, n
    material = (ROOT / "docs" / "plan" / "2026-11-04_ピッチ資料の材料.md").read_text(encoding="utf-8")
    assert "全国の物流傾向" in material


# ---------------------------------------------------------------- F07 明暗比
def _lum(h: str) -> float:
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _over(fg: str, a: float, bg: str) -> str:
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a * x + (1 - a) * y):02x}" for x, y in zip(f, b))


def test_f07_faint_text_has_4_5_contrast_on_panel_backgrounds():
    css = read("style.css")
    faint = re.search(r"--faint:\s*(#[0-9a-fA-F]{6})", css).group(1)
    muted = re.search(r"--muted:\s*(#[0-9a-fA-F]{6})", css).group(1)
    panel = re.search(r"--glass-solid:\s*(#[0-9a-fA-F]{6})", css).group(1)
    bgs = {"パネル": panel, "地図の背景": "#070b12", "作業パネル": _over("#0b111c", 0.88, "#070b12"),
           "明るい地図の上のガラス": _over("#0d131f", 0.74, "#2c3a4e"), "カードの地": _over("#ffffff", 0.035, panel)}
    for name, bg in bgs.items():
        assert _ratio(faint, bg) >= 4.5, (name, faint, _ratio(faint, bg))
        assert _ratio(muted, bg) >= 4.5, (name, muted)
    for sel in (".card .note", ".sum-src", ".f-hint", ".f-price small"):  # 前提・出典・注意の文は --muted
        line = next(x for x in css.splitlines() if x.startswith(sel + " {"))
        assert "color: var(--muted)" in line, sel


# ---------------------------------------------------------------- N5 吹き出し
def test_n5_municipality_tooltip_balance_uses_rounded_numbers():
    js = read("muni.js")
    assert "const balText = (i) =>" in js and "Math.round(U.d.out[i]) - Math.round(U.d.in[i])" in js
    assert "b === 0 ? '0'" in js and "推定 B ${balText(i)} t（" in js and "推定 B ${signed(" not in js


# ---------------------------------------------------------------- 画面（Edge）
def test_f06_supplement_is_a_button_that_opens_the_text_inline():
    with _browser(1920, 1080) as (page, open_):
        _watch(page)
        open_("#v=compare&o=13104&l=km&d=27205")
        page.wait(lambda: page.js("document.querySelectorAll('#cards .card').length>=4"), 60, "カード")
        r = json.loads(page.js("""(function(){const b=[...document.querySelectorAll('#cards .tip')];
          return JSON.stringify({n:b.length, tag:b.map(x=>x.tagName), exp:b.map(x=>x.getAttribute('aria-expanded')), titles:b.filter(x=>x.hasAttribute('title')).length,
          basis:[...document.querySelectorAll('#cards .note.basis')].map(x=>x.textContent)});})()"""))
        assert r["n"] >= 1 and set(r["tag"]) == {"BUTTON"} and set(r["exp"]) == {"false"} and r["titles"] == 0
        assert any("計算した国の目安です" in x for x in r["basis"]), r  # 計算の前提は、閉じた状態でもカードの本文に 1 行出る
        page.js("document.querySelector('#cards .card .tip').focus()")
        _key(page, "Enter", "Enter", 13, text="\r")
        o = json.loads(page.js("""(function(){const b=document.querySelector('#cards .card .tip'),t=document.getElementById(b.getAttribute('aria-controls'));
          return JSON.stringify({exp:b.getAttribute('aria-expanded'),hidden:t.hidden,text:t.textContent.length});})()"""))
        assert o["exp"] == "true" and o["hidden"] is False and o["text"] > 5, o
        page.js("document.querySelector('#cards .card .tip').click()")
        assert page.js("document.querySelector('#cards .card .tip-body').hidden") is True
        _clean(page)


def test_f5_ship_card_agrees_with_route_card_when_no_route():
    with _browser(1920, 1080) as (page, open_):
        _watch(page)
        open_("#v=compare&o=13104&l=km&d=27128")  # 大阪市中央区
        page.wait(lambda: page.js("document.querySelectorAll('#cards .card').length>=5"), 60, "カード")
        time.sleep(3)
        r = json.loads(page.js("""(function(){const c=[...document.querySelectorAll('#cards .card')].find(x=>x.querySelector('h3').textContent.startsWith('船'));
          return JSON.stringify({none:(c.querySelector('.amt-none')||{}).textContent||'',hub:!!c.querySelector('.hub-btn'),
            chart:document.querySelector('.cc').textContent.includes('なし（つながる航路がありません）')});})()"""))
        assert r["chart"], r  # 図は「なし」と言っている
        assert r["none"] == "なし（つながる航路がありません）" and not r["hub"], r  # 詳しいカードも同じ言い方
        _clean(page)


def test_f05_narrow_chart_is_above_route_cards_and_in_view():
    with _browser(400, 800) as (page, open_):
        _watch(page)
        open_("#v=compare&o=13104&l=km&d=27205")
        page.wait(lambda: page.js("!!document.querySelector('#cards .cc')"), 60, "図")
        time.sleep(3)
        r = json.loads(page.js("""(function(){const cc=document.querySelector('#cards .cc').getBoundingClientRect(), rt=document.querySelector('#rt-modes').getBoundingClientRect(),
          card=document.querySelector('#cards .card').getBoundingClientRect(), d=document.querySelector('#detail').getBoundingClientRect();
          return JSON.stringify({cc:[cc.top,cc.bottom],rt:rt.top,card:card.top,dtop:d.top,dbot:d.bottom,h:innerHeight,rtH:rt.height});})()"""))
        assert r["cc"][0] < r["rt"] or r["rtH"] == 0, r  # 図は経路のカードより上
        assert r["cc"][0] < r["card"] and r["cc"][0] >= r["dtop"] and r["cc"][0] < r["dbot"] - 40, r  # 図の上端がパネルの視野の中
        _clean(page)


def test_f08_loadview_dialog_traps_focus_and_returns_it():
    with _browser(1920, 1080) as (page, open_):
        _watch(page)
        open_("#v=compare&o=13104&l=km&d=27205")
        page.js("document.querySelector('#cargo-toggle').click()")
        page.wait(lambda: page.js("!!document.querySelector('#ed-3d')"), 30, "編集欄")
        page.js("document.querySelector('#ed-3d').focus()")
        page.js("document.querySelector('#ed-3d').click()")
        time.sleep(1.5)
        assert page.js("document.activeElement.classList.contains('lv-x')"), page.js("document.activeElement.outerHTML.slice(0,80)")
        assert page.js("document.querySelector('#panel').hasAttribute('inert')") is True
        inside = "document.querySelector('#loadview .lv-panel').contains(document.activeElement)"
        for _ in range(25):
            _key(page, "Tab", "Tab", 9)
            assert page.js(inside), page.js("document.activeElement.outerHTML.slice(0,80)")
        for _ in range(25):
            _key(page, "Tab", "Tab", 9, shift=True)
            assert page.js(inside)
        _key(page, "Escape", "Escape", 27)
        time.sleep(0.4)
        assert page.js("document.activeElement.id") == "ed-3d"
        assert page.js("document.querySelectorAll('[inert]').length") == 0
        _clean(page)


def test_f9_origin_tag_is_not_under_the_layers_panel_at_1920():
    with _browser(1920, 1080) as (page, open_):
        _watch(page)
        open_("#v=compare&o=13104&l=km&d=27205")
        time.sleep(3)
        # 道路の経路を取得できない環境では時間の帯が出ないため、出発地の位置を返す仮の状態で名札の置き場所だけを確かめる
        page.js("""(function(){const o=__app.S.meta.origins.find(x=>x.code===__app.S.origin);
          __app.Rt.tlState=()=>({p:[o.lon,o.lat]});document.body.classList.add('has-route');
          document.querySelector('#tb-now').textContent='出発';document.querySelector('#tb-day').textContent='1日目';})()""")
        time.sleep(1.5)
        r = json.loads(page.js("""(function(){const e=document.querySelector('#truck-tag'),t=e.getBoundingClientRect(),l=document.querySelector('#layers').getBoundingClientRect();
          const hit=t.right>l.left&&t.left<l.right&&t.bottom>l.top&&t.top<l.bottom;
          return JSON.stringify({hit,t:[t.left,t.right,t.top,t.bottom],l:[l.left,l.right,l.top,l.bottom],on:e.classList.contains('on')});})()"""))
        assert r["on"] and not r["hit"], r
        _clean(page)


def test_tq7_4t_sentence_appears_on_kashikiri_card_and_in_load_view_for_3000kg():
    with _browser(1920, 1080) as (page, open_):
        _watch(page)
        open_("#v=compare&o=13104&l=km&d=27205")
        page.js("document.querySelector('#cargo-toggle').click()")
        page.wait(lambda: page.js("!!document.querySelector('#cargo-editor .it-row')"), 30, "編集欄")
        page.js("""(function(){const set=(k,v)=>{const e=document.querySelector('#cargo-editor .it-row[data-i="0"] [data-k="'+k+'"]');e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));};
          set('count',3);set('length_cm',50);set('width_cm',50);set('height_cm',50);set('weight_kg',1000);})()""")
        page.js("document.querySelector('#ed-confirm').click()")
        page.wait(lambda: page.js("[...document.querySelectorAll('#cards .card')].some(c=>c.textContent.includes('4t車')&&c.textContent.includes('約 2,700kg'))"), 60, "4t のカード")
        txt = page.js("[...document.querySelectorAll('#cards .card')].find(c=>c.textContent.includes('4t車')).querySelector('.note').textContent")
        assert txt == NOTE_4T, txt
        page.js("document.querySelector('#ed-3d').click()")
        page.wait(lambda: page.js("!document.querySelector('#loadview').hidden && !!document.querySelector('#lv-cont')"), 30, "3D")
        page.js("(function(){const s=document.querySelector('#lv-cont');s.value='wing_4t';s.dispatchEvent(new Event('change',{bubbles:true}));})()")
        page.wait(lambda: page.js("!!document.querySelector('#lv-tariff-note')"), 30, "積付けの文")
        assert page.js("document.querySelector('#lv-tariff-note').textContent") == NOTE_4T
        _clean(page)
