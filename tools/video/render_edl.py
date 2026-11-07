"""組み立ての台本（docs/video/edl_r5.json）を、Resolve を使わずに ffmpeg で書き出す。

Resolve が別の作業で使われているときでも書き出せるようにするための道具。重ね方は Resolve と同じで、
番号の大きい映像トラックほど上（V1 < V2 < … < V7）。音は全トラックを足し合わせる。

対応する設定: ZoomX/ZoomY（拡大縮小）、Pan/Tilt（右・上へずらす）、Opacity（不透明度）、
fade_in/fade_out（V1 は黒へのフェード、ほかは透明へのフェード）。重ねる切り替え（xfade_out）は扱わない。

使い方: uv run python tools/video/render_edl.py --edl docs/video/edl_r5.json --out outputs/video/assembly/x.mp4 [--height 720]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FF = "C:/ai/bin/ffmpeg.exe"
FPS = 30


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", default="docs/video/edl_r5.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--height", type=int, default=1080)
    a = ap.parse_args()
    edl = json.loads((ROOT / a.edl).read_text(encoding="utf-8"))
    total = edl["total"]
    args, fl, n = [], [], 0
    fl.append(f"color=c=black:s=1920x1080:r={FPS}:d={total:.3f},format=yuva420p[base]")
    cur = "base"
    for tr in sorted(edl["video"], key=int):
        for d in edl["video"][tr]:
            s, L, off = d["s"] / FPS, (d["e"] - d["s"]) / FPS, d["off"] / FPS
            args += ["-ss", f"{off:.3f}", "-t", f"{L + 0.05:.3f}", "-i", d["path"]]
            p = d.get("p") or {}
            chain = [f"[{n}:v]fps={FPS}", "format=yuva420p", f"trim=duration={L:.3f}", "setpts=PTS-STARTPTS"]
            z = float(p.get("ZoomX") or 1.0)
            if abs(z - 1.0) > 1e-6:
                w, h = int(1920 * z) // 2 * 2, int(1080 * z) // 2 * 2
                chain.append(f"scale={w}:{h}")
                px, py = (1920 - w) / 2 + float(p.get("Pan") or 0), (1080 - h) / 2 - float(p.get("Tilt") or 0)
                if z < 1:
                    chain.append(f"pad=1920:1080:{px:.0f}:{py:.0f}:color=black@0")
                else:
                    chain.append(f"crop=1920:1080:{-px:.0f}:{-py:.0f}")
            op = p.get("Opacity")
            if op is not None and float(op) < 100:
                chain.append(f"colorchannelmixer=aa={float(op) / 100:.3f}")
            fi, fo = d.get("fade_in", 0) / FPS, d.get("fade_out", 0) / FPS
            alpha = "" if tr == "1" else ":alpha=1"
            if fi:
                chain.append(f"fade=t=in:st=0:d={fi:.3f}{alpha}")
            if fo:
                chain.append(f"fade=t=out:st={L - fo:.3f}:d={fo:.3f}{alpha}")
            chain.append(f"setpts=PTS+{s:.3f}/TB")
            fl.append(",".join(chain) + f"[v{n}]")
            fl.append(f"[{cur}][v{n}]overlay=eof_action=pass:enable='between(t,{s:.3f},{s + L:.3f})'[o{n}]")
            cur = f"o{n}"
            n += 1
    aud = []
    for tr in sorted(edl["audio"], key=int):
        for d in edl["audio"][tr]:
            s, L, off = d["s"] / FPS, (d["e"] - d["s"]) / FPS, d["off"] / FPS
            args += ["-ss", f"{off:.3f}", "-t", f"{L:.3f}", "-i", d["path"]]
            ms = int(round(s * 1000))
            fl.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{n}]")
            aud.append(f"[a{n}]")
            n += 1
    fl.append("".join(aud) + f"amix=inputs={len(aud)}:normalize=0,apad=whole_dur={total:.3f}[aout]")
    vout = f"[{cur}]"
    if a.height != 1080:
        fl.append(f"[{cur}]scale=-2:{a.height}[vs]")
        vout = "[vs]"
    script = ROOT / "outputs/video/assembly/_render_edl_filter.txt"
    script.write_text(";\n".join(fl), encoding="utf-8")
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", *args, "-filter_complex_script", str(script),
           "-map", vout, "-map", "[aout]", "-t", f"{total:.3f}", "-r", str(FPS),
           "-c:v", "libx264", "-preset", "medium", "-crf", "18" if a.height >= 1080 else "22", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "256k", a.out]
    subprocess.run(cmd, check=True)
    print(f"書き出し: {a.out}（{total:.1f} 秒、入力 {n} 本）")


if __name__ == "__main__":
    main()
