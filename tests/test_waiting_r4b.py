"""リポジトリの採点 r4b の指摘（2026-11-08）: 相談の節に根拠のない荷主側荷役の文を入れない、本文の合計の数え方を書く、
PC の LINE の手書きメモの行を落とさない、見出しの無い表に貼り直しの案内を出す。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
WAITING = ROOT / "web" / "waiting.html"

LINE_PASTE = ("2026/10/07(水)\n"
              "14:03\t運転者\t荷待ち記録 10/6 江東区 地点07 着 7:50 荷役開始 9:40 終了 10:30 相手 架空A\n"
              "14:10\t運転者\t10/7 船橋DC 着8:05 呼ばれ9:50 終わり10:30\n"
              "14:20\t運転者\tお疲れさまです\n")
HEADERLESS = "2026/10/5\t1234\t8:00\t9:10\t9:50\n2026/10/6\t1234\t8:30\t10:00\t10:40"
HINT = "1 行目に見出し（日付・到着・荷役開始…）を付けて貼り直してください"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def test_python_line_history_keeps_hand_typed_memo_lines():
    from shipcompare import waiting
    d = waiting.parse_paste_detail(LINE_PASTE)
    assert len(d["rows"]) == 2 and {r.get("place") for r in d["rows"]} == {"江東区 地点07", "船橋DC"}
    assert any("手書きのメモの行も読みました" in n for n in d["notes"])


def test_python_line_history_lists_unreadable_memo_lines():
    from shipcompare import waiting
    d = waiting.parse_paste_detail("14:03\t運転者\t荷待ち記録 10/6 江東区 着 7:50 荷役開始 9:40\n14:10\t運転者\t10/7 船橋DC 午前中に着いた\n")
    assert len(d["rows"]) == 1 and any("読まなかった行" in n and "船橋DC" in n for n in d["notes"])


def test_python_headerless_table_gets_the_hint():
    from shipcompare import waiting
    assert HINT in "".join(waiting.parse_paste_detail(HEADERLESS)["notes"])
    ok = "日付\t車番\t到着\t荷役開始\t荷役終了\n2026/10/5\t1234\t8:00\t9:10\t9:50"
    assert HINT not in "".join(waiting.parse_paste_detail(ok)["notes"])


def test_page_parsers_match_python_for_r4b_cases():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 800) as page:
        page.goto(WAITING.as_uri() + "?selftest=0")
        page.wait(lambda: page.js("typeof WaitingShared !== 'undefined'"), 30, "ページ")
        r = page.js("(function(){ const a = WaitingShared.parsePasteFull(" + json.dumps(LINE_PASTE, ensure_ascii=False) + "); "
                    "const b = WaitingShared.parsePasteFull(" + json.dumps(HEADERLESS, ensure_ascii=False) + "); "
                    "return {n: a.rows.length, notes: a.notes, hint: b.notes}; })()")
        assert r["n"] == 2 and any("手書きのメモの行も読みました" in n for n in r["notes"])
        assert HINT in "".join(r["hint"])


def test_consult_lines_do_not_claim_shipper_side_handling_and_body_names_its_basis():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(WAITING.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "書面")
        t = page.js("draftText(S.res.by_partner.find((x) => x.partner === S.partner))")
        consult = t.split("参考：荷待ちを減らすためのご相談")[1].split("場所ごと・月ごとの")[0]
        assert "貴社側で荷役をしていただいた時間も含まれています" not in consult
        first = t.split("\n\n")[2]
        assert "合計で11時間22分（貴社側で荷役をしていただいた時間を含みます）でした" in first


MIXED = ("日付\t車番\t到着\t荷役開始\t荷役終了\t荷主等の都合\n"
         "2026/10/5\t1234\t8:00\t9:30\t10:00\tはい\n"
         "2026/10/6\t1234\t8:30\t10:00\t10:30\tはい\n"
         "2026/10/7\t5678\t9:00\t10:20\t10:50\tいいえ\n")


def test_body_says_not_shippers_fault_waits_are_excluded_and_matches_tile():
    _edge_or_skip()
    import capture_web as cw
    from test_waiting_sparse_paste import _paste, _check_totals
    with cw.edge_page(1920, 1080) as page:
        _paste(page, MIXED)
        r = _check_totals(page)
        t = page.js("draftText(S.res.by_partner[0])")
        assert f"合計で{r['body']}（荷主等の都合でない待ちは含めていません）でした" in t
