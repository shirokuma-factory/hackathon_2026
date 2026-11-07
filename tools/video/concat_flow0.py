"""rec6 の FLOW0a〜d（同じ設定で録った 4K 録画）を、順に 1 本の FLOW0.mp4 につなぐ（再エンコードなし）。
使い方: python tools/video/concat_flow0.py
"""
import os
import subprocess
from pathlib import Path

REC = Path(__file__).resolve().parents[2] / "outputs" / "video" / "rec6"
FF = os.environ.get("FFMPEG", "C:/ai/bin/ffmpeg.exe")
parts = [REC / f"FLOW0{c}.mp4" for c in "abcd"]
lst = REC / "_flow0_list.txt"
with open(lst, "w", encoding="utf-8", newline="\n") as fh:
    for p in parts:
        fh.write(f"file '{p.as_posix()}'\n")
out = REC / "FLOW0.mp4"
r = subprocess.run([FF, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)],
                   capture_output=True, text=True)
print(r.stderr or "ok", out)
lst.unlink()
