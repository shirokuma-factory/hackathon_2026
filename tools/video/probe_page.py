"""録画の台本を作るための下調べ。ページを開いて JS の式を評価し、スクリーンショットを保存する（DSF 1、ソフトウェア描画）。
使い方:  python tools/video/probe_page.py URLパス 出力png [JS式 ...]
JS 式は順に評価して結果を表示する。式の前に 'sleep:秒' で待つ。 'shot:名前' で追加のスクリーンショット。
"""
import base64
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import record_hd as rh  # noqa: E402
import record  # noqa: E402
from record import cs  # noqa: E402
import subprocess

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    path, outp, *exprs = sys.argv[1:]
    port = cs.free_port()
    srv = subprocess.Popen([sys.executable, str(record.ROOT / "tools" / "serve_web.py"), "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    edge = cs.find_edge()
    proc, profile, ws = record.start_edge(edge, 1920, 1080, True)
    page = record.RecPage(ws, 1920, 1080, None)
    try:
        page.goto(f"http://localhost:{port}/{path.lstrip("/")}")
        time.sleep(6)
        def shot(p):
            r = page.send("Page.captureScreenshot", format="png")
            Path(p).write_bytes(base64.b64decode(r["data"]))
        shot(outp)
        for i, e in enumerate(exprs):
            if e.startswith("sleep:"):
                time.sleep(float(e[6:])); continue
            if e.startswith("shot:"):
                shot(e[5:]); continue
            print(i, json.dumps(page.js(e), ensure_ascii=False)[:3000])
    finally:
        page.close(); cs.stop_tree(proc); cs.stop_edge_profile(profile); cs.stop_tree(srv)
main()
