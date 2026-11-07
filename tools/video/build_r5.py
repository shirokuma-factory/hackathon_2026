"""r5 の組み立て: tools/video/r5_plan.json から、声の長さに合わせて映像を並べた台本 docs/video/edl_r5.json を作る。

r4 までは r3 の時刻に間を足していたが、声を全部作り直したので、区間ごとに頭から並べ直す。
- 区間の長さ = lead + 声の長さ + tail（映像の切れで len の合計の方が長ければそちら）
- 映像の切れ: len（秒） / until（その文字を含む字幕の始まりまで） / fill（区間の残り）
- 素材が足りない切れは、最後のコマを止めて延ばした版（*_hold.mp4）を ffmpeg で作って使う
- 字幕は ElevenLabs の文字ごとの時刻から作り（make_subtitles.cards_for）、docs/video/subs_r5_layout.json の改行と強調を当てる
- 出典の行（画面の一番下）と BGM も作る
- 声は ATEMPO 倍に速める（3 分に収めるため。1.0 なら速めない）

使い方: uv run python tools/video/build_r5.py [--atempo 1.04]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OV = ROOT / "outputs" / "video"
sys.path.insert(0, str(ROOT / "tools" / "video"))
FF = "C:/ai/bin/ffmpeg.exe"
FPS = 30
FADE = 10
XF = 8


def dur(p: Path) -> float:
    r = subprocess.run([FF, "-hide_banner", "-i", str(p)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    import re
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def hold(p: Path, need: float) -> Path:
    """素材が need 秒に足りなければ、最後のコマを止めて延ばした版を作る。"""
    have = dur(p)
    if have >= need - 1e-3:
        return p
    q = p.with_name(p.stem + f"_hold{int(need + 2)}b" + (".mov" if p.suffix == ".mov" else ".mp4"))
    if not q.exists():
        codec = ["-c:v", "qtrle", "-pix_fmt", "argb"] if p.suffix == ".mov" else ["-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p"]
        # 動く図（.mov）は最後に消えるので、消え始める前（終わりの 0.5 秒手前）のコマで止めて延ばす
        cut = have - 0.5 if p.suffix == ".mov" else have
        subprocess.run([FF, "-loglevel", "error", "-y", "-i", str(p), "-vf", f"trim=0:{cut:.3f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={need - cut + 2:.2f}", "-an", *codec, str(q)], check=True)
    return q


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--atempo", type=float, default=1.0)
    ap.add_argument("--kinetic", action="store_true", help="字幕を動く字幕（tools/video/make_kinetic.py）で描く")
    a = ap.parse_args()
    import make_subtitles as ms
    stamp = time.strftime("%H%M%S")
    plan = json.loads((ROOT / "tools/video/r5_plan.json").read_text(encoding="utf-8"))
    shots = {s["id"]: s for s in json.loads((ROOT / "docs/video/shots_r5.json").read_text(encoding="utf-8"))["shots"]}
    vdir = OV / plan["voice_dir"]
    tag = plan["voice_tag"]
    edl = {"timeline": "Backfull_r5", "fps": FPS, "video": {}, "audio": {}}
    V = lambda t: edl["video"].setdefault(str(t), [])
    A = lambda t: edl["audio"].setdefault(str(t), [])
    cards_all, voices, t = [], [], 0.0
    prev_last = None
    for b in plan["blocks"]:
        sid = b["id"]
        if b.get("gap"):
            if prev_last is not None:
                prev_last["fade_out"] = FADE
            if b.get("chapter"):
                V(6).append({"s": round(t * FPS), "L": round(2.4 * FPS), "off": 0, "path": str(OV / b["chapter"])})
            A(3).append({"s": round(max(0, t - 0.2) * FPS), "L": 27, "off": 0, "path": str(OV / "sfx/prepared/whoosh.wav")})
            t += b["gap"]
        if b.get("tr"):
            # 印の線が走るつなぎ（1.0 秒、0.5 秒の所で全面が暗い帯に隠れるので、そこが切れ目）
            V(6).append({"s": round((t - 0.5) * FPS), "L": 30, "off": 0, "path": str(OV / "motion/TR_LINE.mov")})
        # 声（必要なら速める）
        src = vdir / f"{sid}_{tag}.wav"
        if a.atempo != 1.0:
            q = vdir / f"{sid}_{tag}_t{int(a.atempo * 100)}.wav"
            if not q.exists():
                subprocess.run([FF, "-loglevel", "error", "-y", "-i", str(src), "-af", f"atempo={a.atempo}", str(q)], check=True)
            src = q
        vd = dur(src)
        vs = t + b.get("lead", 0.3)
        A(1).append({"s": round(vs * FPS), "L": round(vd * FPS), "off": 0, "path": str(src)})
        voices.append((vs, str(src), 0.0))
        # 字幕（声の時刻から。速めた分は時刻も縮める）
        cs = ms.cards_for(shots[sid], 0.0, tag)
        for c in cs:
            c["start"] = round(vs + c["start"] / a.atempo, 3)
            c["end"] = round(vs + c["end"] / a.atempo, 3)
        cards_all += cs

        def card_t(txt: str) -> float:
            for c in cs:
                if txt in c["text"]:
                    return c["start"]
            raise SystemExit(f"{sid}: 「{txt}」を含む字幕がありません")

        L = b.get("lead", 0.3) + vd + b.get("tail", 0.3)
        # 映像の切れの長さを決める
        pos, pcs = t, []
        for i, pc in enumerate(b["v1"]):
            if "len" in pc:
                ln = pc["len"]
            elif "until" in pc:
                ln = card_t(pc["until"]) - pos
            else:
                ln = None
            pcs.append([pc, ln])
            if ln is not None:
                pos += ln
        fixed_end = pos
        end = max(t + L, fixed_end)
        for x in pcs:
            if x[1] is None:
                x[1] = end - sum(y[1] for y in pcs if y[1] is not None) - t
        pos = t
        first = True
        for pc, ln in pcs:
            p = OV / pc["path"]
            off = pc.get("off", 0.0)
            p = hold(p, off + ln)
            item = {"s": round(pos * FPS), "L": round(ln * FPS), "off": round(off * FPS), "path": str(p), "p": pc.get("p", {})}
            if first and b.get("gap"):
                item["fade_in"] = FADE
            first = False
            V(1).append(item)
            prev_last = item
            pos += ln
        t = pos
        blk_end = t
        for o in b.get("over", []):
            at = o["at"]
            st = (vs - b.get("lead", 0.3)) + at["t"] if "t" in at else card_t(at["card"])
            p = OV / o["path"]
            ln = o.get("len") or (card_t(o["until"]) - st if "until" in o else dur(p))
            ln = min(ln, blk_end - st + 1.0)
            V(o["track"]).append({"s": round(st * FPS), "L": round(ln * FPS), "off": 0, "path": str(p)})
            if o.get("scrim"):
                # 後ろの映像を暗く落とす幕（図の下、V3）。出入りは 0.3 秒のフェード
                V(3).append({"s": round(st * FPS), "L": round(ln * FPS), "off": 0, "path": str(OV / "overlays/scrim.mov"), "fade_in": 9, "fade_out": 9})
        for o in b.get("audio", []):
            at = o["at"]
            st = (vs - b.get("lead", 0.3)) + at["t"] if "t" in at else card_t(at["card"])
            p = OV / o["path"]
            A(o["track"]).append({"s": round(max(0, st) * FPS), "L": round(dur(p) * FPS), "off": 0, "path": str(p)})
    total = t + 0.3
    # 字幕の改行と強調（数が合わなければ自動の改行）
    lay_p = ROOT / "docs/video/subs_r5_layout.json"
    lay = json.loads(lay_p.read_text(encoding="utf-8"))["cards"] if lay_p.exists() else []
    if len(lay) == len(cards_all):
        out = []
        for c, Ls in zip(cards_all, lay):
            pieces = Ls.split("|")
            n = [len(p.replace("*", "").replace("/", "")) for p in pieces]
            tt, span = c["start"], c["end"] - c["start"]
            for p, k in zip(pieces, n):
                e = tt + span * k / sum(n)
                out.append({"shot": c["shot"], "text": p, "start": round(tt, 3), "end": round(e if p is not pieces[-1] else c["end"], 3)})
                tt = e
        cards_all = out
    else:
        print(f"注意: 字幕の割り付け {len(lay)} 枚と字幕 {len(cards_all)} 枚が合わないので、自動の改行にします")
    ms.STYLE = "clean"
    ms.OUT = OV / "subs_r5"
    ms.OUT.mkdir(parents=True, exist_ok=True)
    with open(ms.OUT / "cards.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(cards_all, f, ensure_ascii=False, indent=1)
    sub = ms.OUT / f"subs_{stamp}.mov"
    if a.kinetic:
        subprocess.run(["uv", "run", "python", str(ROOT / "tools/video/make_kinetic.py"), "--cards", str(ms.OUT / "cards.json"),
                        "--total", f"{total:.2f}", "--out", str(sub)], check=True)
    else:
        ms.render(cards_all, total)
        (ms.OUT / "subs.mov").replace(sub)
    V(4).append({"s": 0, "L": round(total * FPS), "off": 0, "path": str(sub)})
    # 出典の行
    smap = json.loads((ROOT / "docs/video/sources_r4_map.json").read_text(encoding="utf-8"))
    sc = []
    for tr in ("1", "5"):
        for d in edl["video"].get(tr, []):
            nm = Path(d["path"]).name
            for k, txt in smap.items():
                if nm.startswith(k):
                    sc.append({"start": d["s"] / FPS, "end": (d["s"] + d["L"]) / FPS, "text": txt})
    sc.sort(key=lambda c: c["start"])
    sp = ms.OUT / "sources_cards.json"
    with open(sp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(sc, f, ensure_ascii=False, indent=1)
    smov = ms.OUT / f"sources_{stamp}.mov"
    subprocess.run(["uv", "run", "python", str(ROOT / "tools/video/make_sources.py"), "--cards", str(sp), "--total", str(total), "--out", str(smov)], check=True)
    V(7).append({"s": 0, "L": round(total * FPS), "off": 0, "path": str(smov)})
    # BGM
    subprocess.run(["uv", "run", "python", str(ROOT / "tools/video/make_bgm.py"), "--db", "-15", "--out", f"bgm_r5_{stamp}.wav",
                    "--total", f"{total:.2f}", "--voice", json.dumps(voices)], check=True)
    A(5).append({"s": 0, "L": round(total * FPS), "off": 0, "path": str(OV / f"bgm/r04/bgm_r5_{stamp}.wav")})
    # Resolve 側の台本の形（s, e, off, path, p, fade_in, fade_out, xfade_out）に直す
    for kind in ("video", "audio"):
        for tr, rows in edl[kind].items():
            rows.sort(key=lambda d: d["s"])
            for i, d in enumerate(rows):
                d["e"] = d["s"] + d.pop("L")
                d.setdefault("p", {})
            if kind == "video" and tr == "1":
                for i, d in enumerate(rows):
                    nxt = rows[i + 1] if i + 1 < len(rows) else None
                    d["xfade_out"] = XF if nxt and 0 <= nxt["s"] - d["e"] <= 1 and not d.get("fade_out") and (d["e"] - d["s"]) > 45 and (nxt["e"] - nxt["s"]) > 45 else 0
    edl["total"] = total
    with open(ROOT / "docs/video/edl_r5.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(edl, f, ensure_ascii=False, indent=1)
    print(f"r5: 合計 {total:.1f} 秒、字幕 {len(cards_all)} 枚 -> docs/video/edl_r5.json")


if __name__ == "__main__":
    main()
