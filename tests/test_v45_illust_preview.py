"""説明用イラストの確認ページ（examples/illust_preview/）が、コンソールの誤り・警告なしで開き、全部の絵が読み込まれる（ヘッドレス Edge、1280）。
Edge が無い環境だけ飛ばす。製品の画面には関係しない。
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import edge_page, startup_failed
from test_v36_kit import _make_page_class

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def test_illust_preview_page_loads_without_console_problems(examples_server):
    import capture_screens as cs
    with edge_page(1280, 900, page_cls=_make_page_class(cs)) as page:
        page.send("Log.enable")
        page.goto("about:blank")
        try:
            page.goto(f"http://127.0.0.1:{examples_server}/examples/illust_preview/index.html")
            page.wait(lambda: page.js("!!window.__ready || (window.__errs || []).length > 0"), 60, "確認ページの準備")
        except Exception as e:  # noqa: BLE001
            startup_failed(page, "確認ページが開きません", e, "illust_preview_fail")
        page.pump(2)  # 画像の読み込みが終わるまで
        assert page.js("window.__errs || []") == []
        assert page.js("document.querySelectorAll('[data-tv]').length") == 36  # 2 幅 x 6 組 x 3 運行
        bad = page.js("[...document.images].filter(i => !i.complete || i.naturalWidth === 0).map(i => i.src)")
        assert bad == [], bad
        # TripView の車の絵が読み込めず代わりの図形に切り替わったものが無い
        assert page.js("document.querySelectorAll('[data-image=failed]').length") == 0
        assert page.js("document.querySelectorAll('.card').length") == 8
        assert page.js("document.querySelectorAll('.step').length") == 9
        assert page.problems() == []
