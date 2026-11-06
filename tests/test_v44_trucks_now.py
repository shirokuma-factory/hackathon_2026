"""「いまの大型車」の取得と集計（tools/fetch_trucks_now.py）の試験。通信しない。合成した地点だけを使う。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fetch_trucks_now as ft  # noqa: E402

HOURS = ft.hours_ending_at(202611062100)
WEEK = ft.shift_code(202611062100, -24 * 7)


def feat_1h(pid=1, up=10, down=20, **extra):
    props = {
        "常時観測点コード": pid, "時間コード": 202611062100,
        "上り・大型交通量": up, "下り・大型交通量": down, "上り・小型交通量": 100, "下り・小型交通量": 200,
        "上り・欠測": "0", "上り・停電": "0", "上り・ループ異常": "0", "上り・超音波異常": "0",
        "下り・欠測": "0", "下り・停電": "0", "下り・ループ異常": "0", "下り・超音波異常": "0",
    }
    props.update(extra)
    return {"properties": props, "geometry": {"type": "MultiPoint", "coordinates": [[139.7, 35.68]]}}


def feat_cctv(pid=2, up=5, down=6, up_flag="0", down_flag="0"):
    props = {
        "常時観測点コード": pid, "時間コード": 202611062100,
        "上り・大型交通量": up, "下り・大型交通量": down, "上り・小型交通量": 50, "下り・小型交通量": 60,
        "上り・5分欠測処理フラグ": up_flag, "下り・5分欠測処理フラグ": down_flag,
    }
    return {"properties": props, "geometry": {"type": "MultiPoint", "coordinates": [[139.7, 35.68]]}}


def test_normal_feature_has_values():
    r = ft.parse_feature(feat_1h(), "常設")
    assert (r["up_heavy"], r["down_heavy"], r["up_small"], r["down_small"]) == (10, 20, 100, 200)
    assert (r["lon"], r["lat"]) == (139.7, 35.68)


def test_flagged_or_empty_becomes_null_not_zero():
    r = ft.parse_feature(feat_1h(**{"上り・欠測": "1"}), "常設")
    assert r["up_heavy"] is None and r["up_small"] is None
    assert r["down_heavy"] == 20
    r = ft.parse_feature(feat_1h(up=None), "常設")
    assert r["up_heavy"] is None
    r = ft.parse_feature(feat_1h(**{"下り・ループ異常": "1"}), "常設")
    assert r["down_heavy"] is None


def test_zero_count_is_kept_as_zero():
    r = ft.parse_feature(feat_1h(up=0), "常設")
    assert r["up_heavy"] == 0


def test_cctv_flag_rule():
    assert ft.parse_feature(feat_cctv(up=None, down=None, up_flag="2", down_flag="2"), "CCTV")["up_heavy"] is None
    r = ft.parse_feature(feat_cctv(up=7, up_flag="2"), "CCTV")  # フラグ 2 は台数があっても欠測
    assert r["up_heavy"] is None and r["down_heavy"] == 6
    r = ft.parse_feature(feat_cctv(up_flag="1"), "CCTV")  # フラグ 1 は台数を使う
    assert r["up_heavy"] == 5 and r["flag1"] is True
    assert ft.parse_feature(feat_cctv(up=None), "CCTV")["up_heavy"] is None


def test_pair_total_needs_both_directions():
    assert ft.pair_total(10, 20) == 30
    assert ft.pair_total(0, 0) == 0
    assert ft.pair_total(None, 20) is None
    assert ft.pair_total(10, None) is None


def test_hour_codes():
    assert HOURS[-1] == 202611062100 and len(HOURS) == 24 and HOURS[0] == 202611052200
    assert ft.shift_code(202611010100, -2) == 202610312300
    assert WEEK == 202610302100


# ---------- 県の割り当て ----------

def _index():
    return ft.PrefIndex(json.loads((ROOT / "web" / "data" / "pref.geojson").read_text(encoding="utf-8")))


def test_pref_inside_tokyo():
    assert _index().find(139.70, 35.68) == "東京都"


def test_pref_at_sea_is_none():
    assert _index().find(150.0, 30.0) is None


def test_pref_near_coast_snaps():
    idx = _index()
    # 東京湾の中央あたり（陸から 20 km 以内）はいずれかの県に寄る
    assert idx.find(139.85, 35.45) is not None


# ---------- 集計 ----------

def point(pid, series, week_ago=None, pref="東京都", small=None, up=None, down=None):
    return {"id": pid, "type": "常設", "lon": 139.7, "lat": 35.68, "pref": pref, "up": up, "down": down,
            "small": small, "series": series, "week_ago": week_ago}


def rec(up_h, down_h, up_s=100, down_s=200):
    return {"up_heavy": up_h, "down_heavy": down_h, "up_small": up_s, "down_small": down_s, "flag1": False}


def test_build_points_drops_all_null_series_and_keeps_nulls():
    records = {
        ("常設", 1): {"lon": 139.7, "lat": 35.68, "by_hour": {HOURS[-1]: rec(10, 20), HOURS[-2]: rec(None, 5), WEEK: rec(8, 12)}},
        ("常設", 2): {"lon": 139.7, "lat": 35.68, "by_hour": {HOURS[-1]: rec(None, None)}},
        ("CCTV", 3): {"lon": 139.7, "lat": 35.68, "by_hour": {}},
    }
    pts = ft.build_points(records, HOURS, WEEK, lambda lon, lat: "東京都")
    assert [p["id"] for p in pts] == [1]
    p = pts[0]
    assert p["series"][-1] == 30 and p["series"][-2] is None and p["series"][0] is None
    assert p["week_ago"] == 20 and p["small"] == 300 and (p["up"], p["down"]) == (10, 20)


def test_prefecture_average_uses_only_observed_points():
    na = [None] * 23
    pts = [point(1, na + [100]), point(2, na + [50]), point(3, [10] * 23 + [None])]
    s = ft.summarize(pts)
    assert s["n_points"] == 3 and s["n_observed"] == 2
    assert s["avg_heavy"] == 75.0  # 点 3 は最新が欠測なので、0 として混ぜない
    assert s["series_avg"][0] == 10.0  # 最初の時間は点 3 だけが観測
    assert s["series_avg"][-1] == 75.0


def test_series_average_none_when_nobody_observed():
    assert ft.series_average([point(1, [None] * 24)])[0] is None


def test_week_ratio_null_under_three_common_points():
    na = [None] * 23
    pts = [point(1, na + [100], week_ago=50), point(2, na + [100], week_ago=50), point(3, na + [100], week_ago=None)]
    assert ft.week_ratio(pts) is None
    pts.append(point(4, na + [60], week_ago=50))
    # 点 3 は先週が無いので除く: (100+100+60) / (50+50+50)
    assert ft.week_ratio(pts) == round(260 / 150, 3)


def test_heavy_share_uses_points_with_both_values():
    na = [None] * 23
    pts = [point(1, na + [30], small=70), point(2, na + [30], small=None), point(3, na + [None], small=100)]
    assert ft.heavy_share(pts) == 0.3


def test_build_prefs_sorted_and_excludes_null_pref():
    na = [None] * 23
    pts = [point(1, na + [10], pref="A県"), point(2, na + [90], pref="B県"), point(3, na + [999], pref=None)]
    prefs = ft.build_prefs(pts)
    assert [p["pref"] for p in prefs] == ["B県", "A県"]
    assert prefs[0]["avg_heavy"] == 90.0 and prefs[0]["n_points"] == 1


def test_loop_below_20_is_rejected(monkeypatch):
    import pytest
    monkeypatch.setattr(sys, "argv", ["fetch_trucks_now.py", "--loop", "5"])
    with pytest.raises(SystemExit) as e:
        ft.main()
    assert e.value.code == 2
