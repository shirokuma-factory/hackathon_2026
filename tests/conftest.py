"""試験で共通の部品。"""

from __future__ import annotations

from datetime import date

import pytest

from shipcompare.models import Item, Shipment


def make_shipment(items=None, route="kanto_kansai", **kw) -> Shipment:
    base = dict(
        destinations=1, sender_type="法人", receiver_type="法人", pickup="集荷", delivery="配達",
        ship_date=date(2026, 11, 10), desired_date=date(2026, 11, 12),
    )
    base.update(kw)
    return Shipment(items=items if items is not None else [Item(1, 30, 30, 30, 10)], route=route, **base)


def one(count=1, l=30, w=30, h=30, kg=10) -> list[Item]:
    return [Item(count, l, w, h, kg)]


def row_of(rows, mode):
    return next(r for r in rows if r.mode == mode)


@pytest.fixture
def today() -> date:
    return date(2026, 11, 3)
