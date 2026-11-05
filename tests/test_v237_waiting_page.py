"""SPEC §23.7 荷待ちの試験表と単独ページ web/waiting.html（受入条件 G82・G83）。

- 試験表 tests/data/waiting_vectors.json は tools/build_waiting_vectors.py が waiting.py の出力から作る。ここで Python が表と一致することを確かめる。
- web/waiting.html は tools/build_waiting_page.py がテンプレートから作る。表と同じ内容か、通信をしないかを文字列で確かめ、
  ヘッドレスの Edge で ?selftest=1 を開いて JS が同じ表を通ることを確かめる。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from shipcompare import waiting

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_waiting_page  # noqa: E402
import build_waiting_vectors  # noqa: E402

VECTORS = ROOT / "tests" / "data" / "waiting_vectors.json"
PAGE = ROOT / "web" / "waiting.html"
TARIFF = ROOT / "data" / "tariffs" / "mlit_standard_fare_waiting.json"
OWN_JS = (ROOT / "web" / "own.js").read_text(encoding="utf-8")


def load_vectors() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 試験表（Python 側）
def test_vectors_cover_the_required_situations():
    names = {c["name"] for c in load_vectors()["cases"]}
    for need in ("q29_small_1h_is_1680", "q29_small_2h15_is_7050", "medium_52min_x11_is_19360", "shipper_handling_counts_as_waiting",
                 "driver_handling_across_2h_boundary", "not_shipper_fault_wait_is_zero", "midnight_crossing",
                 "appointed_early_arrival_counts_from_appointed", "appointed_late_arrival_counts_from_arrival", "appointed_none_and_equal",
                 "errors_with_reasons", "example_rows_11_at_chiten07", "paste_with_header", "paste_without_header"):
        assert need in names, need


def test_g82_python_matches_the_vectors_file():
    data = load_vectors()
    assert len(data["cases"]) >= 40
    for c in data["cases"]:
        defaulted: list = []
        notes: list = []
        if c.get("paste"):
            rows, defaulted, notes = waiting.parse_paste_ex(c["text"])
        else:
            rows = c["rows"]
        res = waiting.calc(rows, c["year"])
        got = {"rows": res["rows"], "by_partner": res["by_partner"], "hotspots": res["hotspots"], "by_place_month": res["by_place_month"], "errors": res["errors"]}
        if c.get("paste"):
            got["input_rows"] = rows
            got["defaulted"] = defaulted
            got["notes"] = notes
        assert json.loads(json.dumps(got, ensure_ascii=False)) == c["expect"], c["name"]


def test_vectors_file_is_what_the_generator_writes():
    assert json.loads(json.dumps(build_waiting_vectors.build(), ensure_ascii=False)) == load_vectors()


def test_vector_key_numbers_are_the_known_amounts():
    by = {c["name"]: c["expect"] for c in load_vectors()["cases"]}
    assert by["q29_small_1h_is_1680"]["rows"][0]["amount"] == 1680
    assert by["q29_small_2h15_is_7050"]["rows"][0]["amount"] == 7050
    assert by["medium_52min_x11_is_19360"]["by_partner"][0]["amount"] == 19360  # 昔の G74 の入力を、そのまま残している
    ex = by["example_rows_11_at_chiten07"]["by_partner"][0]
    assert (ex["count"], ex["amount"], ex["over_1h_count"], ex["over_2h_count"]) == (11, 31_020, 3, 1)


def test_example_rows_in_own_js_and_the_page_match_the_vector_example():
    """「例を入れる」の行は own.js・waiting.html・試験表で同じ（8 行は 52 分、2 行は 75 分、1 行は指定の到着時刻つき）。"""
    ex = build_waiting_vectors.example_rows()
    res = waiting.calc(ex, 2026)
    waits = sorted(r["wait_min"] for r in res["rows"])
    assert waits == [52] * 8 + [75] * 2 + [105]
    last = res["rows"][-1]
    assert (last["appointed"], last["wait_from"], last["handler"]) == ("08:30", "指定の到着時刻", "運転者")
    assert (last["handling_normal_units"], last["handling_over_units"]) == (1, 1)  # 2 時間の境（10:30）をまたぐ
    # 地図の画面の例は waiting.html と同じもの（web/waiting_calc.js、SPEC §30.4）。この 11 行の例は試験表の入力として残している
    assert "WaitingShared.exampleRows" in OWN_JS
    shared = (ROOT / "web" / "waiting_calc.js").read_text(encoding="utf-8")
    for frag in ("江東区 地点07", "架空 元請A物流"):
        assert frag in shared, frag


# ---------------------------------------------------------------- ページ（文字列）
def test_page_is_up_to_date_and_embeds_the_same_tariff_and_vectors():
    assert PAGE.read_text(encoding="utf-8") == build_waiting_page.build()
    html = PAGE.read_text(encoding="utf-8")
    m = re.search(r"const TARIFF = (\{.*?\});\nconst VECTORS", html, re.S)
    assert m
    assert json.loads(m.group(1)) == json.loads(TARIFF.read_text(encoding="utf-8"))
    m = re.search(r"const VECTORS = (\{.*?\});\n", html, re.S)
    assert json.loads(m.group(1)) == load_vectors()


def test_g83_page_makes_no_network_calls():
    html = PAGE.read_text(encoding="utf-8")
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource", "importScripts", "import(", "navigator.serviceWorker"):
        assert bad not in html, bad
    assert not re.search(r"<script[^>]+src\s*=", html, re.I)
    assert not re.search(r"<link[^>]+href\s*=", html, re.I)  # 外部の CSS・フォントも読まない
    assert not re.search(r"@import|url\(\s*['\"]?https?:", html, re.I)
    assert not re.search(r"<(img|iframe|embed|object|video|audio|source)\b", html, re.I)
    assert "<form" not in html


def test_page_has_the_required_texts_and_sections():
    html = PAGE.read_text(encoding="utf-8")
    for text in ("参考額は告示の表に当てた計算です。請求するか、いくらにするかは運送会社が決めます。法的な判断は弁護士・運輸支局などに確かめてください",
                 "入れた記録はこの PC の中だけで計算し、外に送りません",
                 "改正法 Q&A 問2-2・2-3", "標準運送約款 第 34 条", "取適法", "Q42・Q132", "トラック・物流Gメンの情報提供の窓口",
                 "例を入れる", "Excel から貼り付ける", "印刷して残す", "CSV で書き出す", "契約の相手ごと", "根拠", "協議の進め方", "指定の到着時刻", "附帯業務",
                 "次回からの契約の書面に、待機時間料と積込料・取卸料の欄を入れてもらうよう頼みます",
                 "過去分を求めるかどうかは、運送会社が決めます",
                 "国交省のリーフレットは、記録を待機時間料などを収受する根拠にできると書いています",
                 "公正取引委員会・中小企業庁・国土交通大臣に知らせたことを理由にした取引の停止などの不利益な扱いが禁じられています（第 5 条第 1 項第 7 号）",
                 "https://truck-logistics-gmen.mlit.go.jp/s/"):
        assert text in html, text
    # 協議の進め方は計算の結果が無いときも描く（renderResult の空の分岐にも processHtml がある）
    body = html[html.index("function renderResult()"):html.index("function collectRows()")]
    assert body.count("processHtml()") == 2


def test_own_js_has_the_process_panel_and_new_columns():
    for text in ("協議の進め方", "w-process", "次回からの契約の書面に、待機時間料と積込料・取卸料の欄を入れてもらうよう頼みます",
                 "国交省のリーフレットは、記録を待機時間料などを収受する根拠にできると書いています",
                 "（第 5 条第 1 項第 7 号）", "https://truck-logistics-gmen.mlit.go.jp/s/",
                 "t: '指定の到着時刻'", "t: '附帯業務の内容'", "t: '附帯業務の分'"):
        assert text in OWN_JS, text


# ---------------------------------------------------------------- ヘッドレスの Edge（G82 の JS 側）
def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001  (見つからないと SystemExit のことがある)
        pytest.skip("Microsoft Edge が見つかりません")


def test_g82_js_selftest_passes_in_headless_edge():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 800) as page:
        page.goto(PAGE.as_uri() + "?selftest=1")
        page.wait(lambda: page.js("document.querySelector('#selftest') && document.querySelector('#selftest').textContent.length > 0"), 30, "自己確認")
        text = page.js("document.querySelector('#selftest').textContent")
    n = len(load_vectors()["cases"])
    assert text == f"SELFTEST PASS {n + 4}/{n + 4}", text


def test_page_ui_example_draft_and_csv_in_headless_edge():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!document.querySelector('#b-example')"), 30, "ページ")
        # 計算の結果が無いときも協議の進め方・守る線・根拠が出る
        assert page.js("!!document.querySelector('#process') && document.querySelector('#protect').textContent.includes('外に送りません')")
        page.js("document.querySelector('#b-example').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "下書き")
        info = page.js("""(function(){
          const rows = document.querySelectorAll('#tbody tr').length;
          const sum = document.querySelector('#partners table.par tbody tr').textContent;
          const doc = document.querySelector('#wait-doc').textContent;
          const side = document.querySelector('#process').getBoundingClientRect(), d = document.querySelector('#wait-doc').getBoundingClientRect();
          return {rows, sum, sentence: doc.includes('計 33,550 円（税抜）'), side: doc.includes('検品・仕分け'),
                  beside: side.left >= d.right - 1 && Math.abs(side.top - d.top) < 40, csv: typeof csvText === 'function' ? csvText() : ''};
        })()""")
    assert info["rows"] == 18 and "33,550" in info["sum"] and info["sentence"] and info["side"]
    assert info["beside"], "協議の進め方は書面の下書きの横に出す"
    csv = info["csv"]
    assert csv.startswith("﻿日付,車番,") and "指定の到着時刻,附帯業務の内容,附帯業務の分,荷役のあとの待ちを荷主等の都合として数える,待機の数え始め" in csv.split("\r\n")[0]
    assert len(csv.strip().split("\r\n")) == 19
