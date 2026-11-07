"""送り方比べ（compare_lite）の目視レビュー（2026-11-08）で見つかった X1・X2・X5 の再現と修正の試験。"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import edge_page

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

PAGE = ROOT / "web" / "compare_lite.html"
ARTIFACT = ROOT / "web" / "publish" / "compare_lite_artifact.html"
SHOT_DIR = os.environ.get("CL_VIS_DIR")   # 指定したときだけ画像を残す


def _open(page, path: Path):
    page.goto(path.as_uri() + "?example=1")
    page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")


def _shot(page, name: str):
    if not SHOT_DIR:
        return
    Path(SHOT_DIR).mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="png", captureBeyondViewport=True)
    (Path(SHOT_DIR) / name).write_bytes(base64.b64decode(r["data"]))


def _change(page, sel: str, value: str):
    page.js("(() => { const e = document.querySelector(" + json.dumps(sel) + "); e.value = " + json.dumps(value, ensure_ascii=False) + "; e.dispatchEvent(new Event('change', {bubbles: true})); })()")


@pytest.mark.parametrize("path", [PAGE, ARTIFACT], ids=["page", "artifact"])
def test_x1_example_distance_follows_the_destination(path):
    with edge_page(1920, 1080, webgl=False) as page:
        _open(page, path)
        first = page.js("({ km: document.querySelector('#km').value, note: document.querySelector('#km-note').textContent, auto: S.kmAuto })")
        assert first["auto"] is True and "手で入れた値" not in first["note"], first
        _change(page, "#to", "沖縄県")
        got = page.js("({ km: document.querySelector('#km').value, note: document.querySelector('#km-note').textContent, req: document.body.innerText })")
        assert got["km"] != first["km"], (first, got)
        assert f"およそ {first['km']}km" not in got["req"] and f"およそ{first['km']}km" not in got["req"], got
        assert "手で入れた値" not in got["note"], got
        _shot(page, "x1_okinawa_1920.png")
        # 手で書き換えたあとは自動で上書きしない
        page.js("(() => { const e = document.querySelector('#km'); e.value = '700'; e.dispatchEvent(new Event('input', {bubbles: true})); })()")
        _change(page, "#to", "愛知県")
        assert page.js("document.querySelector('#km').value") == "700"


@pytest.mark.parametrize("path", [PAGE, ARTIFACT], ids=["page", "artifact"])
def test_x2_contract_price_over_limit_shows_the_real_reason(path):
    with edge_page(1920, 1080, webgl=False) as page:
        _open(page, path)
        page.js("(() => { const set = (sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); };"
                "S.rows = [{n: 4, l: 120, w: 100, h: 130, kg: 100, pk: 'pallet'}]; renderRows(); set('#c-r-rate', '25'); render(); })()")
        row = page.js("(() => { const r = document.querySelector('tr[data-opt=rosen]'); return r ? r.innerText : ''; })()")
        if "要見積" not in row:   # 荷物の重さが上限内なら、この試験の前提が崩れている
            pytest.skip("例の荷物が 1,000kg 以内: " + row)
        assert "kg を超えるので要見積" in row and "計算重量" in row, row
        assert "契約単価を入れると額が出ます" not in row, row
        _shot(page, "x2_rosen_reason_1920.png")


@pytest.mark.parametrize("w", [1920, 1366])
def test_x5_cargo_inputs_are_wide_enough_to_read(w):
    with edge_page(w, 1000, webgl=False) as page:
        _open(page, PAGE)
        page.js("document.querySelectorAll('details').forEach((d) => { if (d.querySelector('#pbody')) d.open = true; })")
        got = page.js("(() => [...document.querySelectorAll('#pbody input')].map((e) => e.getBoundingClientRect().width))()")
        assert got and min(got) >= 46, got   # 閉じた欄の中は幅 0 になるので、開いてから測る
        assert page.js("(() => { const t = document.querySelector('table.in'); return t.parentElement.scrollWidth <= t.parentElement.clientWidth + 1; })()"), "表が横に切れている"
        _shot(page, f"x5_cargo_{w}.png")
