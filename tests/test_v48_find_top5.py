"""案内の全国の物流傾向の上位 5 区間: どれを選んでも「この区間で空きを探す」から、載る便が 1 つ以上ある空きを探すが開く。

棚の便（T1・T3・T5）は 1 口分の床しかないので、場面の荷そのままでは載らない。その区間は個数を減らした例の荷で始める。
載る例の荷が無い区間は、主ボタンを出さずに理由を 1 行出す。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
READY = "!!window.__app && !document.querySelector('#loading')"


def test_reduced_example_is_wired_in_find_js_and_app_js():
    js = (ROOT / "web" / "find.js").read_text(encoding="utf-8")
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "の荷物を ${ex.count} 個にしたもの" in js and "async function exampleSearch(" in js and "checkFit" in js
    assert "V.Find.fitState(pn)" in app and "この向きの便は埋まっています" in app   # 文言の直し 2026-11-07。2026-11-08 の最終評価に合わせて


NO_TRIP = (0, 3)  # 上位 5 の 1 位（東京都 → 兵庫県）と 4 位（愛媛県 → 兵庫県）。帰り便が兵庫県に着く便だけだった


@pytest.mark.parametrize("idx", range(5))
def test_top5_route_opens_find_with_a_fitting_trip(idx):
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        open_app(page, f"http://127.0.0.1:{port}/index.html#v=market&g=1", ready_js=READY, wait_loading=False, start_timeout=180, tag="v48_top5_fail")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 60, "上位 5 の一覧")
        name = page.js(f"document.querySelectorAll('.mg-row')[{idx}].querySelector('.mg-pair').textContent")
        page.js(f"document.querySelectorAll('.mg-row')[{idx}].click()")
        if idx in NO_TRIP:   # 鹿児島版のサンプルには兵庫県行きの便が無い（2026-11-07）。主ボタンを出さず、理由を 1 行出す
            page.wait(lambda: "この向きの例の便は、まだありません" in page.js("document.getElementById('mg-sel').textContent"), 30, f"理由の 1 行 {name}")   # 文言の直し 2026-11-07
            # 行き止まりにしない（2026-11-08）: 主ボタンは「この区間で空きを探す」として出し、空きを探すへ進める
            assert page.js("document.getElementById('mg-next').hidden") is False
            assert page.js("document.getElementById('mg-next').textContent").startswith("この向きで空きを探す")   # 日本語の直し 2026-11-08: 「区間」と「向き」を向きにそろえた
            return
        # 載る例の荷の有無を調べ終えるまで待つ。主ボタンは出ているか、理由が 1 行出ているかのどちらか
        page.wait(lambda: page.js("(function(){const b=document.getElementById('mg-next');return !!b && !b.hidden && !b.disabled;})()") is True, 60, f"主ボタン {name}")
        page.js("document.getElementById('mg-next').click()")
        page.wait(lambda: "v=find" in page.js("location.hash"), 30, "空きを探すへ移る")
        # 選んだ区間の帰り便（origin が荷が少ない向きの発地の県、dest が荷が多い向きの発地の県）のうち 1 便が、いまの荷に載る
        js = """(function(){const F=__app.Find.state, S=__app.S||null; if(!F.pair||!F.loaded) return null;
          const idx=document.__idx||null; return JSON.stringify({sel:F.sel, ok:Object.entries(F.results).filter(e=>e[1].ok).map(e=>e[0]), ex:F.ex.id, n:F.ex.count,
            labels:Object.fromEntries(F.trips.map(t=>[t.id,t.route_label]))});})()"""
        want = {0: "T1,T2,T7", 1: "T3", 2: "T5", 3: "T6", 4: "T4"}  # 上位 5 区間の帰り便（data/demo/carriers.json）
        ids = set(want[idx].split(","))
        def good():
            r = json.loads(page.js(js) or "{}")
            return bool(r) and bool(set(r["ok"]) & ids) and r["sel"] in ids and (r["sel"] in r["ok"])
        page.wait(good, 90, f"区間の帰り便に載る {name}")
        if idx in (1, 2):   # 載る判定が出てから、案内の一文が出るまでに数十ミリ秒かかる（find.js: tryExample の後に autoNote を入れる）。一文が出るのを待ってから読む
            page.wait(lambda: page.js("!!document.querySelector('#f-list .f-autonote')"), 30, f"案内の一文 {name}")
        r = json.loads(page.js(js))
        assert page.js("document.querySelectorAll('#f-list .chip.ok').length") >= 1
        sub = page.js("document.getElementById('f-rsub').textContent")
        note = page.js("(document.querySelector('#f-list .f-autonote')||{textContent:''}).textContent")
        assert not page.js("window.__errs ? window.__errs.length : 0"), page.js("JSON.stringify(window.__errs)")
        if idx in (1, 2):   # 棚の便は個数を減らした例
            assert r["n"] and r["n"] >= 1 and "1 口分" in note, (name, r, note, page.js("document.querySelector('#f-list').innerText.slice(0, 400)"))
        print(name, r["sel"], r["ex"], r["n"], sub)
