"""組み立ての台本の、透明の層どうしの重なりを機械で洗い出す。

V2〜V7（名札、表示、字幕、図、章カード、出典の行）の各素材を 0.5 秒ごとに縮小して取り出し、
不透明な画素（アルファ > 60）の範囲が、ほかの層と重なる面積を計算する。暗くする幕（scrim）は数えない。
画面の 0.2% 以上が重なる時刻を、層の名前と一緒に出す。

使い方: uv run python tools/video/audit_overlap.py [--edl docs/video/edl_r5.json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
FF = "C:/ai/bin/ffmpeg.exe"
FPS = 30
W, H, STEP = 240, 135, 0.5


def masks(path: str, off: float, L: float) -> np.ndarray:
    r = subprocess.run([FF, "-loglevel", "error", "-ss", f"{off:.3f}", "-t", f"{L:.3f}", "-i", path,
                        "-vf", f"fps={1 / STEP},format=rgba,alphaextract,scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    n = len(a) // (W * H)
    return a[: n * W * H].reshape(n, H, W) > 60


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", default="docs/video/edl_r5.json")
    a = ap.parse_args()
    edl = json.loads((ROOT / a.edl).read_text(encoding="utf-8"))
    total = edl["total"]
    nt = int(total / STEP) + 1
    layers = []  # (名前, トラック, 時刻ごとのマスク dict)
    for tr in sorted(edl["video"], key=int):
        if tr == "1":
            continue
        for d in edl["video"][tr]:
            name = Path(d["path"]).name
            if name.startswith("scrim"):
                continue
            s, L, off = d["s"] / FPS, (d["e"] - d["s"]) / FPS, d["off"] / FPS
            m = masks(d["path"], off, L)
            byt = {}
            for k in range(len(m)):
                ti = int(round((s + k * STEP) / STEP))
                if 0 <= ti < nt:
                    byt[ti] = m[k]
            layers.append((f"V{tr}:{name}@{s:.1f}", tr, byt))
    found = []
    for ti in range(nt):
        act = [(n, tr, b[ti]) for n, tr, b in layers if ti in b and b[ti].any()]
        for i in range(len(act)):
            for j in range(i + 1, len(act)):
                if act[i][1] == act[j][1] and act[i][0] == act[j][0]:
                    continue
                ov = (act[i][2] & act[j][2]).mean()
                if ov >= 0.002:
                    found.append((ti * STEP, act[i][0], act[j][0], ov))
    # 同じ組の続きはまとめる
    merged = []
    for t, x, y, ov in found:
        if merged and merged[-1][2] == x and merged[-1][3] == y and t - merged[-1][1] <= STEP + 1e-6:
            merged[-1][1] = t
            merged[-1][4] = max(merged[-1][4], ov)
        else:
            merged.append([t, t, x, y, ov])
    for t0, t1, x, y, ov in merged:
        print(f"{t0:6.1f}〜{t1:6.1f} 秒  {x}  ×  {y}  最大 {ov * 100:.1f}%")
    print(f"重なり {len(merged)} か所")


if __name__ == "__main__":
    main()
