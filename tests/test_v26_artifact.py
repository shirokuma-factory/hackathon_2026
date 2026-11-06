"""SPEC §26 claude.ai の Artifact に載せる版（web/publish/*_artifact.html、G101〜G106）。

- 文字列の決まり（タグ、通信、印刷・保存、色）は Python で確かめる。
- 自己確認と画面（400px で横スクロールが出ない、ダーク、コピー、2 回押しの消去）は、doctype の殻で包んでヘッドレスの Edge で開いて確かめる。
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import artifact_build  # noqa: E402
import build_compare_page  # noqa: E402
import build_waiting_page  # noqa: E402

PUB = ROOT / "web" / "publish"
WAIT = PUB / "waiting_artifact.html"
CMP = PUB / "compare_lite_artifact.html"
PAGES = [(WAIT, "荷待ち協議の書面", build_waiting_page), (CMP, "荷物の送り方比べ", build_compare_page)]
VECTORS_C = ROOT / "tests" / "data" / "compare_lite_vectors.json"
VECTORS_W = ROOT / "tests" / "data" / "waiting_vectors.json"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _shell(path: Path, theme: str | None = None) -> str:
    """殻で包んだものを一時ファイルに書き、その URI を返す（試験のためだけ）。"""
    html = artifact_build.wrap_for_test(path.read_text(encoding="utf-8"), theme)
    out = Path(tempfile.mkdtemp(prefix="artifact_shell_")) / (path.stem + (f"_{theme}" if theme else "") + ".html")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return out.as_uri()


def _css_of(html: str) -> str:
    return re.search(r"<style>(.*?)</style>", html, re.S).group(1)


# ---------------------------------------------------------------- G101 形
@pytest.mark.parametrize("path,title,mod", PAGES)
def test_g101_artifact_files_are_up_to_date_and_have_the_host_shape(path, title, mod):
    assert path.exists()
    html = path.read_text(encoding="utf-8")
    assert html == mod.build_artifact()
    assert "\r" not in html
    assert html.startswith(f"<title>{title}</title>\n<meta name=\"claude:viewport-fit-sides\" content=\"cover\">\n<style>")
    assert not re.search(r"<(!doctype|html|head|body)[\s>]", html, re.I)
    assert len(html.encode("utf-8")) < 16 * 1024 * 1024
    assert "const ARTIFACT = true;" in html
    # ローカルの版は false のまま、印刷と保存を持っている
    local = mod.build()
    assert "const ARTIFACT = false;" in local
    assert "window.print()" in local if mod is build_waiting_page else True


# ---------------------------------------------------------------- G102 通信・印刷・保存
@pytest.mark.parametrize("path,title,mod", PAGES)
def test_g102_no_network_no_print_no_download(path, title, mod):
    html = path.read_text(encoding="utf-8")
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource", "importScripts", "import(", "navigator.serviceWorker"):
        assert bad not in html, bad
    assert not re.search(r"<script[^>]+src\s*=", html, re.I)
    assert not re.search(r"<link\b", html, re.I)
    assert not re.search(r"@import|url\(", html, re.I)
    assert not re.search(r"<(img|iframe|embed|object|video|audio|source|form)\b", html, re.I)
    assert "window.print" not in html and "@media print" not in html and "@page" not in html
    assert "download" not in html.lower()
    assert "createObjectURL" not in html and "new Blob" not in html
    assert not re.search(r"\b(alert|confirm|prompt)\(", html)
    # リンクは href の文字（利用者が押す先）のみ。script・stylesheet の外部読み込みは無い
    assert "navigator.clipboard.writeText" in html


# ---------------------------------------------------------------- G103 色と並び
_COLOR_WORDS = r"(?<![-\w])(white|black|red|green|blue|gray|grey|yellow|orange|purple|transparent)(?![-\w])"


@pytest.mark.parametrize("path,title,mod", PAGES)
def test_g103_every_color_is_a_token_outside_the_theme_blocks(path, title, mod):
    css = _css_of(path.read_text(encoding="utf-8"))
    # 色の定義は、裸の :root、prefers-color-scheme の中の :root:not([data-theme="light"])、:root[data-theme="dark"] の 3 か所
    assert re.search(r"^:root \{ --bg: #", css, re.M)
    assert re.search(r"@media \(prefers-color-scheme: dark\) \{ :root:not\(\[data-theme=\"light\"\]\) \{[^}]*color-scheme: dark;", css)
    assert re.search(r"^:root\[data-theme=\"dark\"\] \{[^}]*color-scheme: dark;", css, re.M)
    rest = re.sub(r"^:root \{[^}]*\}\n", "", css, flags=re.M)
    rest = re.sub(r"@media \(prefers-color-scheme: dark\) \{ :root:not\(\[data-theme=\"light\"\]\) \{[^}]*\} \}\n", "", rest)
    rest = re.sub(r"^:root\[data-theme=\"dark\"\] \{[^}]*\}\n", "", rest, flags=re.M)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", rest), re.findall(r"[^\n]*#[0-9a-fA-F]{3,8}\b[^\n]*", rest)[:3]
    assert not re.search(r"\b(rgb|rgba|hsl|hsla|oklch|oklab|lab|color-mix)\(", rest)
    assert not re.search(_COLOR_WORDS, rest, re.I), re.findall(r"[^\n]*" + _COLOR_WORDS + r"[^\n]*", rest, re.I)[:3]
    # 暗い側は明るい側と同じ名前の色を全部持つ
    light = set(re.findall(r"--([a-z0-9-]+):", re.search(r"^:root \{([^}]*)\}", css, re.M).group(1)))
    dark = set(re.findall(r"--([a-z0-9-]+):", re.search(r"^:root\[data-theme=\"dark\"\] \{([^}]*)\}", css, re.M).group(1)))
    assert light == dark and len(light) >= 12
    # body の明示の背景と、横の余白（決まった書き方）
    assert re.search(r"^body \{[^}]*background: var\(--bg\);", css, re.M)
    assert "padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));" in css
    assert "padding-block:" in css
    assert "[hidden] { display: none !important; }" in css
    assert "overflow-x: auto" in css


def test_g103_local_pages_use_the_same_tokens_and_keep_print():
    for mod, p in ((build_waiting_page, ROOT / "web" / "waiting.html"), (build_compare_page, ROOT / "web" / "compare_lite.html")):
        html = p.read_text(encoding="utf-8")
        css = _css_of(html)
        assert 'data-theme="dark"' in css and "prefers-color-scheme: dark" in css
        assert "padding-left: max(16px, env(safe-area-inset-left, 0px))" in css
    w = (ROOT / "web" / "waiting.html").read_text(encoding="utf-8")
    assert "window.print()" in w and "a.download" in w and "@media print" in w  # ローカルの版は印刷と CSV の保存を残す


# ---------------------------------------------------------------- G104 自己確認（ヘッドレス Edge）
def test_g104_selftest_passes_for_both_artifact_builds():
    _edge_or_skip()
    import capture_web as cw
    nw = len(json.loads(VECTORS_W.read_text(encoding="utf-8"))["cases"])
    nc = len(json.loads(VECTORS_C.read_text(encoding="utf-8"))["cases"])
    with cw.edge_page(1440, 900) as page:
        for path, expect in ((WAIT, f"SELFTEST PASS {nw + 4}/{nw + 4}"), (CMP, f"SELFTEST PASS {nc + 30}/{nc + 30}")):
            page.goto(_shell(path) + "?selftest=1")
            page.wait(lambda: page.js("document.querySelector('#selftest') && document.querySelector('#selftest').textContent.length > 0"), 30, "自己確認")
            assert page.js("document.querySelector('#selftest').textContent") == expect


# ---------------------------------------------------------------- G105 狭い画面とダーク
def _set_view(page, w: int, h: int) -> None:
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_g105_no_horizontal_page_scroll_at_400px_and_theme_applies(theme):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1440, 900) as page:
        for path, ready in ((WAIT, "!!document.querySelector('#wait-doc table.rec')"), (CMP, "!!document.querySelector('table.res')")):
            page.goto(_shell(path, theme) + "?example=1")
            _set_view(page, 400, 900)
            page.wait(lambda: page.js(ready), 30, "例")
            m = page.js("""({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth, bsw: document.body.scrollWidth,
                             bg: getComputedStyle(document.body).backgroundColor, pl: getComputedStyle(document.body).paddingLeft})""")
            assert m["sw"] <= m["cw"] and m["bsw"] <= m["cw"] + 1, (path.name, theme, m)
            assert m["pl"] == "16px", m
            r, g, b = [int(x) for x in re.findall(r"\d+", m["bg"])[:3]]
            if theme == "dark":
                assert r < 60 and g < 60 and b < 60, m
            else:
                assert r > 200 and g > 200 and b > 200, m
            # 広い画面でも崩れない
            _set_view(page, 1440, 900)
            m2 = page.js("({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})")
            assert m2["sw"] <= m2["cw"], (path.name, m2)


# ---------------------------------------------------------------- G106 コピーと 2 回押し
def test_g106_copy_buttons_forget_two_step_and_csv_paste():
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(1440, 900) as page:
        page.goto(_shell(WAIT) + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "例")
        assert page.js("!!document.querySelector('#b-print') || !!document.querySelector('#b-csv')") is False
        assert page.js("document.querySelector('#b-copydoc').textContent") == "書面の文をコピー"
        assert page.js("document.querySelector('#b-copycsv').textContent") == "CSV をコピー"
        assert page.js("document.querySelector('#copybox').hidden") is True
        # 断られたら、見える枠に文を出して選ぶ
        page.js("Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: () => Promise.reject(new Error('denied'))}}); document.querySelector('#b-copydoc').click()")
        page.wait(lambda: page.js("document.querySelector('#copybox').hidden === false"), 10, "枠")
        text = page.js("document.querySelector('#copyta').value")
        for need in ("荷待ち・荷役に関する協議のお願い（下書き）", "【記録の一覧】", "33,550 円", "【根拠】", "第 21 項", "架空 元請A物流 御中"):
            assert need in text, need
        assert text.count("\n2026-10-") == 12  # 記録の一覧は、その相手（架空 元請A物流）の 12 行
        assert page.js("document.activeElement.id") == "copyta"
        # 許されたら、コピーしたと出る
        page.js("window.__copied = null; Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: (t) => { window.__copied = t; return Promise.resolve(); }}}); document.querySelector('#b-copycsv').click()")
        page.wait(lambda: page.js("window.__copied !== null && document.querySelector('#copymsg').textContent.includes('コピーしました')"), 10, "コピー")
        csv = page.js("window.__copied")
        assert csv.startswith("﻿日付,") and csv.count("\r\n") == 19
        assert page.js("document.querySelector('#copybox').hidden") is True
        # CSV の貼り付けで読み戻せる
        page.js("document.querySelector('#b-clear').click()")
        page.js("document.querySelector('#csvpaste').value = window.__copied; document.querySelector('#b-csvpaste').click()")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 10, "貼り付けの読み戻し")
        assert "33,550" in page.js("document.querySelector('#partners table.par tbody tr').textContent")
        # 保存した記録を消すは 2 回押す
        page.js("localStorage.setItem('waiting_page_rows_v1', JSON.stringify({year: 2026, rows: [{date: '2026-10-05'}]}))")
        page.js("document.querySelector('#b-forget').click()")
        assert "もう一度" in page.js("document.querySelector('#b-forget').textContent")
        assert page.js("!!localStorage.getItem('waiting_page_rows_v1')") is True
        assert page.js("!!document.querySelector('#wait-doc table.rec')") is True
        page.js("document.querySelector('#b-forget').click()")
        assert page.js("localStorage.getItem('waiting_page_rows_v1')") is None
        assert page.js("document.querySelector('#b-forget').textContent") == "保存した記録を消す"
        # ファイル入力は見えている
        assert page.js("document.querySelector('#csvfile').hidden") is False

        page.goto(_shell(CMP) + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('table.res')"), 30, "判定")
        page.js("Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: () => Promise.reject(new Error('denied'))}}); document.querySelector('#b-copy').click()")
        page.wait(lambda: page.js("document.querySelector('#copy-msg').textContent.includes('自動でコピーできませんでした')"), 10, "断られた")
        assert page.js("document.activeElement.id") == "quote"
        page.js("window.__c = null; Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: (t) => { window.__c = t; return Promise.resolve(); }}}); document.querySelector('#b-copy').click()")
        page.wait(lambda: page.js("window.__c !== null && document.querySelector('#copy-msg').textContent === 'コピーしました'"), 10, "コピー")
        assert "492km" in page.js("window.__c")
