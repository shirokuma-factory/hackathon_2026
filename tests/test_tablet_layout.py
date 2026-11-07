"""タブレット幅（768 x 1024）で、自社の運行と空きを探すの左右の欄が重ならず、押したい所が上に出ていること。

- 自社の運行（#v=own）: 「1 km あたりの費用」の入力欄の中心が最前面で、左の欄（#own-view）と右の欄（#own-right）が重ならない。
- 空きを探す（#v=find）: 右の欄の見出し（#f-rtitle）の中心が、上の切り替え（#views）に隠れていない。
ヘッドレス Edge は 1 つだけ使う。Edge が無いときだけ飛ばす。
"""

from __future__ import annotations

import json

import pytest
from conftest import edge_page, edge_path_or_skip, open_app, server_port

READY = "!!window.__app && !document.querySelector('#loading')"

TOP_AT_CENTER = r"""(function (sel) {
  const el = document.querySelector(sel);
  if (!el) return { error: 'missing ' + sel };
  const r = el.getBoundingClientRect();
  const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return { ok: !!top && (top === el || el.contains(top)), top: top ? (top.id || top.className || top.tagName) : null, rect: [r.left, r.top, r.right, r.bottom] };
})(%s)"""

RECT = "(function(s){const r=document.querySelector(s).getBoundingClientRect();return [r.left,r.top,r.right,r.bottom];})(%s)"


def _open(page, port, hash_):
    open_app(page, f"http://127.0.0.1:{port}/index.html{hash_}", ready_js=READY, wait_loading=False, start_timeout=180, tag="tablet_layout_fail")


@pytest.mark.parametrize("w,h", [(768, 1024), (1024, 768)])
def test_own_panels_do_not_overlap_and_cost_input_is_clickable(w, h):
    edge_path_or_skip()
    port = server_port("local")
    with edge_page(w, h) as page:
        _open(page, port, "#v=own")
        page.wait(lambda: page.js("!!document.querySelector('#own-perkm')"), 60, "1 km あたりの費用の欄")
        hit = page.js(TOP_AT_CENTER % json.dumps("#own-perkm"))
        assert hit.get("ok"), f"1 km あたりの費用の欄が別の要素に隠れている: {hit}"
        left = page.js(RECT % json.dumps("#own-view"))
        right = page.js(RECT % json.dumps("#own-right"))
        overlap = left[0] < right[2] and right[0] < left[2] and left[1] < right[3] and right[1] < left[3]
        assert not overlap, f"左の欄 {left} と右の欄 {right} が重なっている"
        heading = page.js(TOP_AT_CENTER % json.dumps("#own-view .p-title"))
        assert heading.get("ok"), f"左の欄の見出しが隠れている: {heading}"


@pytest.mark.parametrize("w,h", [(768, 1024), (1024, 768)])
def test_find_right_heading_not_covered_by_top_bar(w, h):
    edge_path_or_skip()
    port = server_port("local")
    with edge_page(w, h) as page:
        _open(page, port, "#v=find")
        page.wait(lambda: page.js("!!document.querySelector('#f-rtitle') && document.querySelector('#f-rtitle').getBoundingClientRect().width > 0"), 60, "右の欄の見出し")
        hit = page.js(TOP_AT_CENTER % json.dumps("#f-rtitle"))
        assert hit.get("ok"), f"右の欄の見出しが別の要素（上の切り替えなど）に隠れている: {hit}"
        left = page.js(RECT % json.dumps("#find-view"))
        right = page.js(RECT % json.dumps("#find-right"))
        assert not (left[0] < right[2] and right[0] < left[2] and left[1] < right[3] and right[1] < left[3]), f"左の欄 {left} と右の欄 {right} が重なっている"
