"""メールのカードが左の入力の欄に重ならないこと（2026-11-07）。

入力の欄は画面に貼り付いて付いてくる。貼り付く範囲が 1 行目を越えると、下に置いたメールのカードと重なって、
入力の欄の右側が隠れ、貼り付けの枠が押せなくなる。1920 幅と 1366 幅で、スクロールの位置を変えながら確かめる。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
WAITING = ROOT / "web" / "waiting.html"


def _edge_or_skip():
    import capture_screens as cs
    try:
        cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


PROBE = """(function(){
  const inp = document.querySelector('#input'), doc = document.querySelector('#wait-doc');
  const a = inp.getBoundingClientRect(), b = doc.getBoundingClientRect();
  const hit = document.elementFromPoint(a.left + a.width / 2, Math.min(Math.max(a.top + a.height / 2, 1), innerHeight - 1));
  const onscreen = a.bottom > 0 && a.top < innerHeight;
  return {hit: onscreen ? !!(hit && hit.closest('#input')) : null,
          overlap: !(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top)};
})()"""


@pytest.mark.parametrize("w,h", [(1920, 1080), (1366, 768)])
def test_mail_card_does_not_overlap_the_input_card(w, h):
    _edge_or_skip()
    import capture_web as cw
    with cw.edge_page(w, h) as page:
        page.goto(WAITING.as_uri() + "?example=1")
        page.wait(lambda: page.js("!!document.querySelector('#wait-doc table.rec')"), 30, "書面")
        time.sleep(0.5)
        ys = [0, 600, 1200, 2000, 3000, 4500]
        for y in ys:
            page.js(f"window.scrollTo(0, {y})")
            time.sleep(0.2)
            info = page.js(PROBE)
            assert info["overlap"] is False, (w, y, info)
            assert info["hit"] in (True, None), (w, y, info)
        page.js("window.scrollTo(0, 0)")
        time.sleep(0.2)
        assert page.js(PROBE)["hit"] is True
