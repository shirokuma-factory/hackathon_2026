"""高解像度録画（デバイス倍率 2: CSS の画面は 1920x1080、取り込みは 3840x2160）。

tools/video/record.py の record_take を写して、次の 3 点だけ変えた版。record.py 本体は変更しない。
  - Emulation.setDeviceMetricsOverride の deviceScaleFactor を 2 にする（ポインタ・枠などの重ね表示は CSS 座標なので一緒に拡大される）
  - Page.startScreencast の maxWidth/maxHeight を 幅×倍率, 高さ×倍率、JPEG 品質 95 にする
  - ffmpeg は 3840x2160 のまま libx264 crf 16 で書く
配信は鹿児島版（第2版）の serve_kago2.py を使う（record_kago.py と同じ仕組みの別の起動役を kago_root2 に作る）。

使い方:  python tools/video/record_hd.py --takes tools/video/takes_r6.json --take R1g --out outputs/video/rec6/R1g.mp4 --gpu
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import record  # noqa: E402
from record import cs  # noqa: E402

DSF = 2

shim_root = REPO / "outputs" / "video" / "kago_root2"
shim = shim_root / "tools" / "serve_web.py"
shim.parent.mkdir(parents=True, exist_ok=True)
with open(shim, "w", encoding="utf-8", newline="\n") as fh:
    fh.write("import runpy\nrunpy.run_path(" + repr(str(HERE / "serve_kago2.py")) + ", run_name='__main__')\n")
record.ROOT = shim_root


def start_edge_hd(edge, w, h, gpu=False):
    """record.start_edge と同じだが、実機のデバイス倍率も DSF にする。"""
    profile = tempfile.mkdtemp(prefix="edge_profile_")
    dbg = cs.free_port()
    proc = subprocess.Popen(
        [edge, "--headless=new", f"--remote-debugging-port={dbg}", f"--window-size={w},{h}", f"--user-data-dir={profile}",
         "--hide-scrollbars", f"--force-device-scale-factor={DSF}", "--no-first-run", "--enable-webgl", "--ignore-gpu-blocklist",
         *(record.GPU_FLAGS if gpu else record.SOFT_FLAGS), *cs.EDGE_CLEAN_FLAGS, "about:blank"],
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


def build_mp4_hd(ffmpeg, frames_dir, names, durations, out):
    with open(frames_dir / "list.txt", "w", encoding="utf-8", newline="\n") as f:
        f.write(record.concat_list(names, durations))
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
           "-vf", f"fps={record.FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
           "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-r", str(record.FPS), "-movflags", "+faststart",
           str(out.resolve())]
    r = subprocess.run(cmd, cwd=frames_dir, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("ffmpeg が失敗しました:\n" + r.stderr[-1500:])


def record_take_hd(take, out, public, ffmpeg, gpu=False, cursor=False):
    w, h = record.parse_size(take.get("size"))
    use_cursor = record.cursor_enabled(take, cursor)
    cs.W, cs.H = w, h
    t_start = time.time()
    edge = cs.find_edge()
    port = cs.free_port()
    cmd = [sys.executable, str(record.ROOT / "tools" / "serve_web.py"), "--port", str(port)]
    if public:
        cmd.append("--public")
    srv = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    edge_proc = profile = page = None
    frames_dir = Path(tempfile.mkdtemp(prefix="rec_frames_hd_"))
    try:
        end = time.time() + 60
        while time.time() < end:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read(1)
                break
            except Exception:
                if srv.poll() is not None:
                    raise RuntimeError("serve_web.py が起動直後に終了しました。")
                time.sleep(0.4)
        edge_proc, profile, ws_url = start_edge_hd(edge, w, h, gpu)
        page = record.RecPage(ws_url, w, h, frames_dir)
        page.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=DSF, mobile=False)
        if take.get("network_log"):
            page.send("Network.enable")
        page.goto("about:blank")
        page.goto(f"http://localhost:{port}{take['url_path']}")
        if take.get("ready"):
            record._wait_sel(page, take["ready"], 120)
        if take.get("ready_js"):
            page.wait(lambda: page.js(take["ready_js"]), 300, "ready_js")
        if use_cursor:
            page.js(record.OVERLAY_JS)
        page.requests.clear()
        page.recording = True
        page.send("Page.startScreencast", format="jpeg", quality=int(__import__("os").environ.get("HD_Q","95")), maxWidth=w * DSF, maxHeight=h * DSF, everyNthFrame=1)
        t_wait = time.time() + 3
        while not page.frames and time.time() < t_wait:
            time.sleep(0.05)
        if not page.frames:
            page.js("window.scrollBy(0,1);window.scrollBy(0,-1)")
            time.sleep(0.5)
        for step in take["steps"]:
            record.run_step(page, step, use_cursor)
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
        durs = record.frame_durations(stamps, end_ts)
        build_mp4_hd(ffmpeg, frames_dir, names, durs, out)
        span = max(end_ts - stamps[0], 1e-6)
        return {"frames": len(frames), "fps_in": len(frames) / span, "span": span, "kept": sum(1 for d in durs if d > 0), "wall": time.time() - t_start}
    finally:
        if page is not None:
            page.close()
        if edge_proc is not None:
            cs.stop_tree(edge_proc)
        if profile:
            cs.stop_edge_profile(profile)
            shutil.rmtree(profile, ignore_errors=True)
        cs.stop_tree(srv)
        shutil.rmtree(frames_dir, ignore_errors=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--takes", required=True)
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--cursor", action="store_true")
    a = ap.parse_args()
    takes = record.load_takes(Path(a.takes))
    take = next((t for t in takes if t["id"] == a.take), None)
    if take is None:
        print("テイクがありません:", a.take)
        return 2
    ffmpeg = record.find_ffmpeg()
    out = Path(a.out)
    res = record_take_hd(take, out, False, ffmpeg, a.gpu, a.cursor)
    print("保存:", out)
    print(record.probe(ffmpeg, out))
    print(f"受け取ったコマ {res['frames']} 枚 / 所要 {res['wall']:.1f} 秒 / 届いたコマ {res['fps_in']:.1f} 枚/秒（{res['span']:.1f} 秒間）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
