"""録画のオレンジ枠が出ている区間（秒）を一覧する（4K 録画対応版。det_times.py と同じ出力）。
使い方: python tools/video/det_times6.py a.mp4 ...   （3840x2160 は 1920x1080 に縮小して検出する）"""
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import focus_clips as fc

for p in sys.argv[1:]:
    r = subprocess.run([fc.FFMPEG, "-hide_banner", "-i", p], capture_output=True, text=True)
    S = 2 if "3840x2160" in r.stderr else 1
    segs, cur = [], None
    for i, f in enumerate(fc.read_frames(p, 0, 1e4, S, down=True)):
        if i % 3:
            continue
        b = fc.detect(f)
        t = i / fc.FPS
        if b:
            if cur and max(abs(a - c) for a, c in zip(b, cur[2])) <= 15:
                cur[1] = t
            else:
                if cur: segs.append(cur)
                cur = [t, t, b]
        elif cur and t - cur[1] > 0.3:
            segs.append(cur); cur = None
    if cur: segs.append(cur)
    print(p, f"dur={fc.video_duration(Path(p)):.2f}")
    for a, b, bx in segs:
        print(f"   {a:5.1f}-{b:5.1f}  box={bx}")
