"""SPEC §32 #4〜#7 の画面写真を撮る（公開版の見積依頼の窓口、比べるページの見積を頼む先と路線便の行、荷待ちの書面の表の見出し、登録の値段の注記）。

使い方: uv run python tools/capture_v33_texts.py
保存先: docs/assets/2026-11-05_v33_texts_*.jpg
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import capture_screens as cs  # noqa: E402
import capture_web as cw  # noqa: E402
import capture_waiting_map as cwm  # noqa: E402

OUT = ROOT / "docs" / "assets"
PREFIX = "2026-11-05_v33_texts_"


def serve_public(port: int) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(ROOT / "tools" / "serve_web.py"), "--public", "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def boot(page, url: str) -> None:
    page.goto("about:blank")
    page.goto(url)
    page.wait(lambda: page.js("!!window.__app"), 180, "アプリの起動")
    page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"), 240, "読み込み画面の終了")
    time.sleep(1.0)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    port = cs.free_port()
    server = serve_public(port)
    time.sleep(2.0)
    base = f"http://localhost:{port}/index.html"
    rc = 0
    try:
        # 1 公開版の見積依頼（窓口を並べず案内の文だけ）
        page, proc, profile = cwm.session(port, 1920, 1080)
        try:
            boot(page, base)
            page.js("__app.setView('compare')")
            time.sleep(2.0)
            page.js("(()=>{const A=window.__app;const i=A.S.idx.names.findIndex((n)=>n.includes('大阪市中央区'));A.select(i);return i;})()")
            time.sleep(4.0)
            # 路線便の行（地図の画面の目安額のカード）
            page.wait(lambda: page.js("document.body.innerText.includes('路線便')"), 60, "路線便のカード")
            page.js("(()=>{const e=[...document.querySelectorAll('#cards *')].filter((x)=>x.children.length===0&&x.textContent.includes('区間の表を持つ区間に限ります'))[0];if(e)e.scrollIntoView({block:'center'});return !!e;})()")
            time.sleep(0.8)
            cwm.shot(page, OUT / (PREFIX + "map_rosen.jpg"), 1920, 1080)
            page.js("document.querySelector('#make-request').click()")
            page.wait(lambda: page.js("document.body.innerText.includes('見積を頼む先')"), 60, "見積依頼")
            time.sleep(1.0)
            page.js("(()=>{const e=[...document.querySelectorAll('#w-body h3')].find((x)=>x.textContent.includes('見積を頼む先'));if(e)e.scrollIntoView({block:'center'});})()")
            time.sleep(0.8)
            print("窓口の行:", page.js("document.querySelectorAll('#w-body .tbl tbody tr').length"))
            cwm.shot(page, OUT / (PREFIX + "map_request_contacts.jpg"), 1920, 1080)
            # 登録の欄の値段の注記（運送会社の役）
            cw.select_s6(page, base)
            cw.click(page, '#f-role button[data-role="carrier"]', 0.8)
            cw.set_select(page, "#f-dir-from", "東京都")
            cw.set_select(page, "#f-dir-to", "大阪府")
            page.wait(lambda: page.js("!!document.querySelector('#fr-basis')"), 40, "登録の欄")
            for _ in range(30):
                if "国の個建運賃の式" in (page.js("document.querySelector('#fr-basis').textContent") or ""):
                    break
                time.sleep(1.0)
            print("登録の注記:", page.js("document.querySelector('#fr-basis').textContent"))
            page.js("document.querySelector('#fr-basis').scrollIntoView({block:'center'})")
            time.sleep(0.8)
            cwm.shot(page, OUT / (PREFIX + "register_price.jpg"), 1920, 1080)
        finally:
            cwm.close(proc, profile)
        # 2 比べるページ（見積を頼む先と路線便の行）
        with cw.edge_page(1920, 1080) as pg:
            pg.goto((ROOT / "web" / "compare_lite.html").as_uri() + "?example=1")
            pg.wait(lambda: pg.js("!!document.querySelector('table.res')"), 30, "判定")
            time.sleep(0.8)
            pg.js("document.querySelector('tr[data-opt=rosen]').scrollIntoView({block:'center'})")
            time.sleep(0.5)
            cwm.shot(pg, OUT / (PREFIX + "compare_rosen.jpg"), 1920, 1080)
            pg.js("document.querySelector('#asks').scrollIntoView({block:'center'})")
            time.sleep(0.5)
            cwm.shot(pg, OUT / (PREFIX + "compare_asks.jpg"), 1920, 1080)
        # 3 荷待ちの書面の表の見出し
        with cw.edge_page(1920, 1080) as pg:
            pg.goto((ROOT / "web" / "waiting.html").as_uri())
            pg.wait(lambda: pg.js("!!document.querySelector('#doc-pm-title')"), 30, "書面")
            pg.js("document.querySelector('#doc-pm-title').scrollIntoView({block:'center'})")
            time.sleep(0.5)
            cwm.shot(pg, OUT / (PREFIX + "waiting_letter.jpg"), 1920, 1080)
    except Exception as e:  # noqa: BLE001
        print("失敗:", e)
        rc = 1
    finally:
        cs.stop_tree(server)
    return rc


if __name__ == "__main__":
    sys.exit(main())
