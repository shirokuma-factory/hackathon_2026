"""SPEC §43 自社の画面（#v=own）の直し（実務の効き目 r3、道具の採点 r5）。

- 曜日の表は、実際の発地 → 着地の組（pair_table）ごとに、組の名前を見出しにして出す
- 求荷求車の文は、同じ曜日が月に 3 回以上あるときだけ「毎週」と書き、ほかは日付を並べる。市役所のある区に置いた地点は、その区を書かず「（区は確認）」を付ける
- 「明日の空きから作る」: 日付つきの 1 便ぶんの文を作る
- 荷待ちの下書きの車種は、売上の目安と同じ区分（4t・増t は中型）
- 帰り荷の売上の目安は、告示の額で相場ではないと書き、自社の成約単価を入れる欄がある
- 大きな数字の下に区間の数を出し、5 件未満は「参考」。不明・読めない行を空とした場合の実車率を並べ、印刷の 1 枚にも載せる
- 自分のファイルの確認のカードに Claude の文言を出さない。残るもの・消えるものを分けて書く。ファイル名は 3 件まで。1 件ずつ外せる
画面の写真は、環境変数 V43_SHOT_DIR があればそこへ、無ければ一時フォルダへ書く。
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

from test_v42_own_screen import FAR_HAISHA, RETURN_HAISHA

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "own"
EX_FILES = [EXAMPLES / "haisha_2026-10.csv", EXAMPLES / "digitaco_1234.csv", EXAMPLES / "digitaco_5678.csv"]
WDN = "月火水木金土日"


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
            fail_with_evidence(page, "地図の画面が起動しません", e, "own_v43_fail")
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
    d = Path(os.environ.get("V43_SHOT_DIR") or tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=quality)
    (d / name).write_bytes(base64.b64decode(r["data"]))


def _set_input(page, sel, value):
    page.js("(function(s, v){const e = document.querySelector(s); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true}));})(%s, %s)" % (json.dumps(sel), json.dumps(value)))


# ---------------------------------------------------------------- 1. 曜日の表は実際の組ごと

def test_weekday_table_is_built_from_the_actual_pairs(app, tmp_path):
    page = app
    _import(page, [_write(tmp_path, "haisha_far.csv", FAR_HAISHA)])
    pt = json.loads(page.js("JSON.stringify(Own.state.data.pair_table.map(t => t.label).filter((v, i, a) => a.indexOf(v) === i))"))
    assert any("名古屋" in x for x in pt) and any("横浜" in x for x in pt), pt
    page.js("document.querySelectorAll('#own-tw details').forEach(d => { d.open = true; })")
    heads = page.js("[...document.querySelectorAll('#own-tw tr.grp td')].map(t => t.textContent)")
    assert len(heads) >= 3, heads
    for label in pt:
        assert any(label in h for h in heads), (label, heads)
    # 「行き ○○ → ○○ / 帰り ○○ → ○○」の 2 本だけの見出しではない
    assert not any(h.startswith("行き") or h.startswith("帰り") for h in heads), heads
    assert any("名古屋 → 加古川" in h for h in heads), heads
    # 組ごとの便数の合計 = その組の d.pairs の便数
    tot = json.loads(page.js("""JSON.stringify((function(){const d = Own.state.data; return d.pairs.map(p => [p.label, p.trips, d.pair_table.filter(t => t.pair === p.id).reduce((a, t) => a + t.trips, 0)]);})())"""))
    assert all(a == b for _, a, b in tot), tot
    _to_sample(page)


# ---------------------------------------------------------------- 2. 求荷求車の文

def test_board_says_every_week_only_for_three_or_more_and_marks_hall_ward_places(app, tmp_path):
    page = app
    # 12 日の配車表: 同じ曜日は多くても 2 回なので「毎週○曜」とは書かず、日付を並べる
    _import(page, [_write(tmp_path, "haisha_far.csv", FAR_HAISHA)])
    page.js("Own.setTab('insight')")
    page.js("document.querySelector('[data-act=board]').click()")
    txt = page.js("document.querySelector('#own-board-ta').value")
    assert not re.search(r"毎週[月火水木金土日]曜", txt), txt
    assert re.search(r"\d+/\d+（[月火水木金土日]）", txt), txt
    # 市の名前だけで市役所のある区に置いた地点は、区を書かず、元の名前に「（区は確認）」を付ける
    assert "名古屋（区を確かめてください）" in txt and "中区" not in txt, txt
    assert "兵庫県加古川市" in txt
    # 1 か月の配車表（例）: 同じ曜日が 3 回以上あるので「毎週」と書く。その曜日は、区間の空車の曜日別の便数で 3 以上
    _import(page, EX_FILES)
    page.js("Own.setTab('insight')")
    page.js("document.querySelector('[data-act=board]').click()")
    txt2 = page.js("document.querySelector('#own-board-ta').value")
    assert re.search(r"毎週[月火水木金土日]曜", txt2), txt2
    wd = json.loads(page.js("JSON.stringify(OwnMonth.returnPairs(Own.state.data)[0].wd)"))
    for w in re.findall(r"毎週([月火水木金土日])曜", txt2.split("----------")[0]):
        assert wd.get(str(WDN.index(w)), 0) >= 3, (w, wd)
    _to_sample(page)


def test_unit_hall_ward_name(app):
    page = app
    d = page.js("""(function(){const pl = { frm: { pref: '愛知県', muni: '名古屋市中区', label: '名古屋', short: '名古屋', note: '市役所のある区に置きました（区が分かれば直せます）' },
        to: { pref: '兵庫県', muni: '加古川市', label: '加古川', short: '加古川' } };
      return [OwnMonth.placeName(pl.frm), OwnMonth.placeName(pl.to), OwnMonth.isHallWard(pl.frm), OwnMonth.isHallWard(pl.to)];})()""")
    assert d == ["愛知県名古屋（区を確かめてください）", "兵庫県加古川市", True, False], d


# ---------------------------------------------------------------- 3. 明日の空きから作る

def test_tomorrow_form_makes_a_dated_single_trip_text(app, tmp_path):
    page = app
    _import(page, [_write(tmp_path, "haisha_far.csv", FAR_HAISHA)])
    page.js("Own.setTab('insight')")
    assert page.js("!!document.querySelector('#own-tmr')")
    assert "次の空車の掲示文を作る" in page.js("document.querySelector('#own-tmr').innerText")
    v = json.loads(page.js("""JSON.stringify({date: document.querySelector('#own-tmr-date').value, frm: document.querySelector('#own-tmr-frm').value, to: document.querySelector('#own-tmr-to').value,
      cls: document.querySelector('#own-tmr-cls').value, kg: document.querySelector('#own-tmr-kg').value, time: document.querySelector('#own-tmr-time').value})"""))
    assert v["date"] == page.js("OwnMonth.tomorrowIso()") and v["frm"] and v["to"] and v["frm"] != v["to"], v  # 初期値は明日の日付と、いちばん多い空車の帰りの区間
    assert v["cls"] in ("2t", "4t", "10t") and float(v["kg"]) > 0 and re.fullmatch(r"(\d{2}:\d{2})?", v["time"]), v  # 配車表に出発の時刻が無ければ空欄
    _set_input(page, "#own-tmr-date", "2026-11-14")
    _set_input(page, "#own-tmr-time", "07:30")
    _set_input(page, "#own-tmr-kg", "3.5")
    page.js("document.querySelector('[data-act=tmr-make]').click()")
    txt = page.js("document.querySelector('#own-tmr-ta').value")
    assert "【求荷】空車情報" in txt and "11/14（土） 07:30 ごろ出発" in txt and "最大 3.5 t まで" in txt, txt
    assert "毎週" not in txt and ("発地: " + v["frm"]) in txt and ("着地: " + v["to"]) in txt, txt
    assert "連絡先: （" in txt
    page.js("document.querySelector('[data-act=tmr-copy]').click()")
    page.wait(lambda: page.js("document.querySelector('#own-tmr-msg').textContent") != "", 10, "コピーの結果")
    # 日付が空のときは入れるよう書く
    _set_input(page, "#own-tmr-date", "")
    page.js("document.querySelector('[data-act=tmr-make]').click()")
    assert "（日付を入れてください）" in page.js("document.querySelector('#own-tmr-ta').value")
    _to_sample(page)


# ---------------------------------------------------------------- 4. 荷待ちの下書きの車種

def test_waiting_draft_class_uses_the_revenue_class_mapping(app):
    page = app
    m = json.loads(page.js("JSON.stringify(['4t', '増t', '増トン', '2t', '3t', '10t', '大型', '中型', '小型', '不明', '', '8t', 'トレーラー'].map(c => OwnMonth.waitClass(c)))"))
    assert m == ["中型", "中型", "中型", "小型", "小型", "大型", "大型", "中型", "小型", "", "", "大型", "トレーラー"], m
    _import(page, EX_FILES)
    rows = json.loads(page.js("JSON.stringify(OwnMonth.draftRows(Own.state.data))"))
    vs = json.loads(page.js("JSON.stringify(Object.fromEntries(Own.state.data.vehicles.map(v => [v.plate, v.cls])))"))
    assert rows and any(c == "4t" for c in vs.values()), (vs, len(rows))
    for r in rows:
        want = page.js(f"OwnMonth.waitClass({json.dumps(vs.get(r['vehicle_no'], ''))})")
        assert r["vehicle_class"] == want, (r, vs)
    assert all(r["vehicle_class"] == "中型" for r in rows if vs.get(r["vehicle_no"]) == "4t"), rows
    assert any(vs.get(r["vehicle_no"]) == "4t" for r in rows), (rows, vs)
    _to_sample(page)


# ---------------------------------------------------------------- 5. 売上の目安と成約単価

def test_revenue_states_it_is_a_tariff_not_a_market_rate_and_takes_an_own_rate(app):
    page = app
    page.js("try { localStorage.removeItem('backfull.own.v43.rate'); } catch (e) {}")
    _import(page, EX_FILES)
    page.js("Own.state.rate = { kind: 'km', value: '' }; Own.setTab('import')")
    box = page.js("document.querySelector('#own-rev').innerText")
    assert "国の標準的運賃で計算した額です" in box, box
    assert page.js("!!document.querySelector('#own-rate') && !!document.querySelector('#own-rate-kind')")
    assert not page.js("!!document.querySelector('#own-rev-own')")  # 入れるまでは自社の額を出さない
    back = json.loads(page.js("JSON.stringify(Own.state.data.legs.filter(l => l.dir === 'back' && l.kind === 'empty').map(l => l.km))"))
    assert back, "空車の帰りの便が無い"
    _set_input(page, "#own-rate", "120")
    exp = sum(round(k * 120) for k in back if k is not None)
    t = page.js("document.querySelector('#own-rev-own').innerText")
    assert f"{exp:,}" in t and "120 円/km" in t, (exp, t)
    assert "国の標準的運賃" in page.js("document.querySelector('#own-rev').innerText")  # 告示の額も並べて出す
    assert page.js("JSON.parse(localStorage.getItem('backfull.own.v43.rate')).value") == "120"
    page.js("(function(){const s = document.querySelector('#own-rate-kind'); s.value = 'trip'; s.dispatchEvent(new Event('change', {bubbles: true}));})()")
    t2 = page.js("document.querySelector('#own-rev-own').innerText")
    assert f"{120 * len(back):,}" in t2 and "円/便" in t2, t2
    # 印刷の 1 枚にも載る
    assert "自社の成約単価" in page.js("document.querySelector('#own-printsheet').textContent")
    _set_input(page, "#own-rate", "")
    page.js("try { localStorage.removeItem('backfull.own.v43.rate'); } catch (e) {}")
    _to_sample(page)


# ---------------------------------------------------------------- 6. 大きな数字の件数と別の数え方

def test_big_numbers_show_counts_alt_rate_and_the_print_sheet_has_both(app, tmp_path):
    page = app
    _import(page, [_write(tmp_path, "haisha_ret.csv", RETURN_HAISHA)])
    tiles = json.loads(page.js("JSON.stringify([...document.querySelectorAll('#own-right .o-tile')].map(t => ({c: (t.querySelector('.o-tc') || {}).innerText || '', alt: (t.querySelector('.o-talt') || {}).textContent || ''})))"))
    ar = page.js("Own.state.data.kpis.actual_rate.legs")
    assert tiles[0]["c"].startswith(f"{ar} 区間から"), tiles
    assert ar < 5 and "件数が少ないので参考です" in tiles[0]["c"], tiles  # 4 便の配車表では参考
    assert "空車とした場合" in tiles[0]["alt"] and "%" in tiles[0]["alt"], tiles
    sheet = page.js("document.querySelector('#own-printsheet').textContent")
    assert "空車とした場合" in sheet and "読めない行" in sheet and "不明の区間" in sheet and "参考" in sheet, sheet
    # 件数の多い例のファイルでは「参考」を付けない
    _import(page, EX_FILES)
    c0 = page.js("document.querySelector('#own-right .o-tile .o-tc').innerText")
    assert "区間から" in c0 and "参考" not in c0, c0
    _to_sample(page)


# ---------------------------------------------------------------- 7. 文言と、ファイルを外す

def test_own_files_never_mention_claude_and_say_what_stays_and_what_disappears(app):
    page = app
    _import(page, EX_FILES)
    page.js("Own.setTab('confirm')")
    body = page.js("document.querySelector('#own-body').innerText")
    # 2026-11-08 の最終評価に合わせて: 「CSV の中身は…」は閉じた「詳しく」の中に移ったので、textContent で確かめる
    notes = page.js("document.querySelector('#own-notes').textContent")
    body = page.js("document.querySelector('#own-body').textContent")
    assert "Claude" not in body and "Claude" not in notes, (body[:300], notes)
    page.js("Own.setTab('import')")
    imp = page.js("document.querySelector('#own-body').innerText")
    assert "Claude" not in imp, imp
    assert "CSV の中身はページを閉じると消えます" in notes and "数字の要約" in notes and "このブラウザに残ります" in notes, notes
    assert "ページを閉じると消えます" not in notes.replace("CSV の中身はページを閉じると消えます", "")
    _to_sample(page)


def test_file_names_collapse_beyond_three_and_each_file_can_be_removed(app, tmp_path):
    page = app
    # 車番を変えて、例の配車表と重ならない（4 件とも数字のもとになる）ようにする
    extra = _write(tmp_path, "haisha_far.csv", FAR_HAISHA.replace("1234", "2234").replace("5678", "6678"))
    _import(page, EX_FILES + [extra])
    assert page.js("Own.state.data.files_used.length") == 4
    co = page.js("document.querySelector('#own-view .o-co').textContent")
    assert "ほか 1 件" in co and co.split("（")[0].count("、") == 2, co
    assert page.js("document.querySelectorAll('#own-body [data-act=file-remove]').length") == page.js("Own.state.data.files.length")
    victim = "digitaco_5678.csv"
    assert victim in page.js("Own.state.files.map(f => f.name)")
    page.js("document.querySelector('#own-body [data-act=file-remove][data-f=\"%s\"]').click()" % victim)
    page.wait(lambda: victim not in page.js("Own.state.files.map(f => f.name)"), 60, "ファイルを外して計算し直す")
    time.sleep(0.8)
    assert page.js("Own.state.mode") == "import" and len(page.js("Own.state.files")) == 3
    assert page.js("Own.state.data.files.map(f => f.file).indexOf('%s')" % victim) == -1
    assert "ほか" not in page.js("document.querySelector('#own-view .o-co').textContent")
    # 最後の 1 つまで外すとサンプルに戻る
    for _ in range(4):
        if page.js("Own.state.mode") != "import":
            break
        n = page.js("Own.state.files[0].name")
        page.js("document.querySelector('#own-body [data-act=file-remove][data-f=\"%s\"]').click()" % n)
        time.sleep(1.5)
    page.wait(lambda: page.js("Own.state.mode") == "sample", 60, "全部外してサンプルに戻る")
    _to_sample(page)


# ---------------------------------------------------------------- 8. 画面の写真（1920 と 400）

def test_screens_at_1920_and_400(app, tmp_path):
    page = app
    _import(page, EX_FILES + [_write(tmp_path, "haisha_far.csv", FAR_HAISHA)])
    page.js("Own.state.rate = { kind: 'km', value: '' }")
    _set_input(page, "#own-rate", "120")
    page.js("document.querySelector('#own-rev').scrollIntoView()")
    time.sleep(0.4)
    _shot(page, tmp_path, "2026-11-06_v43_own_import_1920.jpg")
    page.js("Own.setTab('insight')")
    time.sleep(0.4)
    _shot(page, tmp_path, "2026-11-06_v43_own_insight_1920.jpg")
    page.send("Emulation.setDeviceMetricsOverride", width=400, height=860, deviceScaleFactor=1, mobile=False)
    time.sleep(1.2)
    page.js("document.querySelector('#own-body').scrollTop = 0")
    _shot(page, tmp_path, "2026-11-06_v43_own_insight_400.jpg")
    page.js("Own.setTab('import')")
    time.sleep(0.4)
    _shot(page, tmp_path, "2026-11-06_v43_own_import_400.jpg")
    page.js("document.querySelector('#own-tw').scrollIntoView()")
    time.sleep(0.3)
    _shot(page, tmp_path, "2026-11-06_v43_own_table_400.jpg")
    for sel in ("#own-right", "#own-view"):
        over = page.js("""(function(sel){const r = document.querySelector(sel), w = r.clientWidth, left = r.getBoundingClientRect().left;
          return [...r.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right - left > w + 1 && e.offsetParent).slice(0, 6).map(e => e.tagName + '.' + e.className + '#' + e.id + ' ' + Math.round(e.getBoundingClientRect().right - left) + '/' + w);})(%s)""" % json.dumps(sel))
        assert page.js(f"document.querySelector('{sel}').scrollWidth <= document.querySelector('{sel}').clientWidth + 1"), (sel, over)
    assert page.js("document.documentElement.scrollWidth <= window.innerWidth + 1")
    _set_input(page, "#own-rate", "")
    page.js("try { localStorage.removeItem('backfull.own.v43.rate'); } catch (e) {}")
    page.send("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=1, mobile=False)
    time.sleep(0.8)
    _to_sample(page)


def test_remembered_max_payload_survives_a_page_reload(local_server):
    """覚えた最大積載量は、ページを開き直しても戻る（O を作る時点で鍵の定数がまだ読めず、空に戻っていた。本線が直した）。"""
    import capture_web as cw
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK,)) as page:
        url = f"http://localhost:{local_server}/index.html#v=own"
        page.send("Page.navigate", url=url)
        try:
            page.wait(lambda: page.js("typeof Own !== 'undefined' && !!Own.state"), 240, "自社の運行の起動")
            page.js("localStorage.setItem('backfull.own.v42.maxload', JSON.stringify({by_plate: {'1234': 13000}, by_class: {}}))")
            page.send("Page.reload")
            time.sleep(1.0)
            page.wait(lambda: page.js("typeof Own !== 'undefined' && !!Own.state"), 240, "開き直したあとの起動")
            got = page.js("Own.state.maxPayload")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "開き直したあとの最大積載量を読めません", e, "own_v43_reload_fail")
        finally:
            page.js("localStorage.removeItem('backfull.own.v42.maxload')")
        assert got["by_plate"] == {"1234": 13000}, got
