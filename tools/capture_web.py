"""web/ の地図 UI を、入っている Microsoft Edge（ヘッドレス）で撮る。

標準ライブラリ + websockets + Pillow だけを使う（tools/capture_screens.py の Page を再利用する）。
使い方:
    uv run python tools/capture_web.py --scene overview
    uv run python tools/capture_web.py --scene selected --size 1280x720 --out tmp/web_1280.jpg
    uv run python tools/capture_web.py --all
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_screens as cs  # noqa: E402

ROOT = cs.ROOT
OUT_DIR = ROOT / "docs" / "assets"

ERR_HOOK = """
window.__errs = [];
window.addEventListener('error', e => window.__errs.push(String(e.message)));
window.addEventListener('unhandledrejection', e => window.__errs.push('rejection: ' + String(e.reason)));
const _ce = console.error; console.error = function(){ window.__errs.push([...arguments].map(String).join(' ')); _ce.apply(console, arguments); };
"""

# 場面: (URL のハッシュ, 待ち秒, 撮る前の JS)
SCENES = {
    "overview": ("#o=13104&l=km", 7.0, None, "2026-11-03_web_map_overview.jpg"),
    "fare": ("#o=13104&l=fare", 6.0, None, "2026-11-03_web_map_fare.jpg"),
    "selected": ("#o=13104&l=fare&d=27128", 9.0, "__app.S.t = __app.S.route.minutes * 0.42; document.querySelector('#track').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight'}))", "2026-11-03_web_map_selected.jpg"),
    "selected_far": ("#o=09201&l=days&d=01101", 9.0, None, "2026-11-03_web_map_selected_far.jpg"),
    "search": ("#o=13104&l=km", 6.0, "(function(){const q=document.querySelector('#q'); q.focus(); q.value='大阪'; q.dispatchEvent(new Event('input'));})()", "2026-11-03_web_map_search.jpg"),
    "selected_tohoku": ("#o=13104&l=km&d=04101", 9.0, None, "2026-11-03_web_map_selected_tohoku.jpg"),
}


def serve(port: int) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--directory", str(ROOT / "web")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", action="append", choices=list(SCENES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--out", help="単一の場面のときの出力先（省略すると docs/assets）")
    ap.add_argument("--click", help="経度,緯度。その地点を本物のマウスでクリックしてから撮る")
    ap.add_argument("--port", type=int, default=0, help="既に起動している配信サーバーのポート（省略すると自分で起動）")
    args = ap.parse_args()
    names = list(SCENES) if args.all else (args.scene or ["overview"])
    w, h = (int(x) for x in args.size.split("x"))
    cs.W, cs.H = w, h

    own = None
    port = args.port
    if not port:
        port = cs.free_port()
        own = serve(port)
        time.sleep(1.2)
    base = f"http://localhost:{port}/index.html"
    edge = cs.find_edge()
    profile = tempfile.mkdtemp(prefix="edge_profile_")
    dbg = cs.free_port()
    edge_proc = subprocess.Popen(
        [edge, "--headless=new", f"--remote-debugging-port={dbg}", f"--window-size={w},{h}", f"--user-data-dir={profile}",
         "--hide-scrollbars", "--force-device-scale-factor=1", "--no-first-run", "--enable-webgl", "--ignore-gpu-blocklist",
         "--enable-unsafe-swiftshader", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rc = 0
    try:
        targets = []
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{dbg}/json/list", timeout=2) as r:
                    targets = [t for t in json.load(r) if t.get("type") == "page"]
                if targets:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not targets:
            raise SystemExit("Edge のページに接続できませんでした。")
        page = cs.Page(targets[0]["webSocketDebuggerUrl"])
        page.send("Page.addScriptToEvaluateOnNewDocument", source=ERR_HOOK)
        for n in names:
            hash_, wait, pre, fname = SCENES[n]
            page.goto("about:blank")  # ハッシュだけの移動だと再読み込みされないので、いったん空白にする
            page.goto(base + hash_)
            page.wait(lambda: page.js("!!window.__app"), 60, "アプリの起動")
            time.sleep(wait)
            if pre:
                page.js(pre)
                time.sleep(1.0)
            if args.click:
                lon, lat = (float(v) for v in args.click.split(","))
                x, y = page.js(f"(function(){{const p=__app.map().project([{lon},{lat}]);return [p.x,p.y];}})()")
                page.mouse(x, y)
                time.sleep(wait)
            errs = page.js("window.__errs") or []
            if errs:
                print("ブラウザのエラー:", *errs[:5], sep="\n  ")
                rc = 1
            r = page.send("Page.captureScreenshot", format="png", fromSurface=True,
                          clip={"x": 0, "y": 0, "width": w, "height": h, "scale": 1})
            from PIL import Image
            img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
            path = Path(args.out) if args.out and len(names) == 1 else OUT_DIR / fname
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path, "JPEG", quality=88)
            print(f"保存: {path}  {img.size[0]}x{img.size[1]}")
    finally:
        cs.stop_tree(edge_proc)
        if own:
            cs.stop_tree(own)
        shutil.rmtree(profile, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
