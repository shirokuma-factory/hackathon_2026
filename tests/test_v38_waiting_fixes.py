"""SPEC §38 荷待ちの洗い出し r1 の修正（F1・F8・W1〜W4・N4）。

- Python（src/shipcompare/waiting.py）の値を固定する。JS は同じ試験表（tests/data/waiting_vectors.json）をヘッドレス Edge の ?selftest=1 で通す。
- web/waiting.html の画面（Edge）: 「例を入れる」は保存した記録を確認なしで置き換えず、元に戻せる。?example=1 は保存した記録を上書きしない。日付の欄が欠けずに見える。
  Edge が無い環境だけ飛ばす。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from shipcompare import waiting

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_waiting_vectors  # noqa: E402

PAGE = ROOT / "web" / "waiting.html"
VECTORS = ROOT / "tests" / "data" / "waiting_vectors.json"
KEY, PREV = "waiting_page_rows_v1", "waiting_page_rows_prev_v1"


def _row(**kw):
    base = {"date": "2026-10-05", "vehicle_class": "中型", "partner": "A", "place": "X", "arrive": "09:00", "start": "10:00", "shipper_fault": "はい"}
    base.update(kw)
    return base


def _one(**kw):
    res = waiting.calc([_row(**kw)], 2026)
    return res["rows"][0] if res["rows"] else res["errors"]


# ---------------------------------------------------------------- W1・W2
def test_dated_cells_use_the_actual_difference_and_multi_day_waits_are_counted():
    assert _one(arrive="2026/10/05 9:00", start="2026/10/05 10:00")["wait_min"] == 60
    r = _one(arrive="2026/10/05 9:00", start="2026/10/06 10:00")   # 25 時間
    assert r["wait_min"] == 1500 and waiting.LONG_WAIT_NOTE in r["notes"]
    assert _one(arrive="10/5 9:00", start="10/7 10:00")["wait_min"] == 2940   # 49 時間
    r2 = _one(arrive="2026/10/05 9:00", start="2026/10/06 8:59")   # 24 時間未満は注記なし
    assert r2["wait_min"] == 1439 and "notes" not in r2


def test_same_day_start_before_arrival_is_an_input_error_but_undated_times_still_roll_over_within_12h():
    err = _one(arrive="2026/10/05 9:00", start="2026/10/05 8:00")
    assert isinstance(err, list) and "荷役開始が到着より前です" in err[0]["message"]
    assert isinstance(_one(arrive="9:00", start="10/5 8:00"), list)   # 到着の日付は日付の欄
    assert _one(arrive="23:30", start="00:40")["wait_min"] == 70   # 日付の無い欄は従来どおり翌日
    over = _one(arrive="09:00", start="08:00")   # 翌日と読むと 23 時間になる逆転は誤りの行にする（SPEC §42）
    assert isinstance(over, list) and "日付が無いので翌日と読むと 23 時間 0 分あいた" in over[0]["message"]
    assert isinstance(_one(arrive="2026/10/05 9:00", start="2026/10/05 10:00", end="2026/10/05 9:30", handler="荷主側"), list)


def test_year_end_with_yearless_dates():
    assert _one(date="2026-12-31", arrive="12/31 23:00", start="1/1 1:00")["wait_min"] == 120


# ---------------------------------------------------------------- W3・W4
def test_place_is_nfkc_normalized_so_full_and_half_width_are_one_place():
    res = waiting.calc([_row(place="Ａ倉庫"), _row(place="A倉庫")], 2026)
    assert {r["place"] for r in res["rows"]} == {"A倉庫"} and len(res["hotspots"]) == 1 and len(res["by_place_month"]) == 1


@pytest.mark.parametrize("text,ok", [("R1.1.5", False), ("令和元年4月1日", False), ("令和元年5月1日", True), ("H1.1.7", False), ("平成元年1月8日", True),
                                     ("S1.12.24", False), ("昭和元年12月25日", True), ("平成31年4月30日", True)])
def test_era_dates_before_the_era_began_are_unreadable(text, ok):
    assert (waiting.parse_date(text, 2026) is not None) is ok


# ---------------------------------------------------------------- N4
def test_post_wait_beyond_two_hours_is_counted_from_the_start_of_waiting_including_handling():
    # 到着 9:00、待機 30 分、運転者の荷役 120 分（〜11:30）、荷役のあとの待ち 60 分（〜12:30）。境は 11:00。
    r = _one(start="09:30", end="11:30", depart="12:30", handler="運転者", method="手積み", post_wait="はい")
    assert r["wait_min"] == 90 and r["wait_normal_units"] == 0 and r["wait_over_units"] == 2
    # 荷役のあとの待ちが境の手前で終わるなら通常の額
    r2 = _one(start="09:30", end="09:50", depart="11:30", handler="運転者", method="手積み", post_wait="はい")
    assert r2["wait_min"] == 130 and r2["wait_over_units"] == 1 and r2["wait_normal_units"] == 3
    # 荷主側の荷役は待機の続きなので、従来どおり合計で数える
    r3 = _one(start="09:30", end="10:10", depart="12:30", handler="荷主側", post_wait="はい")
    assert r3["wait_min"] == 210 and r3["wait_normal_units"] == 3 and r3["wait_over_units"] == 3


def test_spec_records_the_n4_correction():
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    assert "## 25.6 の訂正（2026-11-06" in spec and "別紙 V" in spec


# ---------------------------------------------------------------- 試験表
def test_vectors_file_is_current_and_has_the_new_cases():
    built = build_waiting_vectors.build()
    assert json.loads(VECTORS.read_text(encoding="utf-8")) == json.loads(json.dumps(built, ensure_ascii=False))
    names = {c["name"] for c in built["cases"]}
    assert {"dated_cells_use_actual_difference", "dated_start_before_arrival_is_input_error", "place_is_nfkc_normalized",
            "era_dates_before_the_era_began_are_errors", "post_wait_boundary_counts_from_start_of_waiting_including_handling"} <= names


# ---------------------------------------------------------------- 画面（Edge）
def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except (Exception, SystemExit):  # noqa: BLE001  (見つからないと SystemExit)
        pytest.skip("Microsoft Edge が見つかりません")


def test_js_selftest_passes_with_the_new_cases():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri() + "?selftest=1")
        page.wait(lambda: page.js("!!document.querySelector('#selftest') && document.querySelector('#selftest').textContent.length > 0"), 60, "自己確認")
        text = page.js("document.querySelector('#selftest').textContent")
    assert text.startswith("SELFTEST PASS"), text[:400]


MINE = [{"date": "2026-10-20", "vehicle_class": "中型", "partner": "自分の元請", "place": "自分の倉庫", "arrive": "09:00", "start": "10:00", "shipper_fault": "はい"},
        {"date": "2026-10-21", "vehicle_class": "大型", "partner": "自分の元請", "place": "自分の倉庫", "arrive": "09:00", "start": "09:50", "shipper_fault": "はい"}]


def _seed(page, rows):
    page.goto(PAGE.as_uri())
    page.js(f"localStorage.setItem('{KEY}', JSON.stringify({{year: 2026, rows: {json.dumps(rows, ensure_ascii=False)}}})); localStorage.removeItem('{PREV}')")
    page.goto(PAGE.as_uri())
    page.wait(lambda: page.js("!!document.querySelector('#b-example')"), 30, "ページ")


def test_f1_example_button_asks_before_replacing_saved_records_and_can_be_undone():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _seed(page, MINE)
        assert page.js("document.querySelectorAll('#tbody tr').length") == 2
        # 1 回目: 確認の表示だけ。記録も保存も変わらない
        page.js("document.querySelector('#b-example').click()")
        assert "もう一度" in page.js("document.querySelector('#b-example').textContent")
        assert page.js("document.querySelectorAll('#tbody tr').length") == 2
        assert page.js(f"JSON.parse(localStorage.getItem('{KEY}')).rows.length") == 2
        # 別のボタンを押すと確認は取り消される
        page.js("document.querySelector('#b-add').click()")
        assert page.js("document.querySelector('#b-example').textContent") == "例を入れる"
        page.js("document.querySelector('#b-example').click()")   # もう一度 1 回目
        assert "もう一度" in page.js("document.querySelector('#b-example').textContent")
        # 2 回目: 入れ替える。前の記録は別の鍵に残り、元に戻すが出る
        page.js("document.querySelector('#b-example').click()")
        page.wait(lambda: page.js("document.querySelectorAll('#tbody tr').length") == 18, 20, "例")
        assert page.js("document.querySelector('#b-example').textContent") == "例を入れる"
        assert page.js("document.querySelector('#undo-note').hidden") is False
        assert page.js(f"JSON.parse(localStorage.getItem('{PREV}')).rows.length") == 2
        page.js("document.querySelector('#b-undo').click()")
        page.wait(lambda: page.js("document.querySelectorAll('#tbody tr').length") == 2, 20, "元に戻す")
        assert page.js("document.querySelector('#tbody tr input[data-k=partner]').value") == "自分の元請"
        assert page.js("document.querySelector('#undo-note').hidden") is True
        page.wait(lambda: page.js(f"JSON.parse(localStorage.getItem('{KEY}')).rows[0].partner") == "自分の元請", 10, "保存")
        assert page.js(f"localStorage.getItem('{PREV}')") is None


def test_f1_example_button_needs_no_confirmation_on_the_auto_example():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri())
        page.js("localStorage.clear()")
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("document.querySelectorAll('#tbody tr').length") == 18, 30, "自動の例")
        page.js("document.querySelector('#b-example').click()")
        assert page.js("document.querySelector('#b-example').textContent") == "例を入れる"
        assert page.js("document.querySelector('#undo-note').hidden") is True


def test_f1_example_query_does_not_overwrite_saved_records():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        _seed(page, MINE)
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("document.querySelectorAll('#tbody tr').length") == 18, 30, "例")
        page.js("document.querySelector('#b-add').click()")   # 触っても保存しない
        time.sleep(0.8)
        saved = page.js(f"JSON.parse(localStorage.getItem('{KEY}')).rows")
        assert len(saved) == 2 and saved[0]["partner"] == "自分の元請"
        # 保存した記録が無いときも、例を見るだけ（自動の例と同じで保存しない）
        page.js(f"localStorage.removeItem('{KEY}')")
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("document.querySelectorAll('#tbody tr').length") == 18, 30, "例")
        assert page.js(f"localStorage.getItem('{KEY}')") is None


def test_f8_date_column_shows_the_whole_date_at_1920():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("document.querySelectorAll('#tbody tr').length") == 18, 30, "例")
        info = page.js("(function(){ const i = document.querySelector('#tbody input[data-k=date]'); return {v: i.value, sw: i.scrollWidth, cw: i.clientWidth}; })()")
        assert info["v"] == "2026-10-01" and info["sw"] <= info["cw"], info
