"""SPEC §25 実際の Excel の貼り付け、荷主等の都合の一括入力、荷役のあとの待ち、比べるページの見積先（受入条件 G95〜G100）。

JS 側の同じ表（tests/data/waiting_vectors.json）は web/waiting.html の ?selftest=1 が通す（test_v237_waiting_page.py）。
ここでは Python 側の期待値の要点と、画面（ヘッドレスの Edge）の操作を確かめる。
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from shipcompare import waiting

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

VEC = {c["name"]: c for c in json.loads((ROOT / "tests" / "data" / "waiting_vectors.json").read_text(encoding="utf-8"))["cases"]}
PAGE = ROOT / "web" / "waiting.html"
LITE = ROOT / "web" / "compare_lite.html"
OWN_JS = (ROOT / "web" / "own.js").read_text(encoding="utf-8")
NOTE = "荷主等の都合の欄が無いので『はい』として計算しました。違う行は『いいえ』に変えてください"


def row(**kw):
    base = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A運輸", "arrive": "09:00", "start": "10:00",
            "end": "", "handler": "", "method": "", "shipper_fault": "はい"}
    base.update(kw)
    return base


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


# ---------------------------------------------------------------- G95 読み取りの広がり
def test_g95_date_families_all_read_as_the_same_day():
    exp = VEC["real_dates_all_families"]["expect"]
    assert exp["errors"] == [] and len(exp["rows"]) == 17
    assert {r["date"] for r in exp["rows"]} == {"2026-10-05"}
    era = VEC["real_dates_era_boundaries_and_errors"]["expect"]
    assert [r["date"] for r in era["rows"]] == ["2019-05-01", "2019-05-01", "2019-04-30", "1989-01-07", "2026-10-05"]
    assert [e["row"] for e in era["errors"]] == [5, 6, 7]
    noyear = VEC["real_dates_without_year_need_a_year"]["expect"]
    assert len(noyear["rows"]) == 2 and "年が分かりません" in noyear["errors"][0]["message"]


def test_g95_time_families_all_read_as_0900_and_1000():
    exp = VEC["real_times_all_families"]["expect"]
    assert exp["errors"] == []
    rows = [r for r in exp["rows"] if r["start"] == "10:00"]
    assert len(rows) == 11 and all(r["arrive"] == "09:00" for r in rows)
    assert exp["rows"][4]["start"] == "10:05"   # 「10時5分」
    assert len(VEC["real_times_errors"]["expect"]["errors"]) == 8   # 24:00 / 9:60 / 秒 61 / 25時 / 9.30 / 2400 / 9:0 / 午前


def test_g95_vehicle_families():
    exp = VEC["real_vehicle_classes"]["expect"]
    got = [r["vehicle_class"] for r in exp["rows"]]
    assert got[:8] == ["中型"] * 8 and got[8:11] == ["大型"] * 3 and got[11:16] == ["小型"] * 5 and got[16:20] == ["トレーラー"] * 4
    assert got[20:] == ["中型", "大型", "中型"]
    assert [e["row"] for e in exp["errors"]] == [24, 25, 26]   # 20t / 3t / 軽 は読まない（推測しない）


def test_g95_yes_no_words_and_whitespace():
    exp = VEC["real_yes_no_words"]["expect"]
    flags = [r["shipper_fault"] for r in exp["rows"]]
    assert flags == [True] * 9 + [False] * 7 + [True]
    assert [e["row"] for e in exp["errors"]] == [18]
    ws = VEC["real_whitespace_and_fullwidth_in_other_columns"]["expect"]
    assert ws["errors"] == [] and ws["rows"][0]["partner"] == "A運輸" and ws["rows"][0]["end"] == "10:30" and ws["rows"][0]["side_min"] == 20


def test_g95_all_new_families_have_vectors():
    for name in ("real_dates_all_families", "real_times_all_families", "real_vehicle_classes", "real_yes_no_words",
                 "real_whitespace_and_fullwidth_in_other_columns", "paste_realistic_japanese_block_zero_errors",
                 "post_wait_on_adds_depart_minus_end", "paste_with_fault_column_is_not_defaulted"):
        assert name in VEC, name


# ---------------------------------------------------------------- G96 実際の貼り付けの 1 ブロック（見出しは別名、書き方は混在、誤り 0）
def test_g96_realistic_block_has_zero_errors_in_python():
    c = VEC["paste_realistic_japanese_block_zero_errors"]
    rows, defaulted = waiting.parse_paste_full(c["text"])
    res = waiting.calc(rows, 2026)
    assert res["errors"] == [] and len(res["rows"]) == 6 and defaulted == ["shipper_fault"]
    assert [r["vehicle_class"] for r in res["rows"]] == ["中型", "中型", "中型", "小型", "大型", "トレーラー"]
    assert [r["date"] for r in res["rows"]][:5] == ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
    assert {r["partner"] for r in res["rows"]} == {"A運輸", "B商事"}   # 全角の「Ｂ商事　」も同じ相手
    assert all(r["shipper_fault"] is True for r in res["rows"])
    assert c["expect"]["errors"] == []


def test_g96_default_is_applied_only_when_the_fault_column_is_absent():
    rows, defaulted = waiting.parse_paste_full(VEC["paste_with_fault_column_is_not_defaulted"]["text"])
    assert defaulted == [] and [r["shipper_fault"] for r in rows] == ["渋滞", "荷主都合"]
    res = waiting.calc(rows, 2026)
    assert res["errors"] == [] and [r["shipper_fault"] for r in res["rows"]] == [False, True]
    rows2, d2 = waiting.parse_paste_full(VEC["paste_without_header_and_without_fault_column_is_defaulted"]["text"])
    assert d2 == ["shipper_fault"] and all(r["shipper_fault"] == "はい" for r in rows2)
    assert waiting.parse_paste_full("") == ([], [])


def test_g96_server_api_returns_defaulted_and_the_uis_show_the_note():
    spec = importlib.util.spec_from_file_location("serve_web_v25", ROOT / "tools" / "serve_web.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["serve_web_v25"] = mod
    spec.loader.exec_module(mod)
    res = mod.api_waiting_calc({"year": 2026, "text": VEC["paste_realistic_japanese_block_zero_errors"]["text"]})
    assert res["errors"] == [] and res["defaulted"] == ["shipper_fault"] and len(res["input_rows"]) == 6
    res2 = mod.api_waiting_calc({"year": 2026, "text": VEC["paste_with_fault_column_is_not_defaulted"]["text"]})
    assert res2["defaulted"] == []
    assert NOTE in PAGE.read_text(encoding="utf-8") and NOTE in OWN_JS
    for src in (PAGE.read_text(encoding="utf-8"), OWN_JS):
        assert "列の順は問わず見出しの名前で読みます" in src and "列の順は上の表と同じです" not in src


def test_g96_page_paste_in_headless_edge_has_zero_errors_and_shows_the_note():
    _edge_or_skip()
    import capture_web as cw
    text = VEC["paste_realistic_japanese_block_zero_errors"]["text"]
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri() + "?x=1")
        page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "ページ")
        page.js("document.querySelector('#paste').value = " + json.dumps(text, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "下書き")
        got = page.js("""(function(){
          const sel = (k) => [...document.querySelectorAll('#tbody select[data-k=' + k + ']')].map((s) => s.options[s.selectedIndex].text);
          return {rows: document.querySelectorAll('#tbody tr').length, errs: document.querySelector('#errs').textContent,
                  note: document.querySelector('#paste-note').textContent, msg: document.querySelector('#paste-msg').textContent,
                  noteVisible: document.querySelector('#paste-note').getBoundingClientRect().height > 0,
                  classes: sel('vehicle_class'), partners: [...document.querySelectorAll('#partners table.par:not(#hotspots) tbody tr td:first-child')].map((t) => t.textContent)};
        })()""")
    assert got["rows"] == 6 and got["errs"] == "" and got["note"] == NOTE and got["noteVisible"]
    assert "6 行を表に入れました" in got["msg"]
    assert got["classes"][0] == "4t車" and got["classes"][1] == "中型車"   # 選べる語に無い書き方も、入れた文字のまま表に出す
    assert sorted(got["partners"]) == ["A運輸", "B商事"]


# ---------------------------------------------------------------- G97 一括入力（荷主等の都合、契約の相手・車種の埋め下げ）
def test_g97_bulk_controls_exist_in_both_uis():
    page = PAGE.read_text(encoding="utf-8")
    for src in (page, OWN_JS):
        for t in ("この相手の行をすべて荷主等の都合にする", "契約の相手を下の行に埋める", "車種を下の行に埋める"):
            assert t in src, t


def test_g97_bulk_fault_and_fill_down_in_headless_edge():
    _edge_or_skip()
    import capture_web as cw
    text = VEC["paste_realistic_japanese_block_zero_errors"]["text"]
    with cw.edge_page(1920, 1080) as page:
        page.goto(PAGE.as_uri() + "?x=2")
        page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "ページ")
        page.js("document.querySelector('#paste').value = " + json.dumps(text, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "下書き")
        opts = page.js("[...document.querySelectorAll('#bulk-partner option')].map((o) => o.value)")
        assert opts[0] == "" and set(opts[1:]) == {"A運輸", "B商事"}
        # B商事の行だけを「荷主等の都合でない」にする
        page.js("document.querySelector('#bulk-partner').value = 'B商事'; document.querySelector('#b-fault-no').click()")
        page.wait(lambda: page.js("S.rows.filter((r) => r.shipper_fault === 'いいえ').length === 3"), 10, "いいえ")
        faults = page.js("S.rows.map((r) => r.partner + ':' + r.shipper_fault)")
        assert faults.count("A運輸:はい") == 3 and sum(1 for f in faults if f.endswith(":いいえ")) == 3
        # すべての行を「荷主等の都合」に戻す
        page.js("document.querySelector('#bulk-partner').value = ''; document.querySelector('#b-fault-yes').click()")
        page.wait(lambda: page.js("S.rows.every((r) => r.shipper_fault === 'はい')"), 10, "はい")
        # 結合セルを貼ったときのように空いた欄を、上の値で埋める
        page.js("S.rows[1].partner = ''; S.rows[2].partner = ''; S.rows[4].vehicle_class = ''; document.querySelector('#b-fill-partner').click()")
        page.wait(lambda: page.js("S.rows[1].partner === S.rows[0].partner"), 10, "埋める")
        page.js("document.querySelector('#b-fill-class').click()")
        page.wait(lambda: page.js("S.rows[4].vehicle_class === S.rows[3].vehicle_class"), 10, "車種を埋める")
        out = page.js("({p: S.rows.map((r) => r.partner), msg: document.querySelector('#bulk-msg').textContent})")
    assert out["p"][1] == out["p"][0] == out["p"][2] and "車種の空欄 1 か所" in out["msg"]


# ---------------------------------------------------------------- G98 荷役のあとの待ち
def test_g98_post_wait_off_by_default_and_on_adds_depart_minus_end():
    off = waiting.calc([row(start="09:30", end="09:52", depart="12:52", handler="荷主側")], 2026)["rows"][0]
    on = waiting.calc([row(start="09:30", end="09:52", depart="12:52", handler="荷主側", post_wait="はい")], 2026)["rows"][0]
    assert off["wait_min"] == 52 and off["post_wait"] is False and off["post_wait_min"] == 0 and off["amount"] == 1680
    # 52 + 180 = 232 分。30 分の差し引きは 1 回。通常は 120 − 30 = 90 分（3 単位）、2 時間超は 112 分（4 単位）。小型 1,680 / 2,010
    assert on["wait_min"] == 232 and on["post_wait_min"] == 180 and (on["wait_normal_units"], on["wait_over_units"]) == (3, 4)
    assert on["wait_fee"] == 3 * 1680 + 4 * 2010 == 13_080


def test_g98_post_wait_uses_one_30_minute_deduction_and_starts_the_2h_boundary_at_the_start():
    r = waiting.calc([row(start="09:30", end="09:52", depart="10:22", handler="荷主側", post_wait="はい")], 2026)["rows"][0]
    assert r["wait_min"] == 82 and r["wait_normal_units"] == 2 and r["wait_over_units"] == 0   # (82 − 30) = 52 分 → 2 単位
    # 指定の到着時刻 08:30 が到着 08:10 より遅いとき、待機は 08:30 から数える。運転者の荷役の 2 時間の境は 10:30 のまま
    c = VEC["post_wait_with_driver_handling_and_appointed"]["expect"]["rows"]
    assert c[0]["wait_min"] == 170 and c[0]["post_wait_min"] == 65 and c[1]["wait_min"] == 105
    assert (c[0]["handling_normal_units"], c[0]["handling_over_units"]) == (c[1]["handling_normal_units"], c[1]["handling_over_units"])
    mid = VEC["post_wait_across_midnight"]["expect"]["rows"][0]
    assert mid["post_wait_min"] == 70   # 23:30 → 00:40


def test_g98_post_wait_only_counts_for_shippers_fault_and_needs_end_and_depart():
    r = waiting.calc([row(start="09:30", end="09:52", depart="12:52", handler="荷主側", post_wait="はい", shipper_fault="いいえ")], 2026)["rows"][0]
    assert r["wait_min"] == 0 and r["post_wait_min"] == 0 and r["amount"] == 0
    errs = VEC["post_wait_needs_end_and_depart"]["expect"]["errors"]
    assert [e["row"] for e in errs] == [1, 2, 3]
    assert "荷役終了と出発" in errs[0]["message"] and "読めません" in errs[2]["message"]


def test_g98_post_wait_text_column_and_article_34_wording_in_both_uis():
    page = PAGE.read_text(encoding="utf-8")
    for src in (page, OWN_JS):
        assert "荷役のあとの待ちを荷主等の都合として数える" in src
        assert "「到着後、荷送人又は荷受人の責により待機した時間」" in src
        assert "標準運送約款 第 34 条（標準貨物自動車運送約款）" in src
        assert "荷役のあとの待ち ${r.post_wait_min} 分を含む" in src
        assert "既定では数えず" in src
    assert "r.post_wait ? 'はい' : ''" in page and "r.post_wait ? 'はい' : ''" in OWN_JS   # CSV の入力列


# ---------------------------------------------------------------- G99 比べるページ: 目安額が出せない手段と、見積を頼む先
def test_g99_lite_page_has_texts_and_contact_links_from_contacts_json():
    html = LITE.read_text(encoding="utf-8")
    assert "目安額は出せません（運賃表の全体を持っていないため）" in html and "見積を頼む先" in html
    contacts = json.loads((ROOT / "data" / "contacts.json").read_text(encoding="utf-8"))["contacts"]
    for key in ("takuhai", "rosen", "jitbox"):
        for c in contacts[key]:
            assert c["url"] in html, c["url"]
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "localStorage"):
        assert bad not in html, bad
    assert not re.search(r"<script[^>]+src\s*=", html, re.I) and not re.search(r"<link[^>]+href\s*=", html, re.I)
    assert not re.search(r"<(img|iframe|form)\b", html, re.I)
    # 窓口の記録が無い手段に、URL を作り出していない（軽貨物のハコベル・PickGo など）
    assert "hacobell" not in html.lower() and "pickgo.jp" not in html.lower()


def test_g99_lite_page_rows_in_headless_edge():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1920, 1080) as page:
        page.goto(LITE.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        got = page.js("""(function(){
          const o = {};
          document.querySelectorAll('table.res tr[data-opt]').forEach((tr) => { o[tr.dataset.opt] = {cls: tr.className, noamt: !!tr.querySelector('.noamt'), ask: tr.querySelector('.ask') ? tr.querySelector('.ask').textContent : '',
            links: [...tr.querySelectorAll('.ask a')].map((a) => a.href + '|' + a.target + '|' + a.rel)}; });
          return o;
        })()""")
        # 小さな荷では軽貨物が ○、路線便が ○。額を出せないと書き、頼む先を出す
        page.js("""(function(){ S.rows = [{n: 1, l: 60, w: 40, h: 30, kg: 20}]; document.querySelector('#from').value = '東京都'; document.querySelector('#to').value = '大阪府';
          document.querySelector('#km').value = '492'; render(); })()""")
        small = page.js("""(function(){ const o = {}; document.querySelectorAll('table.res tr[data-opt]').forEach((tr) => { o[tr.dataset.opt] = {cls: tr.className, noamt: !!tr.querySelector('.noamt'), ask: !!tr.querySelector('.ask')}; }); return o; })()""")
    # 額を出せない文は ○ / △ の行に出て、× の行には出ない
    assert got["rosen"]["noamt"] and got["rosen"]["ask"].startswith("見積を頼む先")
    assert got["rosen"]["links"] and all(x.endswith("|_blank|noopener") for x in got["rosen"]["links"])
    assert any("nipponexpress.com" in x for x in got["rosen"]["links"]) and any("meitetsuunyu.co.jp" in x for x in got["rosen"]["links"])
    assert got["kei"]["cls"] == "o-ng" and not got["kei"]["noamt"] and got["kei"]["ask"] == ""
    assert got["takuhai"]["cls"] == "o-ng" and got["takuhai"]["ask"] == ""
    assert got["kashikiri_4t"]["cls"] == "o-ok" and "公式の窓口は" not in got["kashikiri_4t"]["ask"] and "会社ごとに問い合わせます" in got["kashikiri_4t"]["ask"]
    assert not got["kashikiri_4t"]["links"]
    assert got["home"]["ask"].startswith("見積を頼む先") and "デモ" in got["home"]["ask"]
    assert small["kei"] == {"cls": "o-ok", "noamt": True, "ask": True}
    assert small["rosen"]["noamt"] and small["takuhai"]["ask"] and small["jitbox"]["cls"] == "o-check"


# ---------------------------------------------------------------- G100 画面写真
def test_g100_screenshots_exist():
    for n in ("2026-11-05_v237_standalone.jpg", "2026-11-05_v24_compare_lite.jpg", "2026-11-05_v25_paste_real.jpg"):
        assert (ROOT / "docs" / "assets" / n).stat().st_size > 30_000, n
