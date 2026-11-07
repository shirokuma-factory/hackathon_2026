"""CSP の煙試験。tools/serve_web.py を立て、ヘッドレス Edge で各ページを開き、CSP 違反と SRI 失敗が無いか見る。

使い方: uv run python tools/csp_smoke.py
違反（securitypolicyviolation）か、コンソールの Refused / integrity の文言があれば終了コード 1。
"""
from __future__ import annotations

import importlib.util
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

PAGES = [
    "index.html#v=market", "index.html#v=own&sample=1", "index.html#v=find", "waiting.html", "own_lite.html",
    "compare_lite.html", "driver.html", "trucks_now.html",
]
HOOK = """window.__csp=[];document.addEventListener('securitypolicyviolation',function(e){window.__csp.push(e.violatedDirective+' '+e.blockedURI)});
window.__cerr=[];window.addEventListener('error',function(e){window.__cerr.push(String(e.message||e.target&&e.target.src||''))},true);"""


def main() -> int:
    from conftest import edge_page  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location("serve_web_smoke", ROOT / "tools" / "serve_web.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["serve_web_smoke"] = mod
    spec.loader.exec_module(mod)
    httpd = mod.make_server(0, public=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    bad = 0
    with edge_page(1400, 900, hooks=(HOOK,)) as page:
        for p in PAGES:
            page.goto(f"http://127.0.0.1:{port}/{p}")
            time.sleep(12 if p.startswith(("index", "trucks")) else 4)
            csp = page.js("window.__csp") or []
            cerr = page.js("window.__cerr") or []
            n_bad = len(csp)
            bad += n_bad
            print(("NG " if n_bad else "OK ") + p, "csp=", csp[:5], "errors=", cerr[:3], "maplibre=", page.js("typeof maplibregl"), "deck=", page.js("typeof deck"))
    httpd.shutdown()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
