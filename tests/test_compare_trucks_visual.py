"""目視の指摘（docs/reviews/2026-11-08_画面の目視_比べると大型車.md）の X3・X4・X6 と、依頼文の車格・日付の欄の回帰試験。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import conftest  # noqa: E402
from conftest import edge_page  # noqa: E402

WEB = ROOT / "web"
NODE = shutil.which("node")
STUB = "global.document={addEventListener(){}};global.Rt=undefined;global.window={};"


def _node(script: str) -> str:
    if not NODE:
        pytest.skip("node がありません")
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _chart(cards: list[dict]) -> str:
    return json.loads(_node(STUB + _read("compare_chart.js") + f";console.log(JSON.stringify(CompareChart.html({json.dumps(cards, ensure_ascii=False)},null,false)))"))


def _open_app(page, hash_):
    conftest.open_app(page, f"http://127.0.0.1:{conftest.server_port('public')}/index.html{hash_}",
                      ready_js="!!window.__app && !document.querySelector('#loading')", wait_loading=False, start_timeout=180)


# ---------------------------------------------------------------- X3 パレットの大きさ
def test_x3_chart_shows_the_true_reason_when_the_input_is_missing():
    cards = [{"label": "路線便", "amount": None, "status": "計算できない", "sub": "", "none": "計算できない（不足: 荷物1行目の縦、荷物1行目の横、荷物1行目の高さ）"}]
    html = _chart(cards)
    assert "荷物の入力が足りないため計算できません" in html and "荷物1行目の縦" in html, html
    assert "この行き先の料金表がないため" not in html
    plain = [{"label": "路線便", "amount": None, "status": "計算できない", "sub": "", "none": "運賃表なし・問い合わせ"}]
    assert "この行き先の料金表がないため" in _chart(plain)


def test_x3_pallet_without_a_size_cannot_be_confirmed():
    with edge_page(1920, 1080) as page:
        _open_app(page, "#v=compare&o=13104&l=km&d=27128")
        page.js("document.querySelector('#cargo-toggle').click()")
        page.wait(lambda: page.js("!!document.querySelector('#sc-simple #sc-reset') || !!document.querySelector('#sc-simple [data-sc-kind]')"), 30, "荷物の欄")
        page.js("(function(){const b=document.querySelector('#sc-reset'); if (b) b.click();})()")
        page.wait(lambda: page.js("!!document.querySelector('#sc-simple [data-sc-kind=pallet]')"), 30, "荷の形")
        page.js("document.querySelector('[data-sc-kind=pallet]').click()")
        before = page.js("JSON.stringify(Work.confirmedShipment())")
        page.js("document.querySelector('#ed-confirm').click()")
        time.sleep(0.8)
        st = page.js("document.querySelector('#ed-state').textContent")
        assert "パレットの大きさを選んでください" in st, st
        assert page.js("JSON.stringify(Work.confirmedShipment())") == before   # 確定されていない
        # 標準の大きさを選べば確定できる
        page.js("document.querySelector('[data-sc-size=std]').click()")
        page.js("(function(){const set=(k,v)=>{const e=document.querySelector('#sc-simple [data-sc='+k+']'); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true}));}; set('weight_kg',300); set('count',2);})()")
        page.js("document.querySelector('#ed-confirm').click()")
        page.wait(lambda: json.loads(page.js("JSON.stringify(Work.confirmedShipment().items[0])"))["length_cm"] == 110, 60, "確定")


# ---------------------------------------------------------------- X4 那覇の運転時間
def test_x4_naha_shows_no_road_drive_time():
    with edge_page(1920, 1080) as page:
        _open_app(page, "#v=compare&o=13104&l=km&d=47201")
        page.wait(lambda: page.js("!!document.querySelector('#s-time') && document.querySelector('#s-time').textContent.length > 0"), 60, "詳細")
        top = page.js("document.querySelector('#s-time').textContent")
        assert "148" not in top, top
        assert "時間" not in top.replace("船の時間は見積で確認", ""), top
        assert "船の時間は見積で確認" in top, top
        page.wait(lambda: page.js("typeof Rt !== 'undefined' && !!Rt.cur && !!Rt.cur.sets && !!Rt.timeline()"), 60, "経路")
        time.sleep(1)
        bot = page.js("document.querySelector('#tb-end').textContent")
        assert "トラックで走る時間" in bot and "港・空港まで" in bot, bot
    # 陸続きの届け先は今までどおり運転時間を出す
    with edge_page(1920, 1080) as page:
        _open_app(page, "#v=compare&o=13104&l=km&d=27128")
        page.wait(lambda: page.js("!!document.querySelector('#s-time') && /時間|分/.test(document.querySelector('#s-time').textContent)"), 60, "運転時間")


# ---------------------------------------------------------------- 依頼文の車格と日付の欄
def test_request_text_keeps_the_vehicle_class():
    src = _read("work.js")
    fn = re.search(r"function withClass\(text, label\) \{.*?\n\}\n", src, re.S).group(0)
    text = "【見積依頼】\n検討している送り方: 貸切\n\n■ 荷物"
    out = _node(fn + f"console.log(JSON.stringify([withClass({json.dumps(text)}, '貸切 2t車'), withClass({json.dumps(text)}, '貸切'), withClass({json.dumps(text)}, null)]))")
    a, b, c = json.loads(out)
    assert "検討している送り方: 貸切（2t 車）\n" in a, a
    assert b == text and c == text


def test_request_step_has_optional_date_inputs_in_both_screens():
    src = _read("work.js")
    assert src.count("data-opt-date") >= 3 and "出荷日・希望着日（任意）" in src
    assert "${need}${dates}" in src and "${miss}${need || dates}" in src


def test_two_t_check_sentence_is_not_clipped():
    assert re.search(r'class="cc-why" style="display:block;overflow:visible"', _read("compare_chart.js"))


# ---------------------------------------------------------------- X6 いまの大型車
POINT_JS = """(() => { const S = window.__trucks; const p = S.pts.find(q => q.pref === '愛知県' && q.series[23] > 150);
  S.map.jumpTo({ center: [p.lon, p.lat], zoom: 10 }); return [p.lon, p.lat]; })()"""
XY_JS = """(() => { const S = window.__trucks; const p = S.pts.find(q => q.pref === '愛知県' && q.series[23] > 150);
  const e = S.map.project([p.lon, p.lat]), r = document.querySelector('.mapwrap').getBoundingClientRect(); return [r.left + e.x, r.top + e.y]; })()"""
TIP_BOX_JS = """JSON.stringify((() => { const e = document.querySelector('#tip'), t = e.getBoundingClientRect(), m = document.querySelector('.mapwrap').getBoundingClientRect();
  return { tipBottom: t.bottom, tipRight: t.right, mapBottom: m.bottom, mapRight: m.right, h: t.height, over: e.scrollWidth - e.clientWidth }; })())"""


def _open_trucks(page):
    conftest.open_app(page, f"http://127.0.0.1:{conftest.server_port('local')}/trucks_now.html",
                      ready_js="!!window.__trucks && !!window.__trucks.api", wait_loading=False, start_timeout=90,
                      what="いまの大型車の画面が起動しません", tag="trucks_now")
    page.wait(lambda: page.js("window.__trucks.map && window.__trucks.map.loaded()"), 60, "地図")
    time.sleep(2)
    page.js(POINT_JS)
    time.sleep(2)


def _mouse(page, t, x, y):
    page.send("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1)


def test_x6_tip_stays_in_the_map_follows_time_and_hides_on_click():
    with edge_page(1920, 1080) as page:
        _open_trucks(page)
        x, y = page.js(XY_JS)
        _mouse(page, "mouseMoved", x, y)
        page.wait(lambda: page.js("document.querySelector('#tip').style.display") == "block", 10, "吹き出し")
        r = json.loads(page.js(TIP_BOX_JS))
        assert r["tipRight"] <= r["mapRight"] and r["tipBottom"] <= r["mapBottom"] - 100, r   # 地図の中、下の時間の帯に重ならない
        assert r["over"] <= 1 and r["h"] < 260, r   # 中身が枠からはみ出さず、縦に伸びない
        # 時間が動くと、吹き出しの値も変わる（古い時間の台数を残さない）
        v23 = page.js("document.querySelector('#tip .big').textContent")
        page.js("window.__trucks.api.setT(4)")
        page.wait(lambda: page.js("window.__trucks.lastIdx === 4"), 10, "4 時")
        if page.js("document.querySelector('#tip').style.display") == "block":
            assert page.js("document.querySelector('#tip .big').textContent") != v23
        # クリックしたら、カードが開いて吹き出しは消える
        _mouse(page, "mouseMoved", x, y)
        _mouse(page, "mousePressed", x, y)
        _mouse(page, "mouseReleased", x, y)
        page.wait(lambda: page.js("!document.querySelector('#pin').hidden"), 10, "カード")
        assert page.js("document.querySelector('#tip').style.display") == "none"
        _mouse(page, "mouseMoved", x + 1, y)
        time.sleep(0.5)
        assert page.js("document.querySelector('#tip').style.display") == "none"   # 開いたカードの地点には吹き出しを重ねない
        page.js("window.__trucks.api.setT(10)")
        page.wait(lambda: page.js("window.__trucks.lastIdx === 10"), 10, "10 時")
        time.sleep(0.5)
        card = page.js("document.querySelector('#pin').innerText")
        assert "series" not in card and "week_ago" not in card and "JSON" not in card, card
        assert "21時台 の" not in card, card   # 選んだ時間と違う時間の上り・下りを並べない
        assert "データなし" in card


def test_x6_card_shows_up_down_only_for_the_latest_hour():
    with edge_page(1920, 1080) as page:
        _open_trucks(page)
        html = page.js("(() => { const S = window.__trucks; const p = S.pts.find(q => q.up != null && q.down != null && q.series[23] != null); S.T = 23; return S.api.cardHtml(p, true); })()")
        assert "上り / 下り" in html and "データなし" not in html and "series[" not in html
        html4 = page.js("(() => { const S = window.__trucks; const p = S.pts.find(q => q.up != null && q.down != null && q.series[4] != null); S.T = 4; return S.api.cardHtml(p, true); })()")
        assert "データなし" in html4 and "series[" not in html4


def test_x6_tapping_a_point_on_a_phone_opens_the_card():
    with edge_page(390, 844) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        _open_trucks(page)
        x, y = page.js(XY_JS)
        page.send("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x + 6, "y": y + 6}])   # 指は点の真上には当たらない
        page.send("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
        page.wait(lambda: page.js("!document.querySelector('#pin').hidden"), 10, "カード")
