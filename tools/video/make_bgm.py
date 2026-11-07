"""BGM を動画の長さに合わせて切り、ナレーションの下で自動で下げた WAV を作る。

曲の前半（0〜CUT_A 秒）をそのまま使い、曲の終わりの部分（CUT_B 秒〜最後）へ XF 秒かけてつなぐ。
こうすると、曲の最後の和音が動画の終わり（TOTAL 秒）に来る。
ナレーションは Resolve のタイムラインの A1 と同じ置き場所で重ね、それを合図に BGM を下げる（sidechaincompress）。

使い方:
  uv run python tools/video/make_bgm.py
出力: outputs/video/bgm/r04/bgm_fit.wav（切っただけ）、bgm_duck.wav（声の下で下げたもの。Resolve に入れる）、
      check_mix.wav（声と BGM を重ねた確認用）
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FF = "C:/ai/bin/ffmpeg.exe"
D = ROOT / "outputs" / "video" / "bgm" / "r04"
SRC = D / "r04_1.wav"
V = ROOT / "outputs" / "video" / "voice" / "tight_r3"
TOTAL = 170.0
SONG_LEN = 193.6
XF = 1.5
CUT_B = 176.0                                # 曲の終わりの部分の始まり（ピアノの締め）
CUT_A = TOTAL - (SONG_LEN - CUT_B) + XF      # 前半を使う長さ
BASE_DB = -10.0                               # 声のない所の BGM の大きさ（元の曲からの差）

# (置く秒, ファイル, 頭を切る秒) — Resolve の Backfull_r3 の A1 と同じ
VOICE = [(0.6, "H0_v3.wav", 0), (6.5, "H1.wav", 0), (18.3, "H2_v3.wav", 0), (36.0, "H3.wav", 0),
         (46.5, "H4.wav", 0), (58.4, "R4_v3.wav", 0.2), (94.9, "R5.wav", 0), (114.5, "R6_v3.wav", 0),
         (133.5, "R7.wav", 0), (154.0, "H8_v3.wav", 0)]


def run(args: list[str]) -> None:
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def main() -> None:
    fit = D / "bgm_fit.wav"
    run(["-i", str(SRC), "-filter_complex",
         f"[0]atrim=0:{CUT_A},asetpts=PTS-STARTPTS[a];[0]atrim={CUT_B},asetpts=PTS-STARTPTS[b];"
         f"[a][b]acrossfade=d={XF}:c1=tri:c2=tri,atrim=0:{TOTAL},afade=t=in:d=0.8[o]",
         "-map", "[o]", "-ar", "48000", str(fit)])
    voice = D / "voice_only.wav"
    ins, parts = [], []
    for i, (t, f, skip) in enumerate(VOICE):
        ins += ["-i", str(V / f)]
        ms = int(round(t * 1000))
        parts.append(f"[{i}]atrim={skip},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[v{i}]")
    mix = "".join(f"[v{i}]" for i in range(len(VOICE)))
    run([*ins, "-filter_complex", ";".join(parts) + f";{mix}amix=inputs={len(VOICE)}:normalize=0,apad=whole_dur={TOTAL}[o]",
         "-map", "[o]", "-t", str(TOTAL), str(voice)])
    duck = D / "bgm_duck.wav"
    run(["-i", str(fit), "-i", str(voice), "-filter_complex",
         f"[0]volume={BASE_DB}dB[m];[1]asplit=2[sc][x];[x]anullsink;"
         f"[m][sc]sidechaincompress=threshold=0.015:ratio=10:attack=80:release=600:makeup=1,apad=whole_dur={TOTAL}[o]",
         "-map", "[o]", str(duck)])
    run(["-i", str(duck), "-i", str(voice), "-filter_complex", "[0][1]amix=inputs=2:normalize=0[o]",
         "-map", "[o]", str(D / "check_mix.wav")])
    print(f"CUT_A={CUT_A:.2f} CUT_B={CUT_B} -> {duck}")


if __name__ == "__main__":
    main()
