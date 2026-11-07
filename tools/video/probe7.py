"""rec7 用の下調べ。起動済みの serve_kago2.py（既定 8660）に Edge でつないで、JS の式を評価し、スクリーンショットを保存する。
使い方:  python tools/video/probe7.py [--size 1920x1080] [--dsf 1] [--port 8660] URLパス 出力png [式 ...]
式の前に 'sleep:秒'、'shot:パス' が使える。'@ファイル' はその JS ファイルを評価する。
"""
import argparse
import base64
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import record  # noqa: E402
from record import cs  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--dsf", type=float, default=1)
    ap.add_argument("--port", type=int, default=8660)
    ap.add_argument("--wait", type=float, default=6)
    ap.add_argument("path")
    ap.add_argument("out")
    ap.add_argument("exprs", nargs="*")
    a = ap.parse_args()
    w, h = record.parse_size(a.size)
    edge = cs.find_edge()
    proc, profile, ws = record.start_edge(edge, w, h, True)
    page = record.RecPage(ws, w, h, None)
    try:
        page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=a.dsf, mobile=w < 600)
        page.goto(f"http://localhost:{a.port}/{a.path.lstrip('/')}")
        time.sleep(a.wait)

        def shot(p):
            r = page.send("Page.captureScreenshot", format="png")
            Path(p).write_bytes(base64.b64decode(r["data"]))
        shot(a.out)
        for i, e in enumerate(a.exprs):
            if e.startswith('@'):
                e = Path(e[1:]).read_text(encoding='utf-8')
            if e.startswith("sleep:"):
                time.sleep(float(e[6:])); continue
            if e.startswith("shot:"):
                shot(e[5:]); continue
            print(i, json.dumps(page.js(e), ensure_ascii=False)[:3000])
    finally:
        page.close(); cs.stop_tree(proc); cs.stop_edge_profile(profile)
main()
