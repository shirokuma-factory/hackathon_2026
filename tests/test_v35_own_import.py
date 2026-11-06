"""SPEC §35 自社のファイルで分析する（受入条件 G145〜G149）。

- G145: data/own_sample/raw/ の 3 つの CSV を、Python（own.py。tools/build_own_sample.py の build と同じ処理）と、
  JS（web/own_calc.js の OwnCalc.analyze をヘッドレス Edge で動かす）の両方に通し、区間・判定・指標・気づきなどが一致する。
  Claude の一致の項だけは両側で常に偽にそろえる。
- G146: 自社のファイルを落とす（DOM.setFileInputFiles）と分析が出て、取り込みの前後で /api/ へのリクエストが増えない。サンプルに戻せる。
- G147: 単価を入れると空車 km の費用が式つきで出る。空なら費用の欄が出ない。
- G148: 区間の判定の CSV と、数字・気づきの文をコピーできる。
- G149: examples/own/ と docs/guide/自社の運行を分析する.md がある。
Edge が無い環境だけ、画面の試験を飛ばす（起動の時間切れは失敗）。
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from pathlib import Path

import pytest
from conftest import edge_page, edge_path_or_skip, fail_with_evidence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

WEB = ROOT / "web"
RAW = ROOT / "data" / "own_sample" / "raw"
EXAMPLES = ROOT / "examples" / "own"
GUIDE = ROOT / "docs" / "guide" / "自社の運行を分析する.md"
RAW_NAMES = ["haisha_2026-10.csv", "digitaco_1234.csv", "digitaco_5678.csv"]

NET_HOOK = """
window.__reqs = [];
(function(){
  const f = window.fetch;
  window.fetch = function(u){ try { window.__reqs.push(String(u && u.url ? u.url : u)); } catch (e) {} return f.apply(this, arguments); };
  const o = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(m, u){ try { window.__reqs.push(String(u)); } catch (e) {} return o.apply(this, arguments); };
  window.__warns = [];
  const w = console.warn; console.warn = function(){ window.__warns.push([...arguments].map(String).join(' ')); w.apply(console, arguments); };
})();
"""


# ---------------------------------------------------------------- 静的な確認（G149 と、通信しないこと）

def test_g149_examples_and_guide_exist():
    for n in RAW_NAMES:
        assert (EXAMPLES / n).read_bytes() == (RAW / n).read_bytes(), n  # サンプルと同じ架空の会社の CSV
        assert b"\r" not in (EXAMPLES / n).read_bytes()
    g = GUIDE.read_text(encoding="utf-8")
    for t in ("ファイルの用意", "列の名前", "実車率", "積載率", "積載効率", "62.15%", "38.97%", "Shift_JIS", "外に送りません", "単価"):
        assert t in g, t
    assert "\r" not in g
    for bad in ("シームレス", "活用することで", "いかがでしょうか"):
        assert bad not in g


def test_own_calc_never_talks_to_the_network():
    calc = (WEB / "own_calc.js").read_text(encoding="utf-8")
    for t in ("fetch(", "XMLHttpRequest", "/api/", "sendBeacon", "WebSocket", "import("):
        assert t not in calc, t
    own = (WEB / "own.js").read_text(encoding="utf-8")
    i0 = own.rindex("/*", 0, own.index("自社のファイルの取り込み（SPEC §35.2）"))
    i1 = own.index("function draftNote()")
    block = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", own[i0:i1], flags=re.S))  # コメントを除く
    assert "fetch(" not in block and "XMLHttpRequest" not in block and "/api/" not in block
    assert "OwnCalc.analyze" in block and "OwnCalc.decode" in block
    assert "own_calc.js" in (WEB / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- G145 JS と Python の一致

def _py_reference() -> dict:
    """Python 側。tools/build_own_sample.py の build() を、書き込み無し・Claude の提案無しで走らせた結果。"""
    import build_own_sample as bos
    return bos.build(write=False, use_claude=False)


def _js_options(ref: dict) -> dict:
    """JS に渡す前提。Python 側の build が決め打ちにしているもの（車両の登録、地点と別名、基点と行き先、推定距離、運転者名）を同じにする。"""
    import build_own_sample as bos
    return {
        "year": bos.YEAR, "mon": bos.MON, "places": ref["places"], "base": ref["base"], "dock": ref["dock"],
        "aliases": {"known": [[a, p] for a, p in bos.KNOWN], "proposed": [[a, p] for a, p in bos.PROPOSED]},
        "estKm": {"|".join(sorted((ref["base"], ref["dock"]))): bos.OSRM_KM},
        "vehicles": ref["vehicles"], "driverLabels": bos.DRIVER_LABEL,
    }


def _close(a, b, path, out, ignore):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in ignore.get(path.count("."), ()) and path.count(".") == 0:
                continue
            if k not in a or k not in b:
                if (a.get(k) is None and b.get(k) is None):
                    continue
                out.append(f"{path}.{k}: キーが片方だけ {k in a} / {k in b}")
                continue
            _close(a[k], b[k], f"{path}.{k}", out, ignore)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: 長さが違う {len(a)} / {len(b)}")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{i}]", out, ignore)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
            out.append(f"{path}: {a!r} / {b!r}")
    elif a != b:
        out.append(f"{path}: {a!r} / {b!r}")


def _strip(d: dict) -> dict:
    """比べない項目を外す。Claude の欄、道路の形、JS だけが足した項目。"""
    d = json.loads(json.dumps(d))
    # SPEC §41 で JS だけが足した項目（列の役割の選択、ファイルの形の目印、重複、県内の往復の情報）も比べない
    for k in ("claude", "route", "paper", "company", "fiction", "unplaced", "encodings", "version", "month", "year_choice", "period", "failed", "files_read", "files_unread",
              "column_roles_needed", "shape_signatures", "layouts", "needs_month", "month_choice", "digitaco_duplicates", "notices", "geo",
              "year_choices", "months", "hired", "time_dropped", "haisha_choice",  # SPEC §42 で JS だけが足した項目
              "multi_month", "month_label", "months_detail", "files_used", "file_order"):  # SPEC §43 で JS だけが足した項目
        d.pop(k, None)
    for c in d["columns"]:
        c.pop("claude_note", None)
        c.pop("claude_input", None)
    for v in d["vehicles"]:
        v.pop("registered", None)
        v.pop("max_load_assumed", None)
    for f in d["files"]:
        for k in ("encoding", "layout_note_ja", "shape_signature", "duplicates", "year", "reason", "excluded"):
            f.pop(k, None)
    d["counts"].pop("outside_pair", None)
    for k in ("hired_cells", "hired_legs", "time_dropped", "local_legs", "return_unmarked", "dropped_overlap", "unreadable_rows", "dest_missing", "unplaced_legs"):  # SPEC §42・§43 で JS だけが足した件数
        d["counts"].pop(k, None)
    for c in d["confirm"]:
        c.pop("file", None)  # JS は「デジタコの CSV」と書く（Python は digitaco_*.csv）
        for k in ("key", "effect", "decision"):  # 確認の決定の鍵と反映の欄は JS だけ（SPEC §39）
            c.pop(k, None)
    return d


def test_g145_js_and_python_give_the_same_result_on_the_three_raw_csvs(local_server):
    edge_path_or_skip()
    ref = _py_reference()
    opts = _js_options(ref)
    files = [{"name": n, "text": (RAW / n).read_text(encoding="utf-8")} for n in RAW_NAMES]
    # Shift_JIS にしたファイルでも同じ結果になること（文字コードの判定）
    sjis = [{"name": n, "bytes": list((RAW / n).read_text(encoding="utf-8").encode("cp932"))} for n in RAW_NAMES]

    port = local_server
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{port}/data/meta.json")  # 同じ配信元の何もない文書に own_calc.js を script として読み込む
        page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
        assert page.js("typeof OwnCalc === 'object'")
        res = page.js(f"OwnCalc.analyze({json.dumps(files, ensure_ascii=False)}, {json.dumps(opts, ensure_ascii=False)})")
        res_sjis = page.js(
            "(function(){const fs = %s.map(f => { const r = OwnCalc.decode(new Uint8Array(f.bytes)); return { name: f.name, text: r.text, encoding: r.encoding }; });"
            "return { enc: fs.map(f => f.encoding), res: OwnCalc.analyze(fs, %s) };})()" % (json.dumps(sjis), json.dumps(opts, ensure_ascii=False)))
        cases = [[2.5, 0], [3.5, 0], [0.5, 0], [0.125, 2], [1.005, 2], [2.675, 2], [0.15, 1], [0.25, 1], [0.75, 1], [11.35, 1], [9.55, 1], [37.5, 0], [0.3333333335, 9], [12.25, 1]]
        js_round = page.js("%s.map(([x, n]) => OwnCalc.pyRound(x, n))" % json.dumps(cases))

    # 四捨五入は Python の round と同じ（ちょうど 0.5 のときだけ偶数へ）
    assert js_round == [round(x, n) for x, n in cases]
    a, b = _strip(ref), _strip(res)
    diffs: list[str] = []
    _close(a, b, "$", diffs, {})
    assert not diffs, f"{len(diffs)} 件の違い: " + " | ".join(diffs[:8])
    # 中身が空の一致になっていないこと
    assert len(b["legs"]) == 107 and len(b["insights"]) == 6 and len(b["confirm"]) == 7 and len(b["unreadable"]) == 3
    assert b["kpis"]["actual_rate"]["value"] == pytest.approx(0.7761887643518305)
    # 文字コード
    assert res_sjis["enc"] == ["Shift_JIS"] * 3, res_sjis["enc"]
    diffs2: list[str] = []
    _close(a, _strip(res_sjis["res"]), "$", diffs2, {})
    assert not diffs2, f"Shift_JIS: {len(diffs2)} 件の違い: " + " | ".join(diffs2[:8])


# 壊れた行（SPEC §39）。Python と JS が同じ理由・同じ走行と停車を返す
BROKEN_ROWS_CSV = """KBN,ST_TIME,ED_TIME,ODO_KM,JK_FLG,POS_NAME,DRV_CD
1,2026-10-06 20:10,2026-10-07 03:00,551.8,1,,D01
2,2026-10-07 03:00,2026-10-07 04:10,,,加古川,D01
1,2026-10-07 04:10,2026-10-07 05:00,abc,0,,D01
2,2026-10-07 05:00,2026-10-07 05:00,,,江東,D01
1,2026-10-07 06:00,2026-10-07 05:30,12,1,,D01
1,2026-10-07 08:00,2026-10-07 09:00,"1,234.5km",0,,D01
"""


def test_g145_js_and_python_agree_on_end_before_start_and_non_numeric_distance(local_server):
    import csv
    import io

    from shipcompare import own
    edge_path_or_skip()
    py = own.parse_digitaco(list(csv.DictReader(io.StringIO(BROKEN_ROWS_CSV))), lambda n: n, "1234")
    assert [(e["line"], e["reason"]) for e in py["errors"]] == [(4, "距離が読めません"), (5, own.END_BEFORE_START), (6, own.END_BEFORE_START)]
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
        rows = list(csv.reader(io.StringIO(BROKEN_ROWS_CSV)))
        js = page.js(f"OwnCalc.parseDigitaco({json.dumps(rows, ensure_ascii=False)}, (n) => n, '1234')")
    assert js == json.loads(json.dumps(py))


# ---------------------------------------------------------------- 画面の試験（G146〜G148）

@pytest.fixture(scope="module")
def app(local_server):
    """Edge（1920x1080）を 1 つ起動し、自社の運行の画面を開いた状態の page を渡す。配信サーバーはセッションで 1 つ。終わると必ず止める。"""
    import capture_web as cw
    port = local_server
    with edge_page(1920, 1080, hooks=(cw.ERR_HOOK, NET_HOOK)) as page:
        # 地図の部品の CDN が遅いと、Page.goto の 30 秒（読み込みの完了待ち）で足りない。移動だけして、起動を 240 秒まで待つ。時間切れは失敗
        from conftest import RESOURCE_HOOK
        page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
        page.send("Page.navigate", url=f"http://localhost:{port}/index.html#v=own")
        try:
            page.wait(lambda: page.js("!!window.__app"), 240, "アプリの起動")
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "地図の画面が起動しません", e, "own_import_fail")
        try:
            page.wait(lambda: page.js("!!document.querySelector('#own-file')"), 60, "ファイルを落とす欄")
        except Exception as e:  # noqa: BLE001
            fail_with_evidence(page, "ファイルを落とす欄が出ません", e, "own_import_fail")
        time.sleep(2.0)
        yield {"page": page, "port": port, "warns0": page.js("window.__warns.length")}


def _api_calls(page) -> list[str]:
    reqs = page.js("window.__reqs")
    perf = page.js("performance.getEntriesByType('resource').map(e => e.name)")
    return [u for u in reqs + perf if "/api/" in u]


def _set_files(page, paths):
    doc = page.send("DOM.getDocument", depth=1)
    nid = page.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="#own-file")["nodeId"]
    page.send("DOM.setFileInputFiles", files=[str(p) for p in paths], nodeId=nid)


def _src_text(page) -> str:
    return page.js("(document.querySelector('#own-src') || {textContent: ''}).textContent")


def _to_sample(page):
    if page.js("Own.state.mode") != "sample":
        page.js("document.querySelector('#own-src [data-act=reset]').click()")
    page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "サンプルに戻る")


def _import_examples(page):
    _to_sample(page)
    page.js("Own.setTab('import')")
    _set_files(page, [EXAMPLES / n for n in RAW_NAMES])
    page.wait(lambda: "自社のファイル: 3 件" in _src_text(page), 120, "取り込み")
    time.sleep(0.5)


def test_g146_import_by_input_and_drop_sends_nothing_to_the_api_and_can_go_back(app):
    page = app["page"]
    assert page.js("Own.state.mode") == "sample" and "サンプル（架空の会社）" in _src_text(page)
    before = _api_calls(page)
    n_req = page.js("window.__reqs.length")
    # 1) ファイルの欄（DOM.setFileInputFiles）
    _import_examples(page)
    d = page.js("Own.state.data")
    assert d["counts"]["legs"] == 107 and d["counts"]["unreadable"] == 3
    assert d["counts"]["loaded"] + d["counts"]["empty"] + d["counts"]["unknown"] == 107
    assert [f["encoding"] for f in d["files"]] == ["UTF-8"] * 3
    right = page.js("document.querySelector('#own-right').innerText")
    pct = f"{d['kpis']['actual_rate']['value'] * 100:.1f}"
    assert "実車率" in right and pct in right and "全国 62.15%" in right and "全国 39.08%" in right
    assert "式が違います" in right and "比べられるのは実車率だけです" in right
    assert page.js("Own.state.mode") == "import" and "サンプルに戻す" in _src_text(page)
    assert "自社のファイルを分析しています" in page.js("document.querySelector('#own-notes .f-n1').textContent")
    # 左の 3 つのタブも、取り込んだ結果で描き直される
    left = page.js("document.querySelector('#own-body').innerText")
    for f in RAW_NAMES:
        assert f in left
    assert "読めない 3 行" in left and "月が分かりません" in left
    page.js("Own.setTab('insight')")
    assert page.js("document.querySelectorAll('#own-body .o-ins').length") == len(d["insights"]) >= 4
    page.js("Own.setTab('confirm')")
    assert page.js("document.querySelectorAll('#own-body .o-card').length") == len(d["confirm"])
    # 地図: 市区町村の名前の先頭で置けた 2 地点と、置けなかった地点の一覧
    placed = {p["short"] for p in d["places"].values() if p.get("lon") is not None}
    assert placed == {"加古川", "江東"}
    assert {u["name"] for u in d["unplaced"]} == {"辰巳", "辰巳倉庫", "辰巳DC3"}
    assert page.js("document.querySelectorAll('.own-tag').length") == 2
    assert page.js("Own.layers().length") == 3
    page.js("Own.setTab('import')")
    assert page.js("document.querySelectorAll('#own-body .o-up').length") == 3
    # 2) 落とす（本物のドラッグ・ドロップのイベント）。サンプルに戻してから
    page.js("document.querySelector('#own-src [data-act=reset]').click()")
    page.wait(lambda: "サンプル（架空の会社）" in _src_text(page), 10, "サンプルに戻る")
    assert page.js("Own.state.data.counts.legs") == 107 and page.js("!!Own.state.data.fiction")
    assert "架空の会社のサンプルです" in page.js("document.querySelector('#own-notes .f-n1').textContent")
    x, y = page.js("(function(){const r=document.querySelector('#own-drop').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2];})()")
    data = {"items": [], "files": [str(EXAMPLES / n) for n in RAW_NAMES], "dragOperationsMask": 1}
    for ty in ("dragEnter", "dragOver", "drop"):
        page.send("Input.dispatchDragEvent", type=ty, x=x, y=y, data=data)
    page.wait(lambda: "自社のファイル: 3 件" in _src_text(page), 30, "ドロップの取り込み")
    assert page.js("Own.state.data.counts.legs") == 107 and not page.js("!!Own.state.data.fiction")
    # /api/ へのリクエストは、取り込みの前後で 1 件も増えない
    assert _api_calls(page) == before
    assert not [u for u in page.js("window.__reqs")[n_req:] if "/api/" in u]
    # サンプルに戻す
    page.js("document.querySelector('#own-src [data-act=reset]').click()")
    page.wait(lambda: page.js("Own.state.mode") == "sample", 10, "戻る")
    assert "サンプル（架空の会社）" in _src_text(page) and "サンプルに戻す" not in _src_text(page)
    assert page.js("Own.state.data.insights.length") == 6 and page.js("Own.state.data.confirm.length") == 7


def test_g146_shift_jis_file_and_unreadable_files_are_reported_in_the_page(app, tmp_path):
    page = app["page"]
    _to_sample(page)
    page.js("Own.setTab('import')")
    sj = []
    for n in RAW_NAMES:
        p = tmp_path / n
        p.write_bytes((RAW / n).read_text(encoding="utf-8").encode("cp932"))
        sj.append(p)
    bad = tmp_path / "memo.csv"
    bad.write_text("名前,年齢\n山田,30\n", encoding="utf-8")
    xlsx = tmp_path / "x.xlsx"
    xlsx.write_bytes(b"PK\x03\x04")
    _set_files(page, [*sj, bad, xlsx])
    page.wait(lambda: "自社のファイル: 3 件（読めなかった 1 件）" in _src_text(page), 120, "取り込み")  # memo.csv は読めなかったファイル（SPEC §40）
    d = page.js("Own.state.data")
    enc = {f["file"]: f["encoding"] for f in d["files"]}
    assert enc["haisha_2026-10.csv"] == "Shift_JIS" and enc["digitaco_1234.csv"] == "Shift_JIS"
    assert d["counts"]["legs"] == 107  # 文字コードが違っても同じ結果
    left = page.js("document.querySelector('#own-body').innerText")
    assert "Shift_JIS" in left and "memo.csv" in left and "配車表にもデジタコにも見えません" in left
    assert "CSV ではありません" in page.js("document.querySelector('#own-drop-msg').textContent")  # xlsx は読まずに理由を出す
    _to_sample(page)


def test_g146_unplaced_place_can_be_put_on_the_map_by_a_municipality_name(app):
    page = app["page"]
    _import_examples(page)
    d = page.js("Own.state.data")
    out_ls = sum(r["long_stops"] or 0 for r in d["table"] if r["dir"] == "out")
    page.js("document.querySelector('.o-up[data-name=\"辰巳\"] input').value = '江東区'")
    page.js("document.querySelector('.o-up[data-name=\"辰巳\"] button[data-act=alias]').click()")
    page.wait(lambda: page.js("Own.state.data.unplaced.length") == 0, 20, "別名で計算し直し")
    d2 = page.js("Own.state.data")
    # 辰巳・辰巳倉庫・辰巳DC3 が 江東区 に入り、行き先の 30 分以上の停車が、サンプル（Python）の 11 回になる
    assert sum(r["long_stops"] or 0 for r in d2["table"] if r["dir"] == "out") == 11 > out_ls
    assert len([p for p in d2["places"].values() if p.get("lon") is not None]) == 2
    assert "地図に置けない地点はありません" in page.js("document.querySelector('#own-body').innerText")
    _to_sample(page)


def test_g147_unit_cost_shows_formula_and_is_hidden_when_empty(app):
    page = app["page"]
    _import_examples(page)
    page.js("Own.state.cost.perKm = ''; Own.state.cost.perHour = ''")
    page.js("document.querySelector('#own-perkm').dispatchEvent(new Event('input', {bubbles: true}))")
    # 空のとき: 費用の欄も列も出ない（0 円にしない）
    assert page.js("document.querySelectorAll('#own-cost-out .o-crow').length") == 0
    assert "円" not in page.js("document.querySelector('#own-cost-out').innerText")
    heads = page.js("[...document.querySelectorAll('#own-tw th')].map(t => t.textContent)")
    assert "空車の費用" not in heads and "停車の費用" not in heads and len(heads) == 5
    empty_km = page.js("Own.state.data.legs.filter(l => l.kind === 'empty').reduce((a, l) => a + (l.km || 0), 0)")
    long_min = page.js("Own.state.data.stops.filter(s => s.minutes >= 30).reduce((a, s) => a + s.minutes, 0)")
    assert empty_km > 0 and long_min > 0
    # 円/km を本物の入力で入れる
    page.js("document.querySelector('#own-perkm').focus()")
    page.insert("30")
    page.wait(lambda: page.js("document.querySelectorAll('#own-cost-out .o-crow').length") == 1, 10, "費用の表示")
    km_txt = f"{round(empty_km, 1):,.1f}"  # 式の km は小数 1 桁で、その表示どおりに掛ける（SPEC §39）
    yen = round(round(empty_km, 1) * 30)
    txt = page.js("document.querySelector('#own-cost-out').innerText")
    assert f"空車 {km_txt} km × 30 円/km = {yen:,} 円" in txt, txt
    heads = page.js("[...document.querySelectorAll('#own-tw th')].map(t => t.textContent)")
    assert "空車の費用" in heads and "停車の費用" not in heads
    row_total = page.js("[...document.querySelectorAll('#own-tw tr.tot')].pop().innerText")
    assert "円" in row_total
    # 円/時
    page.js("document.querySelector('#own-perhour').focus()")
    page.insert("3000")
    page.wait(lambda: page.js("document.querySelectorAll('#own-cost-out .o-crow').length") == 2, 10, "停車の費用")
    txt = page.js("document.querySelector('#own-cost-out').innerText")
    assert f"{round(long_min / 60, 1):.1f} 時間 × 3,000 円/時 = {round(round(long_min / 60, 1) * 3000):,} 円" in txt, txt
    assert "停車の費用" in page.js("[...document.querySelectorAll('#own-tw th')].map(t => t.textContent)")
    assert "削減額の約束ではありません" in page.js("document.querySelector('#own-right').innerText")
    # 消すと、また出ない
    for sel in ("#own-perkm", "#own-perhour"):
        page.js(f"(function(){{const e = document.querySelector('{sel}'); e.value = ''; e.dispatchEvent(new Event('input', {{bubbles: true}}));}})()")
    assert page.js("document.querySelectorAll('#own-cost-out .o-crow').length") == 0
    assert len(page.js("[...document.querySelectorAll('#own-tw th')]")) == 5
    _to_sample(page)


def test_g147_unit_cost_works_on_the_sample_too_and_text_includes_it(app):
    page = app["page"]
    _to_sample(page)
    page.js("Own.setTab('import')")
    page.js("(function(){const e = document.querySelector('#own-perkm'); e.value = '25'; e.dispatchEvent(new Event('input', {bubbles: true}));})()")
    assert "円/km" in page.js("document.querySelector('#own-cost-out').innerText")
    page.js("document.querySelector('[data-copy=text]').click()")
    page.wait(lambda: page.js("Own.state.lastCopy.length") > 0, 10, "コピー")
    assert "空車の費用の目安" in page.js("Own.state.lastCopy") and "約束ではありません" in page.js("Own.state.lastCopy")
    page.js("(function(){const e = document.querySelector('#own-perkm'); e.value = ''; e.dispatchEvent(new Event('input', {bubbles: true}));})()")


def test_g148_copy_leg_csv_and_text(app):
    page = app["page"]
    _import_examples(page)
    page.js("document.querySelector('[data-copy=csv]').click()")
    page.wait(lambda: page.js("Own.state.lastCopy.length") > 100, 10, "CSV のコピー")
    csv_text = page.js("Own.state.lastCopy")
    lines = csv_text.strip("\n").split("\n")
    assert lines[0].startswith("区間ID,日付,車番,出発地,到着地,向き,距離km,距離の出どころ,判定,判定の根拠")
    assert len(lines) == 1 + 107
    assert sum(1 for ln in lines[1:] if ",実車," in ln) == 69 and sum(1 for ln in lines[1:] if ",空車," in ln) == 20 and sum(1 for ln in lines[1:] if ",不明," in ln) == 18
    assert "デジタコの実空区分" in csv_text and "配車表の（空）の印" in csv_text
    assert "コピーしました" in page.js("document.querySelector('#own-copy-msg').textContent")
    assert page.js("document.querySelector('#own-copy-ta').value") == csv_text  # 画面の欄にも同じ内容（コピーが使えない環境の予備）
    page.js("document.querySelector('[data-copy=text]').click()")
    page.wait(lambda: page.js("Own.state.lastCopy").startswith("自社の運行の分析"), 10, "文のコピー")
    t = page.js("Own.state.lastCopy")
    d = page.js("Own.state.data")
    assert f"実車率 {d['kpis']['actual_rate']['value'] * 100:.1f}%" in t and "全国は 62.15%" in t
    assert "トンキロの比で式が違います" in t
    for i, ins in enumerate(d["insights"], 1):
        assert f"{i}. {ins['text']}" in t
    # クリップボードそのものにも入っている（許可が出せる環境だけ）
    try:
        page.send("Browser.grantPermissions", permissions=["clipboardReadWrite", "clipboardSanitizedWrite"], origin=f"http://localhost:{app['port']}")
        page.js("document.body.focus()")
        got = page.js("navigator.clipboard.readText()")
        assert got == t
    except Exception:  # noqa: BLE001
        pass
    _to_sample(page)


def test_console_has_no_errors_or_new_warnings_after_the_whole_flow(app):
    page = app["page"]
    _import_examples(page)
    for tab in ("confirm", "insight", "import"):
        page.js(f"Own.setTab('{tab}')")
    _to_sample(page)
    assert page.js("window.__errs") == []
    assert page.js("window.__warns.length") == app["warns0"], page.js("window.__warns")
