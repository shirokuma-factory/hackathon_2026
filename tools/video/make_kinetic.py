"""字幕カードを、動きのある文字（キネティック・タイポグラフィ）の透明動画にする。

入力: cards.json（[{"shot","text","start","end"}]。text の「/」は改行、「*…*」は強調）
出力: 透明な 1920x1080、30fps、qtrle argb の .mov（make_subtitles.render と同じ形式）

動き:
  通常の文のかたまり  0.18 秒 不透明度 0→1、下から 18px、拡大 0.94→1（easeOutCubic）
  強調のかたまり      0.30 秒 1.45 倍から 1 倍へ（少し行き過ぎて戻る）、黄色の帯が左から右へ 0.25 秒で伸びて残る
  カードの終わり      最後の 0.12 秒で薄くなりながら 8px 上へ。次のカードが 0.05 秒以内に始まるなら、そのまま切り替える
  かたまりは、カードの時間の 75% までに文字数の比で順に出る
動いている間のコマだけ描き、止まっている間は 1 コマを長く保つ（ffmpeg の concat）。

使い方:
  uv run python tools/video/make_kinetic.py --cards outputs/video/subs_r5/cards.json --total 179.4 --out outputs/video/subs_r5/kinetic.mov
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import tempfile
from pathlib import Path

FFMPEG = "C:/ai/bin/ffmpeg.exe"
FONT = "C:/Windows/Fonts/NotoSansJP-VF.ttf"
W, H, FPS = 1920, 1080, 30
BASE = 60            # 通常の文字の大きさ
EMPH_SCALE = 1.25    # 強調の大きさの倍率
W_NORMAL, W_EMPH = 800, 900
STROKE = 4
WHITE, YELLOW, DARK = (255, 255, 255), (240, 192, 74), (10, 14, 20)
BASELINE_LAST = 960  # 最後の行のベースライン（下の 120px は出典の行用に空ける）
LINE_H = 1.35
MAX_W = 1760         # 1 行の幅の上限。超えるカードは文字を縮める
REVEAL = 0.75        # カードの時間のこの割合までに全部出す
DUR_N, DUR_E, DUR_BAR, DUR_EXIT = 0.18, 0.30, 0.25, 0.12
EMPH_R = 1.45        # 強調の絵は最大の倍率で作り、縮めて使う
BAND_Y0, BAND_H = 480, 560

PARTICLES = "はがをにでとのへも"
UNIT = r"(?:%|割|分の[0-9]+|年度?|月|日間|日|円|台|件|回|便|トン|分|時間|km|cm|m|人|社)?"
ATOM = re.compile(
    r"約?[0-9][0-9,.]*(?:[万千百億][0-9,.]*)*" + UNIT
    + r"|[A-Za-z][A-Za-z0-9]*(?: [A-Za-z][A-Za-z0-9]*)*")


def ease_out_cubic(p: float) -> float:
    return 1 - (1 - p) ** 3


def ease_out_back(p: float, c1: float = 1.4) -> float:
    c3 = c1 + 1
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


ENDINGS = ("ます", "ました", "ません", "なる", "ない", "いる")


def is_hira(ch: str) -> bool:
    return "ぁ" <= ch <= "ゟ"


def is_kanji(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def units(s: str) -> list[str]:
    """文字ごとに分けるが、数字と単位、英単語は 1 つにまとめる。"""
    out, pos = [], 0
    for m in ATOM.finditer(s):
        out += list(s[pos:m.start()])
        out.append(m.group(0))
        pos = m.end()
    out += list(s[pos:])
    return out


def split_normal(s: str) -> list[str]:
    """通常の文を 2〜6 字ほどのかたまりに分ける。助詞のあと、空白で区切る。"""
    chunks: list[str] = []
    cur = ""
    us = units(s)
    for k, u in enumerate(us):
        if u in (" ", "　"):
            if cur:
                chunks.append(cur + " ")
                cur = ""
            continue
        cur += u
        nxt = us[k + 1] if k + 1 < len(us) else ""
        n = len(cur)
        is_particle = len(u) == 1 and u in PARTICLES
        nxt_blocks = (len(nxt) == 1 and nxt in PARTICLES) or (u == "で" and nxt in ("す", "き", "あ", "し")) or (u == "に" and nxt == "く")
        hira_kanji = n >= 4 and bool(nxt) and is_hira(u[-1]) and is_kanji(nxt[0])
        verb_end = n >= 5 and cur.endswith(ENDINGS) and nxt not in ("が", "し")
        if (is_particle and n >= 3 and nxt and not nxt_blocks) or hira_kanji or verb_end or n >= 8:
            chunks.append(cur)
            cur = ""
    if cur:
        if chunks and len(cur.strip()) < 2 and len(chunks[-1]) <= 8:
            chunks[-1] += cur
        else:
            chunks.append(cur)
    return chunks


def parse_card(text: str) -> list[list[tuple[str, bool]]]:
    """カードの文を、行ごとの (かたまり, 強調か) の並びにする。"""
    lines = []
    for line in text.split("/"):
        row: list[tuple[str, bool]] = []
        for i, part in enumerate(line.split("*")):
            if not part:
                continue
            if i % 2 == 1:
                row.append((part, True))
            else:
                row += [(c, False) for c in split_normal(part)]
        lines.append(row)
    return lines


_fonts: dict = {}


def font(size: float, weight: int):
    from PIL import ImageFont
    key = (round(size), weight)
    if key not in _fonts:
        f = ImageFont.truetype(FONT, round(size))
        f.set_variation_by_axes([weight])
        _fonts[key] = f
    return _fonts[key]


def make_sprite(text: str, emph: bool, size: float, r: float):
    """1 つのかたまりの絵（影の層と文字の層）。r は作るときの倍率。"""
    from PIL import Image, ImageDraw, ImageFilter
    t = text.rstrip(" ")
    f = font(size * r, W_EMPH if emph else W_NORMAL)
    asc, desc = f.getmetrics()
    adv = f.getlength(t)
    pad = round(36 * r)
    w, h = math.ceil(adv) + 2 * pad, asc + desc + 2 * pad
    by = pad + asc
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((pad, by + 4 * r), t, font=f, anchor="ls", fill=(0, 0, 0, 153),
                            stroke_width=round(STROKE * r), stroke_fill=(0, 0, 0, 153))
    sh = sh.filter(ImageFilter.GaussianBlur(8 * r))
    mn = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(mn).text((pad, by), t, font=f, anchor="ls", fill=(YELLOW if emph else WHITE) + (255,),
                            stroke_width=round(STROKE * r), stroke_fill=DARK + (255,))
    return {"sh": sh, "mn": mn, "anchor": (pad + adv / 2, by - 0.35 * size * r), "r": r}


def layout(lines, fit: float):
    """各かたまりの位置（中心の x、ベースライン y）と幅を決める。"""
    def sz(e):
        return BASE * fit * (EMPH_SCALE if e else 1)
    widths = []
    for row in lines:
        widths.append(sum(font(sz(e), W_EMPH if e else W_NORMAL).getlength(c) for c, e in row))
    out = []
    n = len(lines)
    for li, row in enumerate(lines):
        base = BASELINE_LAST - (n - 1 - li) * BASE * fit * LINE_H
        x = (W - widths[li]) / 2
        for c, e in row:
            f = font(sz(e), W_EMPH if e else W_NORMAL)
            adv = f.getlength(c)
            vis = f.getlength(c.rstrip(" "))
            out.append({"text": c, "emph": e, "x": x, "vis": vis, "cx": x + vis / 2, "base": base, "size": sz(e)})
            x += adv
    return out, max(widths)


def prepare(card: dict) -> dict:
    lines = parse_card(card["text"])
    chunks, mw = layout(lines, 1.0)
    fit = min(1.0, MAX_W / mw) if mw else 1.0
    if fit < 1.0:
        chunks, _ = layout(lines, fit)
    total_chars = sum(len(c["text"].strip()) for c in chunks) or 1
    dur = card["end"] - card["start"]
    acc = 0
    for c in chunks:
        c["t0"] = card["start"] + acc / total_chars * dur * REVEAL
        acc += len(c["text"].strip())
        r = EMPH_R if c["emph"] else 1.0
        c["sprite"] = make_sprite(c["text"], c["emph"], c["size"], r)
        c["dur"] = DUR_E if c["emph"] else DUR_N
    return {"chunks": chunks}


def over(canvas, img, x: int, y: int) -> None:
    """はみ出す分を切って重ねる。"""
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(canvas.width, x + img.width), min(canvas.height, y + img.height)
    if x1 <= x0 or y1 <= y0:
        return
    canvas.alpha_composite(img.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


def fade(img, o: float):
    if o >= 0.999:
        return img
    a = img.getchannel("A").point([round(v * o) for v in range(256)])
    img = img.copy()
    img.putalpha(a)
    return img


def chunk_state(c: dict, t: float):
    """時刻 t のかたまりの状態 (不透明度, 拡大, 下へのずれ, 帯の進み)。まだ出ていなければ None。"""
    p = (t - c["t0"]) / c["dur"]
    p = 0.0 if p < 0 else 1.0 if p > 1 else p
    if p <= 0:
        return None
    if c["emph"]:
        op = min(1.0, p / 0.4)
        sc = EMPH_R - (EMPH_R - 1.0) * ease_out_back(p)
        dy = 0.0
        bar = ease_out_cubic(min(1.0, (t - c["t0"]) / DUR_BAR))
    else:
        e = ease_out_cubic(p)
        op, sc, dy, bar = e, 0.94 + 0.06 * e, 18 * (1 - e), 0.0
    return (round(op, 3), round(sc, 3), round(dy, 2), round(bar, 3))


def draw_card(card_data: dict, states: list, exit_p: float):
    from PIL import Image, ImageDraw
    band = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
    bars = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bars)
    for c, s in zip(card_data["chunks"], states):
        if s is None or not c["emph"] or s[3] <= 0:
            continue
        hgt = c["size"] * 0.45
        bot = c["base"] + c["size"] * 0.10 - BAND_Y0
        x0, x1 = c["x"] - 8, c["x"] + c["vis"] + 8
        xe = x0 + (x1 - x0) * s[3]
        if xe - x0 >= 2:
            bd.rounded_rectangle((x0, bot - hgt, xe, bot), radius=hgt * 0.3,
                                 fill=YELLOW + (round(255 * 0.28 * min(1.0, s[0] * 2)),))
    band.alpha_composite(bars)
    for layer in ("sh", "mn"):
        for c, s in zip(card_data["chunks"], states):
            if s is None:
                continue
            op, sc, dy, _ = s
            sp = c["sprite"]
            k = sc / sp["r"]
            img = sp[layer]
            if abs(k - 1) > 1e-3:
                img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
            ax, ay = sp["anchor"]
            gy = c["base"] - 0.35 * c["size"] + dy
            over(band, fade(img, op), round(c["cx"] - ax * k), round(gy - ay * k - BAND_Y0))
    if exit_p > 0:
        band = fade(band, 1 - exit_p ** 2)
    full = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    full.paste(band, (0, round(BAND_Y0 - 8 * exit_p)))
    return full


def timeline(cards: list[dict]):
    """各カードの (表示の始まり, 終わり, 終わりの動きをつけるか)。"""
    info = []
    for i, c in enumerate(cards):
        nxt = cards[i + 1]["start"] if i + 1 < len(cards) else None
        if nxt is not None and nxt - c["end"] <= 0.05:
            info.append((c["start"], max(c["start"], nxt), False))
        else:
            info.append((c["start"], c["end"], True))
    return info


def render(cards: list[dict], total: float, out: Path) -> None:
    from PIL import Image
    n_frames = round(total * FPS)
    tl = timeline(cards)
    prepared = [prepare(c) for c in cards]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="kinetic_", dir=out.parent))
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(tmp / "blank.png")
    names: dict = {}
    seq: list[list] = []  # [ファイル名, コマ数]
    for n in range(n_frames):
        t = n / FPS
        name = "blank.png"
        for i, (s, e, ex) in enumerate(tl):
            if s <= t < e:
                states = [chunk_state(c, t) for c in prepared[i]["chunks"]]
                exit_p = 0.0
                if ex and t > e - DUR_EXIT:
                    exit_p = round(min(1.0, (t - (e - DUR_EXIT)) / DUR_EXIT), 3)
                key = (i, tuple(states), exit_p)
                if key not in names:
                    fn = f"f{len(names):05d}.png"
                    draw_card(prepared[i], states, exit_p).save(tmp / fn, compress_level=1)
                    names[key] = fn
                name = names[key]
                break
        if seq and seq[-1][0] == name:
            seq[-1][1] += 1
        else:
            seq.append([name, 1])
    with open(tmp / "list.txt", "w", encoding="utf-8", newline="\n") as fh:
        for fn, cnt in seq:
            fh.write(f"file '{fn}'\nduration {cnt / FPS:.5f}\n")
        fh.write(f"file '{seq[-1][0]}'\n")
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                    "-vf", f"fps={FPS}", "-frames:v", str(n_frames), "-c:v", "qtrle", "-pix_fmt", "argb", str(out)],
                   check=True)
    print(f"描いたコマ {len(names)} / 全 {n_frames} コマ -> {out}")
    for p in tmp.glob("*"):
        p.unlink()
    tmp.rmdir()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", required=True)
    ap.add_argument("--total", type=float, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cards = json.loads(Path(a.cards).read_text(encoding="utf-8"))
    render(cards, a.total, Path(a.out))


if __name__ == "__main__":
    main()
