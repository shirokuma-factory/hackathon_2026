"""組み立ての台本（EDL）が使っている動く図のうち、docs/video/motion/ に HTML があるものを --scale 2 で撮り直す。

出力: outputs/video/motion/<名前>@2x.mov（3840x2160、qtrle argb）。render_edl.py の --prefer-hd が自動で拾う。
- 名前 = ファイル名の幹（stem）。<名前>_hold<数>b（最後のコマを止めて延ばした版）は、元の図の @2x から同じ長さで作り直す。
- すでに @2x があり、HTML より新しいものは飛ばす（--force で全部撮り直す）。
- 台本にない図や、HTML が無い動画（録画・素材）は触らない。

使い方:
  uv run python tools/video/render_hd_assets.py --edl docs/video/edl_r6.json [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs" / "video" / "motion"
FF = "C:/ai/bin/ffmpeg.exe"
HOLD = re.compile(r"^(?P<base>.+)_hold\d+b$")


def dur(p: Path) -> float:
    r = subprocess.run([FF, "-hide_banner", "-i", str(p)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def fresh(q: Path, *srcs: Path) -> bool:
    return q.exists() and all(q.stat().st_mtime >= s.stat().st_mtime for s in srcs if s.exists())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", default="docs/video/edl_r6.json")
    ap.add_argument("--force", action="store_true", help="新しくても撮り直す")
    ap.add_argument("--dry-run", action="store_true", help="何を撮るかだけ表示する")
    a = ap.parse_args()
    edl = json.loads((ROOT / a.edl).read_text(encoding="utf-8"))
    paths = sorted({Path(d["path"]) for tr in edl["video"].values() for d in tr if d["path"].lower().endswith(".mov")})
    bases: dict[str, Path] = {}   # 名前 -> HTML
    holds: list[tuple[str, Path, str]] = []  # (元の名前, 1080 版のパス, hold の幹)
    skipped = []
    for p in paths:
        stem = p.stem
        m = HOLD.match(stem)
        base = m.group("base") if m else stem
        html = SRC / f"{base}.html"
        if not html.exists() or p.parent.name != "motion":
            skipped.append(p.name)
            continue
        bases[base] = html
        if m:
            holds.append((base, p, stem))
    todo = [n for n, h in bases.items() if a.force or not fresh((ROOT / "outputs/video/motion" / f"{n}@2x.mov"), h)]
    print(f"動く図 {len(bases)} 本、撮り直す {len(todo)} 本、HTML が無くて飛ばす {len(skipped)} 本: {', '.join(skipped) or '-'}")
    if a.dry_run:
        print("撮る:", " ".join(todo))
        return 0
    if todo:
        subprocess.run([sys.executable, str(ROOT / "tools/video/render_motion.py"), "--scale", "2", *todo], check=True)
    mo = ROOT / "outputs/video/motion"
    for base, p1080, stem in holds:
        q = mo / f"{stem}@2x.mov"
        b2 = mo / f"{base}@2x.mov"
        if not a.force and fresh(q, b2):
            continue
        have, want = dur(b2), dur(p1080)
        cut = min(have - 0.5, want)  # 台本の 1080 版が元の図より短いこともある（その場合は延ばさず切るだけ）
        subprocess.run([FF, "-loglevel", "error", "-y", "-i", str(b2), "-vf",
                        f"trim=0:{cut:.3f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={max(0.0, want - cut):.2f}",
                        "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(q)], check=True)
        print(f"保存: {q}  ({want:.2f} 秒、元の図 {have:.2f} 秒を {cut:.2f} 秒で止めて延ばした)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
