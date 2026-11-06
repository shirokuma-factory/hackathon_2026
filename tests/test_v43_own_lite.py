"""自社の運行の 1 枚ページ web/own_lite.html（サーバーなし。file:// で開く）の試験。

- 生成物がテンプレートと現在の web/own_calc.js・web/own_month.js から作り直した内容と同じか（古くなっていないか）
- 通信の呼び出しも外部の部品も無いこと。ヘッドレス Edge で開いて、リソースの取得が 0 件であること
- file:// でサンプルと examples/own/ の CSV を入力欄から入れ、地図の画面と同じ OwnCalc.analyze の数字と一致すること
- 求荷の文・荷待ちの下書き（web/waiting.html の貼り付けで読めること）・400px で横スクロールが出ないこと・コンソールにエラーが出ないこと
- 画面写真 docs/assets/2026-11-06_v43_own_lite_*.jpg を撮る
"""

from __future__ import annotations

import base64
import io
import json
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import artifact_build  # noqa: E402
import build_own_page  # noqa: E402
from conftest import edge_page  # noqa: E402

PAGE = ROOT / "web" / "own_lite.html"
ART = ROOT / "web" / "publish" / "own_lite_artifact.html"
WAITING = ROOT / "web" / "waiting.html"
EXAMPLES = sorted((ROOT / "examples" / "own").glob("*.csv"))
SHOTS = ROOT / "docs" / "assets"

# ページに入れておく監視。console.error と例外、取得したリソースを溜める
HOOK = """
window.__errs = [];
window.addEventListener('error', function (e) {
  var t = e.target;
  if (t && t !== window && t.tagName) window.__errs.push('resource failed: ' + (t.src || t.href));
  else window.__errs.push('error: ' + e.message);
}, true);
window.addEventListener('unhandledrejection', function (e) { window.__errs.push('rejection: ' + (e.reason && e.reason.message || e.reason)); });
(function () { var ce = console.error; console.error = function () { window.__errs.push('console.error: ' + [].slice.call(arguments).join(' ')); return ce.apply(console, arguments); }; })();
"""


def j(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def text_of(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 生成物と文字列の検査（Edge なし）
def test_generated_pages_are_up_to_date():
    assert text_of(PAGE) == build_own_page.build(), "web/own_lite.html が古い。uv run python tools/build_own_page.py で作り直してください"
    assert text_of(ART) == build_own_page.build_artifact(), "web/publish/own_lite_artifact.html が古い。uv run python tools/build_own_page.py で作り直してください"
    for p in (PAGE, ART, ROOT / "tools" / "own_page_template.html", ROOT / "tools" / "build_own_page.py"):
        assert b"\r" not in p.read_bytes(), p


def test_current_calc_and_month_sources_are_inlined():
    html = text_of(PAGE)
    for src in (ROOT / "web" / "own_calc.js", ROOT / "web" / "own_month.js"):
        assert src.read_text(encoding="utf-8").strip("\n").replace("</", "<\\/") in html, src.name
    # 地図の画面に出す市区町村と、サンプルの CSV もページの中にある
    assert len(build_own_page.munis()) > 1800 and "江東区" in html and "haisha_2026-10.csv" in html


@pytest.mark.parametrize("path", [PAGE, ART])
def test_page_has_no_network_calls_or_external_parts(path):
    html = text_of(path)
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource", "importScripts", "@import", "navigator.serviceWorker"):
        assert bad not in html, bad
    assert not re.search(r"<script[^>]*\ssrc\s*=", html, re.I)
    assert not re.search(r"url\(\s*['\"]?https?:", html, re.I)
    for m in re.finditer(r"<link\b[^>]*>", html, re.I):   # 外のスタイルや部品は読まない（データ URL のアイコンだけは可）
        href = re.search(r"href\s*=\s*['\"]([^'\"]*)", m.group(0), re.I)
        assert (href is None) or href.group(1).startswith("data:"), m.group(0)[:120]
    for m in re.finditer(r"<(?:img|iframe|source|video|audio|form)\b[^>]*>", html, re.I):
        assert not re.search(r"(?:src|action)\s*=\s*['\"]https?:", m.group(0), re.I), m.group(0)[:120]


def test_artifact_version_follows_the_artifact_rules():
    html = text_of(ART)
    assert html.startswith("<title>自社の運行を分析する</title>\n")
    assert not re.search(r"<(!doctype|html|head|body)[\s>]", html, re.I)
    assert "window.print" not in html and "download" not in html.lower()
    assert "const ARTIFACT = true;" in html and "const ARTIFACT = false;" not in html
    local = text_of(PAGE)
    assert "doPrint" in local and "doPrint() { window.print()" not in html


def test_page_text_has_usage_and_limits():
    html = text_of(PAGE)
    for need in ("サンプルで試す", "通信なし", "地図は", "実在の会社の配車表・デジタコでは確かめていません", "標準的運賃", "相場ではありません", "【求荷】", "荷待ちの下書きをコピー", "数字と気づきを文でコピー",
                 "最大積載量を変える", "不明の区間を空車として数えると"):
        assert need in html, need


# ---------------------------------------------------------------- ヘッドレス Edge（file://）
def _set_files(page, paths: list[Path]) -> None:
    root = page.send("DOM.getDocument", depth=1)["root"]["nodeId"]
    node = page.send("DOM.querySelector", nodeId=root, selector="#file-in")["nodeId"]
    page.send("DOM.setFileInputFiles", nodeId=node, files=[str(p.resolve()) for p in paths])


def _state_js() -> str:
    return """(function () {
      const S = window.OwnLite.state, d = S.data;
      const out = { pending: S.pending ? S.pending.type : null, hasData: !!d };
      if (d) {
        const k = d.kpis;
        out.actual = k.actual_rate.value; out.load = k.load_rate.value; out.eff = k.load_efficiency.value; out.legs = d.counts.legs; out.unknown = d.counts.unknown;
        out.tiles = [...document.querySelectorAll('[data-kpi]')].map((t) => [t.dataset.kpi, t.dataset.value]);
      }
      return out; })()"""


def _wait_settled(page) -> None:
    page.wait(lambda: page.js("!!(window.OwnLite.state.data || window.OwnLite.state.pending) || (window.OwnLite.state.msg || '').length > 0"), 60, "取り込み")


REF_JS = """
(function () {
  const MUNIS = %s.map(([code, name, pref, lon, lat]) => ({ code, name, pref, lon, lat }));
  const sets = %s;
  return sets.map((files) => {
    const d = OwnCalc.analyze(files, { munis: MUNIS, userAliases: {}, largeMaxLoadKg: 10000, decisions: {} });
    if (d.needs_year) return { pending: 'year' };
    if (d.failed) return { pending: (d.column_roles_needed || []).length ? 'roles' : 'failed' };
    const k = d.kpis;
    return { pending: null, hasData: true, actual: k.actual_rate.value, load: k.load_rate.value, eff: k.load_efficiency.value, legs: d.counts.legs, unknown: d.counts.unknown };
  });
})()
"""


@pytest.fixture(scope="module")
def reference(local_server):
    """地図の画面と同じ web/own_calc.js を http で読み込み、同じファイルを OwnCalc.analyze に通した数字。"""
    sample = [(ROOT / "examples" / "own" / n) for n in build_own_page.SAMPLE_FILES]
    sets = {"sample": sample, "all": EXAMPLES, **{p.name: [p] for p in EXAMPLES}}
    payload = [[{"name": p.name, "text": text_of(p)} for p in ps] for ps in sets.values()]
    with edge_page(1280, 800, webgl=False) as page:
        page.goto(f"http://localhost:{local_server}/data/meta.json")
        page.js("new Promise((ok, ng) => { const s = document.createElement('script'); s.src = '/own_calc.js'; s.onload = ok; s.onerror = () => ng(new Error('own_calc.js')); document.head.appendChild(s); })")
        res = page.js(REF_JS % (j(build_own_page.munis()), j(payload)))
    return dict(zip(sets.keys(), res)), sets


def _close(a, b) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1e-9)


def test_kpis_equal_the_map_apps_calculation_for_sample_and_examples(reference):
    ref, sets = reference
    assert ref["sample"]["hasData"] and ref["sample"]["legs"] > 50
    got = {}
    with edge_page(1920, 1080, webgl=False, hooks=(HOOK,)) as page:
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
        # サンプルのボタン
        page.js("document.querySelector('[data-act=sample]').click()")
        _wait_settled(page)
        got["sample"] = page.js(_state_js())
        for name, ps in sets.items():
            if name == "sample":
                continue
            page.js("document.querySelector('#b-reset') && !document.querySelector('#b-reset').hidden && document.querySelector('#b-reset').click()")
            _set_files(page, ps)
            page.wait(lambda: page.js("(function () { const S = window.OwnLite.state; return !!(S.data || S.pending) || /読めるファイルがありません|前の表示|計算できません/.test(S.msg || ''); })()"), 60, name)
            got[name] = page.js(_state_js())
        errs = page.js("window.__errs")
        res_count = page.js("performance.getEntriesByType('resource').filter(function (r) { return !/^data:/.test(r.name); }).length")
    assert errs == [], errs
    assert res_count == 0   # このページは、ほかのファイルも通信も読まない
    for name, r in ref.items():
        g = got[name]
        if r.get("hasData"):
            assert g["hasData"] and g["pending"] is None, (name, g)
            for key in ("actual", "load", "eff"):
                assert _close(r[key], g[key]), (name, key, r[key], g[key])
            assert (g["legs"], g["unknown"]) == (r["legs"], r["unknown"]), name
        else:   # 年を選ぶ・列の役割を選ぶ・読めない、のいずれかになるところは、同じ止まり方をする
            assert (g["pending"] == r["pending"]) or (not g["hasData"] and r["pending"] == "failed"), (name, r, g)


def test_sample_screen_flow_and_outputs():
    with edge_page(1920, 1080, webgl=False, hooks=(HOOK,)) as page:
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
        page.js("document.querySelector('[data-act=sample]').click()")
        page.wait(lambda: page.js("!!document.querySelector('[data-kpi=actual_rate]')"), 60, "数字")
        got = page.js("""(function () {
          const q = (s) => document.querySelector(s), t = (s) => (q(s) || {}).textContent || '';
          const tiles = [...document.querySelectorAll('[data-kpi]')].map((e) => e.textContent);
          const empties = [...document.querySelectorAll('#pair-table tbody tr')].map((r) => Number(r.children[4].textContent));
          return { tiles, kpi: t('#sec-kpi'), empties, ins: [...document.querySelectorAll('.ins .rule')].map((e) => e.textContent), confirm: t('#sec-confirm'),
                   board: [...document.querySelectorAll('#sec-actions details[data-keep^=board] textarea')].map((e) => e.value), draft: (q('#draft-ta') || {}).value || '',
                   chart: !!q('#chart svg'), rev: t('#sec-actions'), files: t('#sec-import'), cards: document.querySelectorAll('.cf').length }; })()""")
        assert len(got["tiles"]) == 3 and "式:" in got["tiles"][0] and "実車率" in got["tiles"][0]
        assert "不明の区間を空車として数えると" in got["kpi"] and "区間" in got["kpi"]
        assert got["empties"] and got["empties"] == sorted(got["empties"], reverse=True) and got["empties"][0] > 0
        assert got["ins"] and all("規則" not in r for r in got["ins"])
        assert got["chart"]
        assert got["board"] and all(b.startswith("【求荷】空車情報") for b in got["board"])
        assert "発地:" in got["board"][0] and "着地:" in got["board"][0] and "車格:" in got["board"][0]
        assert "相場ではありません" in got["rev"] and "告示" in got["rev"] and "上限" in got["rev"]
        assert got["cards"] >= 1 and "最大積載量" in text_of(PAGE)
        assert got["draft"].startswith("荷待ち記録 ") and got["draft"].count("荷待ち記録") >= 1
        assert re.search(r"荷待ち記録 \d+/\d+ .* 着 \d+:\d\d 出発 \d+:\d\d 車番 \d{4}", got["draft"].splitlines()[0]), got["draft"].splitlines()[0]

        # 確認のカードを 1 つ確定すると、数が変わって計算し直す
        before = page.js("document.querySelector('#sec-confirm h2').textContent")
        page.js("document.querySelector('.cf [data-act=ok]').click()")
        page.wait(lambda: page.js("document.querySelector('#sec-confirm h2').textContent") != before, 20, "確定")
        assert "確定 1" in page.js("document.querySelector('#sec-confirm h2').textContent")

        # 明日の空きから作る
        page.js("""(function () { const s = window.OwnLite.state; const f = document.querySelector('[data-tm=date]'); f.value = '2026-11-07'; f.dispatchEvent(new Event('change', {bubbles: true}));
                  const k = document.querySelector('[data-tm=kg]'); k.value = '5'; k.dispatchEvent(new Event('change', {bubbles: true})); document.querySelector('[data-act=tm-make]').click(); })()""")
        out = page.js("(document.querySelector('#tm-out') || {}).value")
        assert out.startswith("【求荷】空車情報") and "11/7（土）" in out and "最大 5 t まで" in out

        # 単価を入れると費用が式つきで出る
        page.js("var e = document.querySelector('#perkm'); e.value = '30'; e.dispatchEvent(new Event('input', {bubbles: true}))")
        assert "空車の費用の目安" in page.js("document.querySelector('#cost-out').textContent")

        assert page.js("document.querySelectorAll('.ins').length") >= 1
        errs_own = page.js("window.__errs")
        assert errs_own == [], errs_own

        # サンプルは保存しない
        assert page.js("localStorage.getItem('backfull.own.v41')") is None


def test_waiting_draft_is_read_by_the_waiting_page_paste():
    """「荷待ちの下書きをコピー」の文を、web/waiting.html の「Excel から貼り付ける」に貼って読めること（契約の相手・荷役開始は空なので、その欄の注意だけが出る）。"""
    with edge_page(1920, 1080, webgl=False, hooks=(HOOK,)) as page:
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
        page.js("document.querySelector('[data-act=sample]').click()")
        page.wait(lambda: page.js("!!document.querySelector('#draft-ta')"), 60, "下書き")
        draft = page.js("document.querySelector('#draft-ta').value")
        assert page.js("window.__errs") == []
    n_lines = len(draft.splitlines())
    assert n_lines >= 5
    with edge_page(1920, 1080, webgl=False, hooks=(HOOK,)) as page:
        page.goto(WAITING.as_uri() + "?x=own_lite")
        page.wait(lambda: page.js("!!document.querySelector('#b-paste')"), 30, "荷待ちのページ")
        page.js("document.querySelector('#paste').value = " + j(draft))
        page.js("document.querySelector('#b-paste').click()")
        try:
            page.wait(lambda: page.js("typeof S !== 'undefined' && S.rows.length > 0"), 20, "貼り付け")
        except Exception:
            pytest.fail("web/waiting.html で貼り付けが動きません（ページの読み込みエラー: " + str(page.js("window.__errs")) + "）。waiting.html を作り直した直後か確かめてください", pytrace=False)
        rows = page.js("({n: S.rows.length, errs: document.querySelector('#errs').textContent, plates: S.rows.map((r) => r.vehicle_no), arrive: S.rows.map((r) => r.arrive), depart: S.rows.map((r) => r.depart)})")
    assert rows["n"] == n_lines, rows
    assert all(re.fullmatch(r"\d{4}", p or "") for p in rows["plates"]) and all(rows["arrive"]) and all(rows["depart"])
    # 出る注意は、空にしてある欄（契約の相手・荷役開始）だけ
    assert set(re.findall(r"行目: (.+?)(?=表の|$)", rows["errs"])) <= {"契約の相手がありません", "荷役開始がありません"}, rows["errs"]


def test_import_saves_only_summaries_and_compares_previous_month():
    with edge_page(1920, 1080, webgl=False, hooks=(HOOK,)) as page:
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
        _set_files(page, [ROOT / "examples" / "own" / n for n in build_own_page.SAMPLE_FILES])
        page.wait(lambda: page.js("!!document.querySelector('[data-kpi=actual_rate]')"), 60, "数字")
        raw = page.js("localStorage.getItem('backfull.own.v41')")
        assert raw and "神戸 100 か" not in raw and "加古川 → 江東" not in raw   # CSV の中身は入っていない
        data = json.loads(raw)
        assert len(data["entries"]) == 1 and data["entries"][0]["summary"]["month"] == "2026-10"
        assert "保存しました" in page.js("document.querySelector('#sec-month').textContent")
        # 前の月（9 月）の保存を足して描き直すと、並べて出る
        page.js("""(function () { const s = JSON.parse(JSON.stringify(OwnMonth.list()[0])); s.month = '2026-09'; s.sig = 'zzzz'; s.summary.month = '2026-09'; s.summary.label = '2026 年 9 月';
                  s.summary.actual_rate = 0.7; OwnMonth.save(s); render(); })()""")
        t = page.js("(document.querySelector('#cmp-table') || {}).textContent")
        assert t and "2026 年 9 月" in t and "2026 年 10 月" in t and "実車率" in t and "ポイント" in t
        assert page.js("window.__errs") == []


def test_phone_width_has_no_horizontal_scroll_and_screenshots():
    from PIL import Image
    SHOTS.mkdir(parents=True, exist_ok=True)

    def jpg(page, name, w, h, scale=1.0, full=False, y=0):
        if full:
            m = page.send("Page.getLayoutMetrics")
            h = int(min(m["cssContentSize"]["height"], h))
        r = page.send("Page.captureScreenshot", format="png", captureBeyondViewport=True, fromSurface=True, clip={"x": 0, "y": y, "width": w, "height": h, "scale": scale})
        img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
        path = SHOTS / name
        img.save(path, "JPEG", quality=62, optimize=True)
        return path, img.size

    with edge_page(1920, 1080, webgl=False, hooks=(HOOK,)) as page:
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
        jpg(page, "2026-11-06_v43_own_lite_1920_empty.jpg", 1920, 700, 0.5)
        page.js("document.querySelector('[data-act=sample]').click()")
        page.wait(lambda: page.js("!!document.querySelector('[data-kpi=actual_rate]')"), 60, "数字")
        time.sleep(0.6)
        jpg(page, "2026-11-06_v43_own_lite_1920.jpg", 1920, 1080, 0.6)
        top = page.js("Math.round(document.querySelector('#sec-actions').getBoundingClientRect().top + window.scrollY)")
        jpg(page, "2026-11-06_v43_own_lite_1920_actions.jpg", 1920, 1100, 0.6, y=top)
        top = page.js("Math.round(document.querySelector('#sec-pairs').getBoundingClientRect().top + window.scrollY)")
        jpg(page, "2026-11-06_v43_own_lite_1920_table.jpg", 1920, 1100, 0.6, y=top)
        assert page.js("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.js("window.__errs") == []
    with edge_page(400, 900, webgl=False, hooks=(HOOK,)) as page:
        page.send("Emulation.setDeviceMetricsOverride", width=400, height=900, deviceScaleFactor=1, mobile=False)
        page.goto(PAGE.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
        page.js("document.querySelector('[data-act=sample]').click()")
        page.wait(lambda: page.js("!!document.querySelector('[data-kpi=actual_rate]')"), 60, "数字")
        time.sleep(0.6)
        # 閉じている詳細も開いて、いちばん広がる状態で横スクロールを調べる
        page.js("document.querySelectorAll('details').forEach((d) => { d.open = true; })")
        time.sleep(0.4)
        sw = page.js("({sw: document.documentElement.scrollWidth, iw: window.innerWidth, bw: document.body.scrollWidth})")
        assert sw["sw"] <= sw["iw"] and sw["bw"] <= sw["iw"], sw
        wide = page.js("[...document.querySelectorAll('body *')].filter((e) => !e.closest('.scroll-x') && e.getBoundingClientRect().right > window.innerWidth + 1 && getComputedStyle(e).position !== 'fixed').slice(0, 5).map((e) => e.tagName + '.' + e.className + ':' + Math.round(e.getBoundingClientRect().right))")
        assert wide == [], wide
        page.js("document.querySelectorAll('details').forEach((d) => { d.open = false; }); window.scrollTo(0, 0)")
        time.sleep(0.4)
        jpg(page, "2026-11-06_v43_own_lite_400.jpg", 400, 2600, 1.0, full=True)
        assert page.js("window.__errs") == []


def test_artifact_version_runs_without_print_and_without_network(tmp_path):
    f = tmp_path / "artifact_wrapped.html"
    f.write_text(artifact_build.wrap_for_test(text_of(ART)), encoding="utf-8", newline="\n")
    with edge_page(1280, 900, webgl=False, hooks=(HOOK,)) as page:
        page.goto(f.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 30, "ページ")
        page.js("document.querySelector('[data-act=sample]').click()")
        page.wait(lambda: page.js("!!document.querySelector('[data-kpi=actual_rate]')"), 60, "数字")
        assert page.js("!document.querySelector('[data-act=print]') && !document.querySelector('#printsheet').innerHTML.trim()")
        assert page.js("document.querySelector('#sec-actions').textContent.includes('荷待ちの下書きをコピー')")
        assert page.js("window.__errs") == []
        assert page.js("performance.getEntriesByType('resource').filter(function (r) { return !/^data:/.test(r.name); }).length") == 0
