"""ElevenLabs でナレーションを作る（区間ごと）。

キーは .env の ELEVENLABS_API_KEY から読み、画面にもログにも出さない。
使い方:
    uv run python tools/video/tts_eleven.py --voices            # 使える声の一覧（名前・言語・voice_id）
    uv run python tools/video/tts_eleven.py --shot S2 --voice <voice_id>
出力: outputs/video/voice/<shot>.mp3 と <shot>.json（文字ごとの時刻、使った設定、長さ）
読みは docs/video/shots.json の read を使う。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs" / "video" / "voice"
API = "https://api.elevenlabs.io/v1"
MODEL = "eleven_multilingual_v2"


def key() -> str:
    """.env の ELEVENLABS_API_KEY、なければ環境変数の ELEVENLABS_API_KEY を使う。値は表示しない。"""
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("ELEVENLABS_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    v = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if v:
        return v
    sys.exit("ELEVENLABS_API_KEY が .env にも環境変数にもありません。リポジトリの .env に ELEVENLABS_API_KEY=... の 1 行を足してください。")


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"xi-api-key": key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def voices() -> None:
    d = call("GET", "/voices")
    for v in d.get("voices", []):
        labels = v.get("labels") or {}
        langs = [x.get("language") for x in (v.get("verified_languages") or [])]
        print(v["voice_id"], v.get("name"), v.get("category"), labels.get("gender"), labels.get("accent"), ",".join(l for l in langs if l))


def shot(sid: str, voice: str, stability: float, speed: float) -> None:
    shots = json.loads((ROOT / "docs" / "video" / "shots.json").read_text(encoding="utf-8"))["shots"]
    s = next(x for x in shots if x["id"] == sid)
    text = s["read"]
    body = {"text": text, "model_id": MODEL, "language_code": "ja",
            "voice_settings": {"stability": stability, "similarity_boost": 0.75, "speed": speed}}
    d = call("POST", f"/text-to-speech/{voice}/with-timestamps?output_format=mp3_44100_128", body)
    audio = base64.b64decode(d["audio_base64"])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{sid}.mp3").write_bytes(audio)
    al = d.get("alignment") or {}
    ends = al.get("character_end_times_seconds") or [0]
    meta = {"shot": sid, "voice_id": voice, "model": MODEL, "settings": body["voice_settings"],
            "text": text, "duration_s": round(ends[-1], 2), "planned_s": s["end"] - s["start"],
            "sha256": hashlib.sha256(audio).hexdigest(), "alignment": al}
    with open(OUT / f"{sid}.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"{sid}: {meta['duration_s']} 秒（予定 {meta['planned_s']} 秒） -> {OUT / (sid + '.mp3')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", action="store_true")
    ap.add_argument("--shot")
    ap.add_argument("--voice")
    ap.add_argument("--stability", type=float, default=0.5)
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    if a.voices:
        voices()
    elif a.shot and a.voice:
        shot(a.shot, a.voice, a.stability, a.speed)
    else:
        ap.print_help()
