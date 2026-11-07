"""CSV の式注入（スプレッドシートで開いたとき、先頭が = + - @ タブ CR の文字が式になる）への対策。

書き出す CSV のセルが式の文字で始まり、数値でなければ、先頭に ' を付ける。
荷待ちのページが自分で書いた CSV を読み戻すときは、先頭の ' を 1 つだけ外す。
node があれば関数を実際に動かし、無ければ文字列の確認だけにする。
Python 側（src/shipcompare/own.py）には利用者の値を CSV に書く処理が無いので、試験表の行は足していない。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "tools" / "waiting_page_template.html").read_text(encoding="utf-8")
OWN_JS = (ROOT / "web" / "own.js").read_text(encoding="utf-8")
OWN_CALC = (ROOT / "web" / "own_calc.js").read_text(encoding="utf-8")
GUARD = r"/^[=+\-@\t\r]/.test("
NODE = shutil.which("node")

EVIL = ['=HYPERLINK("http://x","a")', "+1+1", "-1+1", "@SUM(A1)", "\t=1", "\r=1"]
KEEP = ["-5", "+5", "12.5", "-0.5", "普通の場所", "a=b"]


def _node(script: str):
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _fn(src: str, name: str) -> str:
    m = re.search(r"^function " + name + r"\(.*?^\}\n", src, re.S | re.M)
    assert m, name
    return m.group(0)


def _expected(v: str) -> str:
    t = ("'" + v) if (v and v[0] in "=+-@\t\r" and not re.fullmatch(r"[-+]?\d+(\.\d+)?", v)) else v
    return '"' + t.replace('"', '""') + '"' if re.search(r'[",\r\n]', t) else t


def test_every_js_csv_writer_has_the_guard():
    assert OWN_JS.count(GUARD) == 1 and OWN_CALC.count(GUARD) == 1 and TEMPLATE.count(GUARD) == 1
    for page in ("web/waiting.html", "web/publish/waiting_artifact.html", "web/own_lite.html", "web/publish/own_lite_artifact.html"):
        assert GUARD in (ROOT / page).read_text(encoding="utf-8"), page


@pytest.mark.skipif(not NODE, reason="node が無い")
def test_waiting_csv_neutralizes_and_parse_csv_restores():
    vals = EVIL + KEEP
    script = (
        _fn(TEMPLATE, "csvTextOf") + _fn(TEMPLATE, "parseCsv")
        + "const rows = " + json.dumps([{"partner": v, "place": v} for v in vals], ensure_ascii=False)
        + ";const csv = csvTextOf(rows);console.log(JSON.stringify({csv, back: parseCsv(csv)}));"
    )
    out = _node(script)
    for v in EVIL:
        assert "'" + v.replace('"', '""') in out["csv"], v
    # 見出しの 4 列目（契約の相手）と 5 列目（場所）が、元の値に戻る
    got = [(r[3], r[4]) for r in out["back"][1:]]
    assert got == [(v, v) for v in vals]


@pytest.mark.skipif(not NODE, reason="node が無い")
def test_own_calc_legs_csv_neutralizes():
    vals = EVIL + KEEP
    script = (
        "const OwnCalc = (new Function(require('fs').readFileSync('web/own_calc.js','utf8') + ';return OwnCalc;'))();"
        "const vals = " + json.dumps(vals, ensure_ascii=False) + ";"
        "const mk = (v) => ({id: 'L', date: '2026-10-01', plate: v, frm: 'a', to: 'b', dir: 'out', km: 1, km_src: '', kind: 'loaded', kind_src: '', flags: []});"
        "const d = {places: {a: {label: '-5'}, b: {label: '@x'}}, legs: vals.map(mk)};"
        "console.log(JSON.stringify(OwnCalc.legsCsv(d)));"
    )
    csv = _node(script)
    assert "'@x" in csv and ",-5," in csv
    for v in EVIL:
        assert "'" + v.replace('"', '""') in csv, v
    for v in ("+5", "12.5", "-0.5", "普通の場所"):
        assert "," + v + "," in csv, v


@pytest.mark.skipif(not NODE, reason="node が無い")
def test_cell_functions_follow_the_rule():
    for src, pat, name in ((OWN_JS, r"const cell = \(v\) => \{[^\n]*\};", "cell"), (OWN_CALC, r"const csvCell = \(v\) => \{[^\n]*\};", "csvCell")):
        decl = re.search(pat, src).group(0)
        vals = EVIL + KEEP + [""]
        out = _node(decl + "console.log(JSON.stringify(" + json.dumps(vals, ensure_ascii=False) + ".map(" + name + ")));")
        assert out == [_expected(v) for v in vals], name


def test_waiting_page_round_trip_in_edge():
    try:
        import capture_screens as cs
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")
    import capture_web as cw
    with cw.edge_page(1280, 900) as page:
        page.goto((ROOT / "web" / "waiting.html").as_uri() + "?example=1")
        page.wait(lambda: page.js("typeof csvTextOf === 'function'"), 30, "ページ")
        got = page.js("""(function(){
          const mk = (p, pl) => ({date: '2026-10-05', vehicle_no: '1234', vehicle_class: '中型', partner: p, place: pl, arrive: '07:30', start: '08:50', end: '', depart: '',
                                  handler: '', method: '', shipper_fault: 'はい', appointed: '', side_work: '', side_min: '', post_wait: ''});
          const rows = [mk('=1+1', '-5'), mk('@x', '+SUM(1)')];
          const csv = csvTextOf(calc(rows, 2026, TARIFF, S.defaults).rows);
          const back = csvToRows(csv);
          return {csv, p: back.map((r) => r.partner), pl: back.map((r) => r.place)};
        })()""")
    assert "'=1+1" in got["csv"] and "'@x" in got["csv"]
    assert got["p"] == ["=1+1", "@x"] and got["pl"] == ["-5", "+SUM(1)"]
