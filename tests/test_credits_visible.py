"""出典の表示の確認（権利の監査 3〜5 行目）。

- 静的: 地図の出典・交通量 API の注記・地形の出典が配信するファイルにあり、閉じた details の中や display:none の既定に入っていない。
- 画面（Edge）: 最初の画面と市場を見る画面を 1920 幅と 360 幅で開き、出典の文字が実際に見えている。Edge が無い環境だけ飛ばす。
"""

from __future__ import annotations

import contextlib
import re
import time
from pathlib import Path

import pytest
from conftest import edge_page, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
JARTIC = "このサービスは、交通量 API 機能を使用していますが、サービスの内容は国土交通省によって保証されたものではありません。"
GSI_URL = "https://maps.gsi.go.jp/development/ichiran.html"


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_map_credit_is_outside_any_details_and_has_the_links():
    html = read("index.html")
    m = re.search(r'<div id="map-credit".*?</div>', html, re.S)
    assert m, "#map-credit がありません"
    block = m.group(0)
    for s in ("© OpenStreetMap contributors", "https://www.openstreetmap.org/copyright", "OpenFreeMap", "OpenMapTiles"):
        assert s in block
    before = html[: m.start()]
    assert before.count("<details") == before.count("</details>"), "閉じた details の中に入っています"


def test_map_credit_css_shows_it_in_market_and_in_narrow_screens():
    css = read("style.css")
    assert re.search(r"#map-credit \{ display: none;", css)
    assert "body.view-market #map-credit { display: block; }" in css
    assert re.search(r"@media \(max-width: 600px\) \{\s*body:not\(\.view-home\) #map-credit \{ display: block;", css)


def test_jartic_notice_is_in_the_always_visible_line_and_the_credit_list():
    assert JARTIC in read("index.html")
    assert JARTIC in read("market.js")
    assert "#mc-jt').hidden = !M.jartic" in read("market.js")


def test_terrain_credit_is_on_the_first_screen():
    html = read("index.html")
    m = re.search(r'<p class="home-terrain">.*?</p>', html, re.S)
    assert m and "地形: " in m.group(0) and "国土地理院の標高タイルを加工して作成" in m.group(0) and GSI_URL in m.group(0)
    main = re.search(r'<main id="home".*?</main>', html, re.S).group(0)
    assert m.group(0) in main
    assert re.search(r"\.home-terrain \{[^}]*font-size: 1[1-9]px", read("style.css"))


# ---------------------------------------------------------------- 画面（Edge）
PROBE = """(sel) => { const e = document.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(), c = getComputedStyle(e);
  return {w: r.width, h: r.height, l: r.left, r: r.right, t: r.top, b: r.bottom, disp: c.display, vis: c.visibility, op: c.opacity, text: e.innerText, vw: innerWidth, vh: innerHeight}; }"""


def _probe(page, sel: str):
    return page.js(f"({PROBE})({sel!r})")


def _assert_visible(p, *needles: str):
    assert p, "要素がありません"
    assert p["disp"] != "none" and p["vis"] == "visible" and float(p["op"]) > 0 and p["w"] > 20 and p["h"] > 8, p
    assert p["l"] >= -1 and p["r"] <= p["vw"] + 1 and p["t"] >= -1 and p["b"] <= p["vh"] + 1, p
    for n in needles:
        assert n in p["text"], (n, p["text"])


@contextlib.contextmanager
def _browser(width: int, height: int):
    port = server_port("public")
    with edge_page(width, height) as page:
        def open_(hash_: str, ready: str = "!!window.__app && !document.querySelector('#loading')") -> None:
            page.send("Page.navigate", url="about:blank")
            time.sleep(0.4)
            open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}", ready_js=ready,
                     wait_loading=False, start_timeout=180, tag="credits_fail")
            time.sleep(2.5)
        yield page, open_


@pytest.mark.parametrize("width,height", [(1920, 1080), (360, 800)])
def test_credits_are_visible_without_opening_anything(width, height):
    with _browser(width, height) as (page, open_):
        open_("", "!!document.querySelector('.home-terrain') && document.body.classList.contains('view-home')")   # 最初の画面は地図を起こさない
        _assert_visible(_probe(page, ".home-terrain"), "地形: 国土地理院の標高タイルを加工して作成")
        open_("#v=market")
        _assert_visible(_probe(page, "#map-credit"), "© OpenStreetMap contributors", "OpenFreeMap", JARTIC)
