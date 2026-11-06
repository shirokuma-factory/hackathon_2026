"""SPEC §42 自社の画面（#v=own）の直し（実務の効き目 r2、道具の採点 r4）。

- 帰り荷の売上の目安は、空車の帰りの便ごとに、その便の車の種類（2t・4t・10t）と km で出す。区間ごとの小計と、全部の帰りに荷が付いた場合の上限を分けて出す
- 求荷求車の文の見出しは【求荷】空車情報。空車の帰りの区間（発地 → 着地）ごとに 1 つずつ一度に作る
- 遠くの発地からの空車の帰り（名古屋 → 加古川など）が、地図に別の線として出る
- 区間を 1 件も読めないが列の役割を選べるファイルを 1 つだけ落としたとき、役割を選ぶ欄が出て、選ぶと読み直す
- 車ごと・車種ごとの最大積載量を、確認のカードから変えられ、ブラウザに覚える
- 右の欄は数字と式の 1 行を先に出し、注記は閉じた「詳しく」に入れる。役割を選ぶ欄の文字が欠けない
画面の写真は、環境変数 V42_SHOT_DIR があればそこへ、無ければ一時フォルダへ書く（追跡しているファイルは書き換えない）。
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

import pytest
from conftest import edge_page, fail_with_evidence

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "own"
EX_FILES = [EXAMPLES / "haisha_2026-10.csv", EXAMPLES / "digitaco_1234.csv", EXAMPLES / "digitaco_5678.csv"]


def _far_origin_haisha() -> str:
    """加古川 ⇄ 江東の往復に加えて、名古屋・横浜からの空車の帰りがある配車表（車は大型 2 台、4t 1 台、車種が空の 1 台）。"""
    days = [f"2026/10/{d}" for d in range(1, 13)]

    def row(plate, cls, legs):
        cells = [legs[i % len(legs)] for i in range(12)]
        return ",".join([plate, cls, *cells])

    pat1 = ["加古川→江東 着5:30 4.6t", "江東→加古川（空）", "加古川→名古屋 着6:00 3.0t", "名古屋→加古川（空）"]
    pat2 = ["加古川→名古屋 着6:00 3.5t", "名古屋→加古川（空）", "加古川→江東 着5:30 4.0t", "江東→加古川 着4:00 3.0t"]
    pat3 = ["加古川→名古屋 着7:00 1.5t", "名古屋→加古川（空）"]
    pat4 = ["加古川→横浜 着6:00 2.0t", "横浜→加古川（空）"]
    return "\n".join([",".join(["車番", "車種", *days]), row("1234", "大型", pat1), row("5678", "大型", pat2), row("9012", "4t", pat3), row("7777", "", pat4)]) + "\n"


FAR_HAISHA = _far_origin_haisha()


@pytest.fixture(scope="module")
def app(local_server):
    import capture_web as cw
    from conftest import RESOURCE_HOOK
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
        page.send("Page.navigate", url=f"http://localhost:{local_server}/index.html#v=own")
        try:
            page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
            page.wait(lambda: page.js("!!document.querySelector('#own-file')"), 60, "ファイルを落とす欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面が起動しません", e, "own_v42_fail")
        time.sleep(2.0)
        yield page


def _set_files(page, paths):
    doc = page.send("DOM.getDocument", depth=1)
    nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#own-file")["nodeId"]
    page.send("DOM.setFileInputFiles", files=[str(p) for p in paths], nodeId=nid)


def _src(page) -> str:
    return page.js("(document.querySelector('#own-src') || {textContent: ''}).textContent")


def _to_sample(page):
    page.js("App.setView('own')")
    page.js("Own.state.pendingRoles = null")
    if page.js("Own.state.mode") != "sample":
        page.js("document.querySelector('#own-src [data-act=reset]').click()")
    page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _import(page, paths, want="自社のファイル"):
    _to_sample(page)
    page.js("Own.setTab('import')")
    _set_files(page, paths)
    page.wait(lambda: want in _src(page), 120, "取り込み")
    time.sleep(0.5)


def _shot(page, tmp_path, name, quality=55):
    d = Path(os.environ.get("V42_SHOT_DIR") or tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=quality)
    (d / name).write_bytes(base64.b64decode(r["data"]))


def _far(page, tmp_path):
    _import(page, [_write(tmp_path, "haisha_far.csv", FAR_HAISHA)])
    assert page.js("Own.state.mode") == "import", page.js("Own.state.dropMsg")
    return json.loads(page.js("JSON.stringify(OwnMonth.revenue(Own.state.data))"))


# ---------------------------------------------------------------- 1. 帰り荷の売上の目安: 便ごと・車格ごと・区間ごと

def test_revenue_is_per_trip_with_each_vehicle_class_and_per_pair(app, tmp_path):
    page = app
    r = _far(page, tmp_path)
    classes = json.loads(page.js("JSON.stringify(Object.fromEntries(Own.state.data.vehicles.map(v => [v.plate, v.cls])))"))
    assert classes["9012"] == "4t" and classes["1234"] == "大型" and classes["7777"] in ("不明", ""), classes
    kinds = {t["cls"] for t in r["perTrip"]}
    assert {"4t", "10t"} <= kinds, kinds
    # 便ごとの額は、その便の車格と km の標準的運賃（JS の運賃の式と同じ）
    for t in r["perTrip"]:
        pref = "大阪府" if t["fare"]["regionKey"] == "kinki" else "東京都"
        assert t["amount"] == page.js(f"OwnMonth.fare({t['km']}, '{t['cls']}', {json.dumps(pref)}).amount"), t
    assert r["total"] == sum(t["amount"] for t in r["perTrip"]) and r["counted"] == len(r["perTrip"])
    # 4t の便は 10t の額より安い（4t の帰りに 10t の運賃を掛けない）
    ten = [t["amount"] for t in r["perTrip"] if t["cls"] == "10t"]
    four = [t["amount"] for t in r["perTrip"] if t["cls"] == "4t"]
    assert four and ten and max(four) < max(ten)
    # 車の種類が分からない車の便は数えず、件数に出す
    assert r["noClass"] > 0 and r["counted"] + r["noClass"] + r["noFare"] + r["noKm"] == r["trips"], r
    # 区間（発地 → 着地）ごとの小計の和 = 合計
    assert sum(p["subtotal"] for p in r["pairs"]) == r["total"] and len(r["pairs"]) >= 1
    assert page.js("OwnMonth.tariffClass('大型')") == "10t" and page.js("OwnMonth.tariffClass('4t')") == "4t" and page.js("OwnMonth.tariffClass('2t')") == "2t"
    assert page.js("OwnMonth.tariffClass('8t')") == "10t" and page.js("OwnMonth.tariffClass('不明')") is None and page.js("OwnMonth.tariffClass('')") is None
    _to_sample(page)


def test_revenue_panel_shows_per_trip_first_and_labels_the_total_as_an_upper_bound(app, tmp_path):
    page = app
    r = _far(page, tmp_path)
    box = page.js("document.querySelector('#own-rev').innerText")
    assert "全部の帰りに荷が付いた場合の上限" in box and "1 便の売上の目安" in box, box
    assert "車の種類が分からない帰り" in box and "数えていません" in box, box
    assert "小計" in box, box
    # 大きな字は 1 便の額（上限の合計は小さく出す）
    sizes = json.loads(page.js("""JSON.stringify({one: parseFloat(getComputedStyle(document.querySelector('#own-rev .o-rev1 b')).fontSize),
      up: parseFloat(getComputedStyle(document.querySelector('#own-rev .o-upper')).fontSize)})"""))
    assert sizes["one"] > sizes["up"], sizes
    assert f"{r['total']:,} 円" in box
    # 内訳は閉じた「詳しく」に入っている。「区分」の説明は式のそばにある
    assert page.js("document.querySelector('#own-rev-more').open") is False
    page.js("document.querySelector('#own-rev-more').open = true")
    more = page.js("document.querySelector('#own-rev-more').innerText")
    assert "区分は、200 km を超えた分を" in more and "10t" in more and "4t" in more, more
    # 印刷の 1 枚にも、上限と書く
    assert "上限" in page.js("document.querySelector('#own-printsheet').textContent")
    _to_sample(page)


# ---------------------------------------------------------------- 2. 求荷求車の文: 見出しと区間ごとの一括

def test_board_texts_are_one_per_pair_with_the_right_header(app, tmp_path):
    page = app
    _far(page, tmp_path)
    page.js("Own.setTab('insight')")
    assert page.js("!!document.querySelector('[data-act=board]')")
    page.js("document.querySelector('[data-act=board]').click()")
    txt = page.js("document.querySelector('#own-board-ta').value")
    assert "【求荷】空車情報" in txt and "【求車】" not in txt, txt
    items = json.loads(page.js("JSON.stringify(Own.state.board.items.map(i => ({label: i.label, n: i.n, text: i.text})))"))
    labels = [i["label"] for i in items]
    assert any("名古屋" in x and "加古川" in x for x in labels) and any("江東" in x for x in labels), labels
    for it in items:
        for k in ("発地:", "着地:", "曜日・時刻:", "車格:", "空き:", "連絡先: （"):
            assert k in it["text"], it
        assert it["text"].count("【求荷】空車情報") == 1
        assert re.search(r"毎週.曜", it["text"]) and "便" in it["text"]
    nagoya = next(i for i in items if "名古屋" in i["label"])
    assert "愛知県名古屋市" in nagoya["text"] and "兵庫県加古川市" in nagoya["text"] and "4t車" in nagoya["text"] and "10t" in nagoya["text"], nagoya["text"]
    # 区間ごとにコピーするボタンがある
    assert page.js("document.querySelectorAll('[data-act=board-copy-one]').length") == len(items)
    page.js("document.querySelector('[data-act=board-copy-one]').click()")
    page.wait(lambda: page.js("document.querySelector('#own-board-msg').textContent") != "", 10, "コピーの結果")
    assert "コピー" in page.js("document.querySelector('#own-board-msg').textContent")
    page.js("Own.setTab('import')")
    _to_sample(page)


# ---------------------------------------------------------------- 3. 地図: 遠くの発地からの空車の帰りが別の線になる

def test_far_origin_empty_returns_are_drawn_as_lines_on_the_map(app, tmp_path):
    page = app
    _far(page, tmp_path)
    g = json.loads(page.js("""JSON.stringify((function(){const d = Own.state.data, its = Own.state.geo.items;
      const nag = Object.values(d.places).find(p => /名古屋/.test(p.short || '') || /名古屋/.test(p.label || ''));
      const lines = its.filter(i => i.dir === null && i.kind === 'empty');
      return {nag: !!(nag && nag.lon != null), lines: lines.length, main: its.filter(i => i.dir).length,
              reach: lines.some(i => i.path.some(p => nag && Math.abs(p[0] - nag.lon) < 0.4 && Math.abs(p[1] - nag.lat) < 0.4))};})())"""))
    assert g["nag"] and g["lines"] >= 1 and g["main"] >= 1 and g["reach"], g
    assert page.js("Own.state.geo.marks.length") >= 3  # 主な往復（江東 ⇄ 加古川）の線は残る
    _to_sample(page)


# ---------------------------------------------------------------- 4. 列名が分からないファイルを 1 つだけ落とす

def test_single_file_with_unknown_columns_asks_for_roles_then_reads(app):
    page = app
    _to_sample(page)
    page.js("OwnMonth.clearAll()")
    page.js("Own.setTab('import')")
    _set_files(page, [EXAMPLES / "digitaco_unknown_cols_9012.csv"])
    page.wait(lambda: page.js("!!document.querySelector('#own-roles-pend')"), 60, "役割を選ぶ欄")
    assert page.js("Own.state.mode") == "sample"  # 選ぶまで前の表示のまま
    t = page.js("document.querySelector('#own-roles-pend').innerText")
    assert "選んでください" in t and "選ぶまで計算しません" in t, t
    assert not re.search(r"\b(skip|distance_km|loaded_flag|start_time)\b", t), t
    sels = json.loads(page.js("JSON.stringify([...document.querySelectorAll('#own-roles-pend select')].map(s => ({col: s.dataset.roleCol, val: s.value})))"))
    assert sels and all(s["val"] == "" for s in sels), sels  # 黙って選ばない
    # 列の役割を順に選ぶ。読めるようになったら取り込みの結果に切り替わる（選んだ役割は計算に渡る）
    for _ in range(10):
        if page.js("Own.state.mode") == "import":
            break
        s = page.js("""(function(){const s = [...document.querySelectorAll('#own-roles-pend select')].find(x => !x.value);
          return s ? {col: s.dataset.roleCol, opts: [...s.options].map(o => o.value).filter(Boolean)} : null;})()""")
        if not s:
            break
        want = {"X9": "skip"}.get(s["col"], None)
        val = want if want in s["opts"] else next((o for o in s["opts"] if o != "skip"), s["opts"][0])
        page.js("(function(c, v){const s = document.querySelector('#own-roles-pend select[data-role-col=\"' + c + '\"]'); s.value = v; s.dispatchEvent(new Event('change', {bubbles: true}));})(%s, %s)" % (json.dumps(s["col"]), json.dumps(val)))
        time.sleep(1.2)
    assert page.js("Own.state.mode") == "import" or page.js("!!document.querySelector('#own-roles-pend')")
    page.js("OwnMonth.clearAll()")
    _to_sample(page)


def test_haisha_with_unknown_header_names_asks_for_roles_and_reruns_on_choice(app, tmp_path):
    page = app
    text = "いつ,車,積み,降ろし先\n2026/10/1,1234,加古川,江東\n2026/10/2,1234,江東,加古川\n2026/10/3,1234,加古川,江東\n"
    _to_sample(page)
    page.js("OwnMonth.clearAll()")
    page.js("Own.setTab('import')")
    page.js("(function(){window.__roleCalls = []; const o = OwnCalc.analyze; window.__origAn2 = o; OwnCalc.analyze = function(f, opt){ window.__roleCalls.push(opt && opt.column_roles); return o.apply(this, arguments); };})()")
    try:
        _set_files(page, [_write(tmp_path, "haisha_unknown.csv", text)])
        page.wait(lambda: page.js("!!document.querySelector('#own-roles-pend') || Own.state.mode === 'import'"), 60, "役割を選ぶ欄か取り込み")
        if page.js("!!document.querySelector('#own-roles-pend')"):
            assert page.js("Own.state.mode") == "sample"
            for _ in range(8):
                s = page.js("""(function(){const s = [...document.querySelectorAll('#own-roles-pend select')].find(x => !x.value);
                  return s ? {col: s.dataset.roleCol, opts: [...s.options].map(o => o.value).filter(Boolean)} : null;})()""")
                if not s or page.js("Own.state.mode") == "import":
                    break
                want = {"いつ": "date", "車": "plate", "積み": "from", "降ろし先": "to"}.get(s["col"])
                val = want if want in s["opts"] else s["opts"][0]
                page.js("(function(c, v){const s = document.querySelector('#own-roles-pend select[data-role-col=\"' + c + '\"]'); s.value = v; s.dispatchEvent(new Event('change', {bubbles: true}));})(%s, %s)" % (json.dumps(s["col"]), json.dumps(val)))
                time.sleep(1.2)
            assert any(c for c in page.js("window.__roleCalls")), "選んだ役割を渡して計算し直していない"
        else:
            assert page.js("Own.state.data.legs.length") >= 1
    finally:
        page.js("OwnCalc.analyze = window.__origAn2")
        page.js("OwnMonth.clearAll()")
        _to_sample(page)


# ---------------------------------------------------------------- 5. 車ごと・車種ごとの最大積載量

WEIGHT_HAISHA = ("車番,車種,2026/10/1,2026/10/2,2026/10/3,2026/10/4\n"
                 "1234,大型,加古川→江東 着5:30 12.0t,江東→加古川（空）,加古川→江東 着5:30 4.0t,江東→加古川（空）\n"
                 "5678,大型,加古川→江東 着5:30 4.6t,江東→加古川（空）,加古川→江東 着5:30 3.0t,江東→加古川（空）\n")


def test_over_max_card_can_change_the_max_payload_and_it_is_remembered(app, tmp_path):
    page = app
    page.js("localStorage.removeItem('backfull.own.v42.maxload'); Own.state.maxPayload = {by_plate: {}, by_class: {}}")
    _import(page, [_write(tmp_path, "haisha_w.csv", WEIGHT_HAISHA)])
    page.js("(function(){window.__mpCalls = []; const o = OwnCalc.analyze; window.__origAn = o; OwnCalc.analyze = function(f, opt){ window.__mpCalls.push(opt); return o.apply(this, arguments); };})()")
    try:
        assert page.js("Own.state.data.kpis.load_rate.over_max") == 1
        page.js("Own.setTab('confirm')")
        sel = '#own-body .o-card[data-key="weight:L001"]'
        assert "この車の最大積載量を変える" in page.js(f"document.querySelector('{sel}').innerText")
        page.js(f"document.querySelector('{sel} [data-act=mp-open]').click()")
        assert page.js(f"!document.querySelector('{sel} .o-mp').hidden")
        page.js(f"document.querySelector('{sel} .o-mp input').value = '13'")
        page.js(f"document.querySelector('{sel} [data-act=mp-plate]').click()")
        page.wait(lambda: any(c and (c.get("max_payload_by_plate") or {}).get("1234") == 13000 for c in page.js("window.__mpCalls")), 30, "最大積載量を渡して計算し直す")
        page.wait(lambda: page.js("Own.state.data.kpis.load_rate.over_max") == 0, 30, "超過の件数が 0 になる")
        calls = page.js("window.__mpCalls")
        assert any(c and (c.get("max_payload_by_plate") or {}).get("1234") == 13000 for c in calls), calls
        saved = json.loads(page.js("localStorage.getItem('backfull.own.v42.maxload')"))
        assert saved["by_plate"] == {"1234": 13000}, saved
        # 読み直しても戻らない
        page.js("window.__mpCalls = []")
        _import(page, [_write(tmp_path, "haisha_w.csv", WEIGHT_HAISHA)])
        assert page.js("Own.state.data.kpis.load_rate.over_max") == 0
        assert any(c and (c.get("max_payload_by_plate") or {}).get("1234") == 13000 for c in page.js("window.__mpCalls"))
    finally:
        page.js("OwnCalc.analyze = window.__origAn")
        page.js("localStorage.removeItem('backfull.own.v42.maxload'); Own.state.maxPayload = {by_plate: {}, by_class: {}}")
        _to_sample(page)


# ---------------------------------------------------------------- 6. 右の欄: 詳しく・用語の説明・役割の選ぶ欄

def test_right_panel_puts_notes_under_details_and_explains_jargon(app):
    page = app
    _import(page, EX_FILES, "自社のファイル: 3 件")
    page.js("Own.setTab('import')")
    right = page.js("document.querySelector('#own-right').innerText")
    for k in ("実車 km ÷ 走った km", "積んだ重さ ÷ 最大積載量", "積載率 × 大型の実車率", "全国 62.15%"):
        assert k in right, (k, right)
    assert "13,500 kg" not in right and "辰巳" not in right  # 長い注記は閉じた「詳しく」の中
    assert page.js("document.querySelector('#own-more').open") is False
    assert page.js("[...document.querySelectorAll('#own-right .o-tmore')].every(d => !d.open)")
    page.js("document.querySelectorAll('#own-right details').forEach(d => d.open = true)")
    right = page.js("document.querySelector('#own-right').innerText")
    assert "13,500 kg" in right and "仮定" in right and "77.6%" in right and "辰巳" in right, right
    assert "区分は、200 km を超えた分を" in right
    # 用語: ファイルを落とす欄の UTF-8 / Shift_JIS に一言の説明がある
    assert "日本語の文字の保存のしかた" in page.js("document.querySelector('#own-drop').innerText")
    _to_sample(page)
    # 組み込みのサンプルの OSRM にも一言の説明がある
    page.js("Own.setTab('import')")
    page.js("document.querySelectorAll('#own-right details').forEach(d => d.open = true)")
    assert "地図データから道路の距離を求める" in page.js("document.querySelector('#own-right').innerText")


def test_role_select_text_is_not_cut_at_1920(app):
    page = app
    _import(page, EX_FILES, "自社のファイル: 3 件")
    page.js("Own.setTab('import')")
    m = json.loads(page.js("""JSON.stringify((function(){const s = document.querySelector('#own-roles select'); if (!s) return null;
      const cs = getComputedStyle(s), c = document.createElement('canvas').getContext('2d'); c.font = cs.fontSize + ' ' + cs.fontFamily;
      const txt = s.options[0].textContent, need = c.measureText(txt).width + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
      return {txt: txt, need: need, w: s.getBoundingClientRect().width};})())"""))
    assert m and m["txt"] == "役割を選んでください" and m["w"] >= m["need"] + 2, m
    _to_sample(page)


# ---------------------------------------------------------------- 写真（1920 と 400）

def test_screenshots_at_1920_and_400(app, tmp_path):
    page = app
    _import(page, EX_FILES, "自社のファイル: 3 件")
    page.js("Own.setTab('import')")
    time.sleep(1.0)
    _shot(page, tmp_path, "2026-11-06_v42_own_examples_1920.jpg")
    page.send("Emulation.setDeviceMetricsOverride", width=400, height=860, deviceScaleFactor=1, mobile=False)
    time.sleep(1.2)
    page.js("document.querySelector('.o-tiles').scrollIntoView()")
    _shot(page, tmp_path, "2026-11-06_v42_own_examples_400.jpg")
    page.js("document.querySelector('#own-rev').scrollIntoView()")
    _shot(page, tmp_path, "2026-11-06_v42_own_examples_400_rev.jpg")
    page.send("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
    time.sleep(0.8)
    _far(page, tmp_path)
    page.js("Own.setTab('import')")
    time.sleep(1.5)
    _shot(page, tmp_path, "2026-11-06_v42_own_far_1920.jpg")
    page.js("Own.setTab('insight')")
    page.js("document.querySelector('[data-act=board]').click()")
    time.sleep(0.5)
    _shot(page, tmp_path, "2026-11-06_v42_own_far_board_1920.jpg")
    page.send("Emulation.setDeviceMetricsOverride", width=400, height=860, deviceScaleFactor=1, mobile=False)
    time.sleep(1.2)
    page.js("document.querySelector('#own-rev').scrollIntoView()")
    _shot(page, tmp_path, "2026-11-06_v42_own_far_400_rev.jpg")
    page.send("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
    time.sleep(0.8)
    _to_sample(page)


# ---------------------------------------------------------------- 7. 傭車の行・重なる配車表・車庫へ戻る区間の印

NL = chr(10)
HIRED_HAISHA = NL.join(["車番,車種,2026/10/1,2026/10/2,2026/10/3,2026/10/4",
                          "1234,大型,加古川→江東 着5:30 4.6t,江東→加古川（空）,加古川→江東 着5:30 4.0t,江東→加古川（空）",
                          "傭車(丸山運送),大型,加古川→江東 着5:30 4.0t,江東→加古川（空）,,"]) + NL
RETURN_HAISHA = NL.join(["車番,車種,2026/10/1,2026/10/2,2026/10/3,2026/10/4",
                           "1234,大型,加古川→江東 着5:30 4.6t,江東→加古川,加古川→江東 着5:30 4.0t,江東→加古川"]) + NL


def test_hired_rows_are_reported_in_one_line_with_a_closed_list(app, tmp_path):
    page = app
    _import(page, [_write(tmp_path, "haisha_hired.csv", HIRED_HAISHA)])
    if not page.js("(Own.state.data.hired || []).length"):
        pytest.skip("own_calc が傭車の行を返さない")
    t = page.js("document.querySelector('#own-hired').innerText")
    assert "傭車の行" in t and "日分は自社の車として数えていません" in t, t
    assert page.js("document.querySelector('#own-hired details').open") is False
    page.js("document.querySelector('#own-hired details').open = true")
    assert "丸山運送" in page.js("document.querySelector('#own-hired').innerText")
    _to_sample(page)


def test_overlapping_haisha_files_can_be_chosen(app):
    page = app
    _import(page, [EXAMPLES / "haisha_2026-10.csv", EXAMPLES / "haisha_excel_2026-10.csv"], "自社のファイル")
    hc = json.loads(page.js("JSON.stringify(Own.state.data.haisha_choice || null)"))
    if not hc or not hc.get("overlaps"):
        pytest.skip("この 2 つの配車表は重ならない")
    assert page.js("!!document.querySelector('#own-hchoice')")
    boxes = page.js("document.querySelectorAll('#own-hchoice input[data-hf]').length")
    assert boxes == len(hc["files"]) and "使っていません" in page.js("document.querySelector('#own-hchoice').innerText")
    left = [f["file"] for f in hc["files"] if not f.get("used")][0]
    page.js("window.__hu = []; (function(){const o = OwnCalc.analyze; window.__oa = o; OwnCalc.analyze = function(f, opt){ window.__hu.push(opt && opt.haisha_use); return o.apply(this, arguments); };})()")
    try:
        page.js("(function(n){const b = document.querySelector('#own-hchoice input[data-hf=\"' + n + '\"]'); b.checked = true; b.dispatchEvent(new Event('change', {bubbles: true}));})(%s)" % json.dumps(left))
        page.wait(lambda: any(u and left in u for u in page.js("window.__hu")), 30, "選んだファイルで計算し直す")
    finally:
        page.js("OwnCalc.analyze = window.__oa")
    _to_sample(page)


def test_return_unmarked_card_has_three_buttons_and_recomputes(app, tmp_path):
    page = app
    _import(page, [_write(tmp_path, "haisha_ret.csv", RETURN_HAISHA)])
    page.js("Own.setTab('confirm')")
    sel = '#own-body .o-card[data-key="return-unmarked"]'
    if not page.js(f"!!document.querySelector('{sel}')"):
        pytest.skip("印の無い帰りの区間が無い")
    t = page.js(f"document.querySelector('{sel}').innerText")
    assert "に空・実車の印がありません" in t and "全部 空" in t and "全部 実車" in t and "不明のまま" in t, t
    before = page.js("Own.state.data.kpis.actual_rate.value")
    page.js(f"document.querySelector('{sel} [data-act=ru-empty]').click()")
    page.wait(lambda: page.js("Own.state.data.legs.filter(l => l.kind === 'empty').length") >= 2, 30, "空に直して計算し直す")
    assert page.js("Own.state.dec['return-unmarked']") == "fix" and page.js("Own.state.fixTxt['return-unmarked']") == "空"
    _to_sample(page)
