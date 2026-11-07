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

import json
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
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=float, default=BASE_DB, help="声のない所の BGM の大きさ（元の曲からの差、dB）")
    ap.add_argument("--out", default="bgm_duck.wav", help="Resolve に読み込み済みの名前は上書きしないこと")
    ap.add_argument("--total", type=float, default=TOTAL, help="動画の長さ（秒）")
    ap.add_argument("--voice", default=None, help='声の置き場所の JSON: [[秒, ファイル名, 頭を切る秒], ...]')
    ap.add_argument("--start", type=float, default=0.0, help="BGM を始める秒（冒頭のフックは BGM なし。作成者の指摘 11/7）")
    ap.add_argument("--end", type=float, default=None, help="この秒で今の曲を 2.5 秒かけて消す（締めは --closing の曲に替える）")
    ap.add_argument("--closing", default=None, help="締めの曲の WAV。--closing-at の秒から最後まで置く")
    ap.add_argument("--closing-at", type=float, default=None)
    ap.add_argument("--closing-skip", type=float, default=0.0, help="締めの曲の頭を切る秒（曲の終わりを動画の終わりに合わせる）")
    ap.add_argument("--closing-db", type=float, default=None, help="締めの曲の大きさ（元からの差、dB）。省略時は --db と同じ")
    a = ap.parse_args()
    total = a.total
    voices = [tuple(x) for x in json.loads(a.voice)] if a.voice else VOICE
    fit = D / "bgm_fit.wav"
    st_ms = int(round(a.start * 1000))
    if a.end is None:
        cut_a = total - a.start - (SONG_LEN - CUT_B) + XF
        run(["-i", str(SRC), "-filter_complex",
             f"[0]atrim=0:{cut_a},asetpts=PTS-STARTPTS[a];[0]atrim={CUT_B},asetpts=PTS-STARTPTS[b];"
             f"[a][b]acrossfade=d={XF}:c1=tri:c2=tri,afade=t=in:d=1.5,adelay={st_ms}|{st_ms},apad=whole_dur={total},atrim=0:{total}[o]",
             "-map", "[o]", "-ar", "48000", str(fit)])
        cut_a = round(cut_a, 2)
    else:
        # 曲を置く秒は Python で計算して並べる。ffmpeg 7.1 の adelay がこの組み方では効かず、
        # 冒頭のフックに BGM が鳴り、締めの曲が入らなかった（11/7 に音を測って判明）
        import numpy as np
        import wave as _wave
        SR = 48000

        def dec(p, t0, t1):
            raw = subprocess.run([FF, "-v", "error", "-ss", f"{t0}", "-t", f"{t1 - t0}", "-i", str(p), "-ac", "2", "-ar", str(SR),
                                  "-f", "f32le", "-"], capture_output=True, check=True).stdout
            return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()

        def fade(x, fin, fout):
            n = len(x)
            if fin:
                k = min(n, int(fin * SR)); x[:k] *= np.linspace(0, 1, k)[:, None]
            if fout:
                k = min(n, int(fout * SR)); x[n - k:] *= np.linspace(1, 0, k)[:, None]
            return x

        out = np.zeros((int(total * SR) + 1, 2), np.float32)
        L = a.end - a.start
        m = fade(dec(SRC, 0, L), 1.5, 2.5)
        i0 = int(a.start * SR)
        out[i0:i0 + len(m)] += m[:len(out) - i0]
        if a.closing:
            cdb = a.db if a.closing_db is None else a.closing_db
            c = dec(a.closing, a.closing_skip, a.closing_skip + total - a.closing_at)
            c = fade(c, 2.0, 0) * (10 ** ((cdb - a.db) / 20))
            j0 = int(a.closing_at * SR)
            out[j0:j0 + len(c)] += c[:len(out) - j0]
        with _wave.open(str(fit), "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes((np.clip(out, -1, 1) * 32767).astype("<i2").tobytes())
        cut_a = None
    voice = D / "voice_only.wav"
    ins, parts = [], []
    for i, (t, f, skip) in enumerate(voices):
        ins += ["-i", str(V / f)]
        ms = int(round(t * 1000))
        parts.append(f"[{i}]atrim={skip},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[v{i}]")
    mix = "".join(f"[v{i}]" for i in range(len(voices)))
    run([*ins, "-filter_complex", ";".join(parts) + f";{mix}amix=inputs={len(voices)}:normalize=0,apad=whole_dur={total}[o]",
         "-map", "[o]", "-t", str(total), str(voice)])
    duck = D / a.out
    run(["-i", str(fit), "-i", str(voice), "-filter_complex",
         f"[0]volume={a.db}dB[m];[1]asplit=2[sc][x];[x]anullsink;"
         f"[m][sc]sidechaincompress=threshold=0.015:ratio=10:attack=80:release=600:makeup=1,apad=whole_dur={total}[o]",
         "-map", "[o]", str(duck)])
    run(["-i", str(duck), "-i", str(voice), "-filter_complex", "[0][1]amix=inputs=2:normalize=0[o]",
         "-map", "[o]", str(D / "check_mix.wav")])
    print(f"CUT_A={cut_a} start={a.start} end={a.end} closing={a.closing} -> {duck}")


if __name__ == "__main__":
    main()
