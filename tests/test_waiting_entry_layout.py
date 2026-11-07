"""案内の入口（#g=excel / driver / shipper）でも、結果・メールの欄が潰れないこと（2026-11-08 のページ評価 F1）。

広い画面で、入口から記録を貼ると、メールの欄の幅が 0 になり 1 文字ずつ縦に並んでいた。
入口ごとに貼って、メールの欄の幅・メールの高さ・入力の欄との重なりを確かめる。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
WAITING = ROOT / "web" / "waiting.html"

EXCEL = "日付\t到着\t荷役開始\t荷役終了\t契約の相手\t場所\n10/5\t8:50\t9:35\t9:57\t○○商事\t江東倉庫\n10/6\t9:10\t9:50\t10:12\t○○商事\t江東倉庫\n10/7\t8:30\t10:05\t10:30\t△△食品\t大田センター"
DRIVER = "荷待ち記録 10/5 江東区 ○○倉庫 着 8:50 受付 9:05 荷役開始 9:35 荷役終了 9:57 車番 1234 相手 ○○商事\n荷待ち記録 10/6 江東区 ○○倉庫 着 9:10 荷役開始 9:50 荷役終了 10:12 車番 1234 相手 ○○商事"
SHIPPER = "受付日\t運送会社\t施設\t車番\t受付時刻\t入場\n2026/10/5\tA運輸\t江東区 第1倉庫\t1234\t7:50\t8:30\n2026/10/5\tA運輸\t江東区 第1倉庫\t5678\t8:10\t9:40\n2026/10/6\tB急便\t大田区 配送センター\t1234\t8:00\t10:35"
MODES = [("normal", "?example=1", None), ("excel", "#g=excel", EXCEL), ("driver", "#g=driver", DRIVER), ("shipper", "#g=shipper", SHIPPER)]

PROBE = """(function(){
  const r = (s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return {w: Math.round(b.width), h: Math.round(b.height), l: b.left, r: b.right, t: b.top + scrollY, b: b.bottom + scrollY}; };
  return {input: r('#input'), draft: r('#draft-area'), doc: r('#wait-doc'), partners: r('#partners'), sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth};
})()"""


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


@pytest.mark.parametrize("w,h", [(1920, 1080), (1100, 800)])
@pytest.mark.parametrize("mode,url,text", MODES)
def test_entry_modes_keep_result_and_mail_readable(mode, url, text, w, h):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(w, h) as page:
        page.goto(WAITING.as_uri() + url)
        if text is None:
            page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "書面")
        else:
            page.js("document.querySelector('#paste').value = " + json.dumps(text, ensure_ascii=False))
            page.js("document.querySelector('#b-paste').click()")
            page.wait(lambda: page.js("!!document.querySelector('#partners h2, #partners h3')"), 30, "結果")
        time.sleep(0.6)
        info = page.js(PROBE)
        assert info["sw"] <= info["cw"] + 1, (mode, w, info)
        if mode == "shipper":
            assert info["partners"]["w"] > 600, (mode, w, info)
            return
        assert info["draft"]["w"] > 600, (mode, w, info)
        assert info["doc"]["w"] > 500, (mode, w, info)
        assert info["doc"]["h"] < 4000, (mode, w, info)          # 1 文字ずつ縦に並ぶと数万 px になる
        assert info["partners"]["w"] > 500, (mode, w, info)
        i, d = info["input"], info["doc"]
        assert not (i["r"] > d["l"] and d["r"] > i["l"] and i["b"] > d["t"] and d["b"] > i["t"]), (mode, w, info)   # 入力の欄とメールが重ならない
