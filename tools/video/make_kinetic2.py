"""字幕カードを、位置と大きさを変えられるキネティック・タイポグラフィの透明動画にする（make_kinetic.py の拡張版）。

入力: cards.json（[{"shot","text","start","end"}]）。text の「/」は改行、「*…*」は強調。
      text の先頭に {…} で、そのカードだけの指定を書ける。例: "{pos=tl size=xl anim=slam}積める量の/*6割*が空"
出力: 透明な 1920x1080、30fps、qtrle argb の .mov

指定（空白で区切る。省略した項目は既定値）:
  pos   bc(下の中央、既定) tl tr bl br l r c t(上の中央)。余白は左右 96px、上 80px、下は 990px まで（下の 90px は出典の行）
  size  s=48 m=64(既定) l=96 xl=150 xxl=220。強調はその 1.25 倍。1 行が安全な幅を超えたら全体を縮める
  anim  rise(既定) slam slide type mask
          slam  強調が 1.6 倍から落ちて収まる。収まった瞬間に 2 コマだけ 3px 揺れる（1 回）
          slide かたまりが、文字を置いた側から 60px 滑り込む（順にずらす）
          type  1 文字 0.03 秒ずつ出る
          mask  各行が左から右へやわらかいマスクで現れる
  hide  このカードは何も描かない（単独で書く。{hide}）
  行の揃えは pos に従う（l tl bl は左、r tr br は右、ほかは中央）。
  l / xl / xxl は太さ 900、字間 -0.02em、影を強め、縁は 6px まで。

使い方:
  uv run python tools/video/make_kinetic2.py --cards outputs/video/subs_r6/cards.json --total 179.4 --out outputs/video/subs_r6/kinetic.mov
  確認用に静止画も出せる: --stills DIR --still-times 0.1,0.3,1.5（暗い背景に重ねた 1080p の PNG）
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_kinetic as K  # noqa: E402  (分け方・イージング・合成の部品を共有する)

W, H, FPS = K.W, K.H, K.FPS
SIZES = {"s": 48, "m": 64, "l": 96, "xl": 150, "xxl": 220}
POS = {  # (横, 縦)  横: l/c/r、縦: t/m/b
    "bc": ("c", "b"), "tl": ("l", "t"), "tr": ("r", "t"), "bl": ("l", "b"), "br": ("r", "b"),
    "l": ("l", "m"), "r": ("r", "m"), "c": ("c", "m"), "t": ("c", "t"),
}
ANIMS = ("rise", "slam", "slide", "type", "mask")
MARGIN_X, MARGIN_TOP = 96, 80
BASELINE_LAST = 960          # 下に置くときの最後の行のベースライン（K と同じ。下の 90px は出典の行用）
MAX_W = W - 2 * MARGIN_X     # 1728
MAX_H = BASELINE_LAST - MARGIN_TOP
EMPH_SCALE = K.EMPH_SCALE
SLAM_FROM, DUR_SLAM = 1.6, 0.24
SLIDE_PX, DUR_SLIDE = 60, 0.40
TYPE_STEP, DUR_TYPE = 0.03, 0.09
DUR_MASK, MASK_SOFT = 0.55, 160
SHAKE = ((3, -3), (-3, 2))   # 収まった瞬間の 2 コマ
BG = (5, 8, 12)


@dataclass
class Style:
    pos: str = "bc"
    size: str = "m"
    anim: str = "rise"
    hide: bool = False

    @property
    def big(self) -> bool:
        return self.size in ("l", "xl", "xxl")

    @property
    def h(self) -> str:
        return POS[self.pos][0]

    @property
    def v(self) -> str:
        return POS[self.pos][1]

    @property
    def stroke(self) -> int:
        return {"l": 5, "xl": 6, "xxl": 6}.get(self.size, K.STROKE)

    @property
    def sp_em(self) -> float:
        return -0.02 if self.big else 0.0

    def weight(self, emph: bool) -> int:
        return 900 if (self.big or emph) else K.W_NORMAL


def parse_directive(text: str) -> tuple[Style, str]:
    st = Style()
    m = re.match(r"\s*\{([^}]*)\}", text)
    if not m:
        return st, text
    for tok in m.group(1).split():
        if tok == "hide":
            st.hide = True
            continue
        k, _, v = tok.partition("=")
        if k == "pos" and v in POS:
            st.pos = v
        elif k == "size" and v in SIZES:
            st.size = v
        elif k == "anim" and v in ANIMS:
            st.anim = v
        else:
            raise ValueError(f"不明な指定: {tok}")
    return st, text[m.end():]


def parse_card(text: str, split_chars: bool) -> list[list[tuple[str, bool, int]]]:
    """行ごとの (かたまり, 強調か, 強調の組の番号) の並び。type のときは 1 文字ずつ。"""
    lines, gid = [], 0
    for line in text.split("/"):
        row: list[tuple[str, bool, int]] = []
        for i, part in enumerate(line.split("*")):
            if not part:
                continue
            if i % 2 == 1:
                row.append((part, True, gid))
                gid += 1
            else:
                row += [(c, False, -1) for c in K.split_normal(part)]
        if row:  # 行末の空白は幅に入れない
            t, e, g = row[-1]
            if t.rstrip(" "):
                row[-1] = (t.rstrip(" "), e, g)
            else:
                row.pop()
        if split_chars:
            row = [(ch, e, g) for c, e, g in row for ch in c]
        lines.append(row)
    return lines


def text_len(f, t: str, sp: float) -> float:
    return f.getlength(t) + sp * len(t)


def draw_str(d, x: float, y: float, t: str, f, sp: float, **kw) -> None:
    if sp == 0:
        d.text((x, y), t, font=f, anchor="ls", **kw)
        return
    for k, ch in enumerate(t):
        d.text((x + f.getlength(t[:k]) + sp * k, y), ch, font=f, anchor="ls", **kw)


def make_sprite(text: str, emph: bool, size: float, r: float, st: Style):
    """1 つのかたまりの絵（影の層と文字の層）。r は作るときの倍率。"""
    from PIL import Image, ImageDraw, ImageFilter
    t = text.rstrip(" ")
    if not t.strip():
        return None
    f = K.font(size * r, st.weight(emph))
    asc, desc = f.getmetrics()
    sp = st.sp_em * size * r
    adv = text_len(f, t, sp)
    blur = (0.07 * size if st.big else 8) * r
    off = (0.035 * size if st.big else 4) * r
    sw = round(st.stroke * r)
    pad = math.ceil(3 * blur + off + sw) + 4
    w, h = math.ceil(adv) + 2 * pad, asc + desc + 2 * pad
    by = pad + asc
    sh_a = 200 if st.big else 153
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_str(ImageDraw.Draw(sh), pad, by + off, t, f, sp, fill=(0, 0, 0, sh_a),
             stroke_width=sw, stroke_fill=(0, 0, 0, sh_a))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    mn = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_str(ImageDraw.Draw(mn), pad, by, t, f, sp, fill=(K.YELLOW if emph else K.WHITE) + (255,),
             stroke_width=sw, stroke_fill=K.DARK + (255,))
    return {"sh": sh, "mn": mn, "anchor": (pad + adv / 2, by - 0.35 * size * r), "r": r}


def layout(lines, st: Style, fit: float):
    """各かたまりの位置を決める。戻り値: (かたまりの一覧, 最大の行幅, ブロックの高さ)。"""
    base_sz = SIZES[st.size] * fit

    def sz(e):
        return base_sz * (EMPH_SCALE if e else 1)

    def spx(e):
        return st.sp_em * sz(e)

    widths = [sum(text_len(K.font(sz(e), st.weight(e)), c, spx(e)) for c, e, _ in row) for row in lines]
    lsz = [max([sz(e) for _, e, _ in row] or [base_sz]) for row in lines]
    extra = 0.16 if st.big else 0.35
    rel = [0.0]
    for i in range(1, len(lines)):
        rel.append(rel[-1] + 0.88 * lsz[i] + 0.12 * lsz[i - 1] + extra * max(lsz[i], lsz[i - 1]))
    top, bottom = -0.88 * lsz[0], rel[-1] + 0.12 * lsz[-1]
    height = bottom - top
    if st.v == "t":
        base0 = MARGIN_TOP + 0.88 * lsz[0]
    elif st.v == "b":
        base0 = min(BASELINE_LAST, 990 - (0.22 if st.big else 0.12) * lsz[-1]) - rel[-1]
    else:
        base0 = H / 2 - height / 2 + 0.88 * lsz[0]
    out = []
    for li, row in enumerate(lines):
        base = base0 + rel[li]
        if st.h == "l":
            x = MARGIN_X
        elif st.h == "r":
            x = W - MARGIN_X - widths[li]
        else:
            x = (W - widths[li]) / 2
        for c, e, g in row:
            f = K.font(sz(e), st.weight(e))
            adv = text_len(f, c, spx(e))
            vis = text_len(f, c.rstrip(" "), spx(e))
            out.append({"text": c, "emph": e, "grp": g, "line": li, "x": x, "vis": vis,
                        "cx": x + vis / 2, "base": base, "size": sz(e)})
            x += adv
    return out, (max(widths) if widths else 0), height


def prepare(card: dict) -> dict | None:
    st, text = parse_directive(card["text"])
    if st.hide:
        return None
    lines = parse_card(text, st.anim == "type")
    fit = 1.0
    for _ in range(8):
        chunks, mw, hh = layout(lines, st, fit)
        k = min(1.0, MAX_W / mw if mw else 1.0, MAX_H / hh if hh else 1.0)
        if k >= 0.999:
            break
        fit *= k * 0.995
    dur = card["end"] - card["start"]
    has_emph = any(c["emph"] for c in chunks)
    solid = [c for c in chunks if c["text"].strip()]
    total = sum(len(c["text"].strip()) for c in solid) or 1
    nlines = len(lines)
    step = 0.0
    if st.anim == "mask" and nlines > 1:
        step = max(0.05, min(0.2, (dur * K.REVEAL - DUR_MASK) / (nlines - 1)))
    acc, k_solid = 0, 0
    for c in chunks:
        c["t0"] = card["start"] + acc / total * dur * K.REVEAL
        if st.anim == "type":
            per = min(TYPE_STEP, dur * K.REVEAL / total)
            c["t0"] = card["start"] + k_solid * per
            if c["text"].strip():
                k_solid += 1
        elif st.anim == "mask":
            c["t0"] = card["start"] + c["line"] * step
        acc += len(c["text"].strip())
        e = c["emph"]
        if st.anim == "slam" and (e or not has_emph):
            c["mode"], c["dur"], r = "slam", DUR_SLAM, SLAM_FROM
        elif st.anim == "rise":
            c["mode"], c["dur"], r = ("pop", K.DUR_E, K.EMPH_R) if e else ("rise", K.DUR_N, 1.0)
        elif st.anim == "slam":
            c["mode"], c["dur"], r = "rise", K.DUR_N, 1.0
        elif st.anim == "slide":
            c["mode"], c["dur"], r = "slide", DUR_SLIDE, 1.0
        elif st.anim == "type":
            c["mode"], c["dur"], r = "type", DUR_TYPE, 1.0
        else:
            c["mode"], c["dur"], r = "mask", DUR_MASK, 1.0
        c["rise_dy"] = min(40.0, max(18.0, 0.25 * SIZES[st.size]))
        c["sprite"] = make_sprite(c["text"], e, c["size"], r, st)
    # 滑り込む向き（文字を置いた側から）
    if st.h == "l":
        sdx, sdy = -SLIDE_PX, 0
    elif st.h == "r":
        sdx, sdy = SLIDE_PX, 0
    elif st.v == "t":
        sdx, sdy = 0, -SLIDE_PX
    else:
        sdx, sdy = 0, SLIDE_PX
    for c in chunks:
        c["sdx"], c["sdy"] = sdx, sdy
    groups: dict = {}
    for i, c in enumerate(chunks):
        if c["emph"]:
            groups.setdefault(c["grp"], []).append(i)
    impacts = [c["t0"] + c["dur"] for c in chunks if c["mode"] == "slam"]
    lines_idx = [[i for i, c in enumerate(chunks) if c["line"] == li] for li in range(nlines)]
    return {"style": st, "chunks": chunks, "groups": groups, "lines": lines_idx,
            "impact": min(impacts) if impacts else None}


def ease_out_expo(p: float) -> float:
    return 1.0 if p >= 1 else 1 - 2 ** (-10 * p)


def chunk_state(c: dict, t: float):
    """時刻 t のかたまりの状態 (不透明度, 拡大, 横ずれ, 縦ずれ, 帯の進み, マスクの進み)。まだ出ていなければ None。"""
    p = (t - c["t0"]) / c["dur"]
    p = 0.0 if p < 0 else 1.0 if p > 1 else p
    if p <= 0:
        return None
    m = c["mode"]
    op, sc, dx, dy, mp = 1.0, 1.0, 0.0, 0.0, 1.0
    if m == "pop":
        op, sc = min(1.0, p / 0.4), K.EMPH_R - (K.EMPH_R - 1.0) * K.ease_out_back(p)
    elif m == "rise":
        e = K.ease_out_cubic(p)
        op, sc, dy = e, 0.94 + 0.06 * e, c["rise_dy"] * (1 - e)
    elif m == "slam":
        op, sc = min(1.0, p / 0.35), SLAM_FROM - (SLAM_FROM - 1.0) * K.ease_out_back(p, 1.5)
    elif m == "slide":
        e = ease_out_expo(p)
        op, dx, dy = min(1.0, p / 0.5), c["sdx"] * (1 - e), c["sdy"] * (1 - e)
    elif m == "type":
        e = K.ease_out_cubic(p)
        op, dy = e, 6 * (1 - e)
    else:  # mask
        e = ease_out_expo(p)
        mp, dx = e, -24 * (1 - e)
    bt0 = c["t0"] + (c["dur"] if m == "slam" else 0.0)  # slam は収まってから線を引く
    bar = K.ease_out_cubic(max(0.0, min(1.0, (t - bt0) / K.DUR_BAR))) if c["emph"] else 0.0
    return (round(op, 3), round(sc, 3), round(dx, 2), round(dy, 2), round(bar, 3), round(mp, 3))


def mask_image(x0: float, x1: float, mp: float):
    """左から右へ現れるやわらかいマスク（横方向の勾配を縦に伸ばす）。"""
    from PIL import Image
    edge = (x0 - 60) + mp * ((x1 + 60 + MASK_SOFT) - (x0 - 60))
    row = Image.new("L", (W, 1))
    vals = []
    for x in range(W):
        v = min(1.0, max(0.0, (edge - x) / MASK_SOFT))
        vals.append(round(255 * v * v * (3 - 2 * v)))
    row.putdata(vals)
    return row.resize((W, H), Image.NEAREST)


def draw_card(cd: dict, states: tuple, exit_p: float, shake: tuple):
    from PIL import Image, ImageChops, ImageDraw
    st = cd["style"]
    chunks = cd["chunks"]
    shx, shy = shake
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for idxs in cd["lines"]:
        vis = [i for i in idxs if states[i] is not None]
        if not vis:
            continue
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)) if st.anim == "mask" else canvas
        # 帯（強調の背面）
        bars = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(bars)
        for g, members in cd["groups"].items():
            if members[0] not in idxs or states[members[0]] is None:
                continue
            c0, cl = chunks[members[0]], chunks[members[-1]]
            s0 = states[members[0]]
            x0 = c0["x"] - 8 + s0[2] + shx
            x1 = cl["x"] + cl["vis"] + 8 + s0[2] + shx
            if st.anim == "type":
                shown = [chunks[i] for i in members if states[i] is not None]
                xe = shown[-1]["x"] + shown[-1]["vis"] + 8 + s0[2] + shx
            else:
                xe = x0 + (x1 - x0) * s0[4]
            if xe - x0 < 2 or (st.big and xe - x0 < 20):
                continue
            if st.big:  # 大きい文字は、ベースラインの下に細い線を引く（帰りを埋める線の印）
                th = max(6.0, 0.05 * c0["size"])
                yb = c0["base"] + 0.14 * c0["size"] + s0[3] + shy
                bd.rounded_rectangle((x0 + 8, yb, xe - 8, yb + th), radius=th / 2,
                                     fill=K.YELLOW + (round(255 * min(1.0, s0[0] * 2)),))
            else:
                hgt = c0["size"] * 0.45
                bot = c0["base"] + c0["size"] * 0.10 + s0[3] + shy
                bd.rounded_rectangle((x0, bot - hgt, xe, bot), radius=hgt * 0.3,
                                     fill=K.YELLOW + (round(255 * 0.28 * min(1.0, s0[0] * 2)),))
        layer.alpha_composite(bars)
        for name in ("sh", "mn"):
            for i in vis:
                c, s = chunks[i], states[i]
                sp = c["sprite"]
                if sp is None:
                    continue
                op, sc, dx, dy = s[0], s[1], s[2], s[3]
                k = sc / sp["r"]
                img = sp[name]
                if abs(k - 1) > 1e-3:
                    img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)
                ax, ay = sp["anchor"]
                gy = c["base"] - 0.35 * c["size"] + dy + shy
                K.over(layer, K.fade(img, op), round(c["cx"] + dx + shx - ax * k), round(gy - ay * k))
        if st.anim == "mask":
            lx0 = min(chunks[i]["x"] for i in idxs)
            lx1 = max(chunks[i]["x"] + chunks[i]["vis"] for i in idxs)
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask_image(lx0, lx1, states[vis[0]][5])))
            canvas.alpha_composite(layer)
    if exit_p > 0:
        canvas = K.fade(canvas, 1 - exit_p ** 2)
        full = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        full.paste(canvas, (0, -round(8 * exit_p)))
        canvas = full
    return canvas


def frame_state(prep: list, tl: list, t: float):
    for i, (s, e, ex) in enumerate(tl):
        if s <= t < e:
            cd = prep[i]
            states = tuple(chunk_state(c, t) for c in cd["chunks"])
            exit_p = 0.0
            if ex and t > e - K.DUR_EXIT:
                exit_p = round(min(1.0, (t - (e - K.DUR_EXIT)) / K.DUR_EXIT), 3)
            shake = (0, 0)
            if cd["impact"] is not None:
                k = math.floor((t - cd["impact"]) * FPS + 1e-6)
                if 0 <= k < len(SHAKE):
                    shake = SHAKE[k]
            return i, states, exit_p, shake
    return None


def build(cards: list[dict]):
    cards = [c for c in cards if not parse_directive(c["text"])[0].hide]
    tl = K.timeline(cards)
    return [prepare(c) for c in cards], tl


def render(cards: list[dict], total: float, out: Path, stills: Path | None = None, still_times: list[float] | None = None) -> None:
    from PIL import Image
    n_frames = round(total * FPS)
    prep, tl = build(cards)
    out.parent.mkdir(parents=True, exist_ok=True)
    if stills is not None:
        stills.mkdir(parents=True, exist_ok=True)
        for t in still_times or []:
            fs = frame_state(prep, tl, t)
            img = draw_card(prep[fs[0]], *fs[1:]) if fs else Image.new("RGBA", (W, H), (0, 0, 0, 0))
            bg = Image.new("RGBA", (W, H), BG + (255,))
            bg.alpha_composite(img)
            bg.convert("RGB").save(stills / f"still_{t:07.3f}.png")
        print(f"静止画 {len(still_times or [])} 枚 -> {stills}")
    tmp = Path(tempfile.mkdtemp(prefix="kinetic2_", dir=out.parent))
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(tmp / "blank.png")
    names: dict = {}
    seq: list[list] = []  # [ファイル名, コマ数]
    for n in range(n_frames):
        fs = frame_state(prep, tl, n / FPS)
        name = "blank.png"
        if fs:
            if fs not in names:
                fn = f"f{len(names):05d}.png"
                draw_card(prep[fs[0]], *fs[1:]).save(tmp / fn, compress_level=1)
                names[fs] = fn
            name = names[fs]
        if seq and seq[-1][0] == name:
            seq[-1][1] += 1
        else:
            seq.append([name, 1])
    with open(tmp / "list.txt", "w", encoding="utf-8", newline="\n") as fh:
        for fn, cnt in seq:
            fh.write(f"file '{fn}'\nduration {cnt / FPS:.5f}\n")
        fh.write(f"file '{seq[-1][0]}'\n")
    subprocess.run([K.FFMPEG, "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
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
    ap.add_argument("--stills", help="確認用の静止画（暗い背景に重ねた 1080p）を出すフォルダ")
    ap.add_argument("--still-times", default="", help="静止画の時刻（秒）をカンマ区切りで")
    a = ap.parse_args()
    cards = json.loads(Path(a.cards).read_text(encoding="utf-8"))
    times = [float(x) for x in a.still_times.split(",") if x.strip()]
    render(cards, a.total, Path(a.out), Path(a.stills) if a.stills else None, times)


if __name__ == "__main__":
    main()
