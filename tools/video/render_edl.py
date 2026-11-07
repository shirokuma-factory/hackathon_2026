"""組み立ての台本（docs/video/edl_r5.json）を、Resolve を使わずに ffmpeg で書き出す。

Resolve が別の作業で使われているときでも書き出せるようにするための道具。重ね方は Resolve と同じで、
番号の大きい映像トラックほど上（V1 < V2 < … < V7）。音は全トラックを足し合わせる。

対応する設定: ZoomX/ZoomY（拡大縮小）、Pan/Tilt（右・上へずらす）、Opacity（不透明度）、
fade_in/fade_out（V1 は黒へのフェード、ほかは透明へのフェード）。重ねる切り替え（xfade_out）は扱わない。

使い方: uv run python tools/video/render_edl.py --edl docs/video/edl_r5.json --out outputs/video/assembly/x.mp4 [--height 720]

高解像度で組んで縮める（スーパーサンプリング）:
  --canvas 3840x2160   内部の作業用キャンバス。映像はすべてこの大きさに合わせる（1920x1080 の素材は lanczos で拡大、
                       3840x2160 の素材はそのまま）。EDL の Zoom/Pan/Tilt は 1920 幅の単位なので、キャンバス幅/1920 倍する。
  --out-height 1080    最後に lanczos で縮める高さ。ごく弱いシャープ（明るさだけ、量 0.3）を掛ける。
  --prefer-hd          各映像の隣に <名前>@2x.<拡張子> か <名前>_4k.<拡張子> があれば、そちらを自動で使う。
  --duration 秒        試験用に、頭から指定の秒数だけ書き出す。
  --canvas か --out-height を付けると H.264 high、crf 14、preset slow で書く（付けなければ従来どおり）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FF = "C:/ai/bin/ffmpeg.exe"
FPS = 30


def hd_sibling(path: str) -> str:
    """<名前>@2x.<拡張子> か <名前>_4k.<拡張子> が同じフォルダにあればそれを返す。なければ元のまま。"""
    p = Path(path)
    stems = [p.stem]
    if p.stem.endswith("_h"):
        stems.append(p.stem[:-2])  # F_POST_h.mp4 の 4K 版は F_POST_4k.mp4
    for st in stems:
        for tag in ("@2x", "_4k"):
            q = p.with_name(st + tag + p.suffix)
            if q.exists():
                return str(q)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", default="docs/video/edl_r5.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--canvas", default=None, help="内部キャンバス 例 3840x2160（既定は 1920x1080）")
    ap.add_argument("--out-height", type=int, default=None, help="最後に縮める高さ（lanczos と弱いシャープ）")
    ap.add_argument("--prefer-hd", action="store_true", help="隣に @2x / _4k の版があれば使う")
    ap.add_argument("--duration", type=float, default=None, help="試験用: 頭からこの秒数だけ書き出す")
    a = ap.parse_args()
    CW, CH = (int(x) for x in a.canvas.lower().split("x")) if a.canvas else (1920, 1080)
    hd_mode = a.canvas is not None or a.out_height is not None
    out_h = a.out_height or a.height
    k = CW / 1920  # EDL の Pan/Tilt は 1920 幅の単位
    edl = json.loads((ROOT / a.edl).read_text(encoding="utf-8"))
    total = edl["total"] if a.duration is None else min(edl["total"], a.duration)
    limit = float("inf") if a.duration is None else total * FPS
    used_hd = 0
    args, fl, n = [], [], 0
    fl.append(f"color=c=black:s={CW}x{CH}:r={FPS}:d={total:.3f},format=yuva420p[base]")
    cur = "base"
    for tr in sorted(edl["video"], key=int):
        for d in edl["video"][tr]:
            if d["s"] >= limit:
                continue
            s, L, off = d["s"] / FPS, (d["e"] - d["s"]) / FPS, d["off"] / FPS
            clipped = d["e"] > limit
            if clipped:
                L = (limit - d["s"]) / FPS
            path = hd_sibling(d["path"]) if a.prefer_hd else d["path"]
            used_hd += path != d["path"]
            args += ["-ss", f"{off:.3f}", "-t", f"{L + 0.05:.3f}", "-i", path]
            p = d.get("p") or {}
            chain = [f"[{n}:v]fps={FPS}", "format=yuva420p", f"trim=duration={L:.3f}", "setpts=PTS-STARTPTS"]
            z = float(p.get("ZoomX") or 1.0)
            fl_scale = ":flags=lanczos" if hd_mode else ""
            if abs(z - 1.0) > 1e-6:
                w, h = int(CW * z) // 2 * 2, int(CH * z) // 2 * 2
                chain.append(f"scale={w}:{h}{fl_scale}")
                px, py = (CW - w) / 2 + float(p.get("Pan") or 0) * k, (CH - h) / 2 - float(p.get("Tilt") or 0) * k
                if z < 1:
                    chain.append(f"pad={CW}:{CH}:{px:.0f}:{py:.0f}:color=black@0")
                else:
                    chain.append(f"crop={CW}:{CH}:{-px:.0f}:{-py:.0f}")
            elif hd_mode:
                chain.append(f"scale={CW}:{CH}:flags=lanczos")  # 同じ大きさなら何もしない
            op = p.get("Opacity")
            if op is not None and float(op) < 100:
                chain.append(f"colorchannelmixer=aa={float(op) / 100:.3f}")
            fi, fo = d.get("fade_in", 0) / FPS, d.get("fade_out", 0) / FPS
            alpha = "" if tr == "1" else ":alpha=1"
            if fi:
                chain.append(f"fade=t=in:st=0:d={fi:.3f}{alpha}")
            if fo and not clipped:
                chain.append(f"fade=t=out:st={L - fo:.3f}:d={fo:.3f}{alpha}")
            chain.append(f"setpts=PTS+{s:.3f}/TB")
            fl.append(",".join(chain) + f"[v{n}]")
            fl.append(f"[{cur}][v{n}]overlay=eof_action=pass:enable='between(t,{s:.3f},{s + L:.3f})'[o{n}]")
            cur = f"o{n}"
            n += 1
    aud = []
    for tr in sorted(edl["audio"], key=int):
        for d in edl["audio"][tr]:
            if d["s"] >= limit:
                continue
            s, L, off = d["s"] / FPS, (min(d["e"], limit) - d["s"]) / FPS, d["off"] / FPS
            args += ["-ss", f"{off:.3f}", "-t", f"{L:.3f}", "-i", d["path"]]
            ms = int(round(s * 1000))
            g = d.get("gain_db", 0.0)
            fl.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={g}dB,adelay={ms}|{ms}[a{n}]")
            aud.append(f"[a{n}]")
            n += 1
    fl.append("".join(aud) + f"amix=inputs={len(aud)}:normalize=0,apad=whole_dur={total:.3f}[aout]")
    vout = f"[{cur}]"
    if hd_mode:
        # 縮めるときに明るさだけごく弱くシャープ（量 0.3、色は掛けない）。縮めない場合は掛けない
        post = f"scale=-2:{out_h}:flags=lanczos"
        if out_h < CH:
            post += ",unsharp=5:5:0.3:5:5:0.0"
        fl.append(f"[{cur}]{post},format=yuv420p[vs]")
        vout = "[vs]"
    elif a.height != 1080:
        fl.append(f"[{cur}]scale=-2:{a.height}[vs]")
        vout = "[vs]"
    script = ROOT / "outputs/video/assembly/_render_edl_filter.txt"
    script.write_text(";\n".join(fl), encoding="utf-8")
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", *args, "-filter_complex_script", str(script),
           "-map", vout, "-map", "[aout]", "-t", f"{total:.3f}", "-r", str(FPS),
           *(["-c:v", "libx264", "-profile:v", "high", "-preset", "slow", "-crf", "14"] if hd_mode else
             ["-c:v", "libx264", "-preset", "medium", "-crf", "18" if a.height >= 1080 else "22"]), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "256k", a.out]
    subprocess.run(cmd, check=True)
    print(f"書き出し: {a.out}（{total:.1f} 秒、入力 {n} 本、高解像度の版を使った映像 {used_hd} 本）")


if __name__ == "__main__":
    main()
