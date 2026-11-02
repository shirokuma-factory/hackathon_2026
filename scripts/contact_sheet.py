"""Make a contact sheet (grid of frames) from a video so it can be checked as one image.

Usage: python scripts/contact_sheet.py <video> [--frames 12] [--cols 4] [--width 1600]
Writes <video>.contact.jpg next to the video and prints its path.
Needs ffmpeg on PATH, or the imageio-ffmpeg package.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg が見つかりません。ffmpeg を PATH に入れるか `pip install imageio-ffmpeg` してください。")


def duration_s(ffmpeg: str, video: Path) -> float:
    ffprobe = Path(ffmpeg).with_name(Path(ffmpeg).name.replace("ffmpeg", "ffprobe"))
    if ffprobe.exists():
        out = subprocess.run(
            [str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
            capture_output=True, text=True,
        ).stdout
        return float(json.loads(out)["format"]["duration"])
    # imageio-ffmpeg has no ffprobe; parse "Duration: hh:mm:ss.xx" from ffmpeg -i.
    err = subprocess.run([ffmpeg, "-i", str(video)], capture_output=True, text=True).stderr
    for line in err.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    sys.exit(f"長さを読み取れません: {video}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--width", type=int, default=1600)
    a = ap.parse_args()

    ffmpeg = find_ffmpeg()
    dur = duration_s(ffmpeg, a.video)
    rows = -(-a.frames // a.cols)
    fps = a.frames / dur
    tile_w = a.width // a.cols
    out = a.video.with_suffix(a.video.suffix + ".contact.jpg")
    vf = (
        f"fps={fps:.6f},scale={tile_w}:-2,"
        f"drawtext=text='%{{pts\\:hms}}':x=6:y=6:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.6,"
        f"tile={a.cols}x{rows}:padding=4:color=white"
    )
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(a.video), "-vf", vf, "-frames:v", "1", "-q:v", "3", str(out)]
    if subprocess.run(cmd).returncode != 0:
        # drawtext needs a font; retry without timestamps.
        cmd[cmd.index(vf)] = f"fps={fps:.6f},scale={tile_w}:-2,tile={a.cols}x{rows}:padding=4:color=white"
        subprocess.run(cmd, check=True)
    print(out)


if __name__ == "__main__":
    main()
