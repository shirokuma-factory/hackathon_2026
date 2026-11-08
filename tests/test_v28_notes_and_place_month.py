"""SPEC §28 偏りの読み方の注意、場所ごと・月ごとの表、取り次ぎ（受入条件 G119〜G123）。

- G119: designated_count・shipper_handling_count（Python）と、注記 3 種の出方（ヘッドレス Edge で画面と書面）。
- G120: by_place_month（Python）。JS との一致は tests/test_v237_waiting_page.py の ?selftest=1（試験表に by_place_month を足した）。
- G121: 場所ごと・月ごとの表と 28.2 の文、書面の取り次ぎの 1 文。
- G122: 協議の進め方の取適法の文。
- G123: 例の 8時台の行の指定の到着時刻、Artifact の版（G101〜G106 は tests/test_v26_artifact.py）。
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

NOTE_NO_DESIGNATED = "指定の到着時刻が無い到着が多い組です。相手に到着の時刻を決めてもらうか、自社で到着を遅らせて変わるかを確かめてください"
DOC_NO_DESIGNATED = "指定の到着時刻の無い到着を含みます"
NOTE_SHIPPER = "荷主側の荷役の時間を含む"
NOTE_FEW = "回数が少ない"
PM_LEAD = "特定荷主は 2026 年度から、施設ごと・月別の平均の荷待ち時間を分単位で測り、国に報告します。荷主の数字を、運送会社の記録で確かめるときに使えます。"
PM_CLOSE = "発荷主・着荷主様との調整が必要な場合は、この一覧をそのままお使いください。"
TEKIHO = ("取適法では、相手（元請など）が規模の基準（資本金か従業員数）に当たるとき、公正取引委員会・中小企業庁・国土交通大臣に知らせたことを理由にした"
          "取引の停止などの不利益な扱いが禁じられています（第 5 条第 1 項第 7 号）。相手が基準に当たらないときは使えません。トラック・物流Gメンの窓口への情報提供がこれに当たるかの公式の説明は確かめていません。")


def _row(**kw):
    base = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A", "place": "X", "arrive": "09:00", "start": "10:00", "shipper_fault": "はい"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- G119（Python）
def test_g119_designated_and_shipper_handling_counts():
    rows = [_row(arrive="09:00", appointed="09:00"), _row(arrive="09:10"), _row(arrive="09:20", appointed="09:30"),
            _row(arrive="09:30", start="10:00", end="10:30", handler="荷主側"),
            _row(arrive="09:40", start="10:00", end="10:30", handler="運転者", method="手積み"),
            _row(place="Y", arrive="11:00", start="12:00", end="12:20", handler="荷主側", shipper_fault="いいえ")]
    h = {(x["place"], x["band"]): x for x in waiting.calc(rows, 2026)["hotspots"]}
    x9 = h[("X", "9時台")]
    assert (x9["count"], x9["designated_count"], x9["shipper_handling_count"]) == (5, 2, 1)
    y11 = h[("Y", "11時台")]   # 荷主等の都合が いいえ の行でも、荷役をした人が荷主側なら数える
    assert (y11["count"], y11["designated_count"], y11["shipper_handling_count"]) == (1, 0, 1)


def test_g119_example_counts():
    res = waiting.calc(build_waiting_vectors.page_example_rows(), 2026)
    by = {(h["partner"], h["place"], h["band"]): h for h in res["hotspots"]}
    a8 = by[("架空 元請A物流", "江東区 地点07", "8時台")]
    b8 = by[("架空 B食品", "市川市 地点03", "8時台")]
    assert (a8["count"], a8["designated_count"], a8["shipper_handling_count"]) == (5, 5, 0)
    assert (b8["count"], b8["designated_count"], b8["shipper_handling_count"]) == (3, 3, 0)
    a9 = by[("架空 元請A物流", "江東区 地点07", "9時台")]
    assert (a9["count"], a9["designated_count"], a9["shipper_handling_count"]) == (1, 0, 1)


# ---------------------------------------------------------------- G120（Python）
def test_g120_by_place_month_python():
    rows = [_row(date="2026-10-05", start="10:00"), _row(date="2026-10-06", start="09:51"),   # X・10 月: 60 と 51 → 平均 55.5 → 56
            _row(partner="B", date="2026-11-02", start="11:15"),                                 # X・11 月（相手が違っても場所と月で数える）
            _row(place="", date="2026-10-07", start="09:40"), _row(place="  ", date="2026-11-03", start="10:00"),
            _row(place="Y", date="2026-09-30", start="12:00"), _row(place="Y", date="2026-10-01", start="09:20", shipper_fault="いいえ")]
    got = waiting.calc(rows, 2026)["by_place_month"]
    assert [(g["place"], g["month"], g["count"], g["wait_minutes"], g["avg_minutes"], g["over_1h_count"]) for g in got] == [
        ("X", "2026-10", 2, 111, 56, 1), ("X", "2026-11", 1, 135, 135, 1),
        ("Y", "2026-09", 1, 180, 180, 1), ("Y", "2026-10", 1, 20, 20, 0),   # 荷主等の都合が いいえ でも、定期報告の定義（§29.1）では数える
        ("（場所の記入なし）", "2026-10", 1, 40, 40, 0), ("（場所の記入なし）", "2026-11", 1, 60, 60, 1)]
    assert waiting.calc([], 2026)["by_place_month"] == []


def test_vectors_have_the_new_fields():
    data = json.loads((ROOT / "tests" / "data" / "waiting_vectors.json").read_text(encoding="utf-8"))
    names = {c["name"] for c in data["cases"]}
    assert {"hotspots_designated_and_shipper_handling_counts", "by_place_month_basic"} <= names
    for c in data["cases"]:
        assert "by_place_month" in c["expect"], c["name"]
        assert all("designated_count" in h and "shipper_handling_count" in h for h in c["expect"]["hotspots"]), c["name"]


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


def _url(path: Path) -> str:
    return PAGE.as_uri() if path == PAGE else _shell(ART)


def _notes_rows() -> list[dict]:
    """注記 3 種が出る組と出ない組（すべて相手 A）。
    X 9時台: 4 回、指定なし、荷主側の荷役あり。Y 11時台: 2 回、指定あり。Z 12時台: 3 回のうち指定 2 回、荷主側なし。"""
    rows = [_row(arrive="09:00", start="10:30") for _ in range(3)]
    rows.append(_row(arrive="09:10", start="10:40", end="11:00", handler="荷主側"))
    rows += [_row(place="Y", arrive="11:00", appointed="11:00", start="12:15", date=f"2026-11-0{i}") for i in (2, 3)]
    rows += [_row(place="Z", arrive="12:00", appointed="12:00", start="12:50"), _row(place="Z", arrive="12:05", appointed="12:00", start="12:55"),
             _row(place="Z", arrive="12:10", start="13:00")]
    return rows


FEED_JS = """(function(rows){
  S.noStore = true; S.rows = rows.map((r) => ({...blankRow(), ...r})); S.partner = ''; renderTable(); doCalc();
  const cells = (sel) => [...document.querySelectorAll(sel)].map((tr) => [...tr.children].map((td) => td.textContent));
  return {rows: cells('#hotspots tbody tr'), doc: document.querySelector('#wait-doc').textContent,
          pm: cells('#placemonth tbody tr'), dpm: cells('#doc-placemonth tbody tr'),
          copy: draftText(S.res.by_partner.find((x) => x.partner === S.partner))};
})"""


@pytest.mark.parametrize("path", [PAGE, ART])
def test_g119_g121_notes_and_place_month_on_screen_and_in_document(path):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(_url(path) + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#hotspots')"), 30, "表")
        info = page.js(f"{FEED_JS}({json.dumps(_notes_rows(), ensure_ascii=False)})")
        by = {(r[1], r[2]): r for r in info["rows"]}
        # 画面の注記（最後の列）。当てはまるものだけを括弧で後ろに
        assert by[("X", "9時台")][8] == f"（{NOTE_NO_DESIGNATED}）"   # 2026-11-08 r5b: 画面の表もメールと同じ数え方（荷役開始まで）にしたので、「荷主側の荷役の時間を含む」は付かない
        assert by[("Y", "11時台")][8] == f"（{NOTE_FEW}）"
        assert by[("Z", "12時台")][8] == ""
        # 書面（上位 3 組）の行にも付く。書面の言い方は別
        d = info["doc"]
    # 2026-11-08 の直しに合わせて: 相談の文は「荷待ち（到着から荷役開始まで）が、平均…」の言い方になり、注記（指定の到着時刻のない到着）は各文でなく一覧の後ろに 1 回だけ書く。場所・時間帯ごとの回数・平均・最長は残っている
        assert "Xでは、9時台に到着した4回の荷待ち（到着から荷役開始まで）が、平均90分、最長90分でした。" in d
        assert d.count("指定の到着時刻のない到着が多く含まれます。") == 1 and "貴社側で荷役をしていただいた回は、荷役が終わるまで" in d   # 注記は一覧の後ろに 1 回、荷主側の荷役は冒頭の 1 文目に（2026-11-08）
        assert "Yでは、11時台に到着した2回の荷待ち（到着から荷役開始まで）が、平均75分、最長75分でした。回数が少ないため、参考としてご覧ください。" in d
        assert "Zでは、12時台に到着した3回の荷待ち（到着から荷役開始まで）が、平均50分、最長50分でした。" in d and NOTE_NO_DESIGNATED not in d
        assert "でした。指定の到着時刻のない到着が多く含まれます。貴社側で荷役をしていただいた時間も含まれています。回数が少ないため、参考としてご覧ください。" not in d
        # 場所ごと・月ごと（画面）: 場所の文字の順、月の順。X は 10 月に 4 回、Y は 11 月、Z は 10 月
        assert [r[:2] for r in info["pm"]] == [["X", "2026 年 10 月"], ["Y", "2026 年 11 月"], ["Z", "2026 年 10 月"]]
        assert [r[2] for r in info["pm"]] == ["4", "2", "3"]
        screen = page.js("document.querySelector('#partners').textContent")
        assert PM_LEAD in screen and "場所ごと・月ごとの平均の荷待ち（分）" in screen
        # 書面: 相談の節の最後に表、続けて取り次ぎの 1 文
        assert [r[:2] for r in info["dpm"]] == [["X", "2026年10月"], ["Y", "2026年11月"], ["Z", "2026年10月"]]
        consult = d.split("荷待ちを減らすためのご相談")[1].split("参考：契約に入れていただきたい項目")[0]
        assert consult.rstrip().endswith(PM_CLOSE) and consult.index("ご相談させてください") < consult.index("場所") < consult.index(PM_CLOSE)
        # コピーする文
        assert "参考：荷待ちを減らすためのご相談" in info["copy"] and "Y\t2026年11月\t2\t75\t2" in info["copy"]
        assert info["copy"].index("ご相談させてください") < info["copy"].index(PM_CLOSE) < info["copy"].index("参考：契約に入れていただきたい項目")


@pytest.mark.parametrize("path", [PAGE, ART])
def test_g121_g123_example_8am_rows_and_place_month_tables(path):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(_url(path) + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#hotspots')"), 30, "表")
        rows = page.js("[...document.querySelectorAll('#hotspots tbody tr')].map((tr) => [...tr.children].map((td) => td.textContent))")
        eight = [r for r in rows if r[2] == "8時台"]
        assert len(eight) == 2 and all(NOTE_NO_DESIGNATED not in r[8] for r in eight)
        ten = [r for r in rows if r[2] == "10時台"]
        assert ten and all(NOTE_NO_DESIGNATED not in r[8] for r in ten)   # 10時台は指定が無いが、平均が 30 分未満なので注記は付けない（§29.3）
        doc = page.js("document.querySelector('#wait-doc').textContent")
    # 2026-11-08 の直しに合わせて: 相談の文は「荷待ち（到着から荷役開始まで）が、平均…」の言い方になり、注記（指定の到着時刻のない到着）は各文でなく一覧の後ろに 1 回だけ書く。場所・時間帯ごとの回数・平均・最長は残っている
        assert "江東区の地点07では、8時台に到着した5回の荷待ち（到着から荷役開始まで）が、平均98分、最長135分でした。" in doc
        assert "江東区の地点07では、8時台に到着した5回の荷待ち（到着から荷役開始まで）が、平均98分、最長135分でした。指定" not in doc
        pm = page.js("[...document.querySelectorAll('#placemonth tbody tr')].map((tr) => [...tr.children].map((td) => td.textContent))")
        assert pm == [["川崎市 地点12", "2026 年 10 月", "3", "32", "0"], ["市川市 地点03", "2026 年 10 月", "5", "60", "3"],
                      ["江東区 地点07", "2026 年 10 月", "10", "58", "5"]]
        dpm = page.js("[...document.querySelectorAll('#doc-placemonth tbody tr')].map((tr) => [...tr.children].map((td) => td.textContent))")
        assert dpm == [["川崎市 地点12", "2026年10月", "2", "40", "0"], ["江東区 地点07", "2026年10月", "10", "58", "5"]]   # 書面は、その相手の記録だけ
        assert PM_LEAD in page.js("document.querySelector('#partners').textContent")
        assert PM_CLOSE in doc


def test_example_8am_rows_in_python():
    rows = build_waiting_vectors.page_example_rows()
    eight = [r for r in rows if r["arrive"].startswith("08")]
    assert len(eight) == 8 and all(r["appointed"] == "08:00" for r in eight)
    # 実際の到着は指定の 10 分前（07:50）から指定の時刻までの間。8時台に入るのは 08:00 だけなので、8時台の行はすべて 08:00 着
    assert all(r["arrive"] == "08:00" for r in eight)
    assert {r["place"] for r in eight} == {"江東区 地点07", "市川市 地点03"}


# ---------------------------------------------------------------- G122
@pytest.mark.parametrize("path", [PAGE, ART, ROOT / "web" / "own.js"])
def test_g122_tekiho_sentence_has_the_condition_and_article(path):
    h = path.read_text(encoding="utf-8")
    assert TEKIHO in h
    assert "報復" not in h and "条番号は未確認" not in h


def test_g122_process_panel_shows_the_sentence():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1440, 900) as page:
        page.goto(PAGE.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#process')"), 30, "協議の進め方")
        assert TEKIHO in page.js("document.querySelector('#process').textContent")
