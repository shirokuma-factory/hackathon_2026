"""公開版のどぶさらい（docs/reviews/2026-11-08_公開版のどぶさらい_地図と最初の画面.md）の修正の試験。

- A1: 案内の空きを探すで、運送会社の画面に切り替えると便のカードと承諾ボタンが見える
- A2: 架空の運送会社 C1〜C3 も、書面に必要な項目がそろい、荷主の名称と住所を保存すると確定まで進める
- B1/B4/B7/B9/B10/B12 ほか: 文言と配置
ヘッドレス Edge は同時に 1 つだけ（conftest の edge_page）。
画面写真は環境変数 MAP_SWEEP_SHOTS（無ければ一時フォルダの map_sweep）に残す。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
READY = "!!window.__app && !document.querySelector('#loading')"
SHOTS = Path(os.environ.get("MAP_SWEEP_SHOTS") or Path(tempfile.gettempdir()) / "map_sweep")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _shot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    data = page.send("Page.captureScreenshot", format="png").get("data", "")
    (SHOTS / f"{name}.png").write_bytes(base64.b64decode(data))


def _home(page, port):
    page.goto(f"http://127.0.0.1:{port}/index.html")
    page.wait(lambda: page.js("document.body.classList.contains('view-home') && !!document.querySelector('#role-shipper')"), 60, "最初の画面")


def _open(page, port, hash_):
    open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}", ready_js=READY, wait_loading=False, start_timeout=180, tag="map_sweep_fail")


# ---------------------------------------------------------------- データ（A2）
def test_demo_carriers_have_every_contract_field_and_are_fictional():
    d = json.loads(_read("data/demo/carriers.json"))
    for c in d["carriers"]:
        for k in ("address", "license_no", "terms", "insurance", "payment_options", "cancel_terms"):
            assert c.get(k), (c["id"], k)
        assert "架空" in c["license_no"] and "0000" in c["license_no"], c["license_no"]
        assert "架空" in c["address"], c["address"]


def test_floor_reason_uses_two_decimals():
    from shipcompare import spaceb

    assert spaceb._g2(Decimal("0.70")) == "0.70"
    assert 'quantize(Decimal("0.01"))' in _read("src/shipcompare/spaceb.py")


# ---------------------------------------------------------------- 文言（静的）
def test_static_wording():
    idx, find, mk, mu = _read("web/index.html"), _read("web/find.js"), _read("web/market.js"), _read("web/muni.js")
    assert 'id="p-ship-find"' in idx and 'id="p-car-find"' in idx            # B12
    assert "物流センサスの 3 日間の量（年間ではありません）" in mk                # B7
    assert "運送業・倉庫業" in idx and "44・47</small>" not in idx            # B10
    assert "帰りが空く向き" in find and "荷が多い向き" in find and "荷が多い向き" in mk   # B4
    assert "デモの時計" in find                                                # B1
    assert "。。" not in find                                                  # C2
    assert "${noP31 ? '' : P31_ORDER" in mu                                    # B10
    assert "function repick()" in find                                         # B9
    assert "書面（確定）" in find and "確定済み" in find                       # C6
    assert "未記入（個人事業主は不要）" in _read("src/shipcompare/spaceb.py") and 'data-sh="sole_proprietor"' in find   # U-19
    assert "まであと" in find and "ct-to-carrier" in find                      # U-18
    assert "集荷地:" in find and "例（架空）" in find                          # U-14


# ---------------------------------------------------------------- 画面（A1・A2）
def _visible_js(sel):
    return ("(function(){const e=document.querySelector(%s);if(!e)return false;const r=e.getBoundingClientRect(),l=document.querySelector('#f-list').getBoundingClientRect();"
            "return r.width>0&&r.height>8&&r.bottom>l.top&&r.top<l.bottom;})()") % json.dumps(sel)


SET_SEL = ("(function(k){const s=document.querySelector('#fc-body select[data-ct='+k+']');if(!s||s.options.length<2)return false;s.value=s.options[1].value;"
           "s.dispatchEvent(new Event('change',{bubbles:true}));return true;})")
FILL_SHIPPER = """(function(){const f=(k,v)=>{const i=document.querySelector('#fc-body input[data-sh='+k+']');i.value=v;i.dispatchEvent(new Event('input',{bubbles:true}));};
  f('name','架空商事株式会社');f('address','東京都江東区（架空の住所）');document.querySelector('#fc-shipper').requestSubmit();})()"""


def test_guided_carrier_view_shows_accept_and_contract_confirms_for_t3():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _open(page, port, "#v=market&g=1")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 60, "上位 5 の一覧")
        page.js("document.querySelectorAll('.mg-row')[1].click()")   # 三重県→東京都（T3）
        page.wait(lambda: page.js("(function(){const b=document.getElementById('mg-next');return !!b && !b.hidden && !b.disabled;})()") is True, 60, "主ボタン")
        page.js("document.getElementById('mg-next').click()")
        page.wait(lambda: "v=find" in page.js("location.hash"), 30, "空きを探すへ")
        page.wait(lambda: page.js("!!document.querySelector('#f-list .f-card[data-t=T3] [data-act=hold]')"), 90, "仮予約のボタン")
        page.js("document.querySelector('#f-list .f-card[data-t=T3] [data-act=hold]').click()")
        page.wait(lambda: page.js("!!document.querySelector('#f-list .f-card[data-t=T3] [data-act=to-carrier]')"), 30, "運送会社の画面へのボタン")
        assert "デモの時計" in page.js("document.querySelector('#f-list .f-card[data-t=T3] .f-hold-msg').textContent")   # B1
        _shot(page, "B1_hold_shipper")
        page.js("document.querySelector('#f-list .f-card[data-t=T3] [data-act=to-carrier]').click()")
        page.wait(lambda: page.js(_visible_js("#f-list [data-act=accept]")), 30, "承諾ボタンが見える（A1）")
        _shot(page, "A1_carrier_accept")
        page.js("document.querySelector('#f-list [data-act=accept]').click()")
        page.wait(lambda: page.js(_visible_js("#f-list [data-act=contract]")), 30, "書面のボタンが見える")
        page.js("document.querySelector('#f-list [data-act=contract]').click()")
        page.wait(lambda: page.js("!document.getElementById('f-contract').hidden && !!document.querySelector('#fc-body select[data-ct=fuel]')"), 30, "書面")
        assert page.js(SET_SEL + "('fuel')")
        page.wait(lambda: page.js("!!document.querySelector('#fc-body select[data-ct=pay]') && document.querySelector('#fc-body select[data-ct=pay]').options.length>1"), 30, "支払方法の選択肢")
        assert page.js(SET_SEL + "('pay')")
        page.js(SET_SEL + "('lift')")
        page.js(FILL_SHIPPER)
        page.wait(lambda: page.js("!document.getElementById('fc-confirm').disabled"), 30, "確定できる")
        body = page.js("document.getElementById('fc-body').innerText")
        assert "未登録" not in body.split("荷主の情報")[0], body
        page.js("document.getElementById('fc-confirm').click()")
        page.wait(lambda: page.js("document.getElementById('fc-confirm').textContent") == "確定しました", 30, "確定")
        assert page.js("document.querySelector('#f-contract .fc-head b').textContent") == "書面（確定）"
        assert page.js("document.querySelector('#fc-body input[data-sh=name]').disabled") is True
        page.wait(lambda: "確定済み" in page.js("document.querySelector('#f-list').innerText"), 30, "カードが確定済み")
        _shot(page, "A2_T3_confirmed")
        assert not page.js("window.__errs ? window.__errs.length : 0")


def test_home_offers_find_and_connect():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _home(page, port)
        page.js("document.getElementById('role-shipper').click()")
        page.wait(lambda: page.js("!!document.getElementById('p-ship-find') && document.getElementById('p-ship-find').offsetHeight>0"), 30, "荷主の選択肢")
        _shot(page, "B12_home_shipper")
        page.js("document.getElementById('p-ship-find').click()")
        page.wait(lambda: "v=find" in page.js("location.hash"), 30, "空きを探すへ")
    with edge_page(1920, 1080) as page:
        _home(page, port)
        page.js("document.getElementById('role-carrier').click()")
        page.wait(lambda: page.js("!!document.getElementById('p-car-find') && document.getElementById('p-car-find').offsetHeight>0"), 30, "運送会社の選択肢")
        _shot(page, "B12_home_carrier")


def test_repick_stays_in_current_mode_and_directions_are_worded():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _open(page, port, "#v=market&m=truck")
        page.wait(lambda: page.js("!!document.querySelector('#m-tbody tr')"), 60, "表")
        page.js("document.querySelector('#m-tbody tr').click()")
        page.wait(lambda: page.js("!document.getElementById('m-pair').hidden"), 30, "区間のカード")
        card = page.js("document.getElementById('m-pair').innerText")
        assert "荷が多い向き" in card and "帰りが空く向き" in card and "3 日間" in card, card
        page.js("document.querySelector('#m-pair [data-act=find]').click()")
        page.wait(lambda: "v=find" in page.js("location.hash"), 30, "空きを探す")
        page.wait(lambda: page.js("!document.getElementById('f-pairbar').hidden"), 30, "区間の帯")
        band = page.js("document.getElementById('f-pairbar').innerText")
        assert "帰りが空く向き" in band and "荷が多い向き" in band, band
        _shot(page, "B4_find_band")
        page.js("document.getElementById('f-repick').click()")
        page.wait(lambda: "v=market" in page.js("location.hash"), 30, "全国の物流傾向へ")
        assert "g=1" not in page.js("location.hash") and not page.js("document.body.classList.contains('guided')")


def test_guided_all_trips_shows_cards_with_reasons():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _open(page, port, "#v=find&g=1")
        page.wait(lambda: page.js("document.querySelectorAll('#f-list .f-card').length") >= 7, 90, "便のカード")
        assert page.js("document.body.classList.contains('f-showall')") is True
        vis = page.js("[...document.querySelectorAll('#f-list .f-card')].filter(e=>e.getBoundingClientRect().height>20).length")
        assert vis >= 7, vis
        assert page.js("document.querySelectorAll('#f-list .f-card .f-why li').length") >= 1
        assert "集荷地:" in page.js("document.body.innerText")
        _shot(page, "U01_guided_all_trips")
        assert not page.js("window.__errs ? window.__errs.length : 0")


def test_compare_destination_carries_over_to_find():
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        _open(page, port, "#v=compare")
        page.wait(lambda: page.js("!!__app.S.idx"), 60, "市区町村の表")
        code = page.js("(function(){const S=__app.S;const i=S.idx.names.findIndex((n,k)=>S.idx.prefs[k]==='大阪府'&&S.idx.codes[k]!==S.origin);return i;})()")
        assert code >= 0
        page.js(f"__app.select({code}, {{fly:false}})") if page.js("typeof __app.select") == "function" else page.js(f"__app.S.sel={code}")
        page.js("location.hash='#v=find'")
        page.wait(lambda: page.js("__app.Find.state.active && __app.Find.state.loaded"), 60, "空きを探す")
        want = page.js(f"__app.S.idx.codes[{code}]")
        page.wait(lambda: page.js("__app.Find.state.dest") == want, 30, "届け先の引き継ぎ")
        assert page.js("__app.Find.state.dest") != "46201"
        assert "大阪" in page.js("document.getElementById('f-dest-q').value")
