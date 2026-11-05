"""Web アプリの画面を、実際の操作を流しながら mp4 に録画する（デモ動画の素材用）。

標準ライブラリ + websockets + Pillow と、入っている Microsoft Edge（ヘッドレス）、ffmpeg を使う。
Edge への接続は tools/capture_screens.py の Page を土台にし、配信は tools/serve_web.py を自分で起動する。
録画は CDP の Page.startScreencast（JPEG のコマと時刻）で行い、各コマに Page.screencastFrameAck を返す。
コマは変化があったときだけ届くので、時刻の差から各コマの表示時間を出し、ffmpeg の concat demuxer で
30 fps の一定フレームレートの mp4 にする。

ffmpeg の場所は環境変数 FFMPEG、なければ PATH 上の ffmpeg。どちらもなければ終了する。
Edge の場所は環境変数 EDGE_PATH（capture_screens.py と同じ）。

使い方:
    uv run python tools/video/record.py --list
    uv run python tools/video/record.py --take smoke
    uv run python tools/video/record.py --take R2 --out outputs/video/R2.mp4 --public

台本は tools/video/takes.json（--takes で別ファイルも指定できる）。形式:
    [{"id": "R2", "url_path": "/index.html#v=find", "size": "1920x1080", "network_log": true,
      "ready": "#app", "steps": [{"wait": 2}, {"click": "#b"}, {"type": ["#q", "大阪"]},
      {"paste": ["#q", "text"]}, {"eval": "js"}, {"scroll": ["#list", 400]}, {"wait_for": "#x"}]}]
"ready" は録画を始める前に待つ CSS セレクタ、"ready_js" は真になるまで待つ JS の式（どちらも省略可）。
読み込み画面を映さないためのもので、地図のページは capture_web.py と同じく
  "ready_js": "!!window.__app && (function(){const l=document.querySelector('#loading'); return !l || l.classList.contains('done');})()"
を使う（先読みが終わって読み込み画面が消えるまで待つ。ソフトウェア描画では数十秒かかることがある）。
"cursor": true を付けたテイク（または --cursor）では、画面にマウスポインタ（矢印）とクリックの輪を重ねて映す。
click / type / paste の前にポインタが対象へ動き、{"move": セレクタ} は動かすだけ、
{"highlight": [セレクタ, 秒]} は対象の周りに枠を出す（ポインタが無効でも枠は出る）。
network_log が true なら、録画中の Network.requestWillBeSent の URL を <out の名前>.requests.json に書く。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import capture_screens as cs  # noqa: E402

DEFAULT_TAKES = HERE / "takes.json"
FPS = 30
ACTIONS = ("wait", "click", "type", "paste", "eval", "scroll", "wait_for", "highlight", "move")
MOVE_SEC = 0.5  # ポインタが対象へ動く時間
RING_SEC = 0.4  # クリックの輪が広がる時間


# ------------------------------------------------------------------ 台本の検査（ブラウザなしで試験できる部分）

class TakesError(Exception):
    pass


def parse_size(value, default=(1920, 1080)) -> tuple[int, int]:
    """'1920x1080' か [1920, 1080] を (幅, 高さ) にする。"""
    if value is None:
        return default
    if isinstance(value, str):
        parts = value.lower().split("x")
        if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
            w, h = int(parts[0]), int(parts[1])
            if w > 0 and h > 0:
                return w, h
    elif isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(v, int) and v > 0 for v in value):
        return int(value[0]), int(value[1])
    raise TakesError(f"size は 'WxH' の形で指定してください: {value!r}")


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_step(step, where: str) -> list[str]:
    if not isinstance(step, dict) or len(step) != 1:
        return [f"{where}: 手順は {{動作: 値}} の 1 組だけの辞書にしてください"]
    (act, val), = step.items()
    if act not in ACTIONS:
        return [f"{where}: 知らない動作 {act!r}（使えるのは {', '.join(ACTIONS)}）"]
    if act == "wait":
        return [] if _is_num(val) and val >= 0 else [f"{where}: wait は 0 以上の秒数"]
    if act == "highlight":
        ok = (isinstance(val, list) and len(val) == 2 and isinstance(val[0], str) and val[0].strip()
              and _is_num(val[1]) and val[1] > 0)
        return [] if ok else [f"{where}: highlight は [セレクタ, 0 より大きい秒数]"]
    if act in ("click", "wait_for", "eval", "move"):
        return [] if isinstance(val, str) and val.strip() else [f"{where}: {act} は空でない文字列"]
    if act in ("type", "paste"):
        ok = isinstance(val, list) and len(val) == 2 and all(isinstance(v, str) for v in val) and val[0].strip()
        return [] if ok else [f"{where}: {act} は [セレクタ, 文字列]"]
    if act == "scroll":
        ok = isinstance(val, list) and len(val) == 2 and (val[0] is None or isinstance(val[0], str)) and _is_num(val[1])
        return [] if ok else [f"{where}: scroll は [セレクタまたは null, dy]"]
    return []


def validate_takes(data) -> list[str]:
    """台本の誤りを全部集めて返す（空なら正しい）。"""
    if not isinstance(data, list) or not data:
        return ["台本は 1 つ以上のテイクのリストにしてください"]
    errs: list[str] = []
    seen: set[str] = set()
    for i, t in enumerate(data):
        where = f"takes[{i}]"
        if not isinstance(t, dict):
            errs.append(f"{where}: テイクは辞書にしてください")
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not tid.strip():
            errs.append(f"{where}: id が必要です")
        elif tid in seen:
            errs.append(f"{where}: id {tid!r} が重複しています")
        else:
            seen.add(tid)
            where = f"take {tid}"
        up = t.get("url_path")
        if not isinstance(up, str) or not up.startswith("/"):
            errs.append(f"{where}: url_path は '/' で始まる文字列")
        if "size" in t:
            try:
                parse_size(t["size"])
            except TakesError as e:
                errs.append(f"{where}: {e}")
        if "network_log" in t and not isinstance(t["network_log"], bool):
            errs.append(f"{where}: network_log は true か false")
        if "cursor" in t and not isinstance(t["cursor"], bool):
            errs.append(f"{where}: cursor は true か false")
        if "ready_js" in t and not (isinstance(t["ready_js"], str) and t["ready_js"].strip()):
            errs.append(f"{where}: ready_js は JS の式の文字列")
        if "ready" in t and not (isinstance(t["ready"], str) and t["ready"].strip()):
            errs.append(f"{where}: ready は CSS セレクタの文字列")
        steps = t.get("steps")
        if not isinstance(steps, list) or not steps:
            errs.append(f"{where}: steps は 1 つ以上の手順のリスト")
        else:
            for j, s in enumerate(steps):
                errs.extend(_check_step(s, f"{where} steps[{j}]"))
    return errs


def load_takes(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    errs = validate_takes(data)
    if errs:
        raise TakesError("台本に誤りがあります:\n  " + "\n  ".join(errs))
    return data


# ------------------------------------------------------------------ ポインタ表示（ヘッドレスの録画にはマウスが映らないため重ねる）

def cursor_enabled(take: dict, cli_flag: bool = False) -> bool:
    """--cursor が付いていれば常に有効。なければテイクの "cursor" に従う。"""
    return bool(cli_flag or take.get("cursor", False))


def ease_in_out(t: float) -> float:
    """0..1 の進み具合をなめらかにする（MOVE_JS の式と同じ cubic）。"""
    t = min(max(float(t), 0.0), 1.0)
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


# 重ねる要素を作る JS。何度呼んでも 1 組だけ（遷移で消えたら作り直す）。位置は window.__cur に持つ。
OVERLAY_JS = r"""(function(){
  if (document.getElementById('__rec_cursor')) return true;
  const st = document.createElement('style'); st.id = '__rec_style';
  st.textContent = '@keyframes __rec_ring{from{transform:translate(-50%,-50%) scale(.3);opacity:.9}to{transform:translate(-50%,-50%) scale(1.6);opacity:0}}';
  document.head.appendChild(st);
  const c = document.createElement('div'); c.id = '__rec_cursor';
  c.style.cssText = 'position:fixed;left:0;top:0;width:28px;height:28px;pointer-events:none;z-index:2147483647;will-change:transform;';
  c.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24" style="filter:drop-shadow(0 1px 2px rgba(0,0,0,.45))">'
    + '<path d="M3 2 L3 19 L7.6 14.8 L10.6 21.5 L13.3 20.3 L10.4 13.8 L16.8 13.8 Z" fill="#fff" stroke="#1a1a1a" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  document.documentElement.appendChild(c);
  const cur = window.__cur || {x: innerWidth * 0.5, y: innerHeight * 0.5};
  window.__cur = cur;
  c.style.transform = 'translate(' + cur.x + 'px,' + cur.y + 'px)';
  return true;
})()"""

MOVE_JS = r"""(function(x, y, ms){
  const c = document.getElementById('__rec_cursor'); const cur = window.__cur;
  const x0 = cur.x, y0 = cur.y, t0 = performance.now();
  const ease = t => t < 0.5 ? 4*t*t*t : 1 - Math.pow(-2*t + 2, 3) / 2;
  function step(now){
    const t = Math.min((now - t0) / ms, 1), e = ease(t);
    cur.x = x0 + (x - x0) * e; cur.y = y0 + (y - y0) * e;
    c.style.transform = 'translate(' + cur.x + 'px,' + cur.y + 'px)';
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
})(%s, %s, %s)"""

RING_JS = r"""(function(x, y, ms){
  const r = document.createElement('div');
  r.style.cssText = 'position:fixed;left:' + x + 'px;top:' + y + 'px;width:64px;height:64px;border-radius:50%%;'
    + 'border:4px solid #e8590c;background:rgba(232,89,12,.18);pointer-events:none;z-index:2147483646;'
    + 'animation:__rec_ring ' + ms + 'ms ease-out forwards;';
  document.documentElement.appendChild(r);
  setTimeout(function(){ r.remove(); }, ms + 100);
})(%s, %s, %s)"""

HIGHLIGHT_JS = r"""(function(sel, sec){
  const e = document.querySelector(sel); if (!e) return false;
  let r = e.getBoundingClientRect();
  if (r.top < 0 || r.bottom > innerHeight || r.left < 0 || r.right > innerWidth) {
    e.scrollIntoView({block: 'center', inline: 'center'}); r = e.getBoundingClientRect();
  }
  const b = document.createElement('div');
  b.style.cssText = 'position:fixed;left:' + (r.left - 4) + 'px;top:' + (r.top - 4) + 'px;width:' + (r.width + 8) + 'px;height:' + (r.height + 8)
    + 'px;border:3px solid #e8590c;border-radius:8px;box-sizing:border-box;pointer-events:none;z-index:2147483645;';
  document.documentElement.appendChild(b);
  setTimeout(function(){ b.remove(); }, sec * 1000);
  return true;
})(%s, %s)"""


# ------------------------------------------------------------------ コマの表示時間（時刻 → concat のリスト）

def frame_durations(timestamps: list[float], end_ts: float, fps: int = FPS) -> list[float]:
    """各コマの表示時間（秒）を返す。コマ番号（時刻 × fps を丸めた値）の差で決めるので、誤差がたまらない。

    同じコマ番号に落ちたコマ（間隔が 1/fps 未満）は表示時間 0 になる。concat_list が捨てる。
    """
    if not timestamps:
        return []
    t0 = timestamps[0]
    idx = [round((t - t0) * fps) for t in timestamps] + [max(round((end_ts - t0) * fps), 0)]
    out = [max(b - a, 0) / fps for a, b in zip(idx, idx[1:])]
    if out and out[-1] == 0:  # 録画がすぐ終わっても、最後のコマは 1 コマ分見せる
        out[-1] = 1 / fps
    return out


def concat_list(names: list[str], durations: list[float]) -> str:
    """ffmpeg の concat demuxer 用のリスト。表示時間 0 のコマは除く。最後のコマは仕様上もう一度書く。"""
    kept = [(n, d) for n, d in zip(names, durations) if d > 0]
    lines = []
    for n, d in kept:
        lines.append(f"file '{n}'")
        lines.append(f"duration {d:.6f}")
    if kept:
        lines.append(f"file '{kept[-1][0]}'")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ ffmpeg

def find_ffmpeg() -> str:
    env = os.environ.get("FFMPEG")
    if env and Path(env).exists():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise SystemExit("ffmpeg が見つかりません。環境変数 FFMPEG に ffmpeg.exe のパスを指定してください"
                     "（例: FFMPEG=C:\\ai\\bin\\ffmpeg.exe）。")


def build_mp4(ffmpeg: str, frames_dir: Path, names: list[str], durations: list[float], out: Path) -> None:
    with open(frames_dir / "list.txt", "w", encoding="utf-8", newline="\n") as f:
        f.write(concat_list(names, durations))
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
           "-vf", f"fps={FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS), "-movflags", "+faststart",
           str(out.resolve())]
    r = subprocess.run(cmd, cwd=frames_dir, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg が失敗しました:\n" + r.stderr[-1500:])


# ------------------------------------------------------------------ 録画用のページ（別スレッドで受信する）

class RecPage(cs.Page):
    """cs.Page に、別スレッドの受信を足したもの。録画中も send / js / wait がそのまま使える。"""

    def __init__(self, ws_url: str, w: int, h: int, frames_dir: Path | None = None):
        self._cm = cs.connect(ws_url, max_size=None, open_timeout=20)
        self.ws = self._cm.__enter__() if hasattr(self._cm, "__enter__") else self._cm
        self._id = 0
        self._lock = threading.Lock()
        self._waiters: dict[int, dict] = {}
        self._stop = threading.Event()
        self.frames_dir = frames_dir
        self.frames: list[tuple[str, float, float]] = []  # (ファイル名, CDP の時刻, 受け取った実時間)
        self.requests: list[dict] = []
        self.recording = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                msg = json.loads(self.ws.recv(timeout=0.5))
            except TimeoutError:
                continue
            except Exception:
                break
            try:
                if "id" in msg:
                    w = self._waiters.get(msg["id"])
                    if w is not None:
                        w["msg"] = msg
                        w["ev"].set()
                else:
                    self._on_event(msg.get("method", ""), msg.get("params", {}))
            except Exception:
                pass
        for w in list(self._waiters.values()):  # 接続が切れたら、待っている send を起こす
            w["ev"].set()

    def _on_event(self, method: str, p: dict) -> None:
        if method == "Page.screencastFrame":
            if self.recording and self.frames_dir is not None:
                ts = float(p["metadata"]["timestamp"])
                name = f"f{len(self.frames):06d}.jpg"
                (self.frames_dir / name).write_bytes(base64.b64decode(p["data"]))
                self.frames.append((name, ts, time.time()))
            self._post("Page.screencastFrameAck", sessionId=p["sessionId"])
        elif method == "Network.requestWillBeSent":
            self.requests.append({"t": time.time(), "method": p["request"].get("method"), "url": p["request"].get("url")})

    def _post(self, method: str, **params) -> None:
        with self._lock:
            self._id += 1
            mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))

    def send(self, method: str, **params):
        w = {"ev": threading.Event(), "msg": None}
        with self._lock:
            self._id += 1
            mid = self._id
            self._waiters[mid] = w
        try:
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            if not w["ev"].wait(60) or w["msg"] is None:
                raise RuntimeError(f"{method}: 応答がありません（接続が切れたか時間切れ）")
        finally:
            self._waiters.pop(mid, None)
        msg = w["msg"]
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result", {})

    def close(self) -> None:
        self._stop.set()
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass


# ------------------------------------------------------------------ 手順の実行

def _rect_js(sel: str) -> str:
    return ("(function(){const e=document.querySelector(%s); if(!e) return null;"
            "let r=e.getBoundingClientRect();"
            "if(r.top<0||r.bottom>innerHeight||r.left<0||r.right>innerWidth){e.scrollIntoView({block:'center',inline:'center'});r=e.getBoundingClientRect();}"
            "return [r.x+r.width/2,r.y+r.height/2];})()") % json.dumps(sel)


def _wait_sel(page, sel: str, timeout: float = 30) -> None:
    page.wait(lambda: page.js(f"!!document.querySelector({json.dumps(sel)})"), timeout, "要素 " + sel)


def _move_pointer(page, sel: str, ring: bool = False) -> list:
    """ポインタを対象の中心へ動かして、着くまで待つ。ring なら輪を出して広がるまで待つ。中心の座標を返す。"""
    _wait_sel(page, sel)
    page.js(OVERLAY_JS)  # 遷移で消えていたら作り直す
    page.js(_rect_js(sel))  # 画面の外にあれば中央へスクロールする
    time.sleep(0.25)
    xy = page.js(_rect_js(sel))  # スクロール後の位置を取り直す
    page.js(MOVE_JS % (xy[0], xy[1], int(MOVE_SEC * 1000)))
    time.sleep(MOVE_SEC + 0.1)
    if ring:
        page.js(RING_JS % (xy[0], xy[1], int(RING_SEC * 1000)))
        time.sleep(RING_SEC)
    return xy


def _click(page, sel: str, cursor: bool = False) -> None:
    _wait_sel(page, sel)
    if cursor:
        xy = _move_pointer(page, sel, ring=True)
    else:
        page.js(_rect_js(sel))  # 画面の外にあれば中央へスクロールする
        time.sleep(0.25)
        xy = page.js(_rect_js(sel))  # スクロール後の位置を取り直す
    page.mouse(xy[0], xy[1])
    time.sleep(0.5)


def run_step(page, step: dict, cursor: bool = False) -> None:
    (act, val), = step.items()
    if act == "highlight":
        sel, sec = val
        _wait_sel(page, sel)
        if not page.js(HIGHLIGHT_JS % (json.dumps(sel), float(sec))):
            raise RuntimeError("highlight: 要素が見つかりません: " + sel)
        time.sleep(float(sec))
    elif act == "move":
        if cursor:
            _move_pointer(page, val)
            time.sleep(0.2)
        else:
            _wait_sel(page, val)
    elif act == "wait":
        time.sleep(float(val))
    elif act == "wait_for":
        _wait_sel(page, val)
    elif act == "click":
        _click(page, val, cursor)
    elif act == "type":
        sel, text = val
        _click(page, sel, cursor)
        page.key("a", ctrl=True)
        for ch in text:  # 1 文字ずつ入れて、打っている様子が映るようにする
            page.insert(ch)
            time.sleep(0.05)
        time.sleep(0.2)
    elif act == "paste":
        sel, text = val
        _wait_sel(page, sel)
        if cursor:
            _move_pointer(page, sel)
        page.js("(function(){const e=document.querySelector(%s); e.focus(); e.value=%s;"
                "e.dispatchEvent(new Event('input',{bubbles:true}));})()" % (json.dumps(sel), json.dumps(text)))
        time.sleep(0.3)
    elif act == "eval":
        page.js(val)
    elif act == "scroll":
        sel, dy = val
        if sel:
            _wait_sel(page, sel)
        target = f"document.querySelector({json.dumps(sel)})" if sel else "window"
        for _ in range(20):  # なめらかに動くよう 20 回に分ける
            page.js(f"{target}.scrollBy(0,{float(dy) / 20})")
            time.sleep(0.03)
        time.sleep(0.3)


# ------------------------------------------------------------------ 録画

SOFT_FLAGS = ["--enable-unsafe-swiftshader"]  # ソフトウェア描画（既定。capture_web.py と同じ）
GPU_FLAGS = ["--use-angle=d3d11", "--enable-gpu-rasterization"]  # --gpu（RTX 5080 の D3D11 で WebGL が動くことを確認済み）。--use-gl=angle と --enable-zero-copy を足すと読み込みが終わらない


def start_edge(edge: str, w: int, h: int, gpu: bool = False):
    profile = tempfile.mkdtemp(prefix="edge_profile_")
    dbg = cs.free_port()
    proc = subprocess.Popen(
        [edge, "--headless=new", f"--remote-debugging-port={dbg}", f"--window-size={w},{h}", f"--user-data-dir={profile}",
         "--hide-scrollbars", "--force-device-scale-factor=1", "--no-first-run", "--enable-webgl", "--ignore-gpu-blocklist",
         *(GPU_FLAGS if gpu else SOFT_FLAGS), *cs.EDGE_CLEAN_FLAGS, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    targets = []
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{dbg}/json/list", timeout=2) as r:
                targets = cs.blank_targets(json.load(r))
            if targets:
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not targets:
        cs.stop_tree(proc)
        cs.stop_edge_profile(profile)
        raise RuntimeError("Edge のページに接続できませんでした。")
    return proc, profile, targets[0]["webSocketDebuggerUrl"]


def record_take(take: dict, out: Path, public: bool, ffmpeg: str, gpu: bool = False, cursor: bool = False) -> dict:
    w, h = parse_size(take.get("size"))
    use_cursor = cursor_enabled(take, cursor)
    cs.W, cs.H = w, h
    t_start = time.time()
    edge = cs.find_edge()
    port = cs.free_port()
    cmd = [sys.executable, str(ROOT / "tools" / "serve_web.py"), "--port", str(port)]
    if public:
        cmd.append("--public")
    srv = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    edge_proc = None
    profile = None
    page = None
    frames_dir = Path(tempfile.mkdtemp(prefix="rec_frames_"))
    try:
        end = time.time() + 60
        while time.time() < end:  # 配信サーバーが応答するまで待つ
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read(1)
                break
            except Exception:
                if srv.poll() is not None:
                    raise RuntimeError("serve_web.py が起動直後に終了しました。")
                time.sleep(0.4)
        edge_proc, profile, ws_url = start_edge(edge, w, h, gpu)
        page = RecPage(ws_url, w, h, frames_dir)
        if take.get("network_log"):
            page.send("Network.enable")
        page.goto("about:blank")
        page.goto(f"http://localhost:{port}{take['url_path']}")
        if take.get("ready"):
            _wait_sel(page, take["ready"], 120)
        if take.get("ready_js"):
            page.wait(lambda: page.js(take["ready_js"]), 300, "ready_js")
        if use_cursor:
            page.js(OVERLAY_JS)
        page.requests.clear()  # 読み込み中の要求は数えない（録画する間の要求だけを見る）
        page.recording = True
        page.send("Page.startScreencast", format="jpeg", quality=90, maxWidth=w, maxHeight=h, everyNthFrame=1)
        t_wait = time.time() + 3
        while not page.frames and time.time() < t_wait:
            time.sleep(0.05)
        if not page.frames:  # 静止画面ではコマが来ないことがあるので、1 px 動かして描き直させる
            page.js("window.scrollBy(0,1);window.scrollBy(0,-1)")
            time.sleep(0.5)
        for step in take["steps"]:
            run_step(page, step, use_cursor)
        time.sleep(0.3)
        stop_wall = time.time()
        page.send("Page.stopScreencast")
        page.recording = False
        frames = list(page.frames)
        if not frames:
            raise RuntimeError("コマが 1 枚も届きませんでした。")
        names = [f[0] for f in frames]
        stamps = [f[1] for f in frames]
        end_ts = stamps[-1] + max(stop_wall - frames[-1][2], 0)
        durs = frame_durations(stamps, end_ts)
        build_mp4(ffmpeg, frames_dir, names, durs, out)
        if take.get("network_log"):
            rp = out.with_name(out.stem + ".requests.json")
            with open(rp, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"take": take["id"], "count": len(page.requests), "requests": page.requests},
                          f, ensure_ascii=False, indent=1)
            print(f"通信の記録: {rp}（{len(page.requests)} 件）")
        span = max(end_ts - stamps[0], 1e-6)
        return {"frames": len(frames), "fps_in": len(frames) / span, "span": span, "kept": sum(1 for d in durs if d > 0), "wall": time.time() - t_start}
    finally:
        if page is not None:
            page.close()
        if edge_proc is not None:
            cs.stop_tree(edge_proc)
        if profile:
            cs.stop_edge_profile(profile)  # この録画で起動した Edge だけを、プロファイルの名前で探して止める
            shutil.rmtree(profile, ignore_errors=True)
        cs.stop_tree(srv)
        shutil.rmtree(frames_dir, ignore_errors=True)


def probe(ffmpeg: str, path: Path) -> str:
    """ffmpeg -i の標準エラーから、長さ・解像度・fps を読む。"""
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    s = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", r.stderr)
    f = re.search(r"([\d.]+) fps", r.stderr)
    if not (d and s):
        return r.stderr[-300:]
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    return f"長さ {dur:.2f} 秒 / {s.group(1)}x{s.group(2)} / {f.group(1) if f else '?'} fps"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--take", help="録画するテイクの id")
    ap.add_argument("--list", action="store_true", help="テイクの一覧を出す")
    ap.add_argument("--out", help="出力 mp4（省略すると outputs/video/<id>.mp4）")
    ap.add_argument("--public", action="store_true", help="serve_web.py を --public（公開版）で起動する")
    ap.add_argument("--gpu", action="store_true", help="GPU で描画する（既定はソフトウェア描画）")
    ap.add_argument("--cursor", action="store_true", help="ポインタとクリックの輪を映す（台本の cursor に関わらず有効にする）")
    ap.add_argument("--size", help="画面の大きさ WxH（台本の size より優先）")
    ap.add_argument("--takes", default=str(DEFAULT_TAKES), help="台本の JSON")
    args = ap.parse_args()
    try:
        takes = load_takes(Path(args.takes))
    except (TakesError, OSError, json.JSONDecodeError) as e:
        print(e)
        return 2
    if args.list:
        for t in takes:
            print(f"{t['id']}\t{t['url_path']}\t手順 {len(t['steps'])}")
        return 0
    if not args.take:
        ap.error("--take ID か --list を指定してください")
    take = next((t for t in takes if t["id"] == args.take), None)
    if take is None:
        print(f"テイク {args.take!r} がありません。--list で確認してください。")
        return 2
    if args.size:
        try:
            parse_size(args.size)
        except TakesError as e:
            ap.error(str(e))
        take = dict(take, size=args.size)
    ffmpeg = find_ffmpeg()
    out = Path(args.out) if args.out else ROOT / "outputs" / "video" / f"{take['id']}.mp4"
    res = record_take(take, out, args.public, ffmpeg, args.gpu, args.cursor)
    print(f"保存: {out}")
    print(probe(ffmpeg, out))
    print(f"受け取ったコマ {res['frames']} 枚（使用 {res['kept']} 枚）/ 所要 {res['wall']:.1f} 秒")
    print(f"録画中に届いたコマ {res['fps_in']:.1f} 枚/秒（{res['span']:.1f} 秒間）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
