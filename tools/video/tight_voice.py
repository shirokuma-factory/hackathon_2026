"""ナレーションの無音を詰める。間の種類ごとに残す長さを変える（r9 から）。

それまでは ffmpeg の silenceremove で、文の終わりも「……」の間もすべて 0.3 秒に詰めていた。そこへ 1.1 倍速が
掛かるので、問いかけや強調の前の間が 0.27 秒になり、急いで次へ移るように聞こえた（作成者の指摘 11/7）。

決め方（元の音声の秒。1.1 倍速の前）:
  先頭の無音          0（全部消す）
  末尾の無音          0.1
  「……」を含む間     0.6
  文の終わり（。？）  0.35
  そのほか（、）      0.28
  docs/video/pauses_r9.json の個別の指定（直前の読みの文字で選ぶ）があればそれを使う。元の無音より長い指定は無音を足す。

出力: <out>/<S>_<tag>.wav（48kHz）と <out>/<S>_<tag>.keep.json（無音の区間と残した長さ。make_subtitles.squeeze が使う）

使い方:
  uv run python tools/video/tight_voice.py --shots docs/video/shots_r7.json --tag r7 --out outputs/video/voice/tight_r9 \
    --rules docs/video/pauses_r9.json [--only H2,P1]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_subtitles as ms  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "outputs" / "video" / "voice"
FF = "C:/ai/bin/ffmpeg.exe"
SR = 48000
DEFAULT = {"…": 0.6, "。": 0.35, "、": 0.28}


def decode(p: Path) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-i", str(p), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rules", default=None)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    shots = json.loads((ROOT / a.shots).read_text(encoding="utf-8"))["shots"]
    rules = json.loads((ROOT / a.rules).read_text(encoding="utf-8")) if a.rules else {}
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    only = set(a.only.split(",")) if a.only else None
    for sh in shots:
        sid = sh["id"]
        if only and sid not in only:
            continue
        mp3 = VOICE / f"{sid}_{a.tag}.mp3"
        if not mp3.exists():
            continue
        al = json.loads((VOICE / f"{sid}_{a.tag}.json").read_text(encoding="utf-8"))["alignment"]
        ch, t0, t1 = al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]
        x = decode(mp3)
        total = len(x) / SR
        ms.KEEP_DIR = None
        sil = ms.silences(mp3)
        keeps, parts, pos, log = [], [], 0.0, []
        used = set()
        for i, (s0, s1) in enumerate(sil):
            # 間の直前の読み。文字の終わりの時刻は無音の始まりより少し後ろに出ることがあるので、始まりの時刻で選ぶ
            before = "".join(c for c, s in zip(ch, t0) if s <= s0 + 0.02)
            inside = "".join(c for c, s in zip(ch, t0) if s0 - 0.3 <= s <= s1 + 0.05)
            if i == 0 and s0 < 0.05:
                k, why = 0.0, "先頭"
            elif s1 >= total - 0.02:
                k, why = 0.1, "末尾"
            else:
                kind = "…" if "…" in inside else ("。" if ("。" in inside or "？" in inside) else "、")
                k, why = DEFAULT[kind], kind
                for r in rules.get(sid, []):
                    tail = before.rstrip("、。…？ ")
                    if tail.endswith(r["after"]):
                        k, why = r["keep"], f"指定 {r['after']}"
                        used.add(r["after"])
            keeps.append(k)
            parts.append(x[int(pos * SR):int(s0 * SR)])
            have = s1 - s0
            parts.append(x[int(s0 * SR):int((s0 + min(k, have)) * SR)])
            if k > have:
                parts.append(np.zeros(int((k - have) * SR), dtype=np.float32))
            pos = s1
            log.append(f"{before[-6:]}|{have:.2f}->{k:.2f}({why})")
        parts.append(x[int(pos * SR):])
        y = np.concatenate(parts)
        wav = out / f"{sid}_{a.tag}.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
        with open(out / f"{sid}_{a.tag}.keep.json", "w", encoding="utf-8", newline="\n") as f:
            json.dump({"sil": [[round(s0, 3), round(s1, 3)] for s0, s1 in sil], "keep": keeps}, f, ensure_ascii=False)
        missing = [r["after"] for r in rules.get(sid, []) if r["after"] not in used]
        print(f"{sid}: {total:.2f} -> {len(y) / SR:.2f} 秒  " + "  ".join(log))
        if missing:
            print(f"  注意 {sid}: 個別の指定が当たりませんでした（その位置に 0.3 秒以上の無音がない）: {missing}")


if __name__ == "__main__":
    main()
