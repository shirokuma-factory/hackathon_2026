"""ナレーションの字幕を、声の時刻に合わせて作る。

入力:
  docs/video/shots.json の narration（字幕の文）と read（読み上げた文）
  outputs/video/voice/<S>_v4.json の alignment（ElevenLabs が返した文字ごとの時刻）
  outputs/video/voice/<S>_v4.mp3（無音を詰める前の音声。詰めた量を計算するのに使う）
  --offsets で各区間の声をタイムラインのどこに置いたか（秒）
出力: outputs/video/subs/ に cards.json、subs.srt、字幕の透明動画 subs.mov（1920x1080、30fps、qtrle）

使い方:
  uv run python tools/video/make_subtitles.py --offsets 0,16,31.56,46.17,71.98,113.33,147.13,157.48 --total 176
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "outputs" / "video" / "voice"
OUT = ROOT / "outputs" / "video" / "subs"
FFMPEG = "C:/ai/bin/ffmpeg.exe"
FONT = "C:/Windows/Fonts/NotoSansJP-VF.ttf"
W, H, FPS = 1920, 1080, 30
KEEP = 0.3  # silenceremove で残した無音の長さ（秒）
LINE = 24   # 1 行の最大の字数
MAXC = 44   # 1 枚の最大の字数（2 行）


def silences(mp3: Path) -> list[tuple[float, float]]:
    """詰める前の音声の無音区間。tight の wav を作ったときと同じ条件（-38dB、0.3 秒以上）。"""
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(mp3), "-af",
                        "silencedetect=n=-38dB:d=0.3", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    st = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", r.stderr)]
    en = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    return list(zip(st, en))


def squeeze(t: float, sil: list[tuple[float, float]]) -> float:
    """元の音声の時刻 t を、無音を詰めたあとの時刻に直す。先頭の無音は全部、途中の無音は KEEP 秒を残して消えた。"""
    cut = 0.0
    for i, (a, b) in enumerate(sil):
        keep = 0.0 if (i == 0 and a < 0.05) else KEEP
        if t >= b:
            cut += max(0.0, (b - a) - keep)
        elif t > a:
            cut += max(0.0, (t - a) - keep)
    return max(0.0, t - cut)


def sentences(s: str) -> list[str]:
    return [x + "。" for x in s.split("。") if x.strip()]


def split_card(s: str) -> list[str]:
    """MAXC 字を超える文は、真ん中に近い「、」で分ける。"""
    if len(s) <= MAXC:
        return [s]
    marks = [i + 1 for i, c in enumerate(s) if c == "、"]
    if not marks:
        mid = len(s) // 2
        return split_card(s[:mid]) + split_card(s[mid:])
    cut = min(marks, key=lambda i: abs(i - len(s) / 2))
    return split_card(s[:cut]) + split_card(s[cut:])


def wrap(s: str) -> list[str]:
    if len(s) <= LINE:
        return [s]
    marks = [i + 1 for i, c in enumerate(s) if c in "、。"] or [len(s) // 2]
    cut = min(marks, key=lambda i: abs(i - len(s) / 2))
    if max(cut, len(s) - cut) > LINE + 4:
        cut = len(s) // 2
    return [s[:cut], s[cut:]]


def cards_for(shot: dict, offset: float) -> list[dict]:
    meta = json.loads((VOICE / f"{shot['id']}_v4.json").read_text(encoding="utf-8"))
    al = meta["alignment"]
    chars, t0s, t1s = al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"]
    read = "".join(chars)
    sil = silences(VOICE / f"{shot['id']}_v4.mp3")
    nar, rd = sentences(shot["narration"]), sentences(read)
    if len(nar) != len(rd):
        raise SystemExit(f"{shot['id']}: 字幕の文の数 {len(nar)} と読みの文の数 {len(rd)} が違います")
    out, pos = [], 0
    for ns, rs in zip(nar, rd):
        a, b = pos, pos + len(rs)
        pos = b
        ts, te = t0s[a], t1s[b - 1]
        parts = split_card(ns)
        acc = 0
        for p in parts:
            f0, f1 = acc / len(ns), (acc + len(p)) / len(ns)
            acc += len(p)
            i0 = a + int(round(f0 * (b - a)))
            i1 = min(b - 1, a + int(round(f1 * (b - a))) - 1)
            s0 = squeeze(t0s[i0] if p is not parts[0] else ts, sil)
            s1 = squeeze(t1s[i1] if p is not parts[-1] else te, sil)
            out.append({"shot": shot["id"], "text": p, "start": round(offset + s0, 3), "end": round(offset + s1 + 0.25, 3)})
    return out


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# 強調する言葉（数字と単位、要点の言葉）。色は黄（common.css の --warn）
EMPH = re.compile(
    r"(約?\d[\d,.]*(?:万\d*千?|千)?(?:%|トン|便|回|件|円|m\d*cm|cm|台|年\d*月|月|分|日間)"
    r"|空で帰る|帰りが空|空でした|入れません|社長が一人|理由が出ます|義務|承諾します|Claude Code)")
WHITE, YELLOW = (255, 255, 255), (240, 192, 74)
FADE = 4  # 出入りのコマ数（4 コマ = 0.13 秒）


def spans(line: str) -> list[tuple[str, tuple[int, int, int]]]:
    out, pos = [], 0
    for m in EMPH.finditer(line):
        if m.start() > pos:
            out.append((line[pos:m.start()], WHITE))
        out.append((m.group(0), YELLOW))
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], WHITE))
    return out


def card_image(text: str, f, alpha: float, dy: int):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lines = wrap(text)
    lh = 66
    tw = max(d.textlength(x, font=f) for x in lines)
    bh = lh * len(lines) + 28
    y0 = H - 140 - bh + dy
    d.rounded_rectangle(((W - tw) / 2 - 28, y0, (W + tw) / 2 + 28, y0 + bh), radius=8, fill=(8, 12, 18, int(175 * alpha)))
    for k, x in enumerate(lines):
        cx = (W - d.textlength(x, font=f)) / 2
        for seg, col in spans(x):
            d.text((cx, y0 + 12 + k * lh), seg, font=f, fill=col + (int(255 * alpha),))
            cx += d.textlength(seg, font=f)
    return im


def render(cards: list[dict], total: float) -> None:
    from PIL import Image, ImageFont
    f = ImageFont.truetype(FONT, 46)
    try:
        f.set_variation_by_axes([600])
    except Exception:
        pass
    frames = OUT / "png"
    frames.mkdir(parents=True, exist_ok=True)
    for old in frames.glob("c*.png"):
        old.unlink()
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(frames / "blank.png")
    lst, t, fr = [], 0.0, 1 / FPS
    for i, c in enumerate(cards):
        start = max(c["start"], t)
        if start > t:
            lst.append(("blank.png", start - t))
        end = min(c["end"], cards[i + 1]["start"]) if i + 1 < len(cards) else c["end"]
        dur = max(fr * (2 * FADE + 1), end - start)
        # 前の字幕とすき間なく続くときは、出入りのフェードを付けずに切り替える
        joined_in = i > 0 and abs(start - t) < 1e-6 and t > 0
        joined_out = i + 1 < len(cards) and cards[i + 1]["start"] <= c["end"]
        ins = [] if joined_in else [(k + 1) / (FADE + 1) for k in range(FADE)]
        outs = [] if joined_out else [(FADE - k) / (FADE + 1) for k in range(FADE)]
        for k, a in enumerate(ins):
            n = f"c{i:03d}_i{k}.png"
            card_image(c["text"], f, a, int(8 * (1 - a))).save(frames / n)
            lst.append((n, fr))
        n = f"c{i:03d}.png"
        card_image(c["text"], f, 1.0, 0).save(frames / n)
        lst.append((n, dur - fr * (len(ins) + len(outs))))
        for k, a in enumerate(outs):
            n = f"c{i:03d}_o{k}.png"
            card_image(c["text"], f, a, 0).save(frames / n)
            lst.append((n, fr))
        t = start + dur
    if total > t:
        lst.append(("blank.png", total - t))
    with open(frames / "list.txt", "w", encoding="utf-8", newline="\n") as fh:
        for n, dur in lst:
            fh.write(f"file '{n}'\nduration {dur:.4f}\n")
        fh.write(f"file '{lst[-1][0]}'\n")
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(frames / "list.txt"),
                    "-vf", f"fps={FPS}", "-t", f"{total}", "-c:v", "qtrle", "-pix_fmt", "argb", str(OUT / "subs.mov")], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offsets", required=True, help="S1〜S8 の声の位置（秒、カンマ区切り）")
    ap.add_argument("--total", type=float, required=True)
    a = ap.parse_args()
    offs = [float(x) for x in a.offsets.split(",")]
    shots = json.loads((ROOT / "docs" / "video" / "shots.json").read_text(encoding="utf-8"))["shots"]
    cards = []
    for s, o in zip(shots, offs):
        cards += cards_for(s, o)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "cards.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cards, fh, ensure_ascii=False, indent=1)
    with open(OUT / "subs.srt", "w", encoding="utf-8", newline="\n") as fh:
        for i, c in enumerate(cards, 1):
            fh.write(f"{i}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{c['text']}\n\n")
    render(cards, a.total)
    print(f"字幕 {len(cards)} 枚 -> {OUT / 'subs.mov'}")


if __name__ == "__main__":
    main()
