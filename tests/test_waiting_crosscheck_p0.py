"""公開版のどぶさらい（P0 の周り）2026-11-08 の P-01〜P-07。
荷主が運送会社の書面を自社の受付簿と見比べるとき、自社の額は受付簿の記録からだけ数える。
書面にだけある回・どの回か決められない回は、自社の額に入れず別に書く。手計算（中型、30 分無料、30 分ごとに 1,760 円）と比べる。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
WAITING = ROOT / "web" / "waiting.html"

HEAD = "受付日\t運送会社\t施設\t車番\t受付時刻\t入場\n"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _wait(page, expr, what):
    page.wait(lambda: page.js(expr), 30, what)


def _run(page, log, doc, amount=None, head=HEAD):
    page.goto(WAITING.as_uri() + "?x=p0")
    _wait(page, "!!document.querySelector('#b-paste')", "ページ")
    page.js("document.querySelector('#b-clear').click()")
    page.js("document.querySelector('#paste').value = " + json.dumps(head + log, ensure_ascii=False))
    page.js("document.querySelector('#b-paste').click()")
    _wait(page, "!!document.querySelector('#cross-ta')", "照合の欄")
    page.js("document.querySelector('#cross-ta').value = " + json.dumps(doc, ensure_ascii=False))
    if amount is not None:
        page.js("(function(){ const e = document.querySelector('#cross-doc-amount'); e.value = %s; e.dispatchEvent(new Event('change', {bubbles: true})); })()" % json.dumps(amount, ensure_ascii=False))
    page.js("document.querySelector('#b-cross').click()")
    _wait(page, "!!document.querySelector('#cross-result')", "見比べた結果")


GET = (
    "({r: S.cross.recalc ? {own: S.cross.recalc.own, carrier: S.cross.recalc.carrier, ur: S.cross.recalc.unrecorded.n, "
    "ura: S.cross.recalc.unrecorded.amount, ud: S.cross.recalc.undecided.n, uda: S.cross.recalc.undecided.amount, "
    "cls: S.cross.recalc.class_part} : null, differ: S.cross.differ.length, pairs: S.cross.pairs.length, "
    "onlyC: S.cross.only_carrier.length, onlyS: S.cross.only_shipper.length, "
    "reply: document.querySelector('#reply-ta') ? document.querySelector('#reply-ta').value : '', "
    "text: document.querySelector('#cross-result').textContent})"
)


def test_p01_runs_only_in_document_are_listed_not_counted():
    _edge_or_skip()
    import capture_web as cw
    log = "2026/10/5\tA運輸\t江東区\t1234\t7:50\t8:30\n2026/10/5\tA運輸\t江東区\t5678\t8:10\t9:40\n2026/10/6\tA運輸\t江東区\t1234\t9:00\t10:00\n"
    doc = ("日付  車番  到着  荷役開始  荷役終了\n2026/10/5  1234  7:20  8:30  9:00\n2026/10/5  5678  7:56  9:54  10:20\n"
           "2026/10/6  1234  9:00  10:00  10:30\n2026/10/7  9999  8:00  10:00  10:30\n")
    with cw.edge_page(1920, 1080) as page:
        _run(page, log, doc)
        g = page.js(GET)
        # 受付簿の時刻で数えられるのは 3 回だけ: 1,760 + 3,520 + 1,760 = 7,040 円。10/7 の回（書面 5,280 円）は入れない
        assert g["r"]["own"] == 7040
        assert g["r"]["ur"] == 1 and g["r"]["ura"] == 5280
        assert "受付簿に無い回（1 回、書面の額 5,280" in g["text"]
        assert "7,040" in g["reply"] and "14,080" not in g["reply"]
        assert "【当方の受付簿に無い回 1 回（書面の額 5,280" in g["reply"] and "【両方の記録で確かめられた 3 回】" in g["reply"]


def test_p02_14_minutes_each_way_is_a_difference():
    _edge_or_skip()
    import capture_web as cw
    log = "2026/10/5\tA運輸\t江東区\t5678\t8:10\t9:40\n"
    doc = "日付  車番  到着  荷役開始  荷役終了\n2026/10/5  5678  7:56  9:54  10:20\n"
    with cw.edge_page(1920, 1080) as page:
        _run(page, log, doc)
        g = page.js(GET)
        # 到着 14 分早く・開始 14 分遅く。待機は受付簿 90 分（3,520 円）、書面 118 分（5,280 円）
        assert g["differ"] == 1 and g["r"] is not None
        assert g["r"]["carrier"] == 5280 and g["r"]["own"] == 3520
        assert "時刻が違う組はありません" not in g["text"]


def test_p03_ambiguous_row_is_not_counted():
    _edge_or_skip()
    import capture_web as cw
    log = "2026/10/5\tA運輸\t江東区\t\t8:00\t9:00\n2026/10/5\tA運輸\t江東区\t\t8:05\t9:05\n2026/10/6\tA運輸\t江東区\t1234\t7:50\t8:30\n"
    doc = "日付\t場所\t車番\t到着\t荷役開始\t荷役終了\n2026/10/5\t江東区\t\t8:02\t11:00\t11:30\n2026/10/6\t江東区\t1234\t7:20\t8:30\t9:00\n"
    with cw.edge_page(1920, 1080) as page:
        _run(page, log, doc)
        g = page.js(GET)
        assert g["r"]["own"] == 1760          # 組になった 10/6 の回だけ
        assert g["r"]["ud"] == 1 and g["r"]["uda"] == 9500
        assert "どの回か決められない回（1 回、書面の額 9,500" in g["text"]
        assert "【どの回か決められない回 1 回（書面の額 9,500" in g["reply"] and "11,260" not in g["reply"]


def test_p04_other_facility_and_far_arrival_are_not_paired():
    _edge_or_skip()
    import capture_web as cw
    log = "2026/10/5\tA運輸\t江東区 第1倉庫\t1234\t8:00\t8:40\n"
    doc = "日付\t場所\t車番\t到着\t荷役開始\t荷役終了\n2026/10/5\t大田区 配送センター\t1234\t13:00\t15:00\t15:30\n"
    with cw.edge_page(1920, 1080) as page:
        _run(page, log, doc)
        g = page.js(GET)
        assert g["pairs"] == 0 and g["onlyC"] == 1 and g["onlyS"] == 1
        assert "300 分" not in g["reply"]
    # 場所が書面に無くても、到着が 5 時間離れた同じ車番は別の回
    doc2 = "日付  車番  到着  荷役開始  荷役終了\n2026/10/5  1234  13:00  15:00  15:30\n"
    with cw.edge_page(1920, 1080) as page:
        _run(page, log, doc2)
        g = page.js(GET)
        assert g["pairs"] == 0 and g["onlyC"] == 1


def test_p05_document_amount_column_does_not_become_own_amount():
    _edge_or_skip()
    import capture_web as cw
    log = "2026/10/5\tA運輸\t江東区\t1234\t7:50\t8:30\n2026/10/5\tA運輸\t江東区\t5678\t8:10\t9:40\n"
    doc = "日付  車番  到着  荷役開始  荷役終了  参考額\n2026/10/5  1234  7:50  8:30  9:00  3000\n2026/10/5  5678  7:56  9:54  10:20  6000\n"
    with cw.edge_page(1920, 1080) as page:
        _run(page, log, doc)
        g = page.js(GET)
        assert g["r"]["own"] == 5280          # 1,760 + 3,520。書面の額 9,000 円から作らない
        assert "7,240" not in g["reply"] and "7,240" not in g["text"]
        assert "9,000" in g["text"]


def test_p06_class_only_difference_is_stated_as_class():
    _edge_or_skip()
    import capture_web as cw
    log = "2026/10/5\tA運輸\t江東区\t1234\t7:50\t8:30\n2026/10/5\tA運輸\t江東区\t5678\t8:10\t9:40\n"
    doc = "日付  車番  車種  到着  荷役開始  荷役終了\n2026/10/5  1234  大型  7:50  8:30  9:00\n2026/10/5  5678  大型  8:10  9:40  10:10\n"
    with cw.edge_page(1920, 1080) as page:
        _run(page, log, doc)
        g = page.js("({amount: document.querySelector('#cross-amount').textContent, differ: S.cross.differ.length})")
        assert g["differ"] == 0
        assert "車種" in g["amount"] and "単価の差" in g["amount"]
    # 受付簿に車種がある（大型）ときは、受付簿の車種で数えるので車種の違いは出ない
    head2 = "受付日\t運送会社\t施設\t車番\t車種\t受付時刻\t入場\n"
    log2 = "2026/10/5\tA運輸\t江東区\t1234\t大型\t7:50\t8:30\n"
    doc2 = "日付  車番  車種  到着  荷役開始  荷役終了\n2026/10/5  1234  大型  7:20  8:30  9:00\n"
    with cw.edge_page(1920, 1080) as page:
        _run(page, log2, doc2, head=head2)
        g = page.js("({own: S.cross.recalc.own, carrier: S.cross.recalc.carrier, cls: S.cross.recalc.class_part})")
        assert g["cls"] == 0 and g["own"] == 1890 and g["carrier"] == 3780


def test_p07_yen_signs_and_man_are_read_or_flagged():
    _edge_or_skip()
    import capture_web as cw
    log = "2026/10/5\tA運輸\t江東区\t1234\t7:50\t8:30\n"
    doc = "日付  車番  到着  荷役開始  荷役終了\n2026/10/5  1234  7:20  8:30  9:00\n"
    cases = [("¥9,000", 9000), ("9,000円（税抜）", 9000), ("1万", 10000), ("９，０００", 9000), ("1万2000", 12000)]
    with cw.edge_page(1920, 1080) as page:
        for text, want in cases:
            _run(page, log, doc, amount=text)
            assert page.js("S.cross.doc_amount") == want, text
        _run(page, log, doc, amount="あいう")
        assert page.js("!!document.querySelector('#cross-doc-bad')")
