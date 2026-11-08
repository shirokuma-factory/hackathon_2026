"""2026-11-08 の最終評価（own）の直し。

- 地図の自社の運行: 「手順に沿って進める」はボタン。式・用語・費用の入力と売上の内訳は、閉じた「詳しく」の中（閉じると入力欄が見えない）
- 左の CSV の説明は閉じた「読める CSV の形」の中。置き場の枠に「Excel は CSV で保存」が 1 回だけ出る
- いまの大型車: 凡例の長い説明は広い画面でも閉じた「詳しく」の中。順位の説明は 1 行
- 空車の便が 0 のとき、案内文は作れない理由が短く、own_lite に無い「確認」の欄へ案内しない
ヘッドレス Edge は 1 つだけ使う。Edge が無いときだけ飛ばす。
"""

from __future__ import annotations

import time
from pathlib import Path

from conftest import edge_page, edge_path_or_skip, open_app, server_port

ROOT = Path(__file__).resolve().parents[1]


def test_map_own_view_folds_long_text_and_guided_is_a_button():
    edge_path_or_skip()
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        open_app(page, f"http://127.0.0.1:{port}/index.html#v=own", ready_js="!!window.__app && !document.querySelector('#loading')", wait_loading=False)
        page.wait(lambda: page.js("!!document.querySelector('#own-fold')"), 60, "右の欄")
        assert page.js("document.querySelector('.o-toguide').tagName") == "BUTTON"
        assert "btn-primary" in page.js("document.querySelector('.o-toguide').className")
        page.js("document.querySelector('#own-fold').open = false; document.querySelector('#own-rev-d').open = false")
        assert page.js("!!document.querySelector('#own-perkm').closest('details:not([open])')")  # 費用の入力欄は閉じた「詳しく」の中
        assert page.js("!!document.querySelector('#own-rate').closest('details:not([open])')")
        assert page.js("document.querySelector('#own-fmt details').open") is False
        drop = page.js("document.querySelector('#own-drop').innerText")
        assert "Excel は CSV で保存" in drop and drop.count("文字コード") == 1


def test_trucks_now_legend_is_folded_even_on_wide_screens():
    edge_path_or_skip()
    port = server_port("local")
    with edge_page(1920, 1080) as page:
        page.goto(f"http://127.0.0.1:{port}/trucks_now.html")
        page.wait(lambda: page.js("!!document.querySelector('#legend .collapsible')"), 60, "凡例")
        time.sleep(1)
        assert page.js("getComputedStyle(document.querySelector('#legend .collapsible')).display") == "none"
        assert page.js("getComputedStyle(document.querySelector('#lg-toggle')).display") != "none"
        assert page.js("document.querySelector('#credit-svc').offsetParent !== null")  # 利用規約 5 条 1 項の文は常に見える
        assert len(page.js("document.querySelector('#rank-hint').textContent")) <= 40
