"""道具の採点 r2a・r2b の画面と部品の指摘の確認。

- 閉じている詳細パネルの中のボタンには Tab が入らない（inert）。開くと入る。
- アドレスの #v=... だけを書き換えると、開いたまま画面が切り替わる。
- 押せる所（ボタン・入力・summary など）の高さが、1920 と 400 のどちらでも 24px 以上（地図の画面・荷待ち・送り方を比べる）。
- 部品の形（荷・荷待ちの記録・拠点）の JSON Schema と、examples/kit の例が合っている。
画面の確認はヘッドレス Edge。Edge が無いときだけ飛ばす。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

# 押せる所のうち、見えていて 24px に満たないもの。チェックボックスなどは、包む label か自分の大きい方で測る
SMALL_JS = """JSON.stringify([...document.querySelectorAll('button,input,select,textarea,summary,a[href],[role=tab],[role=button],.f-chk')].filter(e => {
  if (e.type === 'hidden' || e.closest('[inert],[hidden]')) return false;
  const s = getComputedStyle(e); if (s.visibility === 'hidden' || s.display === 'none') return false;
  let b = e.getBoundingClientRect();
  if (e.type === 'checkbox' || e.type === 'radio') { const l = e.closest('label'); if (l) b = l.getBoundingClientRect(); }
  if (!(b.width > 0 && b.height > 0)) return false;
  const ic = (e.textContent || '').trim().length <= 2 && e.tagName === 'BUTTON'; // 記号だけのボタンは幅も見る
  return b.height < 23.5 || (ic && b.width < 23.5);
}).map(e => e.tagName + '#' + e.id + '.' + String(e.className).slice(0, 30) + ' ' + Math.round(e.getBoundingClientRect().width) + 'x' + Math.round(e.getBoundingClientRect().height) + ' ' + (e.textContent || '').trim().slice(0, 12)))"""


def _open_app(width, height, hash_):
    port = server_port("local")
    cm = edge_page(width, height)
    page = cm.__enter__()
    page.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=width < 600)
    open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}", tag="v40_ui_fail")
    time.sleep(3)
    return cm, page


@pytest.mark.parametrize("width,height", [(1920, 1080), (400, 860)])
@pytest.mark.parametrize("view", ["market", "find", "compare", "own"])
def test_map_app_controls_are_at_least_24px(width, height, view):
    cm, p = _open_app(width, height, f"#v={view}")
    try:
        small = json.loads(p.js(SMALL_JS))
        assert small == [], small
    finally:
        cm.__exit__(None, None, None)


def test_closed_detail_panel_is_inert_and_open_one_is_not():
    cm, p = _open_app(1920, 1080, "#v=compare")
    try:
        assert p.js("document.querySelector('#detail').inert") is True
        assert p.js("document.querySelector('#work').inert") is True
        # 閉じている間は、中のボタンにフォーカスが入らない
        p.js("document.querySelector('#make-request').focus()")
        assert p.js("document.activeElement === document.querySelector('#make-request')") is False
        # 着地を選ぶと開き、inert が外れる
        p.js("(() => { const A = window.__app; A.select(A.S.idx.codes.indexOf('27128') >= 0 ? A.S.idx.codes.indexOf('27128') : 0); })()")
        p.wait(lambda: p.js("document.querySelector('#detail').getAttribute('aria-hidden') === 'false'"), 60, "詳細パネルが開く")
        time.sleep(0.3)
        assert p.js("document.querySelector('#detail').inert") is False
        p.wait(lambda: p.js("!!document.querySelector('#cards input[name=cc-pick]')"), 60, "送り方の表")
        p.js("document.querySelector('#cards input[name=cc-pick]').click()")   # 送り方を選ぶと、見積依頼文を作るボタンが出る（SPEC §44）
        p.js("document.querySelector('#make-request').focus()")
        assert p.js("document.activeElement === document.querySelector('#make-request')") is True
        # 閉じると、また inert
        p.js("document.querySelector('#close').click()")
        time.sleep(0.3)
        assert p.js("document.querySelector('#detail').inert") is True
    finally:
        cm.__exit__(None, None, None)


def test_changing_only_the_hash_switches_the_view():
    cm, p = _open_app(1920, 1080, "#v=market")
    try:
        assert p.js("window.__app.S.view") == "market"
        p.js("location.hash = '#v=find'")
        p.wait(lambda: p.js("window.__app.S.view") == "find", 20, "find へ切り替わる")
        p.js("location.hash = '#v=compare'")
        p.wait(lambda: p.js("window.__app.S.view") == "compare", 20, "compare へ切り替わる")
        # 画面を自分で切り替えたあとのアドレスの書き換え（replaceState）で、ループしない
        p.js("window.__app.setView('market')")
        time.sleep(0.5)
        assert p.js("window.__app.S.view") == "market"
        assert "v=market" in p.js("location.hash")
    finally:
        cm.__exit__(None, None, None)


def _file_page(width, height, name, query=""):
    cm = edge_page(width, height)
    page = cm.__enter__()
    page.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=width < 600)
    page.goto((ROOT / "web" / name).as_uri() + query)
    time.sleep(1.5)
    return cm, page


@pytest.mark.parametrize("width,height", [(1920, 1080), (400, 860)])
def test_waiting_page_controls_are_at_least_24px(width, height):
    cm, p = _file_page(width, height, "waiting.html")
    try:
        p.js("document.querySelector('#b-example').click()")
        time.sleep(1.0)
        assert p.js("document.querySelectorAll('.del').length") > 0
        small = json.loads(p.js(SMALL_JS))
        assert small == [], small
    finally:
        cm.__exit__(None, None, None)


@pytest.mark.parametrize("width,height", [(1920, 1080), (400, 860)])
def test_compare_lite_controls_are_at_least_24px(width, height):
    cm, p = _file_page(width, height, "compare_lite.html", "?example=1")
    try:
        small = json.loads(p.js(SMALL_JS))
        assert small == [], small
    finally:
        cm.__exit__(None, None, None)


# ---------------------------------------------------------------- 荷・荷待ちの記録・拠点の形（設計 §4）
def test_item_waiting_record_and_site_schemas_accept_what_the_app_uses():
    from test_v36_kit import _errors, _load, mini_validate
    schema_dir = ROOT / "web" / "kit" / "schema"
    # 荷待ちの記録: 荷待ちのページの例の行（web/waiting_calc.js の COLUMNS）と同じ欄で通る
    rec_schema = _load(schema_dir / "waiting_record.json")
    row = {"date": "2026-10-01", "vehicle_no": "", "vehicle_class": "中型", "partner": "A", "place": "", "arrive": "08:00", "start": "09:25",
           "end": "", "depart": "", "handler": "", "method": "", "shipper_fault": "はい", "appointed": "08:00", "side_work": "", "side_min": "", "post_wait": "",
           "basis": "実測"}
    assert _errors({"records": [row]}, rec_schema) == [] and mini_validate({"records": [row]}, rec_schema) == []
    # 荷: work.js の既定の 1 行（未入力は null）と models.Item の欄
    item_schema = _load(schema_dir / "item.json")
    it = {"count": None, "length_cm": None, "width_cm": None, "height_cm": None, "weight_kg": None, "basis": "仮定"}
    assert _errors({"items": [it]}, item_schema) == [] and mini_validate({"items": [it]}, item_schema) == []
    # 拠点: web/data/own_sample.json の places と同じ欄
    own = _load(ROOT / "web" / "data" / "own_sample.json")
    site_schema = _load(schema_dir / "site.json")
    for pl in own["places"].values():
        if pl["lon"] is None:  # 地図に置かない地点（確認待ちの別名、p08）は部品に渡さない（2026-11-07）
            continue
        s = {"id": pl["id"], "name": pl["label"], "kind": "その他", "lon": pl["lon"], "lat": pl["lat"], "muni": pl["muni"], "code": pl["code"],
             "aliases": pl["aliases"], "basis": "仮定"}
        assert _errors({"sites": [s]}, site_schema) == [], pl["id"]


@pytest.mark.parametrize("fname,key,bad", [
    ("item.json", "items", {"count": 0, "length_cm": 10, "width_cm": 10, "height_cm": 10, "weight_kg": 1, "basis": "仮定"}),
    ("waiting_record.json", "records", {"date": "10/5", "partner": "A", "arrive": "08:00", "start": "09:00", "basis": "実測"}),
    ("waiting_record.json", "records", {"date": "2026-10-05", "partner": "A", "arrive": "8時", "start": "09:00", "basis": "実測"}),
    ("site.json", "sites", {"id": "x", "name": "x", "kind": "その他", "lon": 200, "lat": 35, "basis": "仮定"}),
    ("site.json", "sites", {"id": "x", "name": "x", "kind": "その他", "lon": 139, "lat": 35, "basis": "たぶん"}),
])
def test_new_schemas_reject_wrong_values(fname, key, bad):
    from test_v36_kit import _errors, _load, mini_validate
    schema = _load(ROOT / "web" / "kit" / "schema" / fname)
    assert _errors({key: [bad]}, schema) != []
    assert mini_validate({key: [bad]}, schema) != []


def test_guide_lists_the_new_schema_files():
    guide = (ROOT / "docs" / "guide" / "部品の使い方.md").read_text(encoding="utf-8")
    for f in ("item.json", "waiting_record.json", "site.json", "items.json", "waiting_records.json", "sites.json"):
        assert f in guide, f
