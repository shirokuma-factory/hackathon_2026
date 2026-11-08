"""SPEC §27 どこで・いつ待っているかと、荷待ちを減らす相談（受入条件 G113〜G118）。

- G113: hotspots の計算（Python）。G114 は tests/test_v237_waiting_page.py の ?selftest=1（試験表に hotspots と over_1h_share を足した）。
- G115・G116・G118: ヘッドレス Edge で、画面の表・根拠の欄・書面（画面の版とコピーの文）・例の対比を確かめる。
- G117: Artifact の版の再生成は tests/test_v26_artifact.py（G101〜G106）が通ることで確かめる。
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
import build_waiting_vectors  # noqa: E402

PAGE = ROOT / "web" / "waiting.html"
ART = ROOT / "web" / "publish" / "waiting_artifact.html"


def _row(**kw):
    base = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A", "place": "X", "arrive": "09:00", "start": "10:00", "shipper_fault": "はい"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- G113
def test_g113_hotspots_basic_and_empty_place():
    rows = [_row(arrive="08:10", start="09:20"), _row(arrive="08:40", start="09:20"),
            _row(place="Y", arrive="13:05", start="14:05", shipper_fault="いいえ")]
    h = waiting.calc(rows, 2026)["hotspots"]
    assert (h[0]["partner"], h[0]["place"], h[0]["band"], h[0]["count"], h[0]["wait_minutes"], h[0]["avg_minutes"], h[0]["max_minutes"], h[0]["over_1h_count"]) == \
        ("A", "X", "8時台", 2, 110, 55, 70, 1)
    assert (h[1]["partner"], h[1]["place"], h[1]["band"], h[1]["count"], h[1]["wait_minutes"]) == ("A", "Y", "13時台", 1, 0)
    assert len(h) == 2
    blank = waiting.calc([_row(place=""), _row(place="   ")], 2026)["hotspots"]
    assert [x["place"] for x in blank] == ["（場所の記入なし）"] and blank[0]["count"] == 2


def test_hotspots_group_by_actual_arrival_and_order():
    rows = [_row(arrive="08:50", start="09:50", appointed="09:30"),   # 指定より早い到着ではなく、実際の到着（8時台）で分ける
            _row(arrive="09:10", start="10:10"), _row(arrive="09:20", start="09:55"),
            _row(partner="B", arrive="09:05", start="10:05"), _row(arrive="10:00", start="11:00")]
    h = waiting.calc(rows, 2026)["hotspots"]
    assert [(x["partner"], x["band"], x["wait_minutes"]) for x in h] == [("A", "9時台", 95), ("A", "10時台", 60), ("B", "9時台", 60), ("A", "8時台", 20)]
    # 同じ合計は 相手・場所・時間帯の文字の順（"10時台" は "9時台" より前）
    same = waiting.calc([_row(arrive="09:00", start="10:00"), _row(arrive="10:00", start="11:00")], 2026)["hotspots"]
    assert [x["band"] for x in same] == ["10時台", "9時台"]


def test_hotspots_average_rounds_half_up_and_over_1h_share():
    h = waiting.calc([_row(start="09:50"), _row(start="09:51")], 2026)["hotspots"]   # 50 と 51 分: 平均 50.5 → 51
    assert h[0]["avg_minutes"] == 51
    res = waiting.calc([_row(start="10:00"), _row(start="09:30"), _row(start="09:30")], 2026)   # 3 回のうち 1 回 → 33%
    assert res["by_partner"][0]["over_1h_share"] == 33 and res["by_partner"][0]["over_1h_count"] == 1
    res = waiting.calc([_row(start="10:00"), _row(start="09:30")], 2026)   # 1 / 2 → 50%
    assert res["by_partner"][0]["over_1h_share"] == 50
    res = waiting.calc([_row(start="10:00")] * 3 + [_row(start="09:30")] * 5, 2026)   # 3 / 8 = 37.5 → 38
    assert res["by_partner"][0]["over_1h_share"] == 38
    assert waiting.calc([], 2026)["hotspots"] == []


# ---------------------------------------------------------------- G118 の例（Python 側）
def test_example_has_the_8_vs_10_contrast_at_the_same_place():
    rows = build_waiting_vectors.page_example_rows()
    assert len(rows) <= 20
    res = waiting.calc(rows, 2026)
    assert not res["errors"] and len(res["by_partner"]) >= 2
    assert len({r["place"] for r in rows}) >= 3
    by = {(h["partner"], h["place"], h["band"]): h for h in res["hotspots"]}
    seen = 0
    for (partner, place, band), h in by.items():
        if band == "8時台" and (partner, place, "10時台") in by:
            assert h["avg_minutes"] >= 60 and by[(partner, place, "10時台")]["avg_minutes"] <= 20
            seen += 1
    assert seen >= 1
    assert (len(rows), sum(r["wait_min"] for r in res["rows"]), sum(r["amount"] for r in res["rows"])) == (18, 982, 46_780)
    assert [(p["partner"], p["count"], p["amount"]) for p in res["by_partner"]] == [("架空 元請A物流", 12, 33_550), ("架空 B食品", 6, 13_230)]


# ---------------------------------------------------------------- 画面（ヘッドレス Edge）
def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _shell(path: Path) -> str:
    html = artifact_build.wrap_for_test(path.read_text(encoding="utf-8"), None)
    out = Path(tempfile.mkdtemp(prefix="artifact_shell_")) / (path.stem + ".html")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return out.as_uri()


def test_example_rows_of_the_page_are_the_python_example():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 800) as page:
        page.goto(PAGE.as_uri() + "?selftest=1")
        page.wait(lambda: page.js("!!document.querySelector('#selftest') && document.querySelector('#selftest').textContent.length > 0"), 30, "自己確認")
        got = page.js("exampleRows()")
    want = build_waiting_vectors.page_example_rows()
    assert len(got) == len(want)
    for g, w in zip(got, want):
        for k, v in g.items():
            assert v == w.get(k, ""), (k, g, w)


SCREEN_JS = """(function(){
  const t = document.querySelector('#hotspots');
  const rows = t ? [...t.querySelectorAll('tbody tr')].map(tr => [...tr.children].map(td => td.textContent)) : [];
  return {rows, head: t ? [...t.querySelectorAll('th')].map(th => th.textContent) : [],
          line: document.querySelector('#partners').textContent.includes('この期間に荷主の都合で待った時間は、全部の荷主を合わせると、合計 16 時間 22 分です。'),
          basis: document.querySelector('#basis').textContent, doc: document.querySelector('#wait-doc').textContent,
          h2: [...document.querySelectorAll('#partners h2')].map(h => h.firstChild.textContent)};
})()"""


@pytest.mark.parametrize("path", [PAGE, ART])
def test_g115_g116_g118_screen_basis_and_document(path):
    _edge_or_skip()
    import capture_web as cw
    url = PAGE.as_uri() if path == PAGE else _shell(ART)
    with cw.edge_page(1920, 1080) as page:
        page.goto(url + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#hotspots')"), 30, "表")
        info = page.js(SCREEN_JS)
        # G115: 上位 5 組、合計 0 の組を出さない。列は 27.2 のとおり
        assert info["h2"] == ["荷主ごと", "待ち時間の長い場所と時間帯"] and info["line"]   # 文言の直し 2026-11-07
        assert info["head"] == ["荷主", "場所", "到着の時間帯", "回数", "平均の荷待ち", "最長", "1 時間以上の回数", "参考額（円）", "メモ"]   # 文言の直し 2026-11-07
        rows = info["rows"]
        assert len(rows) == 5
        assert rows[0][:7] == ["架空 元請A物流", "江東区 地点07", "8時台", "5", "98 分", "135 分", "5"]
        assert rows[1][:3] == ["架空 B食品", "市川市 地点03", "8時台"]
        assert not any(r[0] == "架空 B食品" and r[1] == "川崎市 地点12" for r in rows)   # 合計 0 の組（B・地点12・13時台）は出さない
        # G118: 同じ場所の 8時台と 10時台の対比
        a8 = next(r for r in rows if r[:3] == ["架空 元請A物流", "江東区 地点07", "8時台"])
        a10 = next(r for r in rows if r[:3] == ["架空 元請A物流", "江東区 地点07", "10時台"])
        assert int(a8[4].split()[0]) >= 60 and int(a10[4].split()[0]) <= 20
        # 根拠の欄: 27.2 の 3 項目
        b = info["basis"]
        assert "荷待ちの発生を認識している荷主は約 20%" in b and "トラック輸送状況の実態調査、2020 年度" in b
        assert "1時間以上の荷待ち又は2時間以上の荷待ち・荷役等を恒常的にさせている行為" in b
        assert "架空 元請A物流 42%（5 / 12 回）" in b and "架空 B食品 50%（3 / 6 回）" in b and "「恒常的」に当たるかは、このツールは判断しません" in b
        assert "12 時間 26 分" in b and "10 時間 38 分" in b and "原則 13 時間まで" in b and "改善基準告示" in b
        # G116: 書面にご相談が入り、その相手の上位 3 組を出す。Gメンの「恒常的」の文は書面に入れない
        d = info["doc"]
        assert "荷待ちを減らすためのご相談" in d and "恒常的" not in d
        assert "江東区の地点07では、8時台に到着した5回の荷待ち（到着から荷役開始まで）が、平均98分、最長135分でした。" in d   # 不具合探しの直しに合わせて 2026-11-08
        assert "川崎市の地点12では、13時台に到着した2回の荷待ち（到着から荷役開始まで）が、平均40分、最長40分でした。" in d
        assert "江東区の地点07では、10時台に到着した4回の荷待ち（到着から荷役開始まで）が、平均15分、最長20分でした。" in d
        assert d.count("上の時間帯には、指定の到着時刻のない回が多く含まれます。") == 1   # どの文にも付けず、一覧の後ろに 1 回だけ（§29.3）
        assert "江東区の地点07では、9時台に到着した" not in d   # 4 組目は出さない（上位 3 組まで）
        assert "荷待ちが減れば、待機時間料の対象も減ります。到着の時刻の指定、受付の予約、荷役を始める順番などの見直しをご相談させてください。" in d
        assert "荷待ちが残る場合に備えて、次回からの契約の書面に、" in d
        assert d.index("荷待ちを減らすためのご相談") < d.index("参考：契約に入れていただきたい項目（案）")
        # 別の相手に切り替えると、その相手の組だけが出る
        page.js("document.querySelector('tr[data-pick=\"架空 B食品\"]').click()")
        d2 = page.js("document.querySelector('#wait-doc').textContent")
        consult = d2.split("荷待ちを減らすためのご相談")[1].split("参考：契約に入れていただきたい項目")[0]
        assert "市川市の地点03では、8時台に到着した3回の荷待ち（到着から荷役開始まで）が、平均90分、最長100分でした。" in consult and "江東区" not in consult
        # コピーする文
        text = page.js("draftText(S.res.by_partner.find((x) => x.partner === S.partner))")
        assert "参考：荷待ちを減らすためのご相談" in text and "恒常的" not in text
        assert "・市川市の地点03では、10時台に到着した2回の荷待ち（到着から荷役開始まで）が、平均15分、最長15分でした。指定の到着時刻がある回は、その時刻から数えています。荷主等の都合によらない回も含めた、全部の回の数字です。回数が少ないため、参考としてご覧ください。" in text   # 2026-11-08 の最終評価に合わせて: メールの文に数え方の注記が付く
        assert text.index("参考：荷待ちを減らすためのご相談") < text.index("参考：契約に入れていただきたい項目（案）")
        assert "荷待ちが残る場合に備えて、次回からの契約の書面に、" in text


def test_g116_copy_button_in_artifact_has_the_consult_section():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1440, 900) as page:
        page.goto(_shell(ART) + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#b-copydoc')"), 30, "例")
        page.js("Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: () => Promise.reject(new Error('denied'))}}); document.querySelector('#b-copydoc').click(); document.querySelector('#b-copydoc').click()")   # 1 回目は空欄の警告だけ
        page.wait(lambda: page.js("document.querySelector('#copybox').hidden === false"), 10, "枠")
        text = page.js("document.querySelector('#copyta').value")
    assert "参考：荷待ちを減らすためのご相談" in text and "恒常的" not in text
    assert text.count("回の荷待ち（到着から荷役開始まで）が、平均") == 3   # 相手 A の上位 3 組


def test_hotspot_texts_in_both_builds():
    for p in (PAGE, ART):
        h = p.read_text(encoding="utf-8")
        assert "荷待ちを減らすためのご相談" in h and "どこで・いつ待っているか" in h and "function hotspots(" in h
    assert "const ARTIFACT = true;" in ART.read_text(encoding="utf-8")


def test_vectors_have_hotspots_and_over_1h_share():
    data = json.loads((ROOT / "tests" / "data" / "waiting_vectors.json").read_text(encoding="utf-8"))
    names = {c["name"] for c in data["cases"]}
    assert {"hotspots_g113_basic", "hotspots_empty_place_ordering_and_rounding", "example_waiting_page_18_rows"} <= names
    for c in data["cases"]:
        assert "hotspots" in c["expect"], c["name"]
        assert all("over_1h_share" in p for p in c["expect"]["by_partner"]), c["name"]
