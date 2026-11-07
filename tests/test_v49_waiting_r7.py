"""実務の効き目 r7a/r7b・画面の出来 r4a/r4b を受けた、荷待ちのページとドライバーの 1 枚の修正（SPEC §49）。

- 照合: 書面に車番が無くても、日付と場所と到着の時刻（±30 分）で組にする。使った鍵を行ごとに出し、候補が複数あるときは組にせず候補を並べる。
- 額: 組になった行どうし・同じ内訳（待機時間料）だけを並べ、組にならなかった行は別の行に分ける。
- #g=shipper: 注意書きは入力欄より先に出さず、閉じた枠に入れる。
- 荷待ちのページとドライバーの 1 枚の飾りの帯を外す。
Edge の確認は 1 つずつ直列に動かす。Edge が無い環境では Edge の確認を飛ばす。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

WAITING = ROOT / "web" / "waiting.html"
DRIVER = ROOT / "web" / "driver.html"

OWN = ("受付日\t運送会社\t施設\t車番\t受付時刻\t入場\n"
       "2026/10/5\tA運輸\t江東区 第1倉庫\t1234\t8:00\t9:30\n"
       "2026/10/5\tA運輸\t江東区 第1倉庫\t5678\t10:00\t10:40\n"
       "2026/10/6\tA運輸\t川崎市 第2倉庫\t9012\t13:00\t14:30\n")
DOC_NO_VEHICLE = ("日付\t場所\t到着\t荷役開始\t荷役終了\n"
                  "2026/10/5\t江東区 第1倉庫\t8:00\t9:30\t10:00\n"
                  "2026/10/5\t江東区 第1倉庫\t10:30\t11:30\t12:00\n"
                  "2026/10/6\t川崎市 第2倉庫\t13:00\t14:30\t15:00\n")


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _wait_for(page, js, what, timeout=30):
    page.wait(lambda: page.js(js), timeout, what)


def _load_own(page, name, log):
    page.goto(WAITING.as_uri() + "?x=" + name)
    _wait_for(page, "!!document.querySelector('#b-paste')", "荷待ちのページ")
    page.js("document.querySelector('#b-clear').click()")
    page.js("document.querySelector('#paste').value = " + json.dumps(log, ensure_ascii=False))
    page.js("document.querySelector('#b-paste').click()")
    _wait_for(page, "!!document.querySelector('#cross-ta')", "照合の欄")


def _cross(page, doc):
    page.js("document.querySelector('#cross-ta').value = " + json.dumps(doc, ensure_ascii=False))
    page.js("document.querySelector('#b-cross').click()")
    _wait_for(page, "!!document.querySelector('#cross-result') || (S.cross && S.cross.error)", "照合の結果")


def test_cross_check_pure_function_falls_back_to_place_and_time():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1000, 800) as page:
        page.goto(WAITING.as_uri() + "?x=pure49")
        _wait_for(page, "typeof WaitingShared !== 'undefined'", "共有の計算")
        out = page.js("""(function(){
          const c = (date, place, a, s, v) => ({date, place, arrive: a, start: s, vehicle_no: v || ''});
          const r1 = WaitingShared.crossCheck([c('2026-10-05', '江東区 第1倉庫', '8:00', '9:30')], [c('2026-10-05', '江東区第1倉庫', '8:20', '9:30', '1234')], 15);
          const r2 = WaitingShared.crossCheck([c('2026-10-05', '江東', '8:05', '9:30')], [c('2026-10-05', '江東', '8:00', '9:00'), c('2026-10-05', '江東', '8:10', '9:10')], 15);
          const r3 = WaitingShared.crossCheck([c('2026-10-05', '江東', '8:00', '9:30', '1111')], [c('2026-10-05', '江東', '8:00', '9:30', '2222')], 15);
          const r4 = WaitingShared.crossCheck([c('2026-10-05', '', '8:00', '9:30', '1234')], [c('2026-10-05', '', '8:10', '9:30', '99-1234')], 15);
          return {r1: [r1.pairs.length, r1.pairs[0] && r1.pairs[0].key, r1.same], r2: [r2.pairs.length, r2.ambiguous.length, r2.ambiguous[0].candidates.length, r2.only_shipper.length, r2.only_carrier.length],
                  r3: [r3.pairs.length, r3.only_carrier.length, r3.only_shipper.length], r4: [r4.pairs.length, r4.pairs[0].key]};
        })()""")
        assert out["r1"] == [1, "place", 0], out          # 車番が片方に無くても、場所と到着の時刻（20 分差は ±30 分以内）で組にする。20 分差なので時刻が違う組になる
        assert out["r2"] == [0, 1, 2, 0, 0], out          # 候補が 2 件あるときは組にせず、候補を並べる
        assert out["r3"] == [0, 1, 1], out                # 車番が両方にあって違うときは組にしない
        assert out["r4"] == [1, "vehicle"], out           # 車番は数字の下 4 桁で照合する


def test_example_document_without_vehicle_numbers_reconciles_and_shows_key():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        _load_own(page, "ex49", OWN)
        _cross(page, DOC_NO_VEHICLE)
        info = page.js("({pairs: S.cross.pairs.length, keys: S.cross.key_counts, only_c: S.cross.only_carrier.length, only_s: S.cross.only_shipper.length, err: S.cross.error || '', keytext: document.querySelector('#cross-keys').textContent})")
        assert info["err"] == "" and info["pairs"] == 3 and info["keys"] == {"vehicle": 0, "place": 3, "time": 0}, info
        assert info["only_c"] == 0 and info["only_s"] == 0 and "場所と時刻" in info["keytext"], info
        # 例の書面を入れるボタンでも、時刻が違う組が見える形で出る
        page.js("document.querySelector('#b-cross-ex').click()")
        _wait_for(page, "S.crossOn", "例の書面")
        page.js("document.querySelector('#b-cross').click()")
        _wait_for(page, "!!document.querySelector('#cross-result')", "例の結果")
        ex = page.js("({pairs: S.cross.pairs.length, differ: S.cross.differ.length, table: !!document.querySelector('#cross-differ'), cells: [...document.querySelectorAll('#cross-differ tbody tr td:nth-child(3)')].map((t) => t.textContent)})")
        assert ex["pairs"] >= 2 and ex["differ"] >= 1 and ex["table"] and ex["cells"] and all(c in ("場所と到着の時刻", "場所と時刻") for c in ex["cells"]), ex


def test_ambiguous_rows_are_listed_as_candidates_not_guessed():
    _edge_or_skip()
    import capture_web as cw
    own = ("受付日\t運送会社\t施設\t車番\t受付時刻\t入場\n"
           "2026/10/5\tA運輸\t江東区 第1倉庫\t1234\t8:00\t9:30\n2026/10/5\tA運輸\t江東区 第1倉庫\t5678\t8:10\t9:40\n")
    doc = "日付\t場所\t到着\t荷役開始\t荷役終了\n2026/10/5\t江東区 第1倉庫\t8:05\t9:30\t10:00\n"
    with cw.edge_page(1280, 1000) as page:
        _load_own(page, "amb49", own)
        _cross(page, doc)
        info = page.js("({pairs: S.cross.pairs.length, amb: S.cross.ambiguous.length, cand: S.cross.ambiguous[0].candidates.length, shown: !!document.querySelector('#cross-ambiguous'), text: document.querySelector('#cross-result').textContent})")
        assert info["pairs"] == 0 and info["amb"] == 1 and info["cand"] == 2 and info["shown"] and "同じ日・場所に近い時刻の回が複数あります" in info["text"], info


def test_amounts_compare_same_rows_and_same_component_with_unmatched_on_its_own_line():
    _edge_or_skip()
    import capture_web as cw
    own = OWN + "2026/10/7\tA運輸\t江東区 第1倉庫\t3456\t8:00\t11:00\n"      # 書面に無い行（組にならない）
    doc = ("日付\t場所\t到着\t荷役開始\t荷役終了\t参考額（円）\n"
           "2026/10/5\t江東区 第1倉庫\t8:00\t9:30\t10:00\t10000\n"
           "2026/10/5\t江東区 第1倉庫\t10:00\t10:40\t11:10\t6000\n")
    with cw.edge_page(1280, 1000) as page:
        _load_own(page, "amt49", own)
        _cross(page, doc)
        info = page.js("({n: S.cross.pair_n, c: S.cross.pair_carrier_fee, s: S.cross.pair_shipper_fee, un_s: S.cross.unpaired_shipper.length, un_c: S.cross.unpaired_carrier.length, text: document.querySelector('#cross-amount').textContent})")
        t = info["text"]
        assert info["n"] == 2 and info["un_s"] == 2 and info["un_c"] == 0, info
        assert "同じ行どうしの比較" in t and "組になった 2 組" in t and "待機時間料だけ" in t, t
        assert "組にならなかった行" in t and "自社 2 行" in t and "直接並べて差とは読まない" in t, t
        assert "16,000 円" in t, t                                              # 書面に書かれた額は別に書く


def test_guided_shipper_has_no_bold_notice_before_the_input():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 900) as page:
        page.goto(WAITING.as_uri() + "?x=g49#g=shipper")
        _wait_for(page, "document.body.classList.contains('g-step')", "案内の画面")
        info = page.js("""(function(){
          const top = document.querySelector('#paste').getBoundingClientRect().top;
          const bold = [...document.querySelectorAll('.protect p')].filter((p) => p.checkVisibility() && p.getBoundingClientRect().top < top).length;
          const d = document.querySelector('#protect details');
          return {bold, closed: !!d && !d.open};
        })()""")
        assert info == {"bold": 0, "closed": True}, info
        page.goto(WAITING.as_uri() + "?x=g49b#all")
        _wait_for(page, "document.querySelectorAll('#protect p').length >= 2", "全部の画面の注意書き")


def test_no_decorative_banner_on_waiting_and_driver():
    for path in (WAITING, DRIVER, ROOT / "web" / "publish" / "waiting_artifact.html"):
        html = path.read_text(encoding="utf-8")
        assert 'class="banner"' not in html and ".banner" not in html, path.name
