"""SPEC §47 静的ファイルの読み込み失敗: スタイルシートが 1 回失敗しても、index.html が 1 回だけ読み直して画面に反映する。

CDP の Fetch で compare.css の最初の 1 回だけ失敗させ、読み直した link が付き、スタイルが実際に効いていることを確かめる。
読み直しを外すとこの試験は落ちる（最初の失敗のまま compare.css が無い）。ヘッドレス Edge は同時に 1 つだけ。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import edge_page, server_port

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def _make_page_cls():
    import capture_screens as cs

    class InterceptPage(cs.Page):
        """Fetch.requestPaused を send の待ちの中で処理する。compare.css は最初の 1 回だけ失敗させ、あとは通す。"""
        failed: list[str] = []
        passed: list[str] = []

        def send(self, method: str, **params):
            self._id += 1
            mid = self._id
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            while True:
                msg = json.loads(self.ws.recv(timeout=60))
                if msg.get("method") == "Fetch.requestPaused":
                    p = msg["params"]
                    self._id += 1
                    if "compare.css" in p["request"]["url"] and not self.failed:
                        self.failed.append(p["request"]["url"])
                        self.ws.send(json.dumps({"id": self._id, "method": "Fetch.failRequest",
                                                 "params": {"requestId": p["requestId"], "errorReason": "ConnectionReset"}}))
                    else:
                        self.passed.append(p["request"]["url"])
                        self.ws.send(json.dumps({"id": self._id, "method": "Fetch.continueRequest", "params": {"requestId": p["requestId"]}}))
                    continue
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})

    return InterceptPage


def test_stylesheet_failing_once_is_retried_and_applied():
    port = server_port("public")
    cls = _make_page_cls()
    cls.failed, cls.passed = [], []
    with edge_page(1280, 720, page_cls=cls) as page:
        page.send("Fetch.enable", patterns=[{"urlPattern": "*compare.css*", "requestStage": "Request"}])
        page.send("Page.navigate", url=f"http://127.0.0.1:{port}/index.html")
        page.wait(lambda: page.js("document.readyState") == "complete", 60, "ページ読み込み")
        page.wait(lambda: page.js("!!document.querySelector('link[data-retry]')"), 20, "読み直しの link")
        assert len(cls.failed) == 1, "最初の 1 回は失敗させたはず"
        assert any("compare.css?retry=" in u for u in cls.passed), cls.passed
        info = json.loads(page.js("""JSON.stringify({
          retried: [...document.querySelectorAll('link[data-retry]')].map(l => [l.getAttribute('data-retry'), !!l.sheet]),
          loaded: [...document.styleSheets].some(s => (s.href || '').includes('compare.css?retry=') && s.cssRules.length > 0) })"""))
        assert info["retried"] and info["retried"][0][0] == "compare.css" and len(info["retried"]) == 1, info
        page.wait(lambda: page.js("[...document.styleSheets].some(s => (s.href || '').includes('compare.css?retry=') && s.cssRules.length > 0)"),
                  20, "読み直した compare.css が効く")
