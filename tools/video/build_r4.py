"""r3 のタイムライン（docs/video/edl_r3.json）に「間」を入れ、素材を差し替えた r4 の台本を作る。

- BREAKS の位置（r3 の秒）に、指定の長さの間を足す。それより後のものは全部うしろへずれる。
- 章カード（V6）は、間の始まりに置く（間のあいだ、カードと音楽だけになる）。
- V1 で間の前後のクリップには、映像のフェード（暗くして明るく）を付ける。章の中の切り替えは重ねて切り替える。
- 素材の差し替えは tools/video/r4_replace.json（古いパスの末尾 → 新しいパスと頭の切り）。
- 字幕は subs_r3c/cards.json を区間ごとに同じだけずらして描き直す（make_subtitles.render を使う）。
- BGM は新しい声の位置と長さで tools/video/make_bgm.py を呼んで作り直す。

使い方:
  uv run python tools/video/build_r4.py            # docs/video/edl_r4.json、字幕、BGM を作る
出力: docs/video/edl_r4.json（Resolve で組むための台本）、outputs/video/subs_r4/subs.mov、outputs/video/bgm/r04/bgm_r4.wav
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "video"))
FPS = 30
# (r3 の秒, 足す秒)
BREAKS = [(18.0, 0.6), (46.5, 0.8), (58.5, 1.2), (94.5, 1.2), (114.0, 1.2), (133.5, 0.6), (154.0, 1.2)]
R3_TOTAL = 170.0
TOTAL = R3_TOTAL + sum(g for _, g in BREAKS)
FADE = 10   # 間の前後の映像のフェード（コマ）
XF = 8      # 章の中の重ねる切り替え（コマ）


def shift(sec: float) -> float:
    return sec + sum(g for b, g in BREAKS if sec >= b - 1e-6)


def fshift(fr: int) -> int:
    return int(round(shift(fr / FPS) * FPS))


def gap_before(sec: float) -> tuple[float, float] | None:
    """sec の直前（0.5 秒以内）にある間 (r3 の位置, 長さ)。"""
    for b, g in BREAKS:
        if 0 <= sec - b <= 0.5:
            return b, g
    return None


def main() -> None:
    stamp = time.strftime("%H%M%S")  # Resolve に読み込み済みのファイルを上書きしないよう、毎回新しい名前にする
    edl = json.loads((ROOT / "docs/video/edl_r3.json").read_text(encoding="utf-8"))
    rep_p = ROOT / "tools/video/r4_replace.json"
    rep = json.loads(rep_p.read_text(encoding="utf-8")) if rep_p.exists() else {}
    slots_p = ROOT / "tools/video/r4_slots.json"
    slots = {float(k): v for k, v in json.loads(slots_p.read_text(encoding="utf-8")).items() if not k.startswith("_")} if slots_p.exists() else {}
    out = {"timeline": "Backfull_r4", "fps": FPS, "total": TOTAL, "video": {}, "audio": {}}
    for kind in ("video", "audio"):
        for t, rows in edl[kind].items():
            # V1 の区間を、r4_slots.json の切れに置き換える
            if kind == "video" and t == "1":
                exp = []
                for d in rows:
                    key = next((k for k in slots if abs(k - d["s"] / FPS) < 0.02), None)
                    if key is None:
                        exp.append(d)
                        continue
                    s0 = d["s"]
                    for pc in slots[key]:
                        L = int(round(pc["len"] * FPS))
                        exp.append({"s": s0, "e": s0 + L, "off": int(round(pc["off"] * FPS)), "path": pc["path"], "p": pc.get("p", {})})
                        s0 += L
                rows = exp
            new = []
            for d in rows:
                d = dict(d)
                path = d["path"] or ""
                for k, v in rep.items():
                    if path.replace("\\", "/").endswith(k):
                        path = v["path"]
                        if "off" in v:
                            d["off"] = v["off"]
                        if "len" in v:
                            d["e"] = d["s"] + v["len"]
                d["path"] = path
                if kind == "video" and t == "1":
                    for k, v in json.loads(slots_p.read_text(encoding="utf-8")).get("_p", {}).items():
                        if abs(float(k) - d["s"] / FPS) < 0.02:
                            d["p"] = {**(d.get("p") or {}), **v}
                if kind == "video" and t == "3":
                    v3 = json.loads(slots_p.read_text(encoding="utf-8")).get("_v3", {})
                    for k, v in v3.items():
                        if abs(float(k) - d["s"] / FPS) < 0.02:
                            d["path"] = v
                L = d["e"] - d["s"]
                if kind == "video" and t == "6" and gap_before(d["s"] / FPS):
                    b, g = gap_before(d["s"] / FPS)
                    d["s"] = fshift(d["s"]) - int(round(g * FPS))
                elif kind == "audio" and t == "1":
                    # 声は、間の直前（0.2 秒以内）に始まるものも間のあとへ送る
                    d["s"] = int(round((shift(d["s"] / FPS + 0.2) - 0.2) * FPS))
                else:
                    d["s"] = fshift(d["s"])
                d["e"] = d["s"] + L
                new.append(d)
            out[kind][t] = new
    # V1: 間の前後のフェードと、すき間なく続くクリップの重ねる切り替え
    v1 = out["video"]["1"]
    for i, d in enumerate(v1):
        nxt = v1[i + 1] if i + 1 < len(v1) else None
        d["fade_out"] = FADE if nxt is not None and nxt["s"] - d["e"] >= 6 else 0
        prv = v1[i - 1] if i > 0 else None
        d["fade_in"] = FADE if prv is not None and d["s"] - prv["e"] >= 6 else 0
        d["xfade_out"] = XF if nxt is not None and 0 <= nxt["s"] - d["e"] <= 1 and (d["e"] - d["s"]) > 45 and (nxt["e"] - nxt["s"]) > 45 else 0
    # 字幕と BGM は作り直したものに置き換える
    out["video"]["4"] = [{"s": 0, "e": int(TOTAL * FPS), "off": 0, "path": str(ROOT / f"outputs/video/subs_r4/subs_{stamp}.mov"), "p": {}}]
    out["audio"]["5"] = [{"s": 0, "e": int(TOTAL * FPS), "off": 0, "path": str(ROOT / f"outputs/video/bgm/r04/bgm_r4_{stamp}.wav")}]
    with open(ROOT / "docs/video/edl_r4.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    old_a1 = edl["audio"]["1"]
    delta = {Path(o["path"]).name.split("_")[0].split(".")[0]: (n["s"] - o["s"]) / FPS for o, n in zip(old_a1, out["audio"]["1"])}
    regen = {}
    for o, n in zip(old_a1, out["audio"]["1"]):
        nm = Path(n["path"]).name
        if nm.endswith("_r4.wav"):
            regen[nm.split("_")[0]] = (n["s"] - n["off"]) / FPS
    subs(delta, regen)
    shutil.copyfile(ROOT / "outputs/video/subs_r4/subs.mov", ROOT / f"outputs/video/subs_r4/subs_{stamp}.mov")
    voice = [(d["s"] / FPS, d["path"], d["off"] / FPS) for d in out["audio"]["1"]]
    subprocess.run(["uv", "run", "python", str(ROOT / "tools/video/make_bgm.py"), "--db", "-15", "--out", f"bgm_r4_{stamp}.wav",
                    "--total", str(TOTAL), "--voice", json.dumps(voice)], check=True)
    print(f"r4: 合計 {TOTAL:.1f} 秒 -> docs/video/edl_r4.json")


def subs(delta: dict[str, float], regen: dict[str, float]) -> None:
    """字幕は区間（shot）ごとに、その区間の声と同じだけずらす。声を作り直した区間（regen）は、新しい声の時刻から作る。"""
    import make_subtitles as ms
    old = json.loads((ROOT / "outputs/video/subs_r3c/cards.json").read_text(encoding="utf-8"))
    shots = {x["id"]: x for x in json.loads((ROOT / "docs/video/shots_r4.json").read_text(encoding="utf-8"))["shots"]}
    cards, done = [], set()
    for c in old:
        sid = c["shot"]
        if sid in regen:
            if sid not in done:
                cards += ms.cards_for(shots[sid], regen[sid], "r4")
                done.add(sid)
            continue
        d = delta[sid]
        c["start"] = round(c["start"] + d, 3)
        c["end"] = round(c["end"] + d, 3)
        cards.append(c)
    # 改行と強調の割り付け（docs/video/subs_r4_layout.json）。| で分けた字幕は、時間を字数で分ける
    lay = json.loads((ROOT / "docs/video/subs_r4_layout.json").read_text(encoding="utf-8"))["cards"]
    if len(lay) != len(cards):
        raise SystemExit(f"字幕の割り付けが {len(lay)} 枚で、字幕 {len(cards)} 枚と合いません")
    out = []
    for c, L in zip(cards, lay):
        pieces = L.split("|")
        n = [len(p.replace("*", "").replace("/", "")) for p in pieces]
        t, span = c["start"], c["end"] - c["start"]
        for p, k in zip(pieces, n):
            e = t + span * k / sum(n)
            out.append({"shot": c["shot"], "text": p, "start": round(t, 3), "end": round(e if p is not pieces[-1] else c["end"], 3)})
            t = e
    cards = out
    ms.STYLE = "clean"
    ms.OUT = ROOT / "outputs/video/subs_r4"
    ms.OUT.mkdir(parents=True, exist_ok=True)
    with open(ms.OUT / "cards.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(cards, f, ensure_ascii=False, indent=1)
    ms.render(cards, TOTAL)


if __name__ == "__main__":
    main()
