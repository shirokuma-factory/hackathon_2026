"""JARTIC 大型車の集計（SPEC §16.4、受入条件 G40）。通信はしない。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    spec = importlib.util.spec_from_file_location("fetch_jartic", ROOT / "tools" / "fetch_jartic.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fetch_jartic"] = mod
    spec.loader.exec_module(mod)
    return mod


T = load_tool()
HOURS = [202610270000, 202610270100, 202610270200, 202610280000, 202610280100, 202610280200]


def prop(code, up, down, **flags):
    d, t = divmod(code, 10000)
    p = {"観測年月日": d, "時間帯": t, "上り・大型交通量": up, "下り・大型交通量": down}
    p.update(flags)
    return p


def fixture():
    return [
        prop(202610270000, 10, 20),
        prop(202610270100, 30, 40),
        # 202610270200 は応答に無い（欠測）
        prop(202610280000, "", 5),                                  # 上りが空欄（欠測）
        prop(202610280100, 8, 4, **{"下り・欠測": "1"}),             # 下りに欠測フラグ
        prop(202610280200, 6, 3),
    ]


def test_spec16_4_missing_hours_are_none_not_zero():
    up, down = T.hourly_series(fixture(), HOURS)
    assert up == [10, 30, None, None, 8, 6]
    assert down == [20, 40, None, 5, None, 3]
    assert 0 not in up and 0 not in down


def test_spec16_4_daily_totals_skip_missing():
    up, down = T.hourly_series(fixture(), HOURS)
    agg = T.aggregate(HOURS, up, down)
    d1, d2 = agg["daily"]
    assert (d1["date"], d1["up_total"], d1["down_total"], d1["missing_hours"]) == (20261027, 40, 60, 1)
    assert (d2["date"], d2["up_total"], d2["down_total"], d2["missing_hours"]) == (20261028, 14, 8, 2)
    assert agg["up_total"] == 54 and agg["down_total"] == 68
    assert agg["missing_hours"] == 3 and agg["observed_hours"] == 3


def test_spec16_4_ratio_uses_only_hours_with_both_directions():
    up, down = T.hourly_series(fixture(), HOURS)
    agg = T.aggregate(HOURS, up, down)
    # 上りと下りがそろう時間は 0 時・1 時・28日2時 の3つだけ: 上り 10+30+6=46、下り 20+40+3=63
    assert agg["ratio"] == round(46 / 63, 3)
    assert agg["daily"][0]["ratio"] == round(40 / 60, 3)
    assert agg["daily"][1]["ratio"] == 2.0


def test_spec16_4_ratio_none_when_no_data_or_zero_down():
    assert T.ratio(5, 0) is None
    assert T.ratio(None, 5) is None
    agg = T.aggregate(HOURS[:2], [None, None], [None, None])
    assert agg["ratio"] is None and agg["up_total"] is None and agg["missing_hours"] == 2
    assert agg["daily"][0]["up_total"] is None  # 0 ではなく None


def test_spec16_4_zero_count_is_real_zero():
    up, down = T.hourly_series([prop(HOURS[0], 0, 7)], HOURS[:1])
    assert up == [0] and down == [7]
    assert T.aggregate(HOURS[:1], up, down)["ratio"] == 0.0


def test_spec16_4_5min_fallback_needs_all_12_slots():
    h = HOURS[1]
    full = [prop(h + m, 1, 2) for m in range(0, 60, 5)]
    up, down = T.hourly_from_5min(full, [HOURS[0], h])
    assert up == [None, 12] and down == [None, 24]
    up, down = T.hourly_from_5min(full[:-1], [h])
    assert up == [None] and down == [None]


def test_spec16_4_output_file_has_attribution_and_nulls_kept():
    p = ROOT / "data" / "traffic" / "heavy_updown.json"
    if not p.exists():
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["attribution"] == "国土交通省 API 機能による交通量(参考値)を加工して作成"
    n = len(data["hours"])
    for pt in data["points"]:
        assert len(pt["up"]) == len(pt["down"]) == n
        assert pt["missing_hours"] == sum(1 for u, d in zip(pt["up"], pt["down"]) if u is None or d is None)
