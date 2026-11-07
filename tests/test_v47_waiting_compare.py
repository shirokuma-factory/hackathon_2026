"""SPEC §47 道具の採点 r7a と実務の効き目 r5 を受けた、荷待ち・ドライバーの 1 枚・送り方比べの修正。

- 夜中をまたぐ受付（23:50 着・受付 0:20）も、受付から数えた額を書面の主な額にする（Python と JS は試験表で同じ値）。
- 「8:00頃」「ごろ」は 8:00 として読み、見出し「相手」は契約の相手。
- 照らし合わせ: 場所名に空白があっても列がずれない、書面の参考額の列を書面の額にする、運送会社で絞る。
- ドライバーの 1 枚: 印刷したときだけ、乗務記録の荷待ちの欄の写し（見出し・空の行・荷主の確認の欄）になる。
- 送り方比べ: 契約単価が無いとき、関東発・関西あての宅配に公表運賃からの目安の幅（会社名なし、100 円単位）。Excel から荷物の表を貼れる。
- 3 ページ（ローカルの版）に、最初の画面のジオラマの一部の細い帯（30KB 以下、ページの中に埋め込む）。Artifact の版には載せない。
Edge の確認は 1 つずつ直列に動かす。Edge が無い環境では Edge の確認を飛ばす。
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
sys.path.insert(0, str(ROOT / "tests"))

WAITING = ROOT / "web" / "waiting.html"
DRIVER = ROOT / "web" / "driver.html"
COMPARE = ROOT / "web" / "compare_lite.html"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _wait_for(page, js, what):
    page.wait(lambda: page.js(js), 30, what)


def _row(**kw):
    base = {"date": "2026-10-05", "vehicle_class": "小型", "partner": "A運輸", "arrive": "09:00", "start": "10:00",
            "end": "", "handler": "", "method": "", "shipper_fault": "はい"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 夜中をまたぐ受付（Python。JS は試験表 reception_after_midnight_main_figure が同じ値を通す）
def test_reception_after_midnight_is_counted_from_reception():
    r = waiting.calc([_row(arrive="23:50", reception="00:20", start="02:00")], 2026)["rows"][0]
    assert r["wait_min"] == 130 and r["reception_wait_min"] == 100
    assert r["reception_amount"] < r["amount"]
    # 受付に日付があれば日付で差を取る（日付つきの到着・荷役開始のとき）
    d = waiting.calc([_row(date="2026-10-06", arrive="2026-10-06 23:50", reception="2026-10-07 00:20", start="2026-10-07 02:00")], 2026)["rows"][0]
    assert d["reception_wait_min"] == 100 and d["reception_amount"] == r["reception_amount"]
    # 12 時間より後の受付は、受付から数えない（今までと同じ）
    far = waiting.calc([_row(arrive="09:00", reception="22:00", start="23:00")], 2026)["rows"][0]
    assert "reception_amount" not in far
    # 同じ日の受付は今までどおり
    same = waiting.calc([_row(arrive="08:00", reception="08:40", start="10:00")], 2026)["rows"][0]
    assert same["reception_wait_min"] == 80


def test_reception_after_midnight_becomes_the_main_figure_of_the_summary():
    res = waiting.calc([_row(arrive="23:50", reception="00:20", start="02:00")], 2026)
    p = res["by_partner"][0]
    assert p["reception_count"] == 1 and p["main_amount"] == p["reception_amount"] < p["amount"]


def test_approximate_times_and_partner_header_are_read():
    assert waiting.parse_time("8:00頃") == 480 and waiting.parse_time("8時ごろ") == 480
    t = "日付\t相手\t場所\t車番\t車種\t到着\t荷役開始\n10/5\tA運輸\t江東区 第1倉庫\t1234\t中型\t8:00頃\t10:00\n"
    d = waiting.parse_paste_detail(t)
    assert d["rows"][0]["partner"] == "A運輸"
    res = waiting.calc(d["rows"], 2026, defaults={"vehicle_class": "中型", "method": "手積み"})
    assert not res["errors"] and any("頃" in n for n in res["rows"][0].get("notes", []))


# ---------------------------------------------------------------- 照らし合わせ（Edge）
def test_cross_check_place_with_spaces_doc_amount_and_carrier_filter():
    _edge_or_skip()
    import capture_web as cw
    log = ("受付日\t運送会社\t施設\t車番\t受付時刻\t入場\n2026/10/5\tA運輸\t江東区 第1倉庫\t1234\t8:00\t10:35\n"
           "2026/10/6\tA運輸\t江東区 第1倉庫\t5678\t8:10\t9:00\n2026/10/6\tB急送\t江東区 第1倉庫\t9999\t8:10\t9:00\n")
    # 空白 1 つ区切りの表。場所名に空白がある行（江東区 第1倉庫）で列がずれない。書面の参考額の列つき
    one = "日付 車番 場所 到着 荷役開始 荷役終了 参考額（円）\n2026/10/5 1234 江東区 第1倉庫 7:30 11:20 11:50 20000\n2026/10/6 5678 江東区 第1倉庫 8:10 9:00 9:30 3000\n"
    with cw.edge_page(1280, 1000) as page:
        page.goto(WAITING.as_uri() + "?x=cross47")
        _wait_for(page, "!!document.querySelector('#b-paste')", "荷待ちのページ")
        page.js("document.querySelector('#b-clear').click()")
        page.js("document.querySelector('#paste').value = " + json.dumps(log, ensure_ascii=False))
        page.js("document.querySelector('#b-paste').click()")
        _wait_for(page, "!!document.querySelector('#cross-ta')", "照合の欄")
        loose = page.js("(function(){ const r = crossRowsOf(" + json.dumps(one, ensure_ascii=False) + "); return {n: r.rows.length, place: r.rows[0].place, start: r.rows[0].start, amt: r.rows.map((x) => x.doc_amount), skipped: r.skipped.length}; })()")
        assert loose == {"n": 2, "place": "江東区 第1倉庫", "start": "11:20", "amt": [20000, 3000], "skipped": 0}, loose
        # 運送会社の選択（2 社あるときだけ出る）
        assert page.js("!!document.querySelector('#cross-carrier')")
        page.js("document.querySelector('#cross-ta').value = " + json.dumps(one, ensure_ascii=False))
        page.js("document.querySelector('#b-cross').click()")
        _wait_for(page, "!!document.querySelector('#cross-amount')", "額")
        out = page.js("({amount: document.querySelector('#cross-amount').textContent, doc: S.cross.doc_amount, manual: S.cross.doc_manual, pairs: S.cross.pairs.length, only: S.cross.only_shipper.length})")
        assert out["doc"] == 23000 and not out["manual"] and "23,000 円" in out["amount"], out
        # 書面の車番（1234・5678）に当たる行がある運送会社 A運輸 を、書面の運送会社として自動で絞る（SPEC §48）。B急送の行は並べない
        assert out["pairs"] == 2 and out["only"] == 0 and page.js("S.cross.carrier_filter") == "A運輸" and "A運輸の行だけ" in out["amount"], out
        page.js("(function(){ const s = document.querySelector('#cross-carrier'); s.value = '__all'; s.dispatchEvent(new Event('change', {bubbles: true})); })()")
        _wait_for(page, "S.cross && S.cross.carrier_filter === ''", "全部の運送会社")
        out2 = page.js("({only: S.cross.only_shipper.length, text: document.querySelector('#cross-amount').textContent})")
        assert out2["only"] == 1 and "全部の運送会社" in out2["text"] and "範囲が違う" in out2["text"], out2       # 全部を選んだときは範囲が違うと書く
        # 参考額が書いていない書面は、手で入れた額を使う。読めないときの理由は空にしない
        page.js("(function(){ const t = document.querySelector('#cross-ta'); t.value = " + json.dumps("日付 車番 到着 荷役開始\n2026/10/5 1234 7:30 11:20\n", ensure_ascii=False) + "; t.dispatchEvent(new Event('input', {bubbles: true})); })()")
        page.js("(function(){ const e = document.querySelector('#cross-doc-amount'); e.value = '12,000'; e.dispatchEvent(new Event('change', {bubbles: true})); })()")
        _wait_for(page, "S.cross && S.cross.doc_manual === true", "手で入れた額")
        assert page.js("S.cross.doc_amount") == 12000
        page.js("document.querySelector('#cross-ta').value = " + json.dumps("日付 車番 到着\n江東 1234 7:30 8:00 9:00 10:00\n", ensure_ascii=False))
        page.js("S.carrierText = document.querySelector('#cross-ta').value; runCross();")
        err = page.js("S.cross.error || ''")
        assert "（）" not in err


# ---------------------------------------------------------------- ドライバーの 1 枚（Edge）
def test_driver_prints_a_copy_of_the_report_section():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(900, 1100) as page:
        page.goto(DRIVER.as_uri() + "?x=print47")
        _wait_for(page, "!!document.querySelector('#sheet')", "ドライバーの 1 枚")
        page.js("""(function(){
          const mk = (id, date, place, a, s, e, d) => ({id, date, place, arrive: a, start: s, end: e, depart: d, vno: '1234', partner: '○○'});
          localStorage.setItem('driver_records_v1', JSON.stringify([mk('a', '10/5', '尼崎', '07:50', '09:40', '10:30', '10:40')]));
          localStorage.setItem('driver_settings_v1', JSON.stringify({place: '', partner: '', vno: '1234', vclass: '', handler: ''}));
        })()""")
        page.goto(DRIVER.as_uri() + "?x=print47b")
        _wait_for(page, "!!document.querySelector('#day')", "日の選択")
        page.js("(function(){const s=document.querySelector('#day'); s.value='10/5'; s.dispatchEvent(new Event('input',{bubbles:true}));})()")
        probe = "({head: getComputedStyle(document.querySelector('.print-head')).display, pc: getComputedStyle(document.querySelector('th.pc')).display, blank: getComputedStyle(document.querySelector('tr.blank')).display, hide: getComputedStyle(document.querySelector('#distribute')).display})"
        screen = page.js(probe)
        assert screen["head"] == "none" and screen["pc"] == "none" and screen["blank"] == "none", screen
        page.send("Emulation.setEmulatedMedia", media="print")
        try:
            pr = page.js(probe)
            assert pr["head"] == "block" and pr["pc"] == "table-cell" and pr["blank"] == "table-row" and pr["hide"] == "none", pr
            info = page.js("({day: document.querySelector('#ph-day').textContent, vno: document.querySelector('#ph-vno').textContent, rows: document.querySelectorAll('#sheet-body tr').length, th: [...document.querySelectorAll('#sheet thead th')].map((t) => t.textContent)})")
            assert info["day"] == "10/5" and info["vno"] == "1234" and info["rows"] >= 6 and info["th"][-1] == "荷主の確認（印）", info
        finally:
            page.send("Emulation.setEmulatedMedia", media="")


# ---------------------------------------------------------------- 送り方比べ（Edge）
def test_compare_lite_public_fare_range_and_cargo_paste():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1280, 1000) as page:
        page.goto(COMPARE.as_uri() + "?x=pub47")
        _wait_for(page, "!!document.querySelector('#cargo-paste')", "荷物の貼り付け欄")
        page.js("localStorage.removeItem('compare_lite_contract_v1'); localStorage.removeItem('compare_lite_cargo_v1')")
        # Excel の表（タブ区切り・見出しつき・単位つき）を貼る
        page.js("document.querySelector('#cargo-paste-box').open = true; document.querySelector('#cargo-paste').value = " + json.dumps("個数\t縦(cm)\t横(cm)\t高さ(cm)\t重さ(kg)\n3\t120\t50\t50\t40\n", ensure_ascii=False))
        page.js("document.querySelector('#b-cargopaste').click()")
        rows = page.js("S.rows.filter((r) => String(r.n).trim()).map((r) => [r.n, r.l, r.w, r.h, r.kg].join(','))")
        assert rows == ["3,120,50,50,40"], rows
        page.js("(function(){ const f = document.querySelector('#from'), t = document.querySelector('#to'); f.value = '東京都'; t.value = '大阪府'; document.querySelector('#km').value = '492'; renderRows(); render(); })()")
        _wait_for(page, "!!document.querySelector('#pub-range')", "公表運賃の幅")
        text = page.js("document.querySelector('#pub-range').textContent")
        assert "14,000〜14,300 円" in text and "大手宅配の公開料金" in text   # 文言の直し 2026-11-07
        cmp_text = page.js("document.querySelector('#cmp').textContent")
        assert "サガワ" not in cmp_text and "佐川" not in cmp_text and "ヤマト" not in cmp_text
        assert "大手宅配の公開料金からの目安" in page.js("document.querySelector('tr[data-opt=\"takuhai\"]').textContent")
        # 関西あて以外は、幅を出さずに理由を出す
        page.js("document.querySelector('#to').value = '北海道'; document.querySelector('#to').dispatchEvent(new Event('change', {bubbles: true}));")
        assert page.js("!document.querySelector('#pub-range')")
        assert "関東発・関西あて" in page.js("document.querySelector('tr[data-opt=\"takuhai\"]').textContent")
        # 契約単価を入れた会社があるときは、契約単価の額が先
        page.js("document.querySelector('#to').value = '大阪府'; document.querySelector('#to').dispatchEvent(new Event('change', {bubbles: true}));")
        page.js("""(function(){
          const set = (sel, v) => { const e = document.querySelector(sel); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); };
          set('#c-name', 'A社'); set('#c-t-260', '4000');
        })()""")
        _wait_for(page, "document.querySelector('#cmp').textContent.includes('契約単価')", "契約単価の比べ")
        assert page.js("!document.querySelector('#pub-range')")


# ---------------------------------------------------------------- 帯
def test_waiting_and_driver_have_no_decorative_banner():
    # 最初の画面の絵を切り取った飾りの帯は、荷待ちのページとドライバーの 1 枚から外した（画面の出来 r4）
    for path in (WAITING, DRIVER, ROOT / "web" / "publish" / "waiting_artifact.html"):
        html = path.read_text(encoding="utf-8")
        assert 'class="banner"' not in html and "banner_wait" not in html and not re.search(r"url\(data:image/webp;base64,", html), path.name
    for path in (WAITING, DRIVER, COMPARE):
        assert not re.search(r"url\(\s*['\"]?https?:", path.read_text(encoding="utf-8")), path.name
    # 公表運賃の表（宅配）には、会社名・商品名・表への URL を入れない
    for path in (COMPARE, ROOT / "web" / "publish" / "compare_lite_artifact.html"):
        line = next(ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("const PUBLIC_TAKUHAI"))
        assert "佐川" not in line and "飛脚" not in line and "http" not in line and '"fare"' in line
