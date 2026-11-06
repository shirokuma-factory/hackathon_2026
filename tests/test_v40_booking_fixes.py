"""SPEC §39 道具の採点 r2 の修正（予約・保存・積付け）。

- 積載量を超える荷には参考額を出さず「この車に載りません」を返す（ちょうど 100% は通る）。
- 便の登録は、タイムゾーン付きの日時を盤の時計（日本時間）に直す。
- 空・壊れた保存ファイルは日本語の ValueError。
- 容積 90% 以上の △ には理由が付く。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from shipcompare import loadplan, spaceb, storage

from test_v21_backhaul import s6_body, s6_ship, t7


def _ship_kg(total_kg: int):
    body = s6_body()
    body["shipment"] = {"items": [{"count": 1, "length_cm": 100, "width_cm": 100, "height_cm": 100, "weight_kg": total_kg}]}
    return spaceb.parse_shipment(body)


def test_weight_exactly_100_percent_gets_price_and_above_is_refused():
    t = t7()
    assert t["max_load_kg"] == 2700
    ok = spaceb.per_unit_reference(t, _ship_kg(2700), "0.7", "0", "近畿")
    assert ok["weight_ratio"] == 1.0 and ok["reference_yen"] > 0
    with pytest.raises(ValueError, match="この車に載りません"):
        spaceb.per_unit_reference(t, _ship_kg(2701), "0.7", "0", "近畿")


def test_default_register_price_refuses_overweight_cargo():
    t = dict(t7(), tariff_region="近畿")
    r = spaceb.default_register_price(t, _ship_kg(3000))
    assert r["price_ex_tax_yen"] is None and "この車に載りません" in r["basis"]


_BODY = dict(origin_code="13104", dest_code="28110", vehicle="4t", depart_at="2026-11-13T17:00",
             arrive_at="2026-11-14T10:00", weight_slack_kg=2000, unit_footprint_mm=[6000, 2000],
             clear_height_mm=2100, max_piece_kg=300, drop_points=["28110"])


def test_register_trip_converts_aware_datetime_to_jst():
    t = spaceb.build_registered_trip(dict(_BODY, depart_at="2026-11-13T08:00:00+00:00", arrive_at="2026-11-14T01:00:00Z"))
    assert t["depart_at"] == "2026-11-13T17:00" and t["arrive_at"] == "2026-11-14T10:00"


@pytest.mark.parametrize("content", [b"", b"\xff\xfe\x00\x01\x80", b'{"items": [', b"[]"])
def test_load_case_broken_file_is_japanese_value_error(tmp_path, content):
    p = tmp_path / "case.json"
    p.write_bytes(content)
    with pytest.raises(ValueError, match="保存ファイルが読めません"):
        storage.load_case(p)


def test_volume_90_percent_triangle_has_reason():
    c = loadplan.Container("example", "example", 4000, 4000, 1000, 1000, "公表例", "sample")
    r = loadplan.plan_load([loadplan.LoadItem(2, 300, 200, 100, 1, False), loadplan.LoadItem(1, 400, 100, 100, 1, False)], c)
    sm = r["summary"]
    assert sm["verdict"] == "△" and sm["volume_pct"] >= 90
    assert "容積の余裕が小さい" in sm["verdict_reason"] and "%" in sm["verdict_reason"]
