"""SPEC §46 自社の運行の画面（実際の描画で確かめる）。画面の写真は docs/assets/2026-11-07_v46_own_*.jpg（環境変数 V46_SHOT_DIR があればそこ）。

- 手順 2（空いた帰りを見る）: 1920・360 で横のはみ出しが無い。「ほかの曜日」の名札が 1 行。戻る操作は上の案内の帯の 1 つだけ
- 運行の図（TripView）: 暗い画面の上で暗い地面。根拠の表の見出しが 1 字ずつ折れない
- 1 枚版（own_lite.html）の手順 2（360）: 地図の画面と同じ暗い配色・丸み。地図の凡例を出さない。横のはみ出しが無い
ヘッドレス Edge は 1 つだけ使う。
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

from conftest import edge_page
from test_v45_own_guided import app, click, size, to_guided  # noqa: F401  (app は地図の画面を起こす fixture)

ROOT = Path(__file__).resolve().parents[1]
SHOTS = Path(os.environ.get("V46_SHOT_DIR") or ROOT / "docs" / "assets")
LITE = ROOT / "web" / "own_lite.html"


def shot(page, name: str, quality: int = 55) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    r = page.send("Page.captureScreenshot", format="jpeg", quality=quality)
    (SHOTS / name).write_bytes(base64.b64decode(r["data"]))


NO_OVERFLOW = "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
LABEL_ONE_LINE = """(function () {
  const l = [...document.querySelectorAll('#neg-result .neg-lab')].find((e) => e.textContent.trim() === 'ほかの曜日');
  if (!l) return null;
  const r = l.getBoundingClientRect(), fs = parseFloat(getComputedStyle(l).fontSize);
  return r.height < fs * 1.9;
})()"""


def test_guided_step2_at_1920_and_360(app):  # noqa: F811
    page = app
    for w, h in ((1920, 1080), (360, 800)):
        size(page, w, h)
        to_guided(page)
        click(page, "#neg-entry-sample")
        page.wait(lambda: page.js("Negotiate.step()") == 2, 10, "手順 2")
        time.sleep(1.2)
        assert page.js(NO_OVERFLOW), (w, "横にはみ出している")
        assert page.js(LABEL_ONE_LINE) is True, (w, "「ほかの曜日」が折れている")
        # 戻る操作は上の案内の帯の 1 つだけ（手順の欄・見出しにもう 1 つ置かない）
        n = page.js("[...document.querySelectorAll('button,a')].filter((e) => e.textContent.trim() === '最初の画面に戻る' && e.checkVisibility({checkVisibilityCSS: true})).length")
        assert n == 1, (w, n)
        shot(page, f"2026-11-07_v46_own_guided2_{w}.jpg")


def test_trip_view_is_dark_on_the_map_screen_and_the_evidence_table_does_not_wrap_per_character(app):  # noqa: F811
    page = app
    size(page, 1920, 1080)
    to_guided(page)
    page.js("Own.setGuided(false, '')")
    page.js("Negotiate.state.entry = 'sample'; Negotiate.state.view = 'result'; Negotiate.render()")
    time.sleep(0.6)
    page.js("document.querySelector('#neg-ev > summary').click()")
    time.sleep(0.3)
    page.js("document.querySelector('#neg-ev tr.neg-trow').click()")
    time.sleep(0.6)
    assert page.js("document.querySelectorAll('#neg-ev [data-tv]').length") == 1
    lum = page.js("""(function () {
      const g = document.querySelector('#neg-ev .tv'); const c = getComputedStyle(g).backgroundColor.match(/\\d+/g).map(Number);
      return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255;
    })()""")
    assert lum < 0.15, lum  # 暗い画面（#0d141f）の上で、明るい札にならない
    # 根拠の表の見出しは 1 行（「区／間」「曜／日」のように 1 字ずつ折れない）
    tall = page.js("""[...document.querySelectorAll('#neg-ev .neg-tbl th')].filter((e) => e.getBoundingClientRect().height > parseFloat(getComputedStyle(e).fontSize) * 2.6).map((e) => e.textContent)""")
    assert tall == [], tall
    page.js("document.querySelector('#neg-ev .tv').scrollIntoView({block: 'center'})")
    time.sleep(0.4)
    shot(page, "2026-11-07_v46_own_tripview_1920.jpg")
    size(page, 1920, 1080)


def test_lite_step2_at_360_is_dark_without_map_legend():
    with edge_page(360, 800, webgl=False) as page:
        page.send("Page.navigate", url=LITE.as_uri())
        page.wait(lambda: page.js("!!window.OwnLite"), 60, "1 枚版の起動")
        page.js("document.querySelector('#neg-entry-sample').click()")
        page.wait(lambda: page.js("document.body.dataset.step") == "2", 30, "手順 2")
        time.sleep(0.6)
        assert page.js("getComputedStyle(document.body).backgroundColor") == "rgb(7, 11, 18)"
        assert page.js("getComputedStyle(document.querySelector('.btn-primary, button.primary')).borderTopLeftRadius") == "10px"  # 地図の画面のボタンと同じ丸み
        assert page.js("(function () { const e = document.querySelector('#neg-lg'); return !e || !e.checkVisibility({checkVisibilityCSS: true}); })()"), "地図の無い版に地図の凡例を出さない"
        assert page.js(NO_OVERFLOW)
        assert page.js(LABEL_ONE_LINE) is True
        shot(page, "2026-11-07_v46_own_lite_step2_360.jpg")
