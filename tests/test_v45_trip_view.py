"""SPEC §45 選んだ運行の図（web/trip_view.js、試作。自社の運行の画面にはまだ入れていない）。

- 荷物あり・空車・不明の 3 つが、文字と形（色を外しても）で区別できる。
- 経路が分かる組だけ道路を描き（60 点以内）、分からないときは「区間の模式図（実際の道路ではありません）」にする。
- 車種が分からないときは「車種不明」の汎用の記号。
- コンソールに誤りが出ない。360 と 480 の幅で横にはみ出さない。
ヘッドレス Edge は 1 つだけ起動し、終わりに必ず止める（tests/conftest.py の edge_page）。Edge が無い環境では画面の試験を飛ばす。
"""

from __future__ import annotations

import base64
import io
import re
import sys
from pathlib import Path

import pytest
from conftest import RESOURCE_HOOK, edge_page

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
JS = ROOT / "web" / "trip_view.js"
CSS = ROOT / "web" / "trip_view.css"
HARNESS = ROOT / "examples" / "trip_view" / "index.html"
ASSETS = ROOT / "docs" / "assets"

CONSOLE_HOOK = """
window.__errs = window.__errs || [];
['error', 'warn'].forEach(function (k) { var o = console[k]; console[k] = function () { window.__errs.push(k + ': ' + [].slice.call(arguments).join(' ')); return o.apply(console, arguments); }; });
window.addEventListener('error', function (e) { if (e.message) window.__errs.push('onerror: ' + e.message); });
window.addEventListener('unhandledrejection', function (e) { window.__errs.push('rejection: ' + e.reason); });
"""

SCENES = ["loaded", "loaded_out", "empty", "empty_haisha", "unknown", "unknown_class", "no_route", "row"]


def test_files_are_lf_and_have_no_library():
    for p in (JS, CSS, HARNESS):
        assert b"\r" not in p.read_bytes(), p
    js = JS.read_text(encoding="utf-8")
    assert not re.search(r"\bimport\s|require\(|https?://(?!www\.w3\.org)", js), "外部ライブラリや外部 URL を使わない"
    assert "three" not in js.lower()
    # 動かさない（アニメーションを使わない）
    assert "requestAnimationFrame" not in js and "animate" not in CSS.read_text(encoding="utf-8").replace("animation: none", "")


def _shot(page, name: str) -> Path:
    from PIL import Image
    r = page.send("Page.captureScreenshot", format="png", fromSurface=True)
    img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
    ASSETS.mkdir(parents=True, exist_ok=True)
    path = ASSETS / name
    img.save(path, "JPEG", quality=85)
    return path


def _go(page, port: int, scene: str, w: int, h: int = 900):
    page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
    page.goto(f"http://127.0.0.1:{port}/examples/trip_view/index.html?preset={scene}")
    page.wait(lambda: page.js("!!window.__ready && !!document.querySelector('.tv')"), 30, "図の描画")
    page.wait(lambda: page.js("Array.from(document.images).every(i=>i.complete) && Array.from(document.querySelectorAll('image')).length >= 0"), 10, "画像")


INFO_JS = """(() => { const t = document.querySelector('.tv'); const s = t.querySelector('.tv-state');
  const g = s.querySelector('.tv-cargo');
  return { route: t.dataset.route, label: s.querySelector('.tv-state-text').textContent, shape: g.dataset.shape,
    sig: Array.from(g.children).map(c => c.tagName + '.' + c.getAttribute('class')).join('|'),
    pts: (t.querySelector('.tv-route') || {getAttribute: () => null}).getAttribute('data-points'),
    d: (t.querySelector('.tv-route') || {getAttribute: () => ''}).getAttribute('d'),
    chev: t.querySelectorAll('.tv-chev').length, wh: t.querySelectorAll('[data-symbol=warehouse]').length,
    text: t.textContent, veh: t.querySelector('.tv-veh').dataset.vehicle,
    vsym: (t.querySelector('.tv-veh-generic') || {dataset: {}}).dataset.symbol || (t.querySelector('.tv-veh-img') ? 'image' : ''),
    facts: t.querySelector('.tv-facts').textContent }; })()"""


def test_trip_view_in_edge(examples_server):
    shots = []
    with edge_page(1280, 800, hooks=(RESOURCE_HOOK, CONSOLE_HOOK), webgl=False) as page:
        info = {}
        for sc in SCENES:
            _go(page, examples_server, sc, 480)
            info[sc] = page.js(INFO_JS)
            assert page.js("window.__errs.length") == 0, (sc, page.js("window.__errs"))
            ov = page.js("(() => { const t = document.querySelector('.tv'); return [t.scrollWidth, t.clientWidth, document.documentElement.scrollWidth, innerWidth]; })()")
            assert ov[0] <= ov[1] and ov[2] <= ov[3], (sc, 480, ov)

        # 3 つの状態: 文字・形が違う
        a, b, c = info["loaded"], info["empty"], info["unknown"]
        assert (a["label"], b["label"], c["label"]) == ("荷物あり", "空車", "不明")
        assert (a["shape"], b["shape"], c["shape"]) == ("loaded", "empty", "unknown")
        assert len({a["sig"], b["sig"], c["sig"]}) == 3, "形の要素が 3 つとも違うこと"
        assert "？" in c["sig"] or "tv-unk-q" in c["sig"]
        assert "tv-box" in a["sig"] and "tv-frame" in b["sig"] and "tv-box" not in b["sig"]

        # 経路あり: 道路・向きの印・倉庫 2 つ・60 点以内。帰りは行きと向きが逆
        assert a["route"] == "known" and 2 <= int(a["pts"]) <= 60 and a["chev"] >= 2 and a["wh"] == 2
        assert info["loaded_out"]["route"] == "known" and info["loaded_out"]["d"] != a["d"]
        # 模式図: 道路を描かず、断り書きを出す
        for sc in ("unknown", "no_route"):
            assert info[sc]["route"] == "schematic" and info[sc]["pts"] is None and info[sc]["chev"] == 0
            assert "出発と到着の位置だけを示しています" in info[sc]["text"]  # 文言の直し 2026-11-07（模式図の断り書きを 1 文に）
        assert "出発と到着の位置だけを示しています" not in a["text"]
        assert "実際に走った道とは違うことがあります" in a["text"]
        assert "地点不明" in info["unknown"]["text"]

        # 車種
        assert info["unknown_class"]["veh"] == "unknown" and info["unknown_class"]["vsym"] == "generic" and "車種不明" in info["unknown_class"]["text"]
        assert a["veh"] in ("4t", "大型", "2t") and a["vsym"] == "image" and "車種不明" not in a["text"]

        # 日時の根拠・距離の出どころ・荷の出どころ（文言の直し 2026-11-07: 「実出発」→「出発の時刻」など）
        assert "出発の時刻" in info["empty"]["facts"] and "デジタコ" in info["empty"]["facts"]
        assert "日付だけ分かる" in info["empty_haisha"]["facts"] and "推定" in info["empty_haisha"]["facts"] and "配車表" in info["empty_haisha"]["facts"]
        assert "距離不明" in info["unknown"]["facts"] and "元の記録" in info["unknown"]["facts"]
        assert "出発の時刻" in info["row"]["facts"]  # 行の形（returnTrips の rows）でも同じ図になる

        # 色を外して（グレースケール）も 3 つが区別できる: 文字と形の要素が違う
        gray = []
        for sc in ("loaded", "empty", "unknown"):
            _go(page, examples_server, sc, 480)
            page.js("document.body.style.filter = 'grayscale(1)'")
            assert "grayscale" in page.js("getComputedStyle(document.body).filter")
            gray.append(page.js(INFO_JS))
            if sc == "loaded":
                _shot(page, "2026-11-07_v45_trip_gray_loaded_480.jpg")
        assert len({g["label"] for g in gray}) == 3 and len({g["sig"] for g in gray}) == 3

        # 幅 360 / 480 で横にはみ出さない（全場面）
        for w in (360, 480):
            for sc in SCENES:
                _go(page, examples_server, sc, w)
                ov = page.js("(() => { const t = document.querySelector('.tv'); return [t.scrollWidth, t.clientWidth, document.documentElement.scrollWidth, innerWidth]; })()")
                assert ov[0] <= ov[1] and ov[2] <= ov[3], (sc, w, ov)
                assert page.js("window.__errs.length") == 0, (sc, w, page.js("window.__errs"))

        # 画面写真（360 と 1280）
        for sc in ("loaded", "empty", "unknown", "unknown_class", "no_route"):
            for w, h in ((360, 760), (1280, 720)):
                _go(page, examples_server, sc, w, h)
                shots.append(_shot(page, f"2026-11-07_v45_trip_{sc}_{w}.jpg"))
        assert page.js("window.__errs.length") == 0, page.js("window.__errs")

        # destroy で DOM が残らない
        n = page.js("(() => { TripView.destroy(document.getElementById('host')); return document.querySelectorAll('.tv').length; })()")
        assert n == 0
    assert all(p.exists() for p in shots)


def test_simplify_limit_in_edge(examples_server):
    with edge_page(800, 600, hooks=(CONSOLE_HOOK,), webgl=False) as page:
        _go(page, examples_server, "loaded", 480)
        r = page.js("""(() => { const pts = []; for (let i = 0; i < 2000; i++) pts.push([i / 5 + Math.sin(i / 7) * 3, Math.cos(i / 11) * 5 + (i % 13) * 0.2]);
          return TripView.simplify(pts).length; })()""")
        assert 2 <= r <= 60
