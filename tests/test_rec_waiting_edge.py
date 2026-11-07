"""荷待ち計算 waiting.calc の「日付の無い逆転の時刻」の境界の試験。

日付の無い時刻が直前の時刻より小さいときは翌日として読む。
その差がちょうど 720 分（12 時間）なら受け入れ、721 分（12 時間 1 分）なら誤りの行にする。
境界は 3 か所（荷役開始と到着、荷役終了と荷役開始、出発と荷役終了または荷役開始）にあり、
それぞれの 720 分と 721 分を確かめる。定数 REVERSE_MAX_MIN は参照せず、数値を直接書く。
日付つきの時刻には、この上限をかけない。
"""

from __future__ import annotations

import pytest

from shipcompare import waiting

BASE = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A", "shipper_fault": "はい"}


def _calc(**kw):
    row = dict(BASE)
    row.update(kw)
    return waiting.calc([row], 2026)


def _assert_ok(res):
    assert res["errors"] == [] and len(res["rows"]) == 1
    return res["rows"][0]


def _assert_error(res, *parts):
    assert res["rows"] == [] and len(res["errors"]) == 1
    message = res["errors"][0]["message"]
    for part in parts:
        assert part in message, (part, message)


# ---------------------------------------------------------------- 荷役開始と到着
def test_start_before_arrive_exactly_720_min_is_accepted():
    row = _assert_ok(_calc(arrive="12:00", start="00:00"))
    assert row["wait_raw_min"] == 720 and row["crosses_midnight"] is True


def test_start_before_arrive_721_min_is_an_error():
    res = _calc(arrive="12:00", start="00:01")
    _assert_error(res, "荷役開始（00:01）が到着（12:00）より前です", "12 時間 1 分")


# ---------------------------------------------------------------- 荷役終了と荷役開始
HANDLING = {"handler": "運転者", "method": "手積み"}


def test_end_before_start_exactly_720_min_is_accepted():
    row = _assert_ok(_calc(arrive="19:00", start="20:00", end="08:00", **HANDLING))
    assert row["handling_min"] == 720


def test_end_before_start_721_min_is_an_error():
    res = _calc(arrive="19:00", start="20:00", end="08:01", **HANDLING)
    _assert_error(res, "荷役終了（08:01）が荷役開始（20:00）より前です", "12 時間 1 分")


# ---------------------------------------------------------------- 出発と荷役終了（荷役終了がある行）
def test_depart_before_end_exactly_720_min_is_accepted():
    row = _assert_ok(_calc(arrive="19:00", start="19:30", end="20:00", depart="08:00", **HANDLING))
    assert row["depart"] == "08:00"


def test_depart_before_end_721_min_is_an_error():
    res = _calc(arrive="19:00", start="19:30", end="20:00", depart="08:01", **HANDLING)
    _assert_error(res, "出発（08:01）が荷役終了（20:00）より前です", "12 時間 1 分")


# ---------------------------------------------------------------- 出発と荷役開始（荷役終了が無い行）
def test_depart_before_start_without_end_exactly_720_min_is_accepted():
    _assert_ok(_calc(arrive="19:00", start="20:00", depart="08:00"))


def test_depart_before_start_without_end_721_min_is_an_error():
    res = _calc(arrive="19:00", start="20:00", depart="08:01")
    _assert_error(res, "出発（08:01）が荷役開始（20:00）より前です", "12 時間 1 分")


# ---------------------------------------------------------------- 日付つきの時刻には上限をかけない
def test_dated_times_are_not_limited_to_720_min():
    row = _assert_ok(_calc(arrive="2026/10/05 12:00", start="2026/10/06 00:01"))
    assert row["wait_raw_min"] == 721


def test_dated_end_is_not_limited_to_720_min():
    # 荷役終了に日付があれば、荷役開始との差が 721 分でも誤りにしない
    row = _assert_ok(_calc(arrive="19:00", start="20:00", end="2026/10/06 08:01", **HANDLING))
    assert row["handling_min"] == 721


def test_dated_depart_with_end_is_not_limited_to_720_min():
    # 荷役終了がある行で、出発に日付があれば、荷役終了との差が 721 分でも誤りにしない
    row = _assert_ok(
        _calc(arrive="19:00", start="19:30", end="20:00", depart="2026/10/06 08:01", **HANDLING)
    )
    assert row["depart"] == "08:01"


def test_dated_depart_without_end_is_not_limited_to_720_min():
    # 荷役終了が無い行で、出発に日付があれば、荷役開始との差が 721 分でも誤りにしない
    row = _assert_ok(_calc(arrive="19:00", start="20:00", depart="2026/10/06 08:01"))
    assert row["depart"] == "08:01"
