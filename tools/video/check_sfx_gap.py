"""組み立ての台本（EDL）で、場面の切り替えの効果音や章の見出しが終わる前に声が始まっていないかを調べる。

作成者の指摘（11/7）: 「場面が変わるところでの効果音が終わらないうちに次の説明に入っている」。
調べるもの: A3（線・章・ロゴの音）の各音の終わりと、V6（章の見出し・線）の見出しが読める時間（見出しは出始めから 1.4 秒）。
その間に A1（声）が始まっていれば、何秒早いかを出す。

使い方: uv run python tools/video/check_sfx_gap.py --edl docs/video/edl_r10.json
"""
import argparse
import json
from pathlib import Path

FPS = 30


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edl", required=True)
    ap.add_argument("--margin", type=float, default=0.1, help="音の終わりから声までに空ける秒")
    a = ap.parse_args()
    d = json.loads(Path(a.edl).read_text(encoding="utf-8"))
    voices = sorted(r["s"] / FPS for r in d["audio"]["1"])
    busy = []
    for r in d["audio"].get("3", []) + [x for x in d["audio"].get("4", []) if "chime" in x["path"]]:
        busy.append((r["s"] / FPS, r["e"] / FPS, Path(r["path"]).name))
    for r in d["video"].get("6", []):
        nm = Path(r["path"]).name
        if nm.startswith("CHM"):
            busy.append((r["s"] / FPS, r["s"] / FPS + 1.4, nm))
    bad = 0
    for s, e, nm in sorted(busy):
        for v in voices:
            if s - 0.05 <= v < e + a.margin:
                bad += 1
                print(f"{v:7.2f} 声が始まる  {nm} は {s:.2f}〜{e:.2f}（{e + a.margin - v:.2f} 秒早い）")
    print(f"重なり {bad} 件")


if __name__ == "__main__":
    main()
