"""SPEC §29 定期報告と同じ定義の荷待ち、記録の打ち切りの注意、注記の条件、狭い画面（受入条件 G124〜G127）。

- G124: report_wait_min（Python）。JS との一致は tests/test_v237_waiting_page.py の ?selftest=1（試験表に 1 例を足した）。
- G125: 記録の打ち切りの 2 つの文の出方。
- G126: 平均 30 分未満の組に「指定の到着時刻が無い」の注記が出ない。
- G127: 幅 400px で 2 つの表が縦のカードになる（場所の文字が縦に折り返さない、注記が見える、横スクロールが無い）。
  Artifact の版は G101〜G106（tests/test_v26_artifact.py）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

from shipcompare import waiting

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import artifact_build  # noqa: E402

PAGE = ROOT / "web" / "waiting.html"
ART = ROOT / "web" / "publish" / "waiting_artifact.html"

NOTE_NO_DESIGNATED = "指定の到着時刻が無い到着が多い組です。相手に到着の時刻を決めてもらうか、自社で到着を遅らせて変わるかを確かめてください"
PM_LEAD = "特定荷主は 2026 年度から、施設ごと・月別の平均の荷待ち時間を分単位で測り、国に報告します。荷主の数字を、運送会社の記録で確かめるときに使えます。"
PM_DEF = "到着（指定の時刻があればその時刻）から荷役開始までの分。荷主の定期報告の算定方法に合わせています。待機時間料の計算とは数え方が違います。"
TRUNC_LINE = "業務記録の義務は 30 分以上の荷待ちだけです。30 分未満で荷役が始まった到着も入れると、平均と 1 時間以上の割合が実際に近くなります。"
TRUNC_WARN = "30 分未満の荷待ちの記録がありません。平均と割合は高めに出ている可能性があります。"


def _row(**kw):
    base = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A", "place": "X", "arrive": "09:00", "start": "10:00", "shipper_fault": "はい"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- G124（Python）
def test_g124_report_wait_min_three_cases():
    rows = [
        _row(arrive="07:50", appointed="08:00", start="09:00"),                                   # 指定より早い到着: 指定の 08:00 から 60 分
        _row(arrive="08:30", start="09:00", end="09:40", handler="荷主側"),                       # 荷主側の荷役: 荷役の 40 分は入れない → 30 分（wait_min は 70）
        _row(arrive="10:00", start="10:20", end="10:40", depart="11:40", handler="運転者", method="手積み", post_wait="はい"),   # 荷役のあとの待ち 60 分は入れない → 20 分
        _row(arrive="08:00", appointed="08:30", start="08:20"),                                     # 指定より前に荷役開始 → 0 未満は 0
        _row(arrive="09:00", start="09:30", shipper_fault="いいえ"),                              # 荷主等の都合 いいえ でも数える → 30 分（wait_min は 0）
    ]
    res = waiting.calc(rows, 2026)
    got = res["rows"]
    assert [r["report_wait_min"] for r in got] == [60, 30, 20, 0, 30]
    assert got[1]["wait_min"] == 70 and got[2]["wait_min"] == 80 and got[4]["wait_min"] == 0
    pm = res["by_place_month"]
    assert [(p["count"], p["wait_minutes"], p["avg_minutes"], p["over_1h_count"]) for p in pm] == [(5, 140, 28, 1)]


def test_g124_vectors_have_the_case():
    data = json.loads((ROOT / "tests" / "data" / "waiting_vectors.json").read_text(encoding="utf-8"))
    c = next(x for x in data["cases"] if x["name"] == "report_wait_early_arrival_shipper_handling_post_wait")
    assert [r["report_wait_min"] for r in c["expect"]["rows"]] == [60, 30, 30, 20, 0]
    assert all("report_wait_min" in r for x in data["cases"] for r in x["expect"]["rows"])


# ---------------------------------------------------------------- 画面（ヘッドレス Edge）
def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _url(path: Path) -> str:
    if path == PAGE:
        return PAGE.as_uri()
    html = artifact_build.wrap_for_test(ART.read_text(encoding="utf-8"), None)
    out = Path(tempfile.mkdtemp(prefix="artifact_shell_")) / (ART.stem + ".html")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return out.as_uri()


FEED_JS = """(function(rows){
  S.noStore = true; S.rows = rows.map((r) => ({...blankRow(), ...r})); S.partner = ''; renderTable(); doCalc();
  const cells = (sel) => [...document.querySelectorAll(sel)].map((tr) => [...tr.children].map((td) => td.textContent));
  return {rows: cells('#hotspots tbody tr'), part: document.querySelector('#partners').textContent,
          warn: !!document.querySelector('#trunc-warn'), pm: cells('#placemonth tbody tr')};
})"""


def _feed(page, rows):
    return page.js(f"{FEED_JS}({json.dumps(rows, ensure_ascii=False)})")


@pytest.mark.parametrize("path", [PAGE, ART])
def test_g125_truncation_texts(path):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(_url(path) + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#hotspots')"), 30, "表")
        # 例は 30 分未満の記録を含む: 常に出る 1 行だけで、強めの表示は出ない
        ex = page.js("({warn: !!document.querySelector('#trunc-warn'), part: document.querySelector('#partners').textContent})")
        assert ex["warn"] is False and TRUNC_LINE in ex["part"] and PM_LEAD in ex["part"] and PM_DEF in ex["part"]
        # 30 分以上だけの 5 行: 強めの表示が出る（常に出る 1 行も出る）
        five = [_row(arrive=f"09:{i:02d}", start=f"10:{i:02d}") for i in range(5)]
        got = _feed(page, five)
        assert got["warn"] is True and TRUNC_WARN in got["part"] and TRUNC_LINE in got["part"]
        assert got["part"].index(TRUNC_WARN) < got["part"].index(TRUNC_LINE)
        # 4 行だけ: 出ない。30 分未満の行が 1 つある: 出ない。荷主等の都合が いいえ の行では 5 行に数えない
        four = _feed(page, five[:4])
        assert four["warn"] is False and TRUNC_LINE in four["part"]
        assert _feed(page, five[:4] + [_row(arrive="09:00", start="09:20")])["warn"] is False
        assert _feed(page, five[:4] + [_row(arrive="09:00", start="10:30", shipper_fault="いいえ")])["warn"] is False
        # 指定の時刻があれば、その時刻から数える: 到着 07:50・指定 08:00・荷役開始 08:25 は 25 分で、30 分未満
        assert _feed(page, five[:4] + [_row(arrive="07:50", appointed="08:00", start="08:25")])["warn"] is False


@pytest.mark.parametrize("path", [PAGE, ART])
def test_g126_note_needs_average_30_min(path):
    _edge_or_skip()
    import capture_web as cw
    rows = [_row(arrive="09:00", start="09:20") for _ in range(3)]                                   # X 9時台: 指定なし・平均 20 分 → 注記なし
    rows += [_row(place="Y", arrive="09:00", start="09:30") for _ in range(3)]                       # Y 9時台: 指定なし・平均 30 分 → 注記あり
    rows += [_row(place="Z", arrive="09:00", appointed="09:00", start="10:30") for _ in range(3)]    # Z: 指定あり → 注記なし
    with cw.edge_page(1920, 1080) as page:
        page.goto(_url(path) + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#hotspots')"), 30, "表")
        got = _feed(page, rows)
        by = {r[1]: r for r in got["rows"]}
        assert NOTE_NO_DESIGNATED not in by["X"][8]
        assert by["Y"][8] == f"（{NOTE_NO_DESIGNATED}）"
        assert NOTE_NO_DESIGNATED not in by["Z"][8]


MEASURE_JS = """(function(){
  const out = {};
  for (const id of ['hotspots', 'placemonth']) {
    const t = document.getElementById(id), tr = t.querySelector('tbody tr');
    const c1 = [...tr.querySelectorAll('td.c1')].map((e) => { const r = e.getBoundingClientRect(); return {w: r.width, h: r.height, lh: parseFloat(getComputedStyle(e).lineHeight) || 16}; });
    const nums = [...tr.querySelectorAll('td.num')].map((e) => e.getBoundingClientRect().top);
    out[id] = {display: getComputedStyle(tr).display, c1, c1top: tr.querySelector('td.c1').getBoundingClientRect().top, nums,
               headHidden: t.querySelector('thead').getBoundingClientRect().width <= 1, right: Math.max(...[...tr.children].map((e) => e.getBoundingClientRect().right))};
  }
  const note = document.querySelector('#hotspots td.c3:not(:empty)');
  out.note = note ? {top: note.getBoundingClientRect().top, w: note.getBoundingClientRect().width} : null;
  out.sw = document.documentElement.scrollWidth; out.cw = document.documentElement.clientWidth;
  return out; })()"""


@pytest.mark.parametrize("path", [PAGE, ART])
def test_g127_cards_at_400px(path):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=400, height=900, deviceScaleFactor=1, mobile=False)
        page.goto(_url(path) + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#hotspots')"), 30, "表")
        # 例は注記が出る組を含まないことがあるので、注記の出る組（指定なし・平均 30 分以上）を足して測る
        page.js(f"{FEED_JS}({json.dumps([_row(place='東京都江東区の長い場所の名前', arrive='09:00', start='10:00') for _ in range(2)], ensure_ascii=False)})")
        m = page.js(MEASURE_JS)
        assert m["sw"] <= m["cw"], m
        for key in ("hotspots", "placemonth"):
            t = m[key]
            assert t["display"] == "flex" and t["headHidden"], t
            assert t["right"] <= 400, t
            # 場所の文字が 1 文字ずつ折り返さない: 幅が 30px より大きく、高さが 3 行より低い
            place = t["c1"][1] if key == "hotspots" else t["c1"][0]   # 相手は 1 文字の名前なので、場所のセルを見る
            assert place["w"] > 30 and place["h"] < 3 * place["lh"], t
            # 2 行目の数字は 1 行目より下
            assert all(n > t["c1top"] + 4 for n in t["nums"]), t
        assert m["note"] and m["note"]["w"] > 100 and m["note"]["top"] > m["hotspots"]["nums"][0], m


PARTNER_MEASURE_JS = """(function(){
  const rows = [...document.querySelectorAll('#partners table.par:not(#hotspots):not(#placemonth) tbody tr')];
  return {n: rows.length, sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
    cards: rows.map((tr) => { const td = tr.querySelector('td[data-l="参考額"]'), r = td.getBoundingClientRect(), b = tr.getBoundingClientRect();
      return {display: getComputedStyle(tr).display, amountRight: r.right, amountLeft: r.left, amountScroll: td.scrollWidth <= td.clientWidth + 1, rowRight: b.right, text: td.textContent}; })};
})()"""


@pytest.mark.parametrize("path", [PAGE, ART])
def test_g128_partner_cards_at_400px(path):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=400, height=900, deviceScaleFactor=1, mobile=False)
        page.goto(_url(path) + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#hotspots')"), 30, "表")
        m = page.js(PARTNER_MEASURE_JS)
        assert m["n"] >= 1 and m["sw"] <= m["cw"], m
        for c in m["cards"]:
            assert c["display"] == "flex", c
            assert c["amountLeft"] >= 0 and c["amountRight"] <= 400 and c["rowRight"] <= 400, c
            assert c["amountScroll"], c
            assert c["text"].strip(), c
