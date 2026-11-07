"""鹿児島版の配信にヘッドレス Edge でつなぎ、ページを開いて JS の結果を出す（録画の前の確認用）。
使い方: python tools/video/debug_kago.py "/index.html#v=find" "js の式" [待ち秒]"""
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import record_kago  # noqa: F401,E402
import record  # noqa: E402
cs = record.cs

url_path, js = sys.argv[1], sys.argv[2]
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 8
cs.W, cs.H = 1920, 1080
port = cs.free_port()
srv = subprocess.Popen([sys.executable, str(record.ROOT / "tools" / "serve_web.py"), "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
edge_proc = profile = page = None
try:
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read(1)
            break
        except Exception:
            time.sleep(0.4)
    edge_proc, profile, ws = record.start_edge(cs.find_edge(), 1920, 1080, False)
    page = record.RecPage(ws, 1920, 1080, Path(tempfile.mkdtemp(prefix="dbg_frames_")))
    page.goto("about:blank")
    page.goto(f"http://localhost:{port}{url_path}")
    page.wait(lambda: page.js("!!window.__app && (function(){const l=document.querySelector('#loading'); return !l || l.classList.contains('done');})()"), 300, "ready")
    time.sleep(wait)
    r = page.js(js)
    print(json.dumps(r, ensure_ascii=False, indent=1) if not isinstance(r, str) else r)
finally:
    if page is not None:
        page.close()
    if edge_proc is not None:
        cs.stop_tree(edge_proc)
    if profile:
        cs.stop_edge_profile(profile)
        shutil.rmtree(profile, ignore_errors=True)
    cs.stop_tree(srv)
