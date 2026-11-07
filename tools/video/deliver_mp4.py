"""Resolve で書き出した ProRes の元（.mov、3〜4 GB）から、YouTube に上げる mp4 を作る。

作成者の指示（11/7）: 提出は YouTube の限定公開。見て確かめるにも mp4 でないと扱えない。
YouTube の推奨: MP4（moov を先頭）、H.264 High・プログレッシブ・4:2:0、BT.709、AAC-LC 48kHz ステレオ、
1080p は 8 Mbps 前後以上。YouTube は上げた動画を作り直すので、元のビットレートが高いほど文字が崩れにくい。

- 映像と音だけを入れる（ProRes の元にある時刻の情報のトラック tmcd とメタデータは入れない）
- x264 平均 10 Mbps（crf だと画面がほぼ止まっているため 2〜3 Mbps まで下がり、min の指定も効かない）
- 音は -14 LUFS（YouTube の基準の音量。r14 の採点で -16 は 2 dB 小さいと指摘）
- 長さは EDL の total で切る（3 分以内）

--copy-video: Resolve が H.264 の mp4 を直接書き出した場合（r18〜、作成者の指示 11/8「MOV ファイルは作成不要」）。
映像はそのまま写し、音の大きさだけを -14 LUFS に直す（Resolve の書き出しは約 -20 LUFS で、API から音量の正規化を指定できない）。

使い方: uv run python tools/video/deliver_mp4.py --master outputs/video/assembly/Backfull_r14_master.mov --edl docs/video/edl_r14.json --out outputs/video/assembly/Backfull_r14.mp4
"""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FF = "C:/ai/bin/ffmpeg.exe"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", required=True)
    ap.add_argument("--edl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--copy-video", action="store_true", help="映像を写すだけにする（Resolve の H.264 mp4 から）")
    a = ap.parse_args()
    total = json.loads((ROOT / a.edl).read_text(encoding="utf-8"))["total"]
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", "-i", a.master, "-map", "0:v:0", "-map", "0:a:0", "-t", f"{total - 0.04:.3f}",
           "-map_metadata", "-1", "-write_tmcd", "0",
           *(["-c:v", "copy"] if a.copy_video else
             ["-c:v", "libx264", "-preset", "slow", "-b:v", "10M", "-maxrate", "20M", "-bufsize", "40M",
              "-profile:v", "high", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]),
           "-af", "loudnorm=I=-14:TP=-1.0:LRA=11", "-ar", "48000", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", a.out]
    subprocess.run(cmd, check=True)
    r = subprocess.run([FF, "-hide_banner", "-i", a.out], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    print("\n".join(l for l in r.splitlines() if "Duration" in l or "Stream" in l))


if __name__ == "__main__":
    main()
