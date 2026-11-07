"""試験で共通の部品。"""

from __future__ import annotations

import base64
import atexit
import contextlib
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from shipcompare.models import Item, Shipment  # noqa: E402


def make_shipment(items=None, route="kanto_kansai", **kw) -> Shipment:
    base = dict(
        destinations=1, sender_type="法人", receiver_type="法人", pickup="集荷", delivery="配達",
        ship_date=date(2026, 11, 10), desired_date=date(2026, 11, 12),
    )
    base.update(kw)
    return Shipment(items=items if items is not None else [Item(1, 30, 30, 30, 10)], route=route, **base)


def one(count=1, l=30, w=30, h=30, kg=10) -> list[Item]:
    return [Item(count, l, w, h, kg)]


def row_of(rows, mode):
    return next(r for r in rows if r.mode == mode)


@pytest.fixture
def today() -> date:
    return date(2026, 11, 3)


# ====================================================================== Edge と配信サーバーの共通部品
# 画面の試験（ヘッドレス Edge）が使う。試験ごとに配信サーバーや Edge を何度も起動すると、全件の実行で負荷が重なって時間切れになるので、
#   - 配信サーバーはセッションで 1 つ（public_server / local_server / examples_server の fixture）
#   - Edge は edge_page() で 1 つずつ、ロックをかけて直列に起動し、終わりに必ずプロファイルごと止める
# 飛ばしてよいのは「Edge が入っていない」「CDN に届かないことを明示的に確かめた」ときだけ。起動・読み込みの時間切れは失敗にして、
# window.__errs と画面写真のパスを出す（fail_with_evidence）。

# 配信サーバーの listen の待ち行列。標準の 5 では、ブラウザが部品を一斉に取りに来るとき Windows が接続を切る（10054）。
# すると routes.js などが読めず、あとの app.js が「Rt is not defined」で止まる（100 並列の取得で 5% ほど切れ、128 では 0 件になることを確かめた）。
# tools/serve_web.py の make_server にも同じ設定が要る。ここでは試験用の起動だけで先に設定する。
SERVER_QUEUE_SIZE = 128

# スクリプト・スタイル・画像の読み込み失敗は window の error に上がってこない（バブリングしない）ので、捕まえて __errs に残す
RESOURCE_HOOK = """
window.__errs = window.__errs || [];
window.addEventListener('error', function (e) {
  var t = e.target;
  if (t && t !== window && (t.tagName === 'SCRIPT' || t.tagName === 'LINK' || t.tagName === 'IMG')) {
    window.__errs.push('resource failed: ' + (t.src || t.href));
  }
}, true);
"""

_EDGE_LOCK = threading.RLock()  # Edge は同時に 1 つだけ（負荷で時間切れになるので並列に起動しない）
_CDN_PROBE = "https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js"
_cdn_state: dict = {}


def edge_path_or_skip() -> str:
    """Edge が入っていなければ飛ばす（飛ばしてよい唯一の場合）。見つからないと SystemExit が上がる。"""
    import capture_screens as cs
    try:
        return cs.find_edge()
    except BaseException:  # noqa: BLE001
        pytest.skip("Microsoft Edge が見つかりません")


def cdn_unreachable_reason() -> str | None:
    """地図の部品の CDN に届かないことを明示的に確かめる。届けば None。結果は 60 秒だけ覚える。"""
    now = time.time()
    if "t" in _cdn_state and now - _cdn_state["t"] < 60:
        return _cdn_state["r"]
    reason = None
    try:
        with urllib.request.urlopen(urllib.request.Request(_CDN_PROBE, method="HEAD"), timeout=10) as r:
            if r.status != 200:
                reason = f"HTTP {r.status}"
    except Exception as e:  # noqa: BLE001
        reason = f"{type(e).__name__}: {e}"
    _cdn_state.update(t=now, r=reason)
    return reason


def fail_with_evidence(page, what: str, exc, tag: str = "edge_fail"):
    """時間切れ・起動失敗を失敗にする。画面写真を一時フォルダに残し、window.__errs とともにメッセージへ入れる。"""
    shot = errs = ""
    try:
        data = page.send("Page.captureScreenshot", format="png").get("data", "")
        out = Path(tempfile.gettempdir()) / f"{tag}_{int(time.time())}.png"
        out.write_bytes(base64.b64decode(data))
        shot = str(out)
    except Exception as e:  # noqa: BLE001
        shot = f"(画面写真を取れません: {e})"
    try:
        errs = json.dumps(page.js("window.__errs || []"), ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        errs = f"(window.__errs を読めません: {e})"
    pytest.fail(f"{what}: {exc} / 画面写真 {shot} / window.__errs {errs}", pytrace=False)


def startup_failed(page, what: str, exc, tag: str = "edge_fail"):
    """画面の起動の時間切れ。CDN に届かないと確かめられたときだけ飛ばし、それ以外は失敗にする。"""
    reason = cdn_unreachable_reason()
    if reason:
        pytest.skip(f"CDN に届きません（{reason}）。{what}: {exc}")
    fail_with_evidence(page, what, exc, tag)


def open_app(page, url: str, *, ready_js: str = "!!window.__app", wait_loading: bool = True, start_timeout: float = 180,
             load_timeout: float = 240, what: str = "地図の画面が起動しません", tag: str = "edge_fail") -> None:
    """地図の画面を開き、起動と読み込み画面の終了を待つ。時間切れは startup_failed（CDN 不達なら飛ばす、そうでなければ失敗）。"""
    page.send("Page.addScriptToEvaluateOnNewDocument", source=RESOURCE_HOOK)
    try:
        page.goto(url)
        page.wait(lambda: page.js(ready_js), start_timeout, "アプリの起動")
        if wait_loading:
            page.wait(lambda: page.js("!document.querySelector('#loading') || document.querySelector('#loading').classList.contains('done')"),
                      load_timeout, "読み込み")
    except Exception as e:  # noqa: BLE001
        startup_failed(page, what, e, tag)


def _connect_blank(cs, dbg: int, page_cls=None):
    targets = []
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{dbg}/json/list", timeout=2) as r:
                targets = cs.blank_targets(json.load(r))
            if targets:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    if not targets:
        pytest.fail("Edge のページ（about:blank）に接続できませんでした（60 秒）")
    return (page_cls or cs.Page)(targets[0]["webSocketDebuggerUrl"])


_EDGE_PROCS: list = []  # このプロセスが起動して、まだ止めていない Edge。終了時に止める


@contextlib.contextmanager
def edge_page(width: int = 1920, height: int = 1080, *, page_cls=None, hooks: tuple[str, ...] = (), webgl: bool = True):
    """ヘッドレス Edge を 1 つ起動して page を渡す。ロックで直列にし、終わりに必ずプロセスとプロファイルを片づける。
    Edge が無ければ飛ばす。起動に失敗したら失敗。hooks は新しい文書ごとに先に実行する JS。"""
    import capture_screens as cs
    edge = edge_path_or_skip()
    with _EDGE_LOCK:
        profile = cs.new_profile("edge_profile_")
        dbg = cs.free_port()
        args = [edge, "--headless=new", f"--remote-debugging-port={dbg}", f"--window-size={width},{height}", f"--user-data-dir={profile}",
                "--hide-scrollbars", "--force-device-scale-factor=1", "--no-first-run", *cs.EDGE_CLEAN_FLAGS]
        if webgl:
            args += ["--enable-webgl", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"]
        proc = subprocess.Popen(args + ["about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cs.bind_to_job(proc)
        _EDGE_PROCS.append(proc)
        try:
            page = _connect_blank(cs, dbg, page_cls)
            for h in hooks:
                page.send("Page.addScriptToEvaluateOnNewDocument", source=h)
            page.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
            yield page
        finally:
            try:
                cs.stop_tree(proc)
            finally:
                try:
                    _EDGE_PROCS.remove(proc)
                except ValueError:
                    pass
                cs.release_profile(profile)


# 試験用サーバーの共通設定（別プロセスで実行する文字列）。ブラウザーが先に接続を閉じたときの
# ConnectionResetError / BrokenPipeError だけは黙って捨てる（標準では長いトレースバックが出る）。ほかの例外は標準どおり表示する。
_QUIET_RESET = (
    "import socketserver, sys as _s\n"
    "_orig = socketserver.BaseServer.handle_error\n"
    "def _quiet(self, request, client_address):\n"
    "    if isinstance(_s.exc_info()[1], ConnectionError): return\n"
    "    _orig(self, request, client_address)\n"
    "socketserver.BaseServer.handle_error = _quiet\n"
)
_SERVE_WEB_CODE = _QUIET_RESET + (
    "import sys; sys.path.insert(0, 'tools'); import serve_web; "
    f"serve_web.ThreadingHTTPServer.request_queue_size = {SERVER_QUEUE_SIZE}; "
    "port = sys.argv[1]; sys.argv = ['serve_web.py', '--port', port] + sys.argv[2:]; sys.exit(serve_web.main())"
)
# cache の無い公開版。経路の cache（data/routes/cache/、手元で使うほど増える）に依らず「経路が取れない」場面を作る
_SERVE_WEB_NOCACHE_CODE = _SERVE_WEB_CODE.replace(
    "port = sys.argv[1];", "import tempfile, pathlib; serve_web.ROUTE_BASE = pathlib.Path(tempfile.mkdtemp()); port = sys.argv[1];")
_STATIC_CODE = _QUIET_RESET + (
    "import sys, http.server, functools; "
    f"http.server.ThreadingHTTPServer.request_queue_size = {SERVER_QUEUE_SIZE}; "
    "h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=sys.argv[2]); "
    "http.server.ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), h).serve_forever()"
)


def wait_server(port: int, path: str = "/index.html", timeout: float = 90) -> None:
    """path が 200 を返すまで待つ。返らなければ失敗（飛ばさない）。"""
    end = time.time() + timeout
    err = None
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
                if r.status == 200:
                    return
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(0.4)
    pytest.fail(f"配信サーバー(:{port})が {timeout:.0f} 秒以内に {path} を返しません: {err}")


_SERVERS: dict[str, tuple] = {}  # 種類 -> (プロセス, ポート)。セッションで 1 つずつ。最後に必ず止める


def server_port(kind: str) -> int:
    """配信サーバーのポート番号。kind は public / local / examples。初めて呼んだときに起動し、セッションの終わりに止める。
    fixture を使えない場所（モジュールの関数から呼ぶ _browser など）でも使えるよう、fixture とは別に用意している。"""
    import capture_screens as cs
    if kind in _SERVERS and _SERVERS[kind][0].poll() is None:
        return _SERVERS[kind][1]
    code, probe, argv = {
        "public": (_SERVE_WEB_CODE, "/index.html", ("--public",)),
        "public_nocache": (_SERVE_WEB_NOCACHE_CODE, "/index.html", ("--public",)),
        "local": (_SERVE_WEB_CODE, "/index.html", ()),
        "examples": (_STATIC_CODE, "/examples/kit/index.html", (str(ROOT),)),
    }[kind]
    port = cs.free_port()
    proc = subprocess.Popen([sys.executable, "-c", code, str(port), *argv], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cs.bind_to_job(proc)
    _SERVERS[kind] = (proc, port)
    try:
        wait_server(port, probe)
    except BaseException:
        cs.stop_tree(proc)
        _SERVERS.pop(kind, None)
        raise
    return port


def _stop_children() -> None:
    """このプロセスが起動した試験用サーバーと Edge だけを止める（名前での一括停止はしない）。何度呼んでもよい。"""
    import capture_screens as cs
    for proc, _ in list(_SERVERS.values()):
        cs.stop_tree(proc)
    _SERVERS.clear()
    for proc in list(_EDGE_PROCS):
        cs.stop_tree(proc)
    _EDGE_PROCS.clear()


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    _stop_children()


# sessionfinish を通らない終わり方（xdist のワーカーの通常終了など）でも片づける。
# 外から強制終了された場合は atexit も動かないので、Job Object（cs.bind_to_job）が OS 側で子を止める。
atexit.register(_stop_children)


@pytest.fixture(scope="session")
def public_server() -> int:
    """公開版の配信サーバー（セッションで 1 つ）。ポート番号を渡す。"""
    return server_port("public")


@pytest.fixture(scope="session")
def local_server() -> int:
    """手元の版の配信サーバー（セッションで 1 つ）。ポート番号を渡す。"""
    return server_port("local")


@pytest.fixture(scope="session")
def examples_server() -> int:
    """リポジトリ全体の静的配信（examples/kit/ の画面用。セッションで 1 つ）。"""
    return server_port("examples")


# ====================================================================== git の呼び出し
# 負荷が重なると git が失敗して返ることがある。以前は skip や返り値の assert で落ちていたので、取り直し、それでも失敗なら理由つきで失敗にする。
# 飛ばしてよいのは git が入っていないときだけ。
def git_run(*args: str, ok_codes: tuple[int, ...] = (0,), retries: int = 3, timeout: float = 300) -> subprocess.CompletedProcess:
    import shutil
    git = shutil.which("git")
    if git is None:
        pytest.skip("git が入っていません")
    last = None
    for n in range(retries):
        try:
            res = subprocess.run([git, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as e:
            last = f"git {' '.join(args)} が {timeout:.0f} 秒で終わりません: {e}"
        else:
            if res.returncode in ok_codes:
                return res
            last = f"git {' '.join(args)} が終了コード {res.returncode}: {res.stderr.strip()[:300]}"
        time.sleep(2.0 * (n + 1))
    pytest.fail(f"{retries} 回試しても git が成功しません: {last}", pytrace=False)


@pytest.fixture(scope="session")
def tracked_files() -> list[str]:
    """git ls-files の結果（セッションで 1 回だけ取る）。"""
    return git_run("ls-files").stdout.splitlines()
