"""v5.0: 送り方を比べるの荷物を 4 項目にし、カゴ台車の説明、鉄道・船・飛行機の選択を足した。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import serve_web  # noqa: E402
from conftest import edge_page  # noqa: E402

SHIPMENT = {"items": [{"count": 12, "length_cm": 50, "width_cm": 40, "height_cm": 50, "weight_kg": 20}],
            "route": "kanto_kansai", "sender_type": "法人", "receiver_type": "法人", "pickup": "集荷", "delivery": "配達",
            "destinations": 1}


def test_request_text_includes_the_picked_mode():
    for label, word in (("鉄道", "鉄道（コンテナ）"), ("飛行機", "飛行機（航空便）"), ("船", "船（フェリー）"), ("カゴ台車", "カゴ台車 1 本単位")):
        out = serve_web.api_request({"shipment": SHIPMENT, "mode": label})
        assert f"検討している送り方: {word}" in out["text"], out["text"]
    out = serve_web.api_request({"shipment": SHIPMENT, "mode": "鉄道"})
    assert "日数と運賃をお知らせください" in out["text"]
    assert "検討している送り方" not in serve_web.api_request({"shipment": SHIPMENT})["text"]
    with pytest.raises(serve_web.BadRequest):
        serve_web.api_request({"shipment": SHIPMENT, "mode": "ヘリコプター"})


def test_jitbox_row_carries_boxes_and_fare_for_the_near_charter_line():
    out = serve_web.api_estimate({"origin_code": "13104", "dest_code": "27128", "shipment": SHIPMENT})
    row = next(r for r in out["rows"] if r["group"] == "jitbox")
    d = row["detail"]
    want = f"{d['boxes']} 本分（{d['fare_per_box']:,} 円 × {d['boxes']}）" if d["boxes"] > 1 else f"1 本分（{d['fare_per_box']:,} 円）"
    assert d["load_note"] == want


def _open(page, hash_):
    import conftest
    conftest.open_app(page, f"http://127.0.0.1:{conftest.server_port('public')}/index.html{hash_}",
                      ready_js="!!window.__app && !document.querySelector('#loading')", wait_loading=False, start_timeout=180)


def _card(label, amount=None, **kw):
    return {"label": label, "amount": amount, "status": "公開条件に合う" if amount else "問い合わせ", **kw}


def test_chart_renames_cage_row_explains_it_and_offers_rail_and_air():
    with edge_page(1920, 1080) as page:
        _open(page, "#v=compare&o=13104&l=km")
        h = lambda cards: page.js(f"CompareChart.html({json.dumps(cards, ensure_ascii=False)})")  # noqa: E731
        near = [_card("カゴ台車", 92000, note="4 本分（23,000 円 × 4）"), _card("貸切", 98000, sub="2t車"),
                _card("鉄道", None, status="計算できない"), _card("船", None, status="計算できない", routeNone=False)]
        html = h(near)
        assert "カゴ台車 1 本単位" in html
        assert "カゴ単位で払う便" in html   # 文言の直し 2026-11-07
        assert "4 本分（23,000 円 × 4）で、2t 車 1 台を貸し切るのに近い量です。" in html
        # 額の離れているときは理由の 1 行を出さない
        far = h([_card("カゴ台車", 20000, note="1 本分（20,000 円）"), _card("貸切", 98000, sub="2t車")])
        assert "近い量です" not in far and "cc-near" not in far
        # 鉄道・船・飛行機: 額は出さず、選べる。値は内部の名前のまま
        for lab in ("鉄道（コンテナ貨物）。金額は見積で確かめてください", "船（フェリー）。金額は見積で確かめてください", "飛行機（航空便）。金額は見積で確かめてください"):  # 文言の直し 2026-11-07
            assert lab in html, lab
        assert html.count("金額は見積で確かめてください。経路は地図に表示しています。") >= 3  # 文言の直し 2026-11-07
        assert html.count("日数と額は見積で確認") >= 3
        assert 'value="鉄道"' in html and 'value="飛行機"' in html and 'value="船"' in html
        assert not re.search(r"(?<![\d,])0円", html)   # 0 円と書かない


def test_cargo_editor_has_four_inputs_and_closed_details():
    with edge_page(1920, 1080) as page:
        _open(page, "#v=compare&o=13104&l=km&d=27128")
        page.js("document.querySelector('#cargo-toggle').click()")
        page.wait(lambda: page.js("!!document.querySelector('#sc-simple #sc-reset') || !!document.querySelector('#sc-simple [data-sc-kind]')"), 30, "4 項目")
        page.js("(function(){const b=document.querySelector('#sc-reset'); if (b) b.click();})()")   # 初めの例が複数種類の荷のとき
        page.wait(lambda: page.js("!!document.querySelector('#sc-simple [data-sc-kind]')"), 30, "4 項目")
        r = json.loads(page.js("""JSON.stringify({
          detOpen: document.querySelector('#ed-detail').open,
          inSimple: document.querySelectorAll('#sc-simple input, #sc-simple button').length,
          scInDetail: !!document.querySelector('#ed-detail #sc-select'),
          defaults: document.querySelector('#ed-detail .ed-defaults').textContent,
          lbl: document.querySelector('#ed-detail label[for=sc-select]').textContent })"""))
        assert r["detOpen"] is False and r["scInDetail"] is True, r
        assert "法人" in r["defaults"] and "例の荷物を読み込む（架空）" in r["lbl"], r
        # パレットを選び、重さと個数を入れる → 荷物の 1 行目に反映され、確定できる
        page.js("""(function(){ const c=(sel)=>document.querySelector(sel).click(); c('[data-sc-kind=pallet]'); c('[data-sc-size=std]');
          const set=(k,v)=>{const e=document.querySelector('#sc-simple [data-sc='+k+']'); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true}));};
          set('weight_kg', 300); set('count', 4); })()""")
        row = json.loads(page.js("JSON.stringify([...document.querySelectorAll('#cargo-editor .it-row[data-i=\"0\"] [data-k]')].map((e) => e.value))"))
        assert row == ["4", "110", "110", "120", "300"], row
        # 段ボールの 100 サイズ: 3 辺の合計が 100cm の箱になる
        page.js("document.querySelector('[data-sc-kind=box]').click(); document.querySelector('[data-sc-size=\"100\"]').click();")
        dims = json.loads(page.js("JSON.stringify([...document.querySelectorAll('#cargo-editor .it-row[data-i=\"0\"] [data-k]')].map((e) => e.value))"))
        assert dims[1:4] == ["50", "30", "20"], dims
        page.js("document.querySelector('#ed-confirm').click()")
        page.wait(lambda: json.loads(page.js("JSON.stringify(Work.confirmedShipment().items[0])"))["count"] == 4, 60, "確定")   # 2026-11-08 の直しに合わせて: 初めの荷が 50×30×20 なので、長さでは確定を待てない
        sh = json.loads(page.js("JSON.stringify(Work.confirmedShipment())"))
        assert sh["sender_type"] == "法人" and sh["receiver_type"] == "法人" and sh["items"][0]["count"] == 4, sh
