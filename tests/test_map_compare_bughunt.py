"""送り方の不具合探し（docs/reviews/2026-11-08_不具合探し_送り方.md）の S06〜S12 の回帰試験。

JS の確認は node で行う（Edge は使わない）。node が無い環境ではその確認を飛ばす。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import make_shipment, row_of
from shipcompare.estimate import (
    JITBOX_LABEL, ROSEN_LABEL, ST_NOCALC, ST_UNUSABLE, TAKUHAI_LABEL, estimate_all, row_label,
)
from shipcompare.models import ROUTE_OTHER, Item
from shipcompare.request import build_request
from shipcompare.tariffs import load_sample_tariffs
from test_v3_web import post, srv  # noqa: F401  (srv は fixture)

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
SAMPLE = load_sample_tariffs()
NODE = shutil.which("node")
STUB = "global.document={addEventListener(){}};global.Rt=undefined;global.window={};"


def run_node(script: str) -> str:
    if not NODE:
        pytest.skip("node がありません")
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def chart_html(cards: list[dict]) -> str:
    script = STUB + read("compare_chart.js") + f";console.log(JSON.stringify(CompareChart.html({json.dumps(cards, ensure_ascii=False)},null,false)))"
    return json.loads(run_node(script))


def card(label: str, amount, status: str, **kw) -> dict:
    return {"label": label, "amount": amount, "status": status, "sub": "", **kw}


# ---------------------------------------------------------------- S06 貸切は条件に合う車格まで全部カードにする
ROWS_OF_MODE = re.search(r"function rowsOfMode\(group, rows\) \{.*?\n\}\n", read("app.js"), re.S).group(0)


def test_s06_charter_cards_include_every_class_up_to_the_first_fit():
    rows = [
        {"group": "kashikiri", "mode": "kashikiri_2t", "status": "要確認", "amount": 109750},
        {"group": "kashikiri", "mode": "kashikiri_4t", "status": "公開条件に合う", "amount": 126060},
        {"group": "kashikiri", "mode": "kashikiri_10t", "status": "公開条件に合う", "amount": 167860},
        {"group": "takuhai", "mode": "takuhai", "status": "使えない", "amount": None},
    ]
    out = run_node(ROWS_OF_MODE + f"const rows={json.dumps(rows)};"
                   "console.log(JSON.stringify([rowsOfMode('kashikiri',rows).map(r=>r.mode),rowsOfMode('takuhai',rows).map(r=>r.mode)]))")
    assert json.loads(out) == [["kashikiri_2t", "kashikiri_4t"], ["takuhai"]]   # 10t は 4t が合うので出さない。安い順のまま


def test_s06_charter_cards_fall_back_to_first_row_when_nothing_is_usable():
    rows = [{"group": "kashikiri", "mode": "kashikiri", "status": "使えない", "amount": None}]
    assert run_node(ROWS_OF_MODE + f"console.log(rowsOfMode('kashikiri',{json.dumps(rows)}).length)") == "1"


def test_s06_charter_pick_value_keeps_the_class_and_the_request_uses_only_the_mode_name():
    assert "const pickOf = (c) => c.pick || c.label;" in read("compare_chart.js")
    assert 'value="${esc(pickOf(c))}"' in read("compare_chart.js")
    assert "String(v).split(' ')[0]" in read("work.js")   # 「貸切 4t車」は API には「貸切」で渡す（MODE_REQUEST_TEXT のキー）
    assert "`${m.label} ${r.detail.vehicle}車`" in read("app.js")
    # 2 つの貸切の行が別々の丸ボタンになる
    html = chart_html([card("貸切", 109750, "要確認", sub="2t車", pick="貸切 2t車"),
                       card("貸切", 126060, "公開条件に合う", sub="4t車", pick="貸切 4t車")])
    assert 'value="貸切 2t車"' in html and 'value="貸切 4t車"' in html


# ---------------------------------------------------------------- S07 額のある要確認の手段を「料金表がない」と書かない
def test_s07_only_text_does_not_say_no_table_for_a_priced_cart():
    html = chart_html([card("貸切", 126060, "公開条件に合う"), card("カゴ台車", 23100, "要確認"),
                       card("宅配", None, "計算できない"), card("路線便", None, "計算できない")])
    m = re.search(r'<p class="cc-only">(.*?)</p>', html)
    assert m, html
    assert "カゴ台車" not in m.group(1)   # 額が出ている行は「料金表がない」に数えない
    assert "宅配・路線便は料金表がないため" in m.group(1)


# ---------------------------------------------------------------- S08 内寸を超える 1 個のカゴ台車は使えない
def test_s08_jitbox_is_unusable_when_one_piece_exceeds_the_inner_size():
    for item in (Item(1, 110, 110, 150, 450), Item(1, 350, 30, 30, 20), Item(18, 110, 110, 120, 500)):
        r = row_of(estimate_all(make_shipment([item]), tariffs=SAMPLE), "jitbox")
        assert r.status == ST_UNUSABLE and r.amount is None, item
        assert "1個がボックスの内寸を超えます" in r.reasons[0] and "使えません" in r.reasons[0]
    ok = row_of(estimate_all(make_shipment([Item(1, 100, 100, 100, 20)]), tariffs=SAMPLE), "jitbox")
    assert ok.status != ST_UNUSABLE and ok.amount is not None   # 入る荷物は従来どおり


# ---------------------------------------------------------------- S09 近距離の貸切は「使えない」ではなく「額を出せない」
def test_s09_charter_under_200km_is_not_calculable_rather_than_unusable():
    s = make_shipment([Item(1, 110, 110, 150, 450)], route=ROUTE_OTHER, distance_km=6.3, origin_in_kanto=True)
    rows = [r for r in estimate_all(s, tariffs=SAMPLE) if r.mode.startswith("kashikiri")]
    assert len(rows) == 1
    r = rows[0]
    assert r.status == ST_NOCALC and r.amount is None
    assert r.reasons == ["200km 未満は表の範囲外のため、額は見積で確かめてください"]


# ---------------------------------------------------------------- S10 船・飛行機の依頼文に届け先の名前を書く
def _naha_shipment():
    return make_shipment([Item(1, 40, 40, 40, 10)], route=ROUTE_OTHER, distance_km=None, origin_in_kanto=True)


def test_s10_request_text_names_the_destination_when_distance_is_unknown():
    place = {"label": "新宿区 → 那覇市", "km": None, "note": None}
    d = build_request(_naha_shipment(), None, place, "飛行機")
    assert "発着: 新宿区 → 那覇市" in d.text
    assert "距離 不明" not in d.text and "その他（距離" not in d.text
    assert "距離 km" not in d.missing_required   # 船・飛行機は距離を必須にしない
    assert "距離 km" in build_request(_naha_shipment(), None, place, "貸切").missing_required   # トラックは従来どおり


def test_s10_api_accepts_a_place_without_km(srv):  # noqa: F811
    _, port = srv
    sh = {"items": [{"count": 1, "length_cm": 40, "width_cm": 40, "height_cm": 40, "weight_kg": 10}],
          "route": "other", "origin_in_kanto": True, "destinations": 1, "sender_type": "法人", "receiver_type": "法人",
          "pickup": "集荷", "delivery": "配達"}
    status, body = post(port, "/api/request", {"shipment": sh, "place": {"label": "新宿区 → 那覇市", "km": None}, "mode": "船"})
    assert status == 200, body
    assert "発着: 新宿区 → 那覇市" in body["text"] and "距離 不明" not in body["text"]
    status, _ = post(port, "/api/request", {"shipment": sh, "place": {"label": "x", "km": -1}, "mode": "船"})
    assert status == 400   # km が 0 以下なら今までどおり拒否


def test_s10_work_js_sends_the_label_for_ferry_destinations():
    work = read("work.js")
    assert "const kmKnown = !!(g && g.km && !g.no_route && !g.ferry);" in work
    assert "(g && (g.no_route || g.ferry))" in work and "km: kmKnown ? g.km : null" in work


# ---------------------------------------------------------------- S11 航路の表に無いだけの船は、見積を頼む形で選べる
def test_s11_ship_without_route_data_is_selectable_with_a_request_message():
    html = chart_html([card("貸切", 52000, "公開条件に合う"), card("船", None, "計算できない", routeNone=True, dim=True)])
    assert "このデモの航路の表に入っていません。船会社に見積を頼んでください。" in html
    assert "つながる航路がありません" not in html
    assert 'value="船"' in html   # 選べる（依頼文を作れる）


# ---------------------------------------------------------------- S12 架空の表のときは名前にも「架空の表」と出す
def test_s12_labels_say_fictional_table_when_sample_tariffs_are_used():
    rows = estimate_all(make_shipment([Item(1, 20, 20, 20, 2)]), tariffs=SAMPLE)
    for mode in ("takuhai", "rosen", "jitbox"):
        label = row_of(rows, mode).label
        assert "架空の表からの例" in label and "実在の運賃ではありません" in label and "公表運賃" not in label, label
    assert row_of(rows, "takuhai").label.startswith("宅配便（")
    # 実在の表のときは今までの名前
    assert row_label(TAKUHAI_LABEL, {"is_sample": False}) == TAKUHAI_LABEL
    assert row_label(ROSEN_LABEL, {}) == ROSEN_LABEL and row_label(JITBOX_LABEL, {}) == JITBOX_LABEL
