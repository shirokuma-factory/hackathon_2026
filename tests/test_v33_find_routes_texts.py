"""SPEC §33 #2・#4・#5・#6・#7・#8・#9・#12・#14 の静的な確認（画面は Edge の写真と console の記録で確かめる）。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_demo_trips_have_capacity_for_price_formula():
    d = json.loads(_read(ROOT / "data" / "demo" / "carriers.json"))
    for t in d["trips"]:
        assert t["max_load_kg"] >= t["weight_slack_kg"] > 0, t["id"]
        assert t["max_load_kg"] <= 10000, t["id"]  # 距離制運賃の表の最大の車格（10t）に載る
        assert t["floor_total_m2"] > 0, t["id"]


def test_price_formula_works_for_every_demo_trip():
    from decimal import Decimal

    from shipcompare.spaceb import _num  # noqa: F401  (存在の確認)

    d = json.loads(_read(ROOT / "data" / "demo" / "carriers.json"))
    for t in d["trips"]:
        assert _num(t.get("max_load_kg")) and _num(t.get("floor_total_m2")), t["id"]
        assert Decimal(str(t["max_load_kg"])) > 0


def test_pages_have_data_favicon_and_t7_source():
    for name in ("waiting_page_template.html", "compare_lite_template.html"):
        assert 'rel="icon" href="data:image/svg+xml' in _read(ROOT / "tools" / name), name
    for name in ("waiting.html", "compare_lite.html"):
        assert 'rel="icon" href="data:image/svg+xml' in _read(WEB / name), name
    src = "空きが重さ 2,000kg・床 6.0m²（車両は最大 2,700kg・床 13.64m²）"
    assert src in _read(ROOT / "tools" / "compare_lite_template.html")
    assert src in _read(WEB / "compare_lite.html")


def test_routes_labels_and_cards():
    js = _read(WEB / "routes.js")
    assert "rail: '鉄道'" in js and "rail: '電車'" not in js
    assert "ferry: '船'" in js
    assert "なし（つながる航路がありません）" in js
    assert "貨物駅を回るため、道路より長くなります" in js and "s.km > 2 * tk.km" in js
    assert "function modeState(m)" in js and "modeState," in js
    # 画面に出す文で「電車」「フェリー」を手段の名前に使わない（航路・経路の説明の「フェリー」は残してよい）
    assert "'電車'" not in js


def test_routes_keep_vehicle_layers_alive():
    js = _read(WEB / "routes.js")
    assert "cur.sets[m] && cur.sets[m].ok)) out.push(...vehLayers('rt-veh-'" in js
    assert "R.leadMode" in js and "rt-lead-empty" in js


def test_request_step_pointer_links_and_hides_empty_hint():
    js = _read(WEB / "work.js")
    assert 'href="compare_lite.html"' in js
    assert "${rows ? '<span class=\"hint\">会社の五十音順（確認日つき）</span>' : ''}" in js
    from shipcompare.guide import CONTACT_POINTER

    assert "比べるページ" in CONTACT_POINTER


def test_find_tags_avoid_notes_and_list():
    js = _read(WEB / "find.js")
    assert "'#find-notes', '#find-right'" in js and "m.mk.setOffset([dx, dy])" in js
