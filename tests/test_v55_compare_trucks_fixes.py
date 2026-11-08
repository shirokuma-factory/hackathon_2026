"""ページ評価（2026-11-08 比べると大型車）の修正の試験。mm の見出し、入数、最安の表示、荷役、遅れの文。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import edge_page

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

PAGE = ROOT / "web" / "compare_lite.html"
ARTIFACT = ROOT / "web" / "publish" / "compare_lite_artifact.html"
NODE = shutil.which("node")


def _js(expr: str, path: Path = PAGE):
    with edge_page(1200, 800, webgl=False) as page:
        page.goto(path.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        return page.js(expr)


def test_mm_header_converts_dimensions_but_not_weight():
    out = _js("parseCargoTable(" + json.dumps("縦(mm)\t横(mm)\t高さ(mm)\t重量(kg)\n500\t400\t300\t40\n1100\t1100\t1500\t450") + ")")
    assert out["rows"][0]["l"] == "50" and out["rows"][0]["kg"] == "40", out
    assert out["rows"][1]["h"] == "150" and out["rows"][1]["kg"] == "450", out


def test_irisu_is_not_the_case_count():
    out = _js("parseCargoTable(" + json.dumps("ケース数\t入数\t縦\t横\t高さ\t重量\n5\t24\t50\t40\t30\t10") + ")")
    assert out["rows"][0]["n"] == "5", out
    assert any("入数" in n for n in out["notes"]), out


def test_headline_tile_and_bars_use_the_same_cheapest():
    out = _js("""(() => { const inp = { rows: [{ n: 3, l: 50, w: 40, h: 30, kg: 10, pk: 'box' }], from: '東京都', to: '大阪府', km: 492, recv: 'business', flex: 0 };
      const res = evaluate(inp); const best = bestOption(res);
      return { id: best.id, fict: best.amount == null && !!best.pub, head: headlineText(res), tile: statsHtml(res), bars: barsHtml(res) }; })()""")
    assert out["id"] == "takuhai" and out["fict"], out
    assert "架空の表の例" in out["head"] and "架空の表の例" in out["bars"] and "架空の表の例" in out["tile"], out
    assert "109,750" not in out["head"], out


def test_pallet_with_unknown_forklift_is_check_for_charter_and_rosen():
    out = _js("""(() => { const inp = { rows: [{ n: 1, l: 110, w: 110, h: 150, kg: 450, pk: 'pallet' }], from: '東京都', to: '大阪府', km: 492, recv: 'business', flex: 0, fkFrom: 'unknown', fkTo: 'unknown' };
      return evaluate(inp).options.filter((o) => /^(kashikiri_|rosen)/.test(o.id)).map((o) => ({ id: o.id, s: o.status, why: o.reasons.join(' ') })); })()""")
    assert out
    for o in out:
        if o["s"] != "ng":
            assert o["s"] == "check" and "荷役（フォークリフト）を確かめてください" in o["why"], o


def test_artifact_build_has_same_fixes():
    for p in (PAGE, ARTIFACT):
        t = p.read_text(encoding="utf-8")
        assert "const bestOption" in t and "荷役（フォークリフト）を確かめてください" in t, p.name


@pytest.mark.skipif(NODE is None, reason="node が入っていません")
def test_trucks_delay_text_is_computed():
    code = """const C = require(%s);
console.log(JSON.stringify([C.delayText('202611072100', '2026-11-07T23:08:19+09:00'), C.delayText('202611072300', '2026-11-07T23:08:19+09:00'), C.delayMin('202611072100', '2026-11-08T00:24:00+09:00')]));""" % json.dumps(str(ROOT / "web" / "trucks_now_core.js"))
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", check=True)
    a, b, c = json.loads(r.stdout)
    assert a == "約 2 時間遅れ" and b == "8 分遅れ" and c == 204, r.stdout


def test_trucks_page_text_has_no_fixed_delay_and_legend_is_accurate():
    js = (ROOT / "web" / "trucks_now.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "trucks_now.html").read_text(encoding="utf-8")
    assert "およそ20分遅れ" not in js
    assert "ふだんの多さ" not in js and "ふだんの多さ" not in html
    assert "直近 24 時間でいちばん多い時を 100%" in html and "白く光" in html and 'class="purpose"' in html   # 日本語の直し 2026-11-08: 数字と単位の空白
