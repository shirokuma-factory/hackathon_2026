"""送り方比べ（compare_lite）の採点 r5c（2026-11-08）の再現と修正の試験。入力は採点者が貼った文のまま。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import edge_page

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

PAGE = ROOT / "web" / "compare_lite.html"
TAB = chr(9)


def _run(js: str):
    with edge_page(1200, 800, webgl=False) as page:
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        return page.js(js)


def _paste(text: str, then: str = ""):
    js = ("(() => { $('#b-clear').click(); $('#cargo-paste').value = " + json.dumps(text, ensure_ascii=False) + "; cargoPaste(); " + then +
          "return { rows: S.rows.map((r) => [r.n, r.l, r.w, r.h, r.kg]), msg: $('#cargo-msg').textContent, errs: $('#errs').innerText, "
          "btns: [...document.querySelectorAll('#errs button')].map((b) => b.textContent), head: $('#headline').textContent, "
          "tile: $('#stats').innerText }; })()")
    return _run(js)


def test_small_box_in_mm_asks_once_for_whole_table():
    out = _paste("個数\t縦\t横\t高さ\t重さ\n1\t200\t150\t100\t3")
    assert "この表は mm で書かれていますか？" in out["errs"], out
    assert any("mm として読む" in b for b in out["btns"]) and any("cm のまま" in b for b in out["btns"]), out
    assert "109,750" not in out["head"] + out["tile"]


def test_small_box_whole_table_mm_button_converts_all_rows():
    out = _paste("個数\t縦\t横\t高さ\t重さ\n1\t200\t150\t100\t3\n2\t200\t150\t120\t4",
                 "document.querySelector('[data-mmall]').click(); ")
    assert out["rows"] == [["1", "20", "15", "10", "3"], ["2", "20", "15", "12", "4"]], out
    assert "mm で書かれていますか" not in out["errs"]


def test_small_box_whole_table_cm_button_keeps_cm():
    out = _paste("個数\t縦\t横\t高さ\t重さ\n1\t200\t150\t100\t3", "document.querySelector('[data-cmall]').click(); ")
    assert out["rows"] == [["1", "200", "150", "100", "3"]], out
    assert "mm で書かれていますか" not in out["errs"]


def test_normal_cm_table_is_not_asked():
    out = _paste("個数\t縦\t横\t高さ\t重さ\n2\t50\t40\t30\t10\n1\t110\t110\t150\t450")
    assert "mm" not in out["errs"], out


def test_over_1000_row_gets_mm_button_and_same_row_number():
    out = _paste("個数\t縦\t横\t高さ\t重さ\n1\t1100\t1100\t1500\t450")
    assert any("1 行目を mm として読む" in b for b in out["btns"]), out
    assert "1 行目" in out["errs"] and "2 行目" not in out["errs"], out
    assert "1,000cm" in out["msg"] and "2 行目" not in out["msg"], out
    out = _paste("個数\t縦\t横\t高さ\t重さ\n1\t1100\t1100\t1500\t450", "document.querySelector('[data-mmread]').click(); ")
    assert out["rows"] == [["1", "110", "110", "150", "450"]], out


def test_combined_dimension_header_with_mm_imports_rows():
    out = _paste("個数\t縦×横×高さ(mm)\t重さ\n1\t200×150×100\t3")
    assert out["rows"][0][1:4] == ["20", "15", "10"], out
    out = _paste("個数\t縦x横x高さ(mm)\t重さ(kg)\n2\t1100x1100x1500\t450\n")
    assert out["rows"][0][1:4] == ["110", "110", "150"], out


def test_partial_modes_not_counted_as_usable():
    out = _run("(() => { const res = evaluate({ rows: [{n: 1, l: 110, w: 110, h: 150, kg: 450, pk: 'pallet'}, {n: 1, l: 50, w: 40, h: 30, kg: 10, pk: 'box'}], from: '東京都', to: '大阪府', km: 492, recv: 'business', flex: 4 });"
               "return { opts: Object.fromEntries(res.options.map((o) => [o.id, [o.status, !!o.partial]])), head: headlineText(res), tile: statsHtml(res) }; })()")
    assert out["opts"]["takuhai"] == ["check", True], out
    assert "宅配便" in out["head"] and "一部の荷だけ運べます" in out["head"], out
    assert out["head"].split("。")[0].startswith("使える送り方") and "宅配便" not in out["head"].split("。")[0], out
    assert "一部の荷だけ運べる" in out["tile"], out
