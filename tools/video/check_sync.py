"""書き出した動画で、声と画面のずれ・声より先に終わる映像を機械で調べる（r20〜、毎回の書き出しのあとに必ず回す）。

作成者の指示（11/8）: 撮ったものが出力に入り、意図どおりに動くかを毎回確かめる。
話し終わる前に終わる映像や、内容とナレーションのずれは「強く禁止」（品質を大きく下げる）。

調べること（声の時刻は、書き出した mp4 を文字に起こした review_pack.py の transcript.json を使う）:
  E1 声のクリップが切れていない（EDL の長さ ≥ WAV の長さ）、文字に起こした文字数が台本の 85% 以上
  E2 各ブロックの画面（V1）が、そのブロックの最後の言葉が終わってから 0.15 秒以上あとまで続く
  E3 V1 の切り替えが、言葉の途中（1 文字の発音の中）に入らない（注意。日本語は 1 文字ずつ時刻が付くので、話している最中の切り替えは全部出る）
  E4 図（モーション）が描き終わる前に切れない（LOGO は描き終わり、他は元の長さ − 0.5 秒）
  E5 上に重ねた図（5 番）が、文の途中で消えない（消える瞬間に声が続き、次の図がすぐ来ない）
  E6 字幕が、声が続いている間に消えて次の字幕が 0.3 秒以上来ない
  E7 最後の言葉のあと 1.0 秒以上の余韻がある
  E8 0.3 秒以上の黒い画面がない（意図したものは --allow-black 開始秒,終了秒 で除く）
出力: <out>/sync_check.md（誤りの一覧と、切り替えごとの前後の言葉の表）。誤りがあれば終了コード 1。

使い方: uv run python tools/video/check_sync.py --mp4 outputs/video/assembly/Backfull_r20.mp4 \
    --edl docs/video/edl_r20.json --plan tools/video/r20_plan.json --review outputs/video/review/r20
"""
import argparse
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FF = "C:/ai/bin/ffmpeg.exe"
FPS = 30
# 描き終わる時刻（秒、クリップの中の時刻）。書いていない図は元の長さ − 0.5 秒
COMPLETE = {"LOGO_F": 2.5, "LOGO_B": 3.7, "CLAUDE_HUB_m": 7.0, "FLOW_KAGO": 5.0}
# 背景として流し続ける図（次のブロックで続きを足す。描き終わりを問わない）
AMBIENT = ("FLOW_JP",)


def dur_media(p: Path) -> float:
    r = subprocess.run([FF, "-hide_banner", "-i", str(p)], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mp4", required=True)
    ap.add_argument("--edl", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--review", required=True, help="review_pack.py の出力（transcript.json）")
    ap.add_argument("--allow-black", action="append", default=[], help="意図した黒（開始秒,終了秒）")
    a = ap.parse_args()
    edl = json.loads((ROOT / a.edl).read_text(encoding="utf-8"))
    plan = json.loads((ROOT / a.plan).read_text(encoding="utf-8"))
    shots = {s["id"]: s for s in json.loads((ROOT / plan["shots"]).read_text(encoding="utf-8"))["shots"]}
    words = [w for w in json.loads((Path(a.review) / "transcript.json").read_text(encoding="utf-8"))["words"]
             if w.get("type") == "word" and w["text"].strip() and not re.fullmatch(r"[、。，．,.!?！？…・\s]+", w["text"])]
    # 文字起こしは英語の語（BackFull）の前の無音や句点まで 1 語に含め、始まりが 1〜2 秒早く付くことがある。
    # 英字を含む語は、終わりから 1 文字 0.08 秒（最大 0.8 秒）で始まりを付け直す
    for w in words:
        n = len(re.findall(r"[A-Za-z]", w["text"]))
        if n:
            w["start"] = max(w["start"], w["end"] - min(0.8, 0.08 * n))
    total = edl["total"]
    errs: list[str] = []
    warn: list[str] = []

    def said(t0: float, t1: float) -> str:
        return "".join(w["text"] for w in words if w["end"] > t0 and w["start"] < t1)

    def speaking(t: float, pad: float = 0.0) -> bool:
        return any(w["start"] + pad < t < w["end"] - pad for w in words)

    # 声のクリップとブロック
    blocks = plan["blocks"]
    vrows = sorted(edl["audio"].get("1", []), key=lambda d: d["s"])
    bid_of = {}
    for d in vrows:
        m = re.match(r"([A-Z]+\d*)_", Path(d["path"]).name)
        bid_of[m.group(1) if m else "?"] = d
    span = {}
    for b in blocks:
        d = bid_of.get(b["id"])
        if d is None:
            errs.append(f"E1 {b['id']}: 声のクリップが EDL にありません")
            continue
        s0, s1 = d["s"] / FPS, d["e"] / FPS
        p = ROOT / d["path"]
        if p.suffix == ".wav":
            with wave.open(str(p)) as w_:
                wl = w_.getnframes() / w_.getframerate()
            if s1 - s0 < wl - 0.05:
                errs.append(f"E1 {b['id']}: 声のクリップが {wl - (s1 - s0):.2f} 秒切れている（EDL {s1 - s0:.2f} 秒、WAV {wl:.2f} 秒）")
        # 次の声のクリップが始まったら、そこからは次のブロックの言葉（WAV の末尾の無音が次と重なる）
        nxt_s = min((x["s"] / FPS for x in vrows if x["s"] > d["s"]), default=1e9)
        ws = [w for w in words if s0 - 0.05 <= w["start"] < min(s1 + 0.05, nxt_s - 0.02) and w["end"] <= nxt_s + 0.05]
        if not ws:
            errs.append(f"E1 {b['id']}: {s0:.2f}〜{s1:.2f} 秒に声の文字がありません")
            continue
        # 文字起こしは英語の語（backfull など）を 1 語にまとめ、前のブロックにまたがることがあるので、次の声の始まりで止める
        span[b["id"]] = (ws[0]["start"], min(ws[-1]["end"], nxt_s))
        n_script = len(re.sub(r"[\s、。「」…・,.!?！？]", "", shots[b["id"]]["narration"]))
        n_heard = len("".join(w["text"] for w in ws))
        if n_heard < 0.85 * n_script * 0.8:  # 数字や英語は文字数が変わるので緩める
            errs.append(f"E1 {b['id']}: 聞き取れた文字が少ない（{n_heard} / 台本 {n_script}）。声が切れていないか")

    # V1 をブロックに割り当てる（プランの v1 の順に 1 本ずつ）
    v1 = sorted(edl["video"].get("1", []), key=lambda d: d["s"])
    need = sum(len(b["v1"]) for b in blocks)
    if len(v1) != need:
        warn.append(f"V1 の本数 {len(v1)} とプランの切れ {need} が合わないので、E2 はブロックの境の時刻で調べます")
    i = 0
    events = []
    for b in blocks:
        rows = v1[i:i + len(b["v1"])]
        i += len(b["v1"])
        if not rows or b["id"] not in span:
            continue
        end_v = rows[-1]["e"] / FPS
        last_word = span[b["id"]][1]
        if end_v < last_word + 0.15 and end_v < total - 0.01:
            errs.append(f"E2 {b['id']}: 画面 {Path(rows[-1]['path']).name} が {end_v:.2f} 秒で終わるが、声は {last_word:.2f} 秒まで（「{said(end_v - 0.6, last_word)}」）")
        for d in rows:
            nm = Path(d["path"]).name
            st, en = d["s"] / FPS, d["e"] / FPS
            events.append((st, f"V1 {b['id']} {nm} 始まり"))
            stem = re.sub(r"(_hold\w*|_4k|@2x)", "", Path(nm).stem)
            if d["path"].startswith("outputs/video/motion/") or "/motion/" in d["path"]:
                src = ROOT / d["path"]
                full = dur_media(src)
                done = next((v for k, v in COMPLETE.items() if stem.startswith(k)), full - 0.5)
                used_to = d.get("off", 0) / FPS + (en - st)
                if "_hold" not in nm and not stem.startswith(AMBIENT) and used_to < done - 0.05:
                    errs.append(f"E4 {b['id']}: {nm} を {used_to:.2f} 秒で切っている（描き終わりは {done:.2f} 秒）")
            if speaking(st, 0.03) and st > 0.1:
                warn.append(f"E3 {b['id']}: {st:.2f} 秒の切り替えが言葉の途中（「{said(st - 0.3, st + 0.3)}」）")

    # 上に重ねた図
    ov = sorted(edl["video"].get("5", []), key=lambda d: d["s"])
    for k, d in enumerate(ov):
        st, en = d["s"] / FPS, d["e"] / FPS
        nm = Path(d["path"]).name
        events.append((st, f"図 {nm} 始まり"))
        events.append((en, f"図 {nm} 終わり"))
        nxt = ov[k + 1]["s"] / FPS if k + 1 < len(ov) else 1e9
        if speaking(en, 0.0) and nxt - en > 0.1:
            # 文の途中か（次の 0.3 秒にも声がある）
            if said(en, en + 0.3):
                errs.append(f"E5 {nm}: {en:.2f} 秒に消えるが声が続いている（「{said(en - 0.4, en + 0.6)}」）")
        if "/motion/" in d["path"]:
            full = dur_media(ROOT / d["path"])
            used_to = d.get("off", 0) / FPS + (en - st)
            if used_to < min(full - 0.5, 3.0) and "scrim" not in nm:
                warn.append(f"E4 図 {nm}: 元 {full:.1f} 秒のうち {used_to:.2f} 秒だけ使っている")

    # 字幕
    cards_p = ROOT / "outputs/video" / f"subs_{plan['name']}" / "cards.json"
    if cards_p.exists():
        cards = sorted(json.loads(cards_p.read_text(encoding="utf-8")), key=lambda c: c["start"])
        for k, c in enumerate(cards):
            if c["text"].startswith("{hide}"):
                continue
            events.append((c["start"], "字幕 " + re.sub(r"\{[^}]*\}|[*!/]", "", c["text"])[:24]))
            nxt = cards[k + 1]["start"] if k + 1 < len(cards) else 1e9
            if nxt - c["end"] > 0.3 and said(c["end"], c["end"] + 0.3) and speaking(c["end"] + 0.15):
                warn.append(f"E6 字幕「{re.sub(r'[{][^}]*[}]|[*!/]', '', c['text'])[:20]}」が {c['end']:.2f} 秒で消えたあと、声が続くのに次の字幕まで {nxt - c['end']:.2f} 秒")
    else:
        warn.append(f"字幕の表 {cards_p} が無いので E6 を飛ばしました")

    # 余韻
    last = max(w["end"] for w in words)
    if total - last < 1.0:
        errs.append(f"E7 最後の言葉が {last:.2f} 秒に終わり、動画は {total:.2f} 秒。余韻が {total - last:.2f} 秒しかない")

    # 黒い画面
    r = subprocess.run([FF, "-hide_banner", "-i", a.mp4, "-vf", "blackdetect=d=0.3:pix_th=0.06", "-an", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    allow = [tuple(map(float, x.split(","))) for x in a.allow_black]
    for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+)", r):
        b0, b1 = float(m.group(1)), float(m.group(2))
        if not any(x0 <= b0 and b1 <= x1 for x0, x1 in allow):
            errs.append(f"E8 黒い画面 {b0:.2f}〜{b1:.2f} 秒（「{said(b0, b1)}」）")

    out = Path(a.review) / "sync_check.md"
    lines = [f"# 声と画面の点検（{plan['name']}、{Path(a.mp4).name}）", "",
             f"誤り {len(errs)} 件、注意 {len(warn)} 件。声の時刻は書き出した動画の文字起こしから。", "", "## 誤り", ""]
    lines += [f"- {e}" for e in errs] or ["- なし"]
    lines += ["", "## 注意", ""] + ([f"- {w}" for w in warn] or ["- なし"])
    lines += ["", "## 切り替えと、その前後 0.5 秒の言葉", "", "| 秒 | 出来事 | 前 0.5 秒 | 後 0.5 秒 |", "|---:|---|---|---|"]
    for t, ev in sorted(events):
        lines.append(f"| {t:.2f} | {ev} | {said(t - 0.5, t)} | {said(t, t + 0.5)} |")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"誤り {len(errs)} 件、注意 {len(warn)} 件 -> {out}")
    for e in errs:
        print("  " + e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
