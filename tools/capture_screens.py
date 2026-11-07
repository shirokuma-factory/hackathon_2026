"""Edge をリモートデバッグで操作する道具（起動・終了、子プロセスのジョブ管理、Page）。

標準ライブラリ + websockets + Pillow と、入っている Microsoft Edge だけを使う。
Edge の場所は環境変数 EDGE_PATH、なければ一般的なインストール先から探す。
試験（tests/conftest.py など）と tools/ の撮影スクリプトが import して使う。
"""

from __future__ import annotations

import atexit
import base64
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "assets"
W, H = 1920, 1080


class SceneError(Exception):
    pass


# ------------------------------------------------------------------ 起動と終了

# Edge の起動に足す引数。この PC は拡張機能（MetaMask など）を新しいプロファイルに同期するので、拡張機能のページが先頭の対象に混ざる。
# 拡張機能と同期を切り、接続先は about:blank のページだけにする（blank_targets）
EDGE_CLEAN_FLAGS = ["--disable-extensions", "--disable-sync"]


def blank_targets(targets):
    """/json/list の一覧から、about:blank のページだけを返す（拡張機能のページを選ばない）。"""
    return [t for t in targets if t.get("type") == "page" and str(t.get("url", "")).startswith("about:blank")]


def find_edge() -> str:
    env = os.environ.get("EDGE_PATH")
    if env and Path(env).exists():
        return env
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            c = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if c.exists():
                return str(c)
    found = shutil.which("msedge")
    if found:
        return found
    raise SystemExit("Edge が見つかりません。環境変数 EDGE_PATH に msedge.exe のパスを指定してください（Linux では google-chrome / chromium のパスでも動きます）。")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stop_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    try:
        proc.wait(10)
    except Exception:
        proc.kill()

_JOB = None  # このプロセスで 1 つだけ作るジョブ。ハンドルは閉じない（プロセスが死ぬと OS が閉じ、中の子も止まる）


def _job_handle():
    """KILL_ON_JOB_CLOSE のジョブオブジェクトを必要になったときに作る。作れなければ 0。"""
    global _JOB
    if _JOB is not None:
        return _JOB
    _JOB = 0
    if os.name != "nt":
        return _JOB
    try:
        import ctypes
        from ctypes import wintypes

        class _BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return _JOB
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        # 9 = JobObjectExtendedLimitInformation
        if k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            _JOB = job
    except Exception:
        _JOB = 0
    return _JOB


def bind_to_job(proc):
    """起動した子（と、その子がこれから起動する孫）を、このプロセスの終了と一緒に止まるようにする。
    Windows 以外、または失敗したときは何もしない。proc をそのまま返す。"""
    if os.name != "nt":
        return proc
    try:
        import ctypes
        from ctypes import wintypes
        job = _job_handle()
        if job:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.AssignProcessToJobObject(job, int(proc._handle))
    except Exception:
        pass
    return proc


def stop_edge_profile(profile: str) -> None:
    """指定したプロファイルの Edge を全部止める。

    Edge は起動用のプロセスがすぐ終わり、本体は別の親を持たないプロセスとして残るので、
    stop_tree（起動したプロセスの子を止める）だけでは本体が残る。プロファイルのパスで探して止める。
    """
    if os.name != "nt":
        return
    key = os.path.basename(os.path.normpath(profile)).replace("'", "")
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{key}*' }} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        pass


# このプロセスが作ったプロファイル。例外・Ctrl+C・タイムアウトで finally を通れなかった分を、終了時に片づける。
# プロファイルのパスで探すので、ここに登録していない Edge（ほかのセッションや利用者の Edge）は止めない。
_PROFILES: list[str] = []


def new_profile(prefix: str = "edge_profile_") -> str:
    """Edge 用の使い捨てプロファイルのフォルダを作り、終了時の片づけに登録する。"""
    profile = tempfile.mkdtemp(prefix=prefix)
    _PROFILES.append(profile)
    return profile


def release_profile(profile: str) -> None:
    """プロファイルの Edge を止めてフォルダを消す。何度呼んでもよい。"""
    try:
        stop_edge_profile(profile)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        if profile in _PROFILES:
            _PROFILES.remove(profile)


def _cleanup_profiles() -> None:
    for profile in list(_PROFILES):
        try:
            release_profile(profile)
        except BaseException:  # noqa: BLE001  終了処理では例外を外に出さない
            pass


atexit.register(_cleanup_profiles)


# ------------------------------------------------------------------ CDP

FIND_JS = r"""
(function(spec){
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  let list = [];
  if (spec.kind === 'aria') {
    list = [...document.querySelectorAll('[aria-label]')].filter(e => spec.exact
      ? e.getAttribute('aria-label') === spec.text : e.getAttribute('aria-label').includes(spec.text));
    if (spec.tag) list = list.filter(e => e.matches(spec.tag));
  } else if (spec.kind === 'tab') {
    list = [...document.querySelectorAll('[role=tab]')].filter(e => norm(e.innerText).includes(spec.text));
  } else if (spec.kind === 'button') {
    list = [...document.querySelectorAll('button')].filter(e => norm(e.innerText).includes(spec.text));
  } else if (spec.kind === 'text') {
    list = [...document.querySelectorAll('body *')].filter(e => e.children.length === 0 && norm(e.textContent).includes(spec.text));
  } else if (spec.kind === 'css') {
    list = [...document.querySelectorAll(spec.text)];
  }
  list = list.filter(vis);
  return list[spec.index || 0] || null;
})
"""


class Page:
    def __init__(self, ws_url: str):
        self._cm = connect(ws_url, max_size=None, open_timeout=20)
        self.ws = self._cm.__enter__() if hasattr(self._cm, "__enter__") else self._cm
        self._id = 0
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("DOM.enable")
        self.send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)

    def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=60))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError("JS error: " + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400])
        return r["result"].get("value")

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)
        time.sleep(0.5)
        self.wait(lambda: self.js("document.readyState") == "complete", 30, "ページ読み込み")

    def wait(self, cond, timeout: float, what: str):
        end = time.time() + timeout
        while time.time() < end:
            try:
                v = cond()
            except Exception:
                v = False
            if v:
                return v
            time.sleep(0.2)
        raise SceneError(f"待ちが時間切れ: {what}（{timeout:.0f}秒）")

    # ---- マウス・キーボード（本物の入力イベント）
    def mouse(self, x: float, y: float) -> None:
        self.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        self.send("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        self.send("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)

    def key(self, name: str, ctrl: bool = False) -> None:
        codes = {"Enter": (13, "\r"), "Tab": (9, ""), "a": (65, "a")}
        vk, text = codes[name]
        base = dict(key=name, code=("Key" + name.upper() if len(name) == 1 else name),
                    windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk, modifiers=2 if ctrl else 0)
        down = dict(base, type="keyDown")
        if text and not ctrl:
            down["text"] = text
        self.send("Input.dispatchKeyEvent", **down)
        self.send("Input.dispatchKeyEvent", **dict(base, type="keyUp"))

    def insert(self, text: str) -> None:
        self.send("Input.insertText", text=text)

    # ---- 要素の探索
    def _find(self, spec: dict) -> str:
        return f"({FIND_JS})({json.dumps(spec, ensure_ascii=False)})"

    def exists(self, spec: dict) -> bool:
        return bool(self.js(f"!!{self._find(spec)}"))

    def center(self, spec: dict, timeout: float = 15):
        self.wait(lambda: self.exists(spec), timeout, "要素 " + json.dumps(spec, ensure_ascii=False))
        self.js(f"{self._find(spec)}.scrollIntoView({{block:'center',inline:'center'}})")
        time.sleep(0.3)
        return self.js(f"(function(){{const r={self._find(spec)}.getBoundingClientRect();"
                       "return [r.x + r.width/2, r.y + r.height/2];})()")

    def click(self, kind: str, text: str) -> None:
        x, y = self.center(dict(kind=kind, text=text))
        self.mouse(x, y)
        time.sleep(0.4)

    def body_text(self) -> str:
        return self.js("document.body.innerText") or ""


    def wait_text(self, text: str, timeout: float = 20) -> None:
        self.wait(lambda: text in self.body_text(), timeout, f"文字「{text}」の表示")

    def need(self, *texts: str, timeout: float = 15) -> None:
        """画面の文字に全部含まれるまで待つ。時間内に揃わなければ、足りない文字を挙げて失敗する。"""
        end = time.time() + timeout
        while True:
            body = self.body_text()
            miss = [t for t in texts if t not in body]
            if not miss:
                return
            if time.time() > end:
                raise SceneError("画面に必要な文字がありません: " + " / ".join(miss))
            time.sleep(0.4)


    def shot(self, name: str) -> Path:
        from PIL import Image
        time.sleep(1.0)  # グラフの描画を待つ
        r = self.send("Page.captureScreenshot", format="png", fromSurface=True,
                      clip={"x": 0, "y": 0, "width": W, "height": H, "scale": 1})
        img = Image.open(io.BytesIO(base64.b64decode(r["data"]))).convert("RGB")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / name
        img.save(path, "JPEG", quality=85)
        print(f"保存: {path.relative_to(ROOT)}  {img.size[0]}x{img.size[1]}")
        return path
