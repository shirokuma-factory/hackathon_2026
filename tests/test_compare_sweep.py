"""公開版のどぶさらい（docs/reviews/2026-11-08_公開版のどぶさらい_送り方と大型車.md）の H01〜H13 の回帰試験。
ヘッドレス Edge は同時に 1 つだけ（conftest の edge_page）。画面写真は一時フォルダ cmp_sweep に残す。"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

import conftest  # noqa: E402
from conftest import edge_page, make_shipment  # noqa: E402
from shipcompare.request import STATUS_DRAFT, STATUS_READY, build_request  # noqa: E402

WEB = ROOT / "web"
NODE = shutil.which("node")
READY = "!!window.__app && !document.querySelector('#loading')"
SHOTS = Path(tempfile.gettempdir()) / "claude" / "cmp_sweep"


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def _node(script: str) -> str:
    if not NODE:
        pytest.skip("node がありません")
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _shot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    data = page.send("Page.captureScreenshot", format="png").get("data", "")
    (SHOTS / name).write_bytes(base64.b64decode(data))


def _open(page, hash_):
    conftest.open_app(page, f"http://127.0.0.1:{conftest.server_port('local')}/index.html{hash_}",
                      ready_js=READY, wait_loading=False, start_timeout=180, tag="cmp_sweep_fail")


def _pick_osaka(page):
    page.js("""(function(){const q=document.getElementById('q'); q.focus(); q.value='大阪市中央区'; q.dispatchEvent(new Event('input',{bubbles:true}));})()""")
    page.wait(lambda: page.js("document.querySelectorAll('#suggest li[data-i]').length") >= 1, 20, "候補")
    page.js("document.querySelector('#suggest li[data-i]').dispatchEvent(new MouseEvent('mousedown',{bubbles:true}))")
    page.wait(lambda: page.js("!!document.querySelector('#cards .cc-row')"), 90, "結果の表")
    time.sleep(1.0)


# ---------------------------------------------------------------- H01 日付の前後（サーバー側と JS の判定）
def test_h01_request_is_not_ready_when_arrival_is_before_shipping():
    s = make_shipment(ship_date=date(2026, 11, 20), desired_date=date(2026, 11, 18), forklift=True)
    d = build_request(s)
    assert d.status == STATUS_DRAFT, d.status
    assert any("出荷日より前" in m for m in d.missing_required), d.missing_required
    ok = build_request(make_shipment(ship_date=date(2026, 11, 18), desired_date=date(2026, 11, 20)))
    assert not any("出荷日より前" in m for m in ok.missing_required)
    same = build_request(make_shipment(ship_date=date(2026, 11, 20), desired_date=date(2026, 11, 20)))
    assert not any("出荷日より前" in m for m in same.missing_required)
    assert STATUS_READY == "依頼準備完了"


def test_h01_js_date_order_error_blocks_confirm():
    src = _read("work.js")
    fn = re.search(r"function dateOrderError\(d, today\) \{.*?\n\}\n", src, re.S).group(0)
    out = json.loads(_node(fn + "console.log(JSON.stringify([dateOrderError({ship_date:'2026-11-20',desired_date:'2026-11-18'}),"
                           "dateOrderError({ship_date:'2026-11-18',desired_date:'2026-11-20'}),dateOrderError({ship_date:'2026-11-20',desired_date:'2026-11-20'}),"
                           "dateOrderError({ship_date:null,desired_date:'2026-11-18'}),dateOrderError({ship_date:'2025-01-10',desired_date:'2025-01-12'},'2026-11-08')]))"))
    assert "出荷日" in out[0] and "より前" in out[0], out
    assert out[1] == out[2] == out[3] == "", out
    assert "今日" in out[4] and "より前" in out[4], out   # P-10: 過去の出荷日
    # 荷物の確定（詳しい条件の日付欄）もこの判定を通る
    assert re.search(r"function missingDims\(d\) \{\s*const de = dateOrderError\(d, todayStr\(\)\);\s*if \(de\) return de;", src)


# ---------------------------------------------------------------- H02 見出しと印の言い方をそろえる
def test_h02_labels_and_chip_wording():
    src = _read("work.js")
    assert "先に入れてください" not in src and "出荷日・希望着日（任意）" not in src
    assert src.count("<b>出荷日・希望着日</b>") == 2
    chip = re.search(r"function reqChip\(r\) \{.*?\n\}\n", src, re.S).group(0)
    f = "const wesc=(s)=>String(s);" + chip

    def mk(st, req, opt):
        return _node(f + f"console.log(reqChip({{status:'{st}',missing_required:{json.dumps(req, ensure_ascii=False)},missing_optional:{json.dumps(opt, ensure_ascii=False)}}}))")

    assert "あと 2 項目" in mk("下書き", ["出荷日", "希望着日"], ["品目"])
    assert "依頼文ができました（任意の未入力 6 件）" in mk("依頼準備完了", [], list("abcdef"))
    assert "あと 1 項目" in mk("依頼準備完了", ["希望着日が出荷日より前です"], [])   # 必須が残っていれば完了と言わない


# ---------------------------------------------------------------- H06 H07 H09 H10 H11 案内の画面
def test_guided_flow_h06_h07_h09_h10_h11_h01_h02():
    with edge_page(1920, 1080) as page:
        _open(page, "#v=compare&g=1")
        page.wait(lambda: page.js("!!document.querySelector('#ed-confirm')"), 60, "確定ボタン")
        time.sleep(1.5)
        page.js("document.querySelector('#cargo-toggle').click()")
        page.js("document.querySelector('#ed-detail summary').click()")
        time.sleep(0.8)
        # H06: 詳しい条件の文と日付欄が食い違わない
        defaults = page.js("document.querySelector('.ed-defaults').textContent")
        # 2026-11-08 の最終評価に合わせて: 文は短くなり「下の欄でも」は消えた。画面の食い違いの文言が出ていないことだけ確かめる
        assert "見積依頼文の画面で入れます" not in defaults and "初めは" in defaults, defaults
        # H07: 詳しい条件を開いても「この条件で確定」が画面に収まり、欄の中でスクロールできる
        r = json.loads(page.js("""JSON.stringify((function(){const b=document.getElementById('ed-confirm').getBoundingClientRect(), e=document.getElementById('cargo-editor'), p=document.getElementById('panel').getBoundingClientRect();
          return {btnBottom:b.bottom, panelBottom:p.bottom, vh:innerHeight, scroll:e.scrollHeight>e.clientHeight, ov:getComputedStyle(e).overflowY};})())"""))
        assert r["btnBottom"] <= r["vh"] and r["panelBottom"] <= r["vh"], r
        assert r["scroll"] is True and r["ov"] in ("auto", "scroll"), r
        _shot(page, "H07_detail_open_1920.png")
        # H08: 初めの荷物は 10 個・100kg
        assert "10 個・100 kg" in page.js("document.getElementById('cargo').textContent")
        # U-04: 初めの荷物は例の荷物。印が出る。自分の荷物に直したら外れる（重さを入れ替える）
        assert page.js("Work.isExample()") is True and page.js("!!document.querySelector('#cargo .cg-ex')") is True
        page.js("(function(){const e=document.querySelector('#sc-simple [data-sc=weight_kg]'); e.value=12; e.dispatchEvent(new Event('input',{bubbles:true}));})()")
        # 2026-11-08 の最終評価に合わせて: 例の印が外れるのは書き換えた時点ではなく「この条件で確定」を押した時点
        assert page.js("Work.isExample()") is True
        page.js("document.getElementById('ed-confirm').click()")
        page.wait(lambda: page.js("Work.isExample()") is False, 30, "確定で例の印が外れる")
        if page.js("!document.getElementById('cargo-editor').hidden && document.getElementById('cargo-editor').getClientRects().length > 0"):
            page.js("document.querySelector('#cargo-toggle').click()")
        _pick_osaka(page)
        # H09 H10: 鉄道・船・飛行機の行が表の下に出る。使えない手段は 1 つの欄に畳む
        r = json.loads(page.js("""JSON.stringify((function(){const a=document.querySelector('#cards .cc-alt'), u=document.querySelector('#cards .cc-unusable');
          return {alt: !!a && a.getClientRects().length>0, altIn: !!a && !!a.closest('.cards-more'), altText: a?a.querySelector('summary').textContent:'',
                  altRows: a ? a.querySelectorAll('.cc-row').length : 0, un: !!u, unText: u?u.querySelector('summary').textContent:'',
                  unusableInRows: [...document.querySelectorAll('#cards .cc-rows .cc-ok.no')].length};})())"""))
        assert r["altIn"] is True and r["altRows"] == 3, r   # 選べる欄は「金額の出どころ」の中（操作部品を 8 つまでに抑える）
        line = page.js("(document.querySelector('#cards .cc-alt-line')||{}).textContent || ''")
        assert "鉄道" in line and "船" in line and "飛行機" in line and "見積で確認" in line, line   # 表には 1 行で出す
        assert r["unusableInRows"] == 0, r
        _shot(page, "H10_result_1920.png")
        # 依頼文の段
        page.js("document.getElementById('make-request').click()")
        page.wait(lambda: page.js("!!document.getElementById('req-copy')"), 60, "依頼文")
        time.sleep(1.0)
        # H11: 色の目盛りが無い段では、凡例に「色は道路距離」を出さない
        lg = page.js("document.getElementById('g-legend').textContent")
        assert "色は" not in lg, lg
        # H02: 必須の日付が空のあいだは「あと 2 項目」。見出しは「出荷日・希望着日」
        chip = page.js("document.querySelector('.sec-h .chip').textContent")
        assert "あと 2 項目" in chip, chip
        assert page.js("document.querySelector('.w-need > b').textContent") == "出荷日・希望着日"
        # H01: 希望着日が出荷日より前 → 誤りを出し、確定しない（依頼文は完了にならない）
        before = page.js("Work.version()")
        page.js("""(function(){const a=document.querySelector('[data-need=ship_date]'), b=document.querySelector('[data-need=desired_date]'); a.value='2026-11-20'; b.value='2026-11-18'; a.dispatchEvent(new Event('change',{bubbles:true})); b.dispatchEvent(new Event('change',{bubbles:true}));})()""")
        page.wait(lambda: page.js("!document.getElementById('date-err').hidden"), 10, "誤りの表示")
        err = page.js("document.getElementById('date-err').textContent")
        assert "希望着日" in err and "出荷日" in err and "より前" in err, err
        time.sleep(1.0)
        assert page.js("Work.version()") == before
        assert "できました" not in page.js("document.querySelector('.sec-h .chip').textContent")
        assert "依頼準備完了" not in page.js("document.getElementById('req-text').textContent")
        _shot(page, "H01_dates_reversed_1920.png")
        # 正しい順なら確定して「できました」
        page.js("""(function(){const b=document.querySelector('[data-need=desired_date]'); b.value='2026-11-22'; b.dispatchEvent(new Event('change',{bubbles:true}));})()""")
        page.wait(lambda: "できました" in (page.js("document.querySelector('.sec-h .chip') && document.querySelector('.sec-h .chip').textContent") or ""), 60, "完了の印")
        assert page.js("document.getElementById('date-err') && document.getElementById('date-err').hidden") is True
        assert "任意の未入力" in page.js("document.querySelector('.sec-h .chip').textContent")
        # 完了後の見出しも同じ
        assert page.js("document.querySelector('.w-dates > b').textContent") == "出荷日・希望着日"


# ---------------------------------------------------------------- H08 H12 H13 直接表示
def test_direct_entry_h08_h12_h13():
    with edge_page(1920, 1080) as page:
        _open(page, "#v=compare&o=13104&l=km&d=27128")
        page.wait(lambda: "10 個・100 kg" in (page.js("document.getElementById('cargo').textContent") or ""), 90, "初めの荷物が案内と同じ")
        page.wait(lambda: page.js("!!document.querySelector('#cards .cc-row')"), 90, "結果の表")
        time.sleep(1.0)
        # H09/H10: 直接表示でも同じ手段の行（鉄道・船・飛行機の欄、使えない手段の欄）
        assert page.js("!!document.querySelector('#cards .cc-alt') && document.querySelectorAll('#cards .cc-alt .cc-row').length") == 3
        # H12: 荷物の欄を開いたままでも、色の凡例は欄に隠れない
        page.js("document.querySelector('#cargo-toggle').click()")
        page.js("document.querySelector('.lyr[data-layer=fare]').click()")
        time.sleep(1.0)
        r = json.loads(page.js("""JSON.stringify((function(){const l=document.getElementById('legend').getBoundingClientRect(), p=document.getElementById('panel').getBoundingClientRect();
          return {op:getComputedStyle(document.getElementById('legend')).opacity, left:l.left, panelRight:p.right, w:l.width};})())"""))
        assert r["op"] == "1" and r["left"] >= r["panelRight"], r
        _shot(page, "H12_legend_beside_panel_1920.png")
        # H13: 手段の凡例に 2 つの絵の意味を書く
        # 2026-11-08 の最終評価に合わせて: 見出しは「手段」だけにし、2 つの絵の意味は title に移した
        cap = page.js("document.querySelector('#layers .rt-lg-cap').title")
        assert "経路の色" in cap and "車両" in cap, cap


# ---------------------------------------------------------------- H03 H04 海を渡る行き先
def test_h03_h04_sea_destinations_same_rule_top_and_bar():
    with edge_page(1920, 1080) as page:
        _open(page, "#v=compare&o=13104&l=km&d=01101")   # 札幌市中央区（船が要る）
        page.wait(lambda: page.js("!!document.querySelector('#s-time') && document.querySelector('#s-time').textContent.length > 0"), 90, "詳細")
        page.wait(lambda: page.js("typeof Rt !== 'undefined' && !!Rt.cur && !!Rt.cur.sets && !!Rt.timeline()"), 90, "経路")
        time.sleep(1.5)
        top = page.js("document.querySelector('#s-time').textContent")
        bar = page.js("document.querySelector('#tb-end').textContent")
        assert "船の時間は見積で確認" in top, top
        # 上の欄が運転時間を出さないなら、帯も「到着 運転 14 時間」のような合計を出さない
        assert not re.search(r"運転\s*\d+時間", bar) and "到着 運転" not in bar, bar
        assert "見積で確認" in bar, bar
        assert page.js("document.querySelector('#tb-days').textContent.includes('日目')") is False
        assert page.js("document.querySelector('#s-km').previousElementSibling.textContent") == "経路の距離（海を含む）"
    with edge_page(1920, 1080) as page:
        _open(page, "#v=compare&o=13104&l=km&d=27128")   # 陸続き
        page.wait(lambda: page.js("!!document.querySelector('#s-km') && /km/.test(document.querySelector('#s-km').textContent)"), 90, "詳細")
        assert page.js("document.querySelector('#s-km').previousElementSibling.textContent") == "道路距離"


# ---------------------------------------------------------------- P-10 P-11（依頼文の判定）
def test_p10_past_ship_date_is_not_ready_when_the_clock_is_given():
    s = make_shipment(ship_date=date(2025, 1, 10), desired_date=date(2025, 1, 12), forklift=True)
    d = build_request(s, today=date(2026, 11, 8))
    assert d.status == STATUS_DRAFT and any("今日" in m and "より前" in m for m in d.missing_required), d.missing_required
    ok = build_request(make_shipment(ship_date=date(2026, 11, 8), desired_date=date(2026, 11, 9)), today=date(2026, 11, 8))
    assert not any("今日" in m for m in ok.missing_required)
    assert not any("今日" in m for m in build_request(s).missing_required)   # 時計を渡さなければ確かめない（既存の呼び出しを変えない）


def test_p11_pallet_request_says_pallet_and_requires_forklift():
    base = dict(ship_date=date(2026, 11, 20), desired_date=date(2026, 11, 22))
    d = build_request(make_shipment(**base), packaging="pallet")
    assert "（パレット渡し）" in d.text and d.status == STATUS_DRAFT, d.text
    assert any(m.startswith("フォークリフト") for m in d.missing_required), d.missing_required
    ok = build_request(make_shipment(forklift=True, **base), packaging="pallet")
    assert "（パレット渡し）" in ok.text and not any(m.startswith("フォークリフト") for m in ok.missing_required)
    plain = build_request(make_shipment(**base))
    assert "パレット" not in plain.text


def test_p09_mode_problem_downgrades_the_request():
    src = _read("work.js")
    fn = re.search(r"function applyModeProblem\(r, mode\) \{.*?\n}\n", src, re.S).group(0)
    stub = "const W={hooks:{modeProblem:(m)=>m==='宅配'?'3 辺の合計が上限を超えます':''}};"
    call = ("const mk=()=>({status:'依頼準備完了',missing_required:[],text:'【お見積りのお願い】\\n検討している送り方: 宅配\\n\\n■ 荷物\\n  1. x'});"
            "const a=mk(); applyModeProblem(a,'宅配'); const b=mk(); applyModeProblem(b,'貸切'); console.log(JSON.stringify([a,b]))")
    a, b = json.loads(_node(stub + fn + call))
    assert a["status"] == "下書き" and "依頼準備完了" not in a["text"] and "使えません" in a["missing_required"][0] and "注意" not in a["text"], a   # 日本語の直し 2026-11-08: 画面の注意は依頼文に入れない
    assert b["status"] == "依頼準備完了" and not b["missing_required"], b


# ---------------------------------------------------------------- P-08 P-09 P-10 P-11 確定していない荷物の変更
def test_p08_p09_p10_p11_unconfirmed_pallet_cargo_flow():
    with edge_page(1920, 1080) as page:
        _open(page, "#v=compare&g=1&today=2026-11-08")
        page.wait(lambda: page.js("!!document.querySelector('#ed-confirm')"), 60, "確定ボタン")
        time.sleep(1.5)
        # P-08: パレット 2 個・300kg にして「この条件で確定」を押さずに届け先を選ぶ
        page.js("document.querySelector('#cargo-toggle').click()")
        page.js("document.querySelector('[data-sc-kind=pallet]').click()")
        page.js("document.querySelector('[data-sc-size=std]').click()")
        page.js("(function(){const set=(k,v)=>{const e=document.querySelector('#sc-simple [data-sc='+k+']'); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true}));}; set('weight_kg',300); set('count',2);})()")
        assert page.js("Work.isDirty()") is True
        assert "2 個・600 kg" in page.js("document.getElementById('cargo').textContent")
        page.js("document.querySelector('#cargo-toggle').click()")
        _pick_osaka(page)
        sh = json.loads(page.js("JSON.stringify(Work.confirmedShipment())"))
        assert sh["items"][0]["count"] == 2 and sh["items"][0]["length_cm"] == 110 and sh["items"][0]["weight_kg"] == 300, sh   # 結果は表示中の荷物で計算された
        assert page.js("Work.isDirty()") is False
        assert "2 個・600 kg" in page.js("document.getElementById('cargo').textContent")
        rows = page.js("[...document.querySelectorAll('#cards .cc-rows .cc-row')].map(r=>r.textContent).join('|')")
        assert "16,400" not in rows and "段ボール" not in rows, rows   # 前の荷物（宅配 16,400 円）の額が残らない
        un = page.js("(document.querySelector('#cards .cc-unusable summary')||{}).textContent || ''")
        assert "宅配" in un, un   # パレットの荷は宅配が使えない
        _shot(page, "P08_pallet_results_1920.png")
        # P-09: 宅配を選んだ依頼文は完了にならない。P-11: パレット渡しと書き、フォークリフトを求める
        assert page.js("window.__app.S.estimate.rows.filter(r=>r.group==='takuhai').every(r=>r.status==='使えない')") is True
        page.js("document.getElementById('make-request').click()")
        page.wait(lambda: page.js("!!document.getElementById('req-copy')"), 60, "依頼文")
        time.sleep(1.0)
        assert page.js("!!document.querySelector('[data-need-fork]')") is True
        # P-10: 過去の出荷日は確定しない
        page.js("""(function(){const a=document.querySelector('[data-need=ship_date]'), b=document.querySelector('[data-need=desired_date]'); a.value='2025-01-10'; b.value='2025-01-12'; a.dispatchEvent(new Event('change',{bubbles:true})); b.dispatchEvent(new Event('change',{bubbles:true}));})()""")
        page.wait(lambda: page.js("!document.getElementById('date-err').hidden"), 10, "誤りの表示")
        assert "今日" in page.js("document.getElementById('date-err').textContent")
        assert page.js("Work.confirmedShipment().ship_date") is None
        # 正しい日付とフォークリフト
        page.js("""(function(){const a=document.querySelector('[data-need=ship_date]'), b=document.querySelector('[data-need=desired_date]'); a.value='2026-11-20'; b.value='2026-11-22'; a.dispatchEvent(new Event('change',{bubbles:true})); b.dispatchEvent(new Event('change',{bubbles:true}));})()""")
        page.wait(lambda: page.js("Work.confirmedShipment().ship_date") == "2026-11-20", 60, "日付の確定")
        time.sleep(1.0)
        page.js("""(function(){const f=document.querySelector('[data-need-fork]'); if (f) { f.value='true'; f.dispatchEvent(new Event('change',{bubbles:true})); }})()""")
        page.wait(lambda: page.js("Work.confirmedShipment().forklift") is True, 60, "フォークリフトの確定")
        time.sleep(1.5)
        page.js("document.getElementById('req-more').click()")
        text = page.js("document.getElementById('req-text').textContent")
        assert "（パレット渡し）" in text and "フォークリフト: あり" in text, text
        assert "できました" in page.js("document.querySelector('.sec-h .chip').textContent")   # 使える送り方（選択中）なら完了
        _shot(page, "P09_pallet_request_ready_1920.png")


# ---------------------------------------------------------------- U-03 U-04 案内の最初の画面
def test_u03_u04_guided_compare_shows_origin_link_and_example_cargo_notice():
    with edge_page(1920, 1080) as page:
        _open(page, "#v=compare&g=1&today=2026-11-08")
        page.wait(lambda: page.js("!!document.querySelector('#ed-confirm') && Work.isExample()"), 90, "例の荷物")
        # U-03: 関東以外の荷主向けの「地図なしのページ」への案内が見える
        r = json.loads(page.js("""JSON.stringify((function(){const p=document.getElementById('p-origin'), a=p.querySelector('a'); return {shown:p.getClientRects().length>0, ax: a? a.getBoundingClientRect().width : 0, href: a?a.getAttribute('href'):null};})())"""))
        assert r["shown"] is True and r["ax"] > 0 and r["href"] == "compare_lite.html", r
        _shot(page, "U03_origin_link_1920.png")
        # U-04: 触っていない例の荷物のまま届け先を選ぶと、結果に「例の荷物」と出し、依頼文は完了にしない
        _pick_osaka(page)
        assert "例の荷物" in page.js("(document.querySelector('#cards .ex-note')||{}).textContent || ''")
        page.js("document.getElementById('make-request').click()")
        page.wait(lambda: page.js("!!document.getElementById('req-copy')"), 60, "依頼文")
        time.sleep(1.0)
        page.js("document.getElementById('req-more').click()")
        text = page.js("document.getElementById('req-text').textContent")
        assert "例の荷物（架空）のまま" not in text and "依頼準備完了" not in text and "注意" not in text, text   # 日本語の直し 2026-11-08: 画面の注意は依頼文に入れない
        assert "できました" not in page.js("document.querySelector('.sec-h .chip').textContent")
        # 日付を入れても、例の荷物のままなら完了にならない
        page.js("""(function(){const a=document.querySelector('[data-need=ship_date]'), b=document.querySelector('[data-need=desired_date]'); a.value='2026-11-20'; b.value='2026-11-22'; a.dispatchEvent(new Event('change',{bubbles:true})); b.dispatchEvent(new Event('change',{bubbles:true}));})()""")
        page.wait(lambda: page.js("Work.confirmedShipment().ship_date") == "2026-11-20", 60, "日付の確定")
        time.sleep(1.5)
        assert "できました" not in page.js("document.querySelector('.sec-h .chip').textContent")
        # コピーのとき、未入力が残っていれば知らせる
        page.js("window.__t=[]; const o=window.toast; window.toast=function(m){window.__t.push(m); return o(m);};")
        page.js("document.getElementById('req-copy').click()")
        time.sleep(0.8)
        assert any("未入力の項目が残っています" in m for m in page.js("window.__t")), page.js("window.__t")


# ---------------------------------------------------------------- U-02 案内の全国の物流傾向
def test_u02_guided_market_recommends_a_pair_with_a_demo_trip():
    with edge_page(1920, 1080) as page:
        _open(page, "#v=market&g=1")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row').length") == 5, 90, "上位 5 の一覧")
        page.wait(lambda: page.js("document.querySelectorAll('.mg-row .mg-tag').length") == 5, 90, "例の便の印")
        r = json.loads(page.js("""JSON.stringify([...document.querySelectorAll('.mg-row')].map(b=>({t:b.querySelector('.mg-tag').textContent, pair:b.querySelector('.mg-pair').textContent})))"""))
        recs = [x for x in r if "おすすめ" in x["t"]]
        assert len(recs) <= 1, r
        for x in r:   # 「おすすめ」は例の便がある行だけ。例の便の無い行にはおすすめを付けない
            assert not ("おすすめ" in x["t"] and "なし" in x["t"]), x
        first_has_trip = "例の便あり" in r[0]["t"]
        if not first_has_trip and recs:
            assert r[0] is not recs[0]
        _shot(page, "U02_market_1920.png")


# ---------------------------------------------------------------- 回答の状態と窓口の案内の文
def test_quote_state_is_single_and_contact_pointer_is_named_and_linked():
    src = _read("work.js")
    assert "const totalOnlyMark = () => '';" in src and "'合計だけの回答'" in src
    assert src.count("（内訳不明）") == 0 and "内訳がそろった回答だけ選べます" not in src
    assert "replace('地図なしの比べるページ'" in src
    from shipcompare.guide import CONTACT_POINTER
    assert "地図なしの比べるページ" in CONTACT_POINTER


# ---------------------------------------------------------------- A2 架空の表の額の表示 / 荷物の入力の検査
def _chart_html(cards):
    src = _read("compare_chart.js")
    stub = "global.document={addEventListener(){}};global.Rt=undefined;global.window={};"
    return json.loads(_node(stub + src + f";console.log(JSON.stringify(CompareChart.html({json.dumps(cards, ensure_ascii=False)},null,false)))"))


def test_a2_sample_table_amounts_are_labelled_in_rows_mark_and_ratio():
    cards = [
        {"label": "路線便", "amount": 10500, "status": "公開条件に合う", "est": True, "sample": True, "days": "", "sub": ""},
        {"label": "宅配", "amount": 11500, "status": "公開条件に合う", "est": True, "sample": True, "days": "", "sub": ""},
    ]
    html = _chart_html(cards)
    assert html.count("架空の表の例</em>") == 2, html
    assert "最安（架空の表の例）" in html and "架空の表の例を含む" in html, html
    h2 = _chart_html([dict(c, sample=False) for c in cards])
    assert "架空の表" not in h2, h2


def test_p1_cargo_validation_messages_and_summary():
    src = _read("work.js")
    fn = re.search(r"function missingDims\(d\) \{.*?\n\}\n", src, re.S).group(0)
    stub = "const W={simple:{kind:'box'}}; const dateOrderError=()=>''; const todayStr=()=>'2026-11-08';"
    js = (stub + fn + "const mk=(it)=>({items:[Object.assign({count:1,length_cm:10,width_cm:10,height_cm:10,weight_kg:5},it)]});"
          "console.log(JSON.stringify([missingDims(mk({})),missingDims(mk({count:0})),missingDims(mk({count:1.5})),missingDims(mk({count:-2})),"
          "missingDims(mk({weight_kg:0})),missingDims(mk({weight_kg:-80})),missingDims(mk({weight_kg:null})),"
          "missingDims({items:[{count:null,length_cm:null,width_cm:null,height_cm:null,weight_kg:null}]})]))")
    r = json.loads(_node(js))
    assert r[0] == ""
    for m in r[1:4]:
        assert "個数" in m and "整数" in m and "_" not in m, m
    for m in r[4:7]:
        assert "重さ" in m and "0 より大きい" in m and "weight_kg" not in m, m
    assert r[7] != ""   # 空の荷物は確定させない
    assert "const jaFields" in src and "weight_kg: '1個の重さ'" in src and "入力を確認" in src
    jf = re.search(r"const jaFields = .*", src).group(0)
    ff = re.search(r"const FIELD_JA = .*", src).group(0)
    assert _node(ff + jf + "console.log(jaFields('items[0].weight_kg は 0 より大きい数'))") == "items[0].1個の重さ は 0 より大きい数"


def test_p1_negative_count_in_the_editor_blocks_confirm_and_is_not_shown_as_confirmed():
    with edge_page(1920, 1080) as page:
        _open(page, "#v=compare&g=1")
        page.wait(lambda: page.js("!!document.querySelector('#ed-confirm') && Work.isExample()"), 90, "荷物の欄")
        page.js("document.querySelector('#cargo-toggle').click()")
        page.js("(function(){const e=document.querySelector('#sc-simple [data-sc=count]'); e.value=-2; e.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.5)
        pill = page.js("document.getElementById('cargo').textContent")
        assert "入力を確認" in pill and "-2個" not in pill, pill
        st = page.js("document.getElementById('ed-state').textContent")
        assert "確定済み" not in st and "個数" in st, st
        before = page.js("JSON.stringify(Work.confirmedShipment())")
        page.js("document.getElementById('ed-confirm').click()")
        time.sleep(0.8)
        assert page.js("JSON.stringify(Work.confirmedShipment())") == before
        assert "個数は 1 以上の整数" in page.js("document.getElementById('ed-state').textContent")
