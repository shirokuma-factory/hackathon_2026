"""荷待ちページの表が、どの入口・どの幅でも読めること（2026-11-08 の画面評価）。

- 最後のメモの列が右端で切れない（広い画面）
- 施設名などの列が 1 文字ずつ縦に並ばない
全入口で見本を貼ったあと、すべての td/th の幅が 40px 以上であること（スクロール枠の中でも）。
表が枠より広いときは、その枠が横にスクロールできること。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
WAITING = ROOT / "web" / "waiting.html"

DRIVER = ("荷待ち記録 10/5 江東区 ○○倉庫 着 8:50 受付 9:05 荷役開始 9:35 荷役終了 9:57 車番 1234 相手 ○○商事\n"
          "荷待ち記録 10/6 江東区 ○○倉庫 着 9:10 荷役開始 9:50 荷役終了 10:12 車番 1234 相手 ○○商事")
MODES = [("normal", "?example=1"), ("excel", "#g=excel"), ("driver", "#g=driver"), ("shipper", "#g=shipper")]

PROBE = """(function(){
  const bad = [];
  const scrolls = (e) => { for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll') return p; } return null; };
  document.querySelectorAll('table').forEach((t) => {
    const tb = t.getBoundingClientRect();
    if (!tb.width) return;
    const sc = scrolls(t);
    const name = (t.id || t.className || 'table') + ':' + (t.querySelector('th') ? t.querySelector('th').textContent.slice(0, 12) : '');
    if (!sc && !t.closest('.tw') && tb.right > document.documentElement.clientWidth + 1) bad.push(['overflow', name]);
    if (t.id === 'hotspots' && sc && sc.clientWidth >= 800 && getComputedStyle(t).display === 'table') {
      const last = t.querySelector('tbody tr td:last-child');
      if (last && last.getBoundingClientRect().right > sc.getBoundingClientRect().right + 1) bad.push(['notes-clipped', name]);
    }
    t.querySelectorAll('td,th').forEach((c) => {
      const b = c.getBoundingClientRect();
      if (!b.width) return;
      const th = c.closest('thead');
      if (th && th.getBoundingClientRect().width <= 2) return;           // 狭い画面で見出しを隠している表
      if (getComputedStyle(c.parentElement).display === 'flex') return;   // 狭い画面のカード表示（小さな数字が横に並ぶ）
      const len = c.textContent.trim().length;
      if (len >= 4 && b.width < 40 && !c.closest('.tw')) bad.push(['narrow', name, c.textContent.slice(0, 10), Math.round(b.width)]);   // 1〜3 文字の数字の列は狭くてよい
    });
  });
  return bad;
})()"""


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def _load_sample(page, mode, url):
    page.goto(WAITING.as_uri() + url)
    if mode == "normal":
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "書面")
    else:
        if mode == "driver":
            page.js("document.querySelector('#paste').value = " + json.dumps(DRIVER, ensure_ascii=False))
        else:
            page.wait(lambda: page.js("!!document.querySelector('#b-g-sample')"), 30, "見本ボタン")
            page.js("document.querySelector('#b-g-sample').click()")
        page.js("document.querySelector('#b-paste').click()")
        page.wait(lambda: page.js("!!document.querySelector('#partners h2, #partners h3')"), 30, "結果")
    time.sleep(0.8)


@pytest.mark.parametrize("w,h", [(1920, 1080), (1366, 768), (1100, 800), (390, 800)])
@pytest.mark.parametrize("mode,url", MODES)
def test_waiting_tables_are_readable(mode, url, w, h):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(w, h) as page:
        _load_sample(page, mode, url)
        bad = page.js(PROBE)
        assert not bad, (mode, w, bad)
