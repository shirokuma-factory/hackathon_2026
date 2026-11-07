"""送り方比べ（compare_lite）の不具合探し（2026-11-08 S01〜S05・採点 r3c）の再現と修正の試験。入力は不具合を出した値のまま。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import edge_page

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

PAGE = ROOT / "web" / "compare_lite.html"
ARTIFACT = ROOT / "web" / "publish" / "compare_lite_artifact.html"
TAB = chr(9)


def _js(expr: str, path: Path = PAGE):
    with edge_page(1200, 800, webgl=False) as page:
        page.goto(path.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        return page.js(expr)


def _ev(rows, frm="東京都", to="大阪府", km=492, path: Path = PAGE):
    """evaluate の結果から、選択肢の状態・額・理由と、見出し・タイル・棒の文を返す。"""
    inp = {"rows": rows, "from": frm, "to": to, "km": km, "recv": "business", "flex": 4}
    return _js("(() => { const inp = " + json.dumps(inp, ensure_ascii=False) + "; const res = evaluate(inp);"
               "return { errs: res.errs, opts: Object.fromEntries(res.options.map((o) => [o.id, { s: o.status, a: o.amount == null ? null : o.amount, why: o.reasons.join(' / ') }])),"
               "head: res.totals ? headlineText(res) : '', tile: res.totals ? statsHtml(res) : '', bars: res.totals ? barsHtml(res) : '', best: res.totals && bestOption(res) ? bestOption(res).id : null }; })()", path)


BOX = {"n": 1, "l": 50, "w": 40, "h": 30, "kg": 10, "pk": "box"}
PALLET = {"n": 1, "l": 110, "w": 110, "h": 150, "kg": 450, "pk": "pallet"}


def _cmp_text(rows, prices, to="大阪府", km=492):
    """荷物と契約単価（サイズ: 円）を入れた画面の、比べの文・見出し・タイル・棒の文字を返す。"""
    sets = "".join(f"set('#c-t-{z}', '{v}');" for z, v in prices.items())
    with edge_page(1200, 800, webgl=False) as page:
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        page.js("(() => { const set = (sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); };"
                "S.rows = " + json.dumps(rows) + "; $('#from').value = '東京都'; $('#to').value = " + json.dumps(to, ensure_ascii=False) + "; $('#km').value = " + str(km) + "; renderRows(); " + sets + " render(); })()")
        return page.js("({ cmp: document.querySelector('#cmpbox').innerText, head: document.querySelector('#headline').innerText, tile: document.querySelector('#stats').innerText, bars: document.querySelector('#bars').innerText })")


# 1. 海を渡る区間（S01）
@pytest.mark.parametrize("to,status,km", [("沖縄県", "ng", 1945), ("北海道", "check", 800)])
def test_s01_charter_is_not_priced_across_the_sea(to, status, km):
    out = _ev([PALLET], to=to, km=km)
    for cls in ("2t", "4t"):
        o = out["opts"]["kashikiri_" + cls]
        assert o["s"] == status and o["a"] is None, (cls, o)
        assert "海を渡るため、フェリーか船の見積が要ります" in o["why"], o
    assert not (out["best"] or "").startswith("kashikiri"), out
    assert "387,570" not in out["head"] + out["tile"] + out["bars"]


def test_s01_mainland_charter_still_priced():
    out = _ev([PALLET], to="大阪府")
    assert out["opts"]["kashikiri_2t"]["a"] is not None, out


# 2. 寸法のセルの mm（S02）
def test_s02_unit_inside_the_cell_is_used():
    for text in (f"1{TAB}1100×1100×1500mm{TAB}450", f"個数{TAB}寸法{TAB}重さ\n1{TAB}1100x1100x1500mm{TAB}450"):
        out = _js("parseCargoTable(" + json.dumps(text) + ")")
        r = out["rows"][0]
        assert (r["l"], r["w"], r["h"], r["kg"]) == ("110", "110", "150", "450"), (text, out)


def test_s02_over_1000cm_asks_about_mm_and_does_not_compute():
    out = _js("parseCargoTable(" + json.dumps(f"1{TAB}1100×1100×1500{TAB}450") + ")")
    assert any("mm ではありませんか" in n for n in out["notes"]), out
    ev = _ev([{"n": 1, "l": 1100, "w": 1100, "h": 1500, "kg": 450}])
    assert ev["errs"] and "mm ではありませんか" in ev["errs"][0], ev
    assert ev["opts"] == {}, ev


# 3. 宅配の契約単価は重さを見る（S03）
def test_s03_contract_takuhai_uses_the_larger_of_size_by_sum_and_size_by_weight():
    out = _cmp_text([{"n": 1, "l": 30, "w": 20, "h": 10, "kg": 25, "pk": "box"}], {60: 800, 160: 2300})
    assert "2,300 円" in out["cmp"] and "重さ" in out["cmp"], out
    out = _cmp_text([{"n": 1, "l": 50, "w": 40, "h": 30, "kg": 25, "pk": "box"}], {120: 1500, 160: 2300})
    assert "2,300 円" in out["cmp"] and "1,500 円" not in out["cmp"], out


def test_s03_takuhai_table_uses_weight_and_over_50kg_is_not_courier():
    r = _js("""(() => { const tt = parseRateTable('\\t60\\t80\\t100\\t120\\t140\\t160\\t180\\t200\\t260\\n関西\\t800\\t1000\\t1200\\t1500\\t1800\\t2300\\t2600\\t3000\\t4000', 'takuhai');
      const a = lookupTakuhaiTable(tt.table, [{ n: 1, l: 30, w: 20, h: 10, kg: 25 }], '大阪府'), b = lookupTakuhaiTable(tt.table, [{ n: 1, l: 30, w: 20, h: 10, kg: 60 }], '大阪府');
      return { a: a.amount, an: a.note, b: b.amount }; })()""")
    assert r["a"] == 2300 and "重さ" in r["an"], r
    assert r["b"] is None, r
    ev = _ev([{"n": 1, "l": 30, "w": 20, "h": 10, "kg": 60}])
    assert ev["opts"]["takuhai"]["s"] == "ng", ev


# 4. 重量(t)（トン）
def test_ton_header_is_multiplied_by_1000_and_said():
    for head in ("重量(t)", "重量（トン）"):
        out = _js("parseCargoTable(" + json.dumps(f"個数{TAB}縦{TAB}横{TAB}高さ{TAB}{head}\n1{TAB}110{TAB}110{TAB}150{TAB}0.45") + ")")
        assert out["rows"][0]["kg"] == "450", (head, out)
        assert any("1,000 倍" in n for n in out["notes"]), out


# 5. 総重量の列（S05）
def test_s05_total_weight_column_is_divided_by_count_and_said():
    out = _js("parseCargoTable(" + json.dumps(f"個数{TAB}縦{TAB}横{TAB}高さ{TAB}総重量(kg)\n3{TAB}40{TAB}40{TAB}40{TAB}40") + ")")
    r = out["rows"][0]
    assert 40 <= float(r["kg"]) * 3 < 40.01, out
    assert any("総重量" in n and "個数で割" in n for n in out["notes"]), out


# 6. 荷姿の列
def test_packing_column_is_read():
    text = f"個数{TAB}縦{TAB}横{TAB}高さ{TAB}重さ{TAB}荷姿\n1{TAB}110{TAB}110{TAB}150{TAB}450{TAB}パレット\n2{TAB}50{TAB}40{TAB}30{TAB}10{TAB}段ボール\n1{TAB}60{TAB}60{TAB}60{TAB}20{TAB}木箱"
    out = _js("parseCargoTable(" + json.dumps(text) + ")")
    assert [r["pk"] for r in out["rows"]] == ["pallet", "box", "other"], out
    q = _js("(() => { S.rows = parseCargoTable(" + json.dumps(text) + ").rows; renderRows(); render(); return document.querySelector('#quote').value; })()")
    assert "パレット渡し" in q, q


# 7. 120サイズ
def test_size_class_is_accepted_for_takuhai_and_explained():
    text = json.dumps(f"個数{TAB}サイズ{TAB}重さ\n2{TAB}120サイズ{TAB}8")
    out = _js("parseCargoTable(" + text + ")")
    assert len(out["rows"]) == 1 and out["rows"][0]["sz"] == 120, out
    assert any("3 辺合計 120cm 以下" in n and "仮の寸法" in n for n in out["notes"]), out
    ev = _js("(() => { const r = parseCargoTable(" + text + ").rows; const res = evaluate({ rows: r, from: '東京都', to: '大阪府', km: 492, recv: 'business', flex: 4 }); return { sum: sum3(res.pieces[0]), tk: res.options[0].status }; })()")
    assert ev["sum"] == 120 and ev["tk"] == "ok", ev


# 8. 路線便に額がない
def test_headline_says_cheapest_among_priced_and_rosen_needs_a_quote():
    out = _ev([{"n": 3, "l": 120, "w": 50, "h": 50, "kg": 40, "pk": "box"}, PALLET])
    assert out["opts"]["rosen"]["s"] != "ng" and out["opts"]["rosen"]["a"] is None, out
    assert "額が出た中での最安" in out["head"] and "路線便" in out["head"] and "見積" in out["head"], out["head"]
    assert "額が出た中での最安" in out["tile"], out["tile"]


# 9. 契約単価を入れたとき見出し・タイル・棒が比べの表と同じ（S04）
def test_s04_contract_price_wins_everywhere():
    out = _cmp_text([{"n": 3, "l": 40, "w": 40, "h": 40, "kg": 13.3, "pk": "box"}], {120: 1500})
    assert "4,500" in out["cmp"] and "4,500" in out["head"] and "4,500" in out["tile"] and "4,500" in out["bars"], out
    for k in ("head", "tile", "bars"):
        assert "5,700" not in out[k] and "6,000" not in out[k], (k, out)


# 10. 帰り便は T7 の区間だけ
def test_home_trip_needs_the_t7_route():
    ng = _ev([BOX], frm="群馬県", to="宮城県", km=300)["opts"]["home"]
    assert ng["s"] == "ng" and "T7" in ng["why"], ng
    ok = _ev([BOX], frm="東京都", to="鹿児島県", km=1500)["opts"]["home"]
    assert ok["s"] == "check", ok


def test_artifact_build_has_the_same_fixes():
    out = _ev([PALLET], to="沖縄県", km=1945, path=ARTIFACT)
    assert out["opts"]["kashikiri_2t"]["s"] == "ng", out


# 11. 採点 r4c: 単位なしの mm・個数の単位・120 サイズの仮寸法
MM_TEXT = f"個数{TAB}寸法{TAB}重さ\n1{TAB}600×400×300{TAB}20"


def test_r4c_unitless_mm_is_stopped_and_can_be_read_as_mm():
    out = _js("(() => { const r = parseCargoTable(" + json.dumps(MM_TEXT) + ").rows; const res = evaluate({ rows: r, from: '東京都', to: '大阪府', km: 492, recv: 'business', flex: 4 });"
              "S.rows = r; renderRows(); render(); const shown = document.querySelector('#errs').innerText; const btn = !!document.querySelector('[data-mmread=\"0\"]');"
              "document.querySelector('[data-mmread=\"0\"]').click();"
              "return { errs: res.errs, totals: res.totals, shown, btn, after: S.rows[0].l + 'x' + S.rows[0].w + 'x' + S.rows[0].h, err2: document.querySelector('#errs').innerText }; })()")
    assert out["totals"] is None and any("mm ではありませんか" in e and "1 行目" in e for e in out["errs"]), out
    assert out["btn"] and "mm として読む" in out["shown"], out
    assert out["after"] == "60x40x30" and out["err2"] == "", out


def test_r4c_count_cells_with_unit_words():
    for cell, n in [("2パレット", "2"), ("2PL", "2"), ("2梱", "2"), ("3cs", "3"), ("2ケース", "2"), ("5箱", "5")]:
        out = _js("parseCargoTable(" + json.dumps(f"個数{TAB}縦{TAB}横{TAB}高さ{TAB}重さ\n{cell}{TAB}110{TAB}110{TAB}120{TAB}300") + ")")
        assert len(out["rows"]) == 1 and out["rows"][0]["n"] == n, (cell, out)
        assert out["rows"][0]["pk"] == ("pallet" if cell in ("2パレット", "2PL") else "box"), (cell, out)
    bad = _js("parseCargoTable(" + json.dumps(f"個数{TAB}縦{TAB}横{TAB}高さ{TAB}重さ\n1{TAB}50{TAB}40{TAB}30{TAB}10\n二つ{TAB}50{TAB}40{TAB}30{TAB}10") + ")")
    assert bad["rows"] == [] and any("3 行目" in n and "二つ" in n for n in bad["notes"]), bad


def test_r4c_size_class_never_prints_provisional_dimensions():
    text = f"個数{TAB}サイズ{TAB}重さ\n2{TAB}120サイズ{TAB}8"
    out = _js("(() => { const p = parseCargoTable(" + json.dumps(text) + "); S.rows = p.rows; renderRows(); render(); return { q: document.querySelector('#quote').value, notes: p.notes.join(' '), d: pdesc(evaluate({ rows: p.rows, from: '東京都', to: '大阪府', km: 492, recv: 'business', flex: 4 }).pieces[0]) }; })()")
    assert "120 サイズ（3 辺合計 120cm 以内・寸法は未確認）" in out["q"], out
    for k in ("q", "notes", "d"):
        assert "40×40×40" not in out[k] and "40cm" not in out[k] and "53.33" not in out[k] and "40 cm" not in out[k], (k, out)
