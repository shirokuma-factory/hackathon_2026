"""採点者に渡す「見るための材料」を、書き出した動画から作る（docs/plan/2026-11-07_デモ動画の採点基準.md）。

採点者は動画を再生できず、音も聞けない。そこで、書き出した mp4 そのものから次を作る:
  transcript.txt / transcript.json  音を文字に起こした言葉と時刻（ElevenLabs Scribe。鍵は tools/video/tts_eleven.key()、値は表示しない）
  clause_sheets/sheetNN.jpg        文字起こしの句ごとに、言い始め +0.4 秒のコマ（時刻と句つき、4×5）
  sec_sheets/sec_NNN.jpg           1 秒ごとのコマ（時刻つき、5×4 で 20 秒ずつ）
  audio.txt                        全体の LUFS、5 秒ごとの音量、BGM のファイルの冒頭と締めの音量
計算した時刻ではなく、書き出した動画の音と絵から作るので、組み立ての計算のずれもここで見える。

使い方: uv run python tools/video/review_pack.py --mp4 outputs/video/assembly/Backfull_r14.mp4 --edl docs/video/edl_r14.json --out outputs/video/review/r14
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
FF = "C:/ai/bin/ffmpeg.exe"
FONT = "C:/Windows/Fonts/NotoSansJP-VF.ttf"


def frame(mp4: str, t: float, w: int = 480) -> Image.Image:
    raw = subprocess.run([FF, "-v", "error", "-ss", f"{max(0, t):.2f}", "-i", mp4, "-frames:v", "1", "-vf", f"scale={w}:-2",
                          "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, check=True).stdout
    import io
    return Image.open(io.BytesIO(raw)).convert("RGB")


def tile(ims: list, cols: int, out: Path) -> None:
    W, H = ims[0].size
    rows = (len(ims) + cols - 1) // cols
    s = Image.new("RGB", (W * cols, H * rows), "white")
    for i, im in enumerate(ims):
        s.paste(im, ((i % cols) * W, (i // cols) * H))
    s.save(out, quality=85)


def stt(mp4: str) -> dict:
    import tts_eleven as te
    with tempfile.TemporaryDirectory() as td:
        mp3 = Path(td) / "a.mp3"
        subprocess.run([FF, "-v", "error", "-y", "-i", mp4, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k", str(mp3)], check=True)
        data = mp3.read_bytes()
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"model_id\"\r\n\r\nscribe_v1\r\n"
            f"--{b}\r\nContent-Disposition: form-data; name=\"language_code\"\r\n\r\njpn\r\n"
            f"--{b}\r\nContent-Disposition: form-data; name=\"timestamps_granularity\"\r\n\r\nword\r\n"
            f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.mp3\"\r\nContent-Type: audio/mpeg\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(te.API + "/speech-to-text", data=body, method="POST",
                                 headers={"xi-api-key": te.key(), "Content-Type": f"multipart/form-data; boundary={b}"})
    return json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))


def phrases(words: list) -> list:
    out, line, t0 = [], "", None
    for w in words:
        if w.get("type") != "word":
            continue
        if t0 is None:
            t0 = w["start"]
        line += w["text"]
        if re.search(r"[。、？！]$", w["text"]) or len(line) >= 16:
            out.append((t0, line)); line, t0 = "", None
    if line:
        out.append((t0, line))
    return out


def db(x: np.ndarray) -> float:
    return round(float(20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-9)), 1) if len(x) else -180.0


def pcm(path: str, sr: int = 8000) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mp4", required=True)
    ap.add_argument("--edl", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = ROOT / a.out
    (out / "clause_sheets").mkdir(parents=True, exist_ok=True)
    (out / "sec_sheets").mkdir(parents=True, exist_ok=True)
    mp4 = str(ROOT / a.mp4)
    edl = json.loads((ROOT / a.edl).read_text(encoding="utf-8"))
    font = ImageFont.truetype(FONT, 22)

    # 文字起こし
    r = stt(mp4)
    ph = phrases(r.get("words", []))
    (out / "transcript.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    with open(out / "transcript.txt", "w", encoding="utf-8", newline="\n") as f:
        for t, s in ph:
            f.write(f"{t:7.2f}  {s}\n")

    # 句ごとのコマ
    tiles = []
    for t, s in ph:
        im = frame(mp4, t + 0.4)
        W, H = im.size
        c = Image.new("RGB", (W, H + 34), "white"); c.paste(im, (0, 0))
        ImageDraw.Draw(c).text((6, H + 4), f"{t:6.1f}s  {s[:18]}", fill="black", font=font)
        tiles.append(c)
    for i in range(0, len(tiles), 20):
        tile(tiles[i:i + 20], 4, out / "clause_sheets" / f"sheet{i // 20:02d}.jpg")

    # 1 秒ごとのコマ
    total = edl["total"]
    for st in range(0, int(total) + 1, 20):
        ims = []
        for t in range(st, min(st + 20, int(total) + 1)):
            im = frame(mp4, t + 0.5, 384)
            ImageDraw.Draw(im).rectangle([0, 0, 70, 30], fill="black")
            ImageDraw.Draw(im).text((6, 2), f"{t}s", fill="yellow", font=font)
            ims.append(im)
        tile(ims, 5, out / "sec_sheets" / f"sec_{st:03d}.jpg")

    # 音
    x = pcm(mp4)
    loud = subprocess.run([FF, "-hide_banner", "-i", mp4, "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    ms_ = re.findall(r"I:\s+(-?[\d.]+) LUFS", loud)  # 最後の I が全体の値（途中の行は瞬間の値）
    lines = [f"全体の音量 {ms_[-1] if ms_ else '?'} LUFS（YouTube は -14 前後にそろえる）", "", "5 秒ごとの音量（dBFS、声と BGM と効果音を合わせた値）"]
    lines += [f"{s:5d}s  {db(x[s * 8000:(s + 5) * 8000])}" for s in range(0, int(total), 5)]
    bgm = [d for d in edl["audio"].get("5", [])]
    if bgm:
        p = bgm[0]["path"]
        p = p if Path(p).is_absolute() else str(ROOT / p)
        y = pcm(p)
        lines += ["", f"BGM のファイル（{Path(p).name}）", f"  冒頭 0〜6.5 秒: {db(y[:int(6.5 * 8000)])} dBFS（-180 は無音）",
                  f"  最後の 30 秒: {db(y[int((total - 30) * 8000):])} dBFS"]
    (out / "audio.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"{out}: 句 {len(ph)}、句のコマ {len(tiles)}、1 秒のコマ {int(total) + 1}")


if __name__ == "__main__":
    main()
