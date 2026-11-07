"""字幕カードを、落ち着いた編集デザイン風の文字にして透明動画にする（make_kinetic2.py の新しい見た目）。

make_kinetic2 と同じ指定（{pos= size= anim=} と「/」「*強調*」）を受け付ける。見た目と動きだけが違う。
入力: cards.json（[{"shot","text","start","end"}]）
出力: 透明な 1920x1080（--scale 2 なら 3840x2160）、30fps、qtrle argb の .mov

見た目:
  縁取り・帯・下線・箱は使わない。読みやすさのための、ごく柔らかく広い影だけを敷く。
  書体は Noto Sans JP。通常の文は太さ 400、色 #d9e1ea。強調は太さ 700、色 #ffffff。
  強調の中の数字（と単位）は #f0c04a。強調でも文字の大きさは変えない。
  s / m は字間 +0.04em。l / xl / xxl は通常の太さ 340（細すぎると背景に負ける）、強調 700、字間 -0.01em。行の高さは 1.45（大きい文字は 1.22）。
  行が「^」で始まるときは、本文の上に置く小さな見出し（大きさ 0.42 倍、太さ 500、字間 0.18em、色 #9fb0c1）。
    例: "{pos=bc size=m}^荷待ち/積む場所で*長く*待たされること"

動き（どれも 1 回だけ。行き過ぎ・揺れなし）:
  rise(既定) / slam / type  0.45 秒で 10px 上へ動きながら、ぼかし 6px から 0 へ、現れる（easeOutCubic）。順にずらす
  slide                      文字を置いた側から 24px 滑りながら現れる
  mask                       各行が左から右へやわらかいマスクで現れる
  消え方                     最後の 0.25 秒で、6px 上へ動きながら薄くなる
  （slam と type は互換のために受け付けて、rise と同じ動きにする）

使い方:
  uv run python tools/video/make_kinetic3.py --cards outputs/video/subs_r6/cards.json --total 179.4 --out outputs/video/subs_r6/kinetic3.mov
  4K で出す: --scale 2
  確認用の静止画: --stills DIR --still-times 0.1,0.3,1.5 [--still-bg 背景画像]
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
import make_kinetic as K  # noqa: E402
import make_kinetic2 as K2  # noqa: E402

W, H, FPS = K.W, K.H, K.FPS
POS = K2.POS
MARGIN_X, MARGIN_TOP = K2.MARGIN_X, K2.MARGIN_TOP
BASELINE_LAST = K2.BASELINE_LAST
MAX_W, MAX_H = K2.MAX_W, K2.MAX_H

# 見た目と動きはすべてここ。--style FILE.json で一部だけ上書きできる（入れ子は項目ごとに混ぜる）。
# 長さは 1080p の px。em と書いたものはその行の文字の大きさの倍。色は (R, G, B)。
STYLE: dict = {
    "look": "pivot",      # "pivot"（平らな箱、既定）か "editorial"（箱なしの落ち着いた文字。sizes〜motion が効く）
    # pivot 風: 1 行ごとの平らな箱。作法は PIVOT を参考に、色と値は私たちで決めた（docs/video/2026-11-07_PIVOTのテロップの作り.md）
    "pivot": {
        "sizes": {"s": 64, "m": 88, "l": 116, "xl": 150, "xxl": 200}, "weight": 900,
        "box_h": 124 / 88, "pad_x": 28 / 88, "gap": 12 / 88,            # 文字の大きさに対する倍率
        "left_x": 160, "right_margin": 160, "bottom_y": 990, "top_y": 96,
        # 11/7 本線: この動画は暗い場面が多く、紺の箱が背景に沈むので、強調の箱はロゴの「帰りが埋まる」黄に黒い文字にした
        "colors": {"box": (255, 255, 255), "text": (20, 20, 20), "navy": (240, 192, 74), "navy_text": (20, 20, 20),
                   "orange": (232, 89, 12), "orange_on_navy": (18, 53, 91)},   # 紺の箱の上では #E8590C が沈むので明るくした
        "chapter": {"x": 64, "y": 56, "box_h": 72, "pad_x": 24, "bar_w": 8, "size": 40, "weight": 700, "show": 4.0},
        "name": {"x": 160, "bottom_y": 960, "title_size": 30, "title_weight": 500, "title_h": 48, "title_pad_x": 16,
                 "name_size": 52, "name_weight": 700, "name_h": 80, "name_pad_x": 24},
        "motion": {"box_dur": 0.28, "text_delay": 0.08, "text_dur": 0.22, "text_dx": 16, "line_delay": 0.10,
                   "exit_dur": 0.15, "min_show": 1.6},
    },
    "sizes": {"s": 44, "m": 58, "l": 96, "xl": 150, "xxl": 220},
    "weights": {"normal": 400, "normal_big": 340, "emph": 700, "kicker": 500},   # l/xl/xxl を big とする
    "letter_spacing": {"small": 0.04, "big": -0.01, "kicker": 0.18},               # em
    "line_height": {"small": 1.45, "big": 1.22},
    "colors": {"normal": (217, 225, 234), "emph": (255, 255, 255), "number": (240, 192, 74), "kicker": (159, 176, 193)},
    "kicker_size": 0.42,                                  # 本文に対する倍率
    "shadow": {"sigma": 9.0, "alpha": 0.45, "layers_big": 2, "thicken": 0.012},   # alpha は上限。thicken は影だけの太らせ（em）
    # 文字の背面の帯。on を true にする。per は "line"（行ごと）か "block"（全体で 1 つ）。
    # sizes は帯をつける大きさの指定。alpha 1.0 で不透明。on のとき影は shadow_with_bar の倍率に弱まる
    "bar": {"on": False, "color": (8, 12, 18), "alpha": 0.62, "radius": 0.30, "pad_x": 0.55, "pad_y": 0.28,
            "per": "line", "sizes": ["s", "m"], "shadow_with_bar": 0.0, "include_kicker": False},
    # 強調の方法。mode は "color"（色と太さだけ）か "label"（塗りの角丸ラベルの上に文字）
    "emph": {"mode": "color", "number_accent": True,
             "label_color": (240, 192, 74), "label_text": (10, 14, 20), "label_alpha": 1.0,
             "label_radius": 0.18, "label_pad_x": 0.22, "label_pad_y": 0.06},
    "motion": {"fade_dur": 0.45, "fade_dy": 10.0, "fade_blur": 6.0,
               "slide_dur": 0.50, "slide_px": 24.0, "mask_dur": 0.70, "mask_soft": 160,
               "exit_dur": 0.25, "exit_dy": 6.0, "reveal": 0.75, "max_stagger": 1.2, "kicker_lead": 0.14},
}


def merge_style(over: dict, dst: dict | None = None) -> dict:
    dst = STYLE if dst is None else dst
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge_style(v, dst[k])
        else:
            dst[k] = tuple(v) if isinstance(v, list) and k in ("color", "normal", "emph", "number", "kicker", "label_color", "label_text", "box", "text", "navy", "navy_text", "orange", "orange_on_navy") else v
    return dst


BG = K2.BG
NUM = re.compile(r"約?[0-9][0-9,.]*(?:[万千百億][0-9,.]*)*" + K.UNIT)


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
    def sp_em(self) -> float:
        return STYLE["letter_spacing"]["big" if self.big else "small"]

    def weight(self, emph: bool, kick: bool = False) -> int:
        w = STYLE["weights"]
        if kick:
            return w["kicker"]
        if emph:
            return w["emph"]
        return w["normal_big"] if self.big else w["normal"]


def parse_directive(text: str) -> tuple[Style, str]:
    s2, rest = K2.parse_directive(text)
    return Style(s2.pos, s2.size, s2.anim, s2.hide), rest


def parse_card(text: str):
    """行ごとの (種類, [(かたまり, 強調か, 組の番号)])。種類は "k"（見出し）か "b"（本文）。"""
    out = []
    gid = 0
    for line in text.split("/"):
        if line.startswith("^"):
            if line[1:].strip():
                out.append(("k", [(line[1:].strip(), False, -1)]))
            continue
        row = K2.parse_card(line, False)[0]
        row = [(c, e, (g + gid) if e else -1) for c, e, g in row]
        gid += sum(1 for _, e, _ in row if e)
        if row:
            out.append(("b", row))
    return out


def adv_of(f, t: str, sp: float) -> float:
    return f.getlength(t) + sp * len(t)


def layout(lines, st: Style, fit: float):
    base = STYLE["sizes"][st.size] * fit
    ks = STYLE["kicker_size"]
    label = STYLE["emph"]["mode"] == "label"
    lpx = STYLE["emph"]["label_pad_x"]
    sizes = [base * ks if k == "k" else base for k, _ in lines]

    def sp_of(kind):
        return STYLE["letter_spacing"]["kicker"] * base * ks if kind == "k" else st.sp_em * base

    def fnt(kind, e, sz):
        return K.font(sz, st.weight(e, kind == "k"))

    widths = []
    for (kind, row), sz in zip(lines, sizes):
        sp = sp_of(kind)
        widths.append(sum(adv_of(fnt(kind, e, sz), c, sp) + (2 * lpx * sz if label and e and kind != "k" else 0)
                          for c, e, _ in row) - sp)
    rel = [0.0]
    for i in range(1, len(lines)):
        if lines[i - 1][0] == "k":
            rel.append(rel[-1] + 0.72 * sizes[i - 1] + 0.88 * sizes[i])
        else:
            rel.append(rel[-1] + STYLE["line_height"]["big" if st.big else "small"] * sizes[i])
    top, bottom = -0.88 * sizes[0], rel[-1] + 0.12 * sizes[-1]
    height = bottom - top
    if st.v == "t":
        base0 = MARGIN_TOP + 0.88 * sizes[0]
    elif st.v == "b":
        base0 = min(BASELINE_LAST, 990 - 0.12 * sizes[-1]) - rel[-1]
    else:
        base0 = H / 2 - height / 2 + 0.88 * sizes[0]
    out = []
    for li, ((kind, row), sz) in enumerate(zip(lines, sizes)):
        b = base0 + rel[li]
        sp = sp_of(kind)
        x = MARGIN_X if st.h == "l" else (W - MARGIN_X - widths[li]) if st.h == "r" else (W - widths[li]) / 2
        for c, e, g in row:
            f = fnt(kind, e, sz)
            vis = adv_of(f, c, sp) - sp
            lab = label and e and kind != "k"
            if lab:
                x += lpx * sz
            out.append({"text": c, "emph": e, "grp": g, "line": li, "kick": kind == "k", "x": x, "vis": vis,
                        "cx": x + vis / 2, "base": b, "size": sz, "sp": sp, "label": lab,
                        "weight": st.weight(e, kind == "k")})
            x += adv_of(f, c, sp) + (lpx * sz if lab else 0)
    return out, max(widths), height


def char_colors(c: dict) -> list:
    t = c["text"]
    col = STYLE["colors"]
    if c["kick"]:
        return [col["kicker"]] * len(t)
    if c.get("label"):
        return [STYLE["emph"]["label_text"]] * len(t)
    if not c["emph"]:
        return [col["normal"]] * len(t)
    cols = [col["emph"]] * len(t)
    if STYLE["emph"]["number_accent"]:
        for m in NUM.finditer(t):
            for i in range(m.start(), m.end()):
                cols[i] = col["number"]
    return cols


def make_sprite(c: dict, S: int):
    """1 つのかたまりの絵（影の層と文字の層）。S は出力の倍率。"""
    from PIL import Image, ImageDraw, ImageFilter
    t, size = c["text"], c["size"]
    f = K.font(size * S, c["weight"])
    asc, desc = f.getmetrics()
    sp = c["sp"] * S
    shd = STYLE["shadow"]
    sigma = max(shd["sigma"], 0.06 * size) * S
    off = max(2.0, 0.015 * size) * S
    pad = math.ceil(3 * max(sigma, STYLE["motion"]["fade_blur"] * S) + off) + 4
    vis = c["vis"] * S
    w, h = math.ceil(vis) + 2 * pad, asc + desc + 2 * pad
    by = pad + asc
    cols = char_colors(c)
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sw = max(1, round(shd["thicken"] * size * S))  # 影だけを少し太らせ、細い文字でも暗さを確保する（見える縁ではない）
    a = (0, 0, 0, round(255 * shd["alpha"] * c.get("shadow_k", 1.0)))
    for k, ch in enumerate(t):
        sd.text((pad + f.getlength(t[:k]) + sp * k, by + off), ch, font=f, anchor="ls",
                fill=a, stroke_width=sw, stroke_fill=a)
    sh = sh.filter(ImageFilter.GaussianBlur(sigma))
    base_col = cols[0] if len(set(cols)) == 1 else STYLE["colors"]["emph"]
    mn = Image.new("RGBA", (w, h), base_col + (0,))
    md = ImageDraw.Draw(mn)
    for k, ch in enumerate(t):
        md.text((pad + f.getlength(t[:k]) + sp * k, by), ch, font=f, anchor="ls", fill=cols[k] + (255,))
    return {"sh": sh, "mn": mn, "anchor": (pad + vis / 2, by - 0.35 * size * S), "blur": {}}


def prepare(card: dict, S: int) -> dict | None:
    st, text = parse_directive(card["text"])
    if st.hide:
        return None
    lines = parse_card(text)
    fit = 1.0
    for _ in range(8):
        chunks, mw, hh = layout(lines, st, fit)
        k = min(1.0, MAX_W / mw if mw else 1.0, MAX_H / hh if hh else 1.0)
        if k >= 0.999:
            break
        fit *= k * 0.995
    dur = card["end"] - card["start"]
    mo = STYLE["motion"]
    bar_on = STYLE["bar"]["on"] and st.size in STYLE["bar"]["sizes"]
    body = [c for c in chunks if not c["kick"] and c["text"].strip()]
    total = sum(len(c["text"].strip()) for c in body) or 1
    lead = mo["kicker_lead"] if any(c["kick"] for c in chunks) else 0.0
    window = min(dur * mo["reveal"], mo["max_stagger"])
    body_lines = sorted({c["line"] for c in body})
    step = 0.0
    if st.anim == "mask" and len(body_lines) > 1:
        step = max(0.05, min(0.2, (dur * mo["reveal"] - mo["mask_dur"]) / (len(body_lines) - 1)))
    acc = 0
    for c in chunks:
        if c["kick"]:
            c["t0"] = card["start"]
        elif st.anim == "mask":
            c["t0"] = card["start"] + lead + body_lines.index(c["line"]) * step
        else:
            c["t0"] = card["start"] + lead + acc / total * window
            acc += len(c["text"].strip())
        if c["kick"] or st.anim in ("rise", "slam", "type"):
            c["mode"], c["dur"] = "fade", mo["fade_dur"]
        elif st.anim == "slide":
            c["mode"], c["dur"] = "slide", mo["slide_dur"]
        else:
            c["mode"], c["dur"] = "mask", mo["mask_dur"]
        c["shadow_k"] = STYLE["bar"]["shadow_with_bar"] if bar_on else 1.0
        c["sprite"] = make_sprite(c, S)
    if st.h == "l":
        sd = (-mo["slide_px"], 0.0)
    elif st.h == "r":
        sd = (mo["slide_px"], 0.0)
    elif st.v == "t":
        sd = (0.0, -mo["slide_px"])
    else:
        sd = (0.0, mo["slide_px"])
    for c in chunks:
        c["sd"] = sd
    lines_idx = [[i for i, c in enumerate(chunks) if c["line"] == li] for li in range(len(lines))]
    return {"style": st, "chunks": chunks, "lines": lines_idx, "bar": bar_on}


def ease_out_cubic(p: float) -> float:
    return 1 - (1 - p) ** 3


def chunk_state(c: dict, t: float):
    """時刻 t のかたまりの状態 (不透明度, 横ずれ, 縦ずれ, ぼかし, マスクの進み)。まだ出ていなければ None。"""
    p = (t - c["t0"]) / c["dur"]
    if p <= 0:
        return None
    e = ease_out_cubic(min(1.0, p))
    m = c["mode"]
    mo = STYLE["motion"]
    FADE_DY, FADE_BLUR = mo["fade_dy"], mo["fade_blur"]
    if m == "fade":
        return (round(e, 3), 0.0, round(FADE_DY * (1 - e), 2), round(FADE_BLUR * (1 - e) * 2) / 2, 1.0)
    if m == "slide":
        return (round(e, 3), round(c["sd"][0] * (1 - e), 2), round(c["sd"][1] * (1 - e), 2), 0.0, 1.0)
    return (1.0, round(-12 * (1 - e), 2), 0.0, 0.0, round(e, 3))


def mask_image(x0: float, x1: float, mp: float, S: int):
    from PIL import Image
    MASK_SOFT = STYLE["motion"]["mask_soft"]
    edge = (x0 - 60) + mp * ((x1 + 60 + MASK_SOFT) - (x0 - 60))
    row = Image.new("L", (W * S, 1))
    vals = []
    for X in range(W * S):
        v = min(1.0, max(0.0, (edge - X / S) / MASK_SOFT))
        vals.append(round(255 * v * v * (3 - 2 * v)))
    row.putdata(vals)
    return row.resize((W * S, H * S), Image.NEAREST)


def blurred(sp: dict, bl: float, S: int):
    from PIL import ImageFilter
    if bl <= 0:
        return sp["mn"]
    if bl not in sp["blur"]:
        sp["blur"][bl] = sp["mn"].filter(ImageFilter.GaussianBlur(bl * S))
    return sp["blur"][bl]


def rrect(canvas, box, radius, color, alpha, S):
    """角丸の塗り。box は 1080p の (x0, y0, x1, y1)。"""
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = [round(v * S) for v in box]
    if x1 - x0 < 2 or alpha <= 0:
        return
    layer = Image.new("RGBA", (x1 - x0 + 2, y1 - y0 + 2), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((0, 0, x1 - x0, y1 - y0), radius=radius * S, fill=tuple(color) + (round(255 * min(1.0, alpha)),))
    K.over(canvas, layer, x0, y0)


def draw_bars(cd: dict, states: tuple, canvas, S: int):
    b = STYLE["bar"]
    chunks = cd["chunks"]
    groups = []
    if b["per"] == "block":
        groups.append([i for i, c in enumerate(chunks) if b["include_kicker"] or not c["kick"]])
    else:
        for idxs in cd["lines"]:
            if idxs and (b["include_kicker"] or not chunks[idxs[0]]["kick"]):
                groups.append(idxs)
    for g in groups:
        vis = [i for i in g if states[i] is not None and chunks[i]["text"].strip()]
        if not vis:
            continue
        sz = max(chunks[i]["size"] for i in g)
        x0, x1 = min(chunks[i]["x"] - (STYLE["emph"]["label_pad_x"] * chunks[i]["size"] if chunks[i]["label"] else 0) for i in g),             max(chunks[i]["x"] + chunks[i]["vis"] + (STYLE["emph"]["label_pad_x"] * chunks[i]["size"] if chunks[i]["label"] else 0) for i in g)
        ytop = min(chunks[i]["base"] - 0.88 * chunks[i]["size"] for i in g)
        ybot = max(chunks[i]["base"] + 0.22 * chunks[i]["size"] for i in g)
        px, py = b["pad_x"] * sz, b["pad_y"] * sz
        op = max(states[i][0] for i in vis)
        dy = states[vis[0]][2]
        rrect(canvas, (x0 - px, ytop - py + dy, x1 + px, ybot + py + dy), b["radius"] * sz, b["color"], b["alpha"] * op, S)


def draw_card(cd: dict, states: tuple, exit_p: float, S: int):
    from PIL import Image, ImageChops
    st, chunks = cd["style"], cd["chunks"]
    em = STYLE["emph"]
    canvas = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    if cd["bar"]:
        draw_bars(cd, states, canvas, S)
    nsh = STYLE["shadow"]["layers_big"] if st.big else 1
    for idxs in cd["lines"]:
        vis = [i for i in idxs if states[i] is not None]
        if not vis:
            continue
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0)) if st.anim == "mask" else canvas
        for name in ["sh"] * nsh + ["lb", "mn"]:
            for i in vis:
                c, s = chunks[i], states[i]
                sp = c["sprite"]
                if name == "lb":
                    if c["label"]:
                        px, py = em["label_pad_x"] * c["size"], em["label_pad_y"] * c["size"]
                        rrect(layer, (c["x"] - px + s[1], c["base"] - 0.84 * c["size"] - py + s[2],
                                      c["x"] + c["vis"] + px + s[1], c["base"] + 0.22 * c["size"] + py + s[2]),
                              em["label_radius"] * c["size"], em["label_color"], em["label_alpha"] * s[0], S)
                    continue
                if name == "sh" and c["label"]:
                    continue
                img = sp["sh"] if name == "sh" else blurred(sp, s[3], S)
                ax, ay = sp["anchor"]
                gy = c["base"] - 0.35 * c["size"] + s[2]
                K.over(layer, K.fade(img, s[0]), round((c["cx"] + s[1]) * S - ax), round(gy * S - ay))
        if st.anim == "mask":
            lx0 = min(chunks[i]["x"] for i in idxs)
            lx1 = max(chunks[i]["x"] + chunks[i]["vis"] for i in idxs)
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask_image(lx0, lx1, states[vis[0]][4], S)))
            canvas.alpha_composite(layer)
    if exit_p > 0:
        e = ease_out_cubic(exit_p)
        canvas = K.fade(canvas, 1 - e)
        full = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        full.paste(canvas, (0, -round(STYLE["motion"]["exit_dy"] * S * e)))
        canvas = full
    return canvas


# ---------------------------------------------------------------- pivot 風（平らな箱）
def bez(x1: float, y1: float, x2: float, y2: float):
    """cubic-bezier の加減速。"""
    def f(p: float) -> float:
        if p <= 0:
            return 0.0
        if p >= 1:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(30):
            m = (lo + hi) / 2
            if 3 * (1 - m) ** 2 * m * x1 + 3 * (1 - m) * m ** 2 * x2 + m ** 3 < p:
                lo = m
            else:
                hi = m
        t = (lo + hi) / 2
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t ** 2 * y2 + t ** 3
    return f


EASE_PIV = bez(0.2, 0.8, 0.2, 1.0)


def split_ext(text: str) -> tuple[dict, str]:
    """{kind=… line2=…} を取り出し、残りの指定だけを {…} に戻す。"""
    m = re.match(r"\s*\{([^}]*)\}", text)
    if not m:
        return {}, text
    ext, rest = {}, []
    for tok in m.group(1).split():
        k, _, v = tok.partition("=")
        if k in ("kind", "line2", "maxw", "x"):  # maxw=箱の最大幅、x=左端（1080p の px。ブラウザーの窓の横に置くとき）
            ext[k] = v
        else:
            rest.append(tok)
    return ext, ("{" + " ".join(rest) + "}" if rest else "") + text[m.end():]


def segs_of(line: str, used: list) -> list:
    """「*…*」を (文字, 色付きか) の並びにする。色付きは 1 枚のカードで最初の 1 か所だけ。"""
    out = []
    for i, part in enumerate(line.split("*")):
        if not part:
            continue
        col = i % 2 == 1 and not used[0]
        if col:
            used[0] = True
        out.append((part, col))
    return out


def seg_width(f, segs) -> float:
    return sum(f.getlength(t) for t, _ in segs)


def prepare_pivot(card: dict, S: int, ext: dict, st: Style, text: str) -> dict:
    PV = STYLE["pivot"]
    mo = PV["motion"]
    kind = ext.get("kind", "caption")
    used = [False]
    lines = [l for l in text.split("/") if l.strip()]
    boxes = []
    if kind == "chapter":
        c = PV["chapter"]
        f = K.font(c["size"], c["weight"])
        segs = segs_of(lines[0].replace("*", ""), [True])
        w = seg_width(f, segs) + 2 * c["pad_x"]
        boxes.append({"segs": segs, "x": c["x"], "y": c["y"], "w": w + c["bar_w"], "h": c["box_h"], "size": c["size"],
                      "weight": c["weight"], "bg": PV["colors"]["box"], "fg": PV["colors"]["text"],
                      "tx": c["bar_w"] + c["pad_x"], "bar": (c["bar_w"], PV["colors"]["navy"])})
    elif kind == "name":
        c = PV["name"]
        t_segs = segs_of(lines[0].replace("*", ""), [True])
        n_segs = segs_of((lines[1] if len(lines) > 1 else "").replace("*", ""), [True])
        ft, fn = K.font(c["title_size"], c["title_weight"]), K.font(c["name_size"], c["name_weight"])
        ny = c["bottom_y"] - c["name_h"]
        boxes.append({"segs": t_segs, "x": c["x"], "y": ny - c["title_h"], "w": seg_width(ft, t_segs) + 2 * c["title_pad_x"],
                      "h": c["title_h"], "size": c["title_size"], "weight": c["title_weight"], "bg": PV["colors"]["navy"],
                      "fg": PV["colors"]["navy_text"], "tx": c["title_pad_x"], "bar": None})
        if n_segs:
            boxes.append({"segs": n_segs, "x": c["x"], "y": ny, "w": seg_width(fn, n_segs) + 2 * c["name_pad_x"],
                          "h": c["name_h"], "size": c["name_size"], "weight": c["name_weight"], "bg": PV["colors"]["box"],
                          "fg": PV["colors"]["text"], "tx": c["name_pad_x"], "bar": None})
    else:
        base = PV["sizes"][st.size]
        rows = []
        for i, line in enumerate(lines):
            navy = line.startswith("!") or (ext.get("line2") == "navy" and i == 1)
            rows.append((segs_of(line.lstrip("!"), used), navy))
        fit = 1.0
        for _ in range(6):
            sz = base * fit
            fnt = K.font(sz, PV["weight"])
            ws = [seg_width(fnt, s) + 2 * PV["pad_x"] * sz for s, _ in rows]
            lim = W - PV["left_x"] - PV["right_margin"] if st.h != "c" else W - 2 * MARGIN_X
            if "maxw" in ext:
                lim = min(lim, float(ext["maxw"]))
            if max(ws) <= lim:
                break
            fit *= lim / max(ws) * 0.995
        sz = base * fit
        bh, gap = PV["box_h"] * sz, PV["gap"] * sz
        n = len(rows)
        total = n * bh + (n - 1) * gap
        if st.v == "b":
            top0 = PV["bottom_y"] - total
        elif st.v == "t":
            top0 = PV["top_y"]
        else:
            top0 = (H - total) / 2
        for i, ((segs, navy), w) in enumerate(zip(rows, ws)):
            x = float(ext.get("x", PV["left_x"])) if st.h == "l" else (W - PV["left_x"] - w) if st.h == "r" else (W - w) / 2
            boxes.append({"segs": segs, "x": x, "y": top0 + i * (bh + gap), "w": w, "h": bh, "size": sz,
                          "weight": PV["weight"], "bg": PV["colors"]["navy"] if navy else PV["colors"]["box"],
                          "fg": PV["colors"]["navy_text"] if navy else PV["colors"]["text"], "tx": PV["pad_x"] * sz,
                          "navy": navy, "bar": None})
    for i, b in enumerate(boxes):
        b["t0"] = card["start"] + i * mo["line_delay"]
    return {"pivot": True, "boxes": boxes, "exit_dur": mo["exit_dur"]}


def pivot_state(cd: dict, t: float):
    mo = STYLE["pivot"]["motion"]
    out = []
    for b in cd["boxes"]:
        tb = t - b["t0"]
        if tb <= 0:
            out.append(None)
            continue
        e = EASE_PIV(min(1.0, tb / mo["box_dur"]))
        tt = (tb - mo["text_delay"]) / mo["text_dur"]
        et = EASE_PIV(min(1.0, max(0.0, tt)))
        out.append((round(e, 3), round(et, 3), round(-mo["text_dx"] * (1 - et), 2)))
    return tuple(out)


def draw_pivot(cd: dict, states: tuple, exit_p: float, S: int):
    from PIL import Image, ImageDraw
    PV = STYLE["pivot"]
    canvas = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    for b, s in zip(cd["boxes"], states):
        if s is None:
            continue
        bw, bh = math.ceil(b["w"] * S), round(b["h"] * S)
        layer = Image.new("RGBA", (bw, bh), tuple(b["bg"]) + (255,))
        if b["bar"]:
            ImageDraw.Draw(layer).rectangle((0, 0, round(b["bar"][0] * S) - 1, bh), fill=tuple(b["bar"][1]) + (255,))
        if s[1] > 0:
            tl = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            td = ImageDraw.Draw(tl)
            f = K.font(b["size"] * S, b["weight"])
            x = (b["tx"] + s[2]) * S
            by = bh / 2 + 0.34 * b["size"] * S
            for txt, col in b["segs"]:
                if col:
                    c = PV["colors"]["orange_on_navy"] if b.get("navy") else PV["colors"]["orange"]
                else:
                    c = b["fg"]
                td.text((x, by), txt, font=f, anchor="ls", fill=tuple(c) + (255,))
                x += f.getlength(txt)
            layer.alpha_composite(K.fade(tl, s[1]))
        wipe = max(1, round(bw * s[0]))
        K.over(canvas, layer.crop((0, 0, wipe, bh)), round(b["x"] * S), round(b["y"] * S))
    if exit_p > 0:
        canvas = K.fade(canvas, 1 - exit_p ** 2)
    return canvas


# ---------------------------------------------------------------- 共通の流れ
def frame_state(prep: list, tl: list, t: float):
    out = []
    for i, ent in enumerate(tl):
        if ent is None:
            continue
        s, e, ex = ent
        if s <= t < e:
            cd = prep[i]
            if cd.get("pivot"):
                states = pivot_state(cd, t)
                dur = cd["exit_dur"]
            else:
                states = tuple(chunk_state(c, t) for c in cd["chunks"])
                dur = STYLE["motion"]["exit_dur"]
            exit_p = 0.0
            if ex and t > e - dur:
                exit_p = round(min(1.0, (t - (e - dur)) / dur), 3)
            out.append((i, states, exit_p))
    return tuple(out) or None


def draw_frame(prep: list, fs, S: int):
    from PIL import Image
    canvas = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    for i, states, exit_p in fs or ():
        cd = prep[i]
        canvas.alpha_composite(draw_pivot(cd, states, exit_p, S) if cd.get("pivot") else draw_card(cd, states, exit_p, S))
    return canvas


def build(cards: list[dict], S: int):
    pivot = STYLE["look"] == "pivot"
    PV = STYLE["pivot"]
    items = []
    for c in cards:
        ext, txt = split_ext(c["text"])
        if parse_directive(txt)[0].hide:
            continue
        c2 = dict(c, text=txt)
        if pivot:
            if ext.get("kind") == "chapter":
                c2["end"] = c["start"] + PV["chapter"]["show"]
            else:
                c2["end"] = max(c["end"], c["start"] + PV["motion"]["min_show"])
        items.append((c2, ext, ext.get("kind", "caption") if pivot else "caption"))
    groups: dict = {}
    for idx, (c, _, kind) in enumerate(items):
        groups.setdefault(kind, []).append(idx)
    tl: list = [None] * len(items)
    for kind, idxs in groups.items():
        for a, b in zip(idxs, idxs[1:]):  # 同じ層の次のカードが始まったら、そこで切る
            if items[a][0]["end"] > items[b][0]["start"]:
                items[a][0]["end"] = items[b][0]["start"]
        for i, ent in zip(idxs, K.timeline([items[i][0] for i in idxs])):
            tl[i] = ent
    prep = []
    for c, ext, kind in items:
        if pivot:
            st, body = parse_directive(c["text"])
            prep.append(prepare_pivot(c, S, ext, st, body))
        else:
            prep.append(prepare(c, S))
    return prep, tl


def render(cards, total, out: Path, S=1, stills: Path | None = None, still_times=None, still_bg: Path | None = None):
    from PIL import Image
    n_frames = round(total * FPS)
    prep, tl = build(cards, S)
    out.parent.mkdir(parents=True, exist_ok=True)
    if stills is not None:
        stills.mkdir(parents=True, exist_ok=True)
        if still_bg:
            bgimg = Image.open(still_bg).convert("RGBA").resize((W * S, H * S), Image.LANCZOS)
        else:
            bgimg = Image.new("RGBA", (W * S, H * S), BG + (255,))
        for t in still_times or []:
            bg = bgimg.copy()
            bg.alpha_composite(draw_frame(prep, frame_state(prep, tl, t), S))
            bg.convert("RGB").save(stills / f"still_{t:07.3f}.png")
        print(f"静止画 {len(still_times or [])} 枚 -> {stills}")
    if n_frames <= 0 or out.suffix == "":
        return
    tmp = Path(tempfile.mkdtemp(prefix="kinetic3_", dir=out.parent))
    Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0)).save(tmp / "blank.png")
    names: dict = {}
    seq: list[list] = []
    for n in range(n_frames):
        fs = frame_state(prep, tl, n / FPS)
        name = "blank.png"
        if fs:
            if fs not in names:
                fn = f"f{len(names):05d}.png"
                draw_frame(prep, fs, S).save(tmp / fn, compress_level=1)
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
    print(f"描いたコマ {len(names)} / 全 {n_frames} コマ ({W * S}x{H * S}) -> {out}")
    for p in tmp.glob("*"):
        p.unlink()
    tmp.rmdir()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", required=True)
    ap.add_argument("--total", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--style", help="STYLE を一部上書きする JSON（例: {\"bar\": {\"on\": true}}）")
    ap.add_argument("--look", choices=("pivot", "editorial"), help="見た目（既定は STYLE[look]）")
    ap.add_argument("--scale", type=int, default=1, choices=(1, 2), help="2 で 3840x2160")
    ap.add_argument("--stills", help="確認用の静止画を出すフォルダ")
    ap.add_argument("--still-times", default="", help="静止画の時刻（秒）をカンマ区切りで")
    ap.add_argument("--still-bg", help="静止画の背景にする画像（既定は暗い単色）")
    a = ap.parse_args()
    if a.look:
        STYLE["look"] = a.look
    if a.style:
        merge_style(json.loads(Path(a.style).read_text(encoding="utf-8")))
    cards = json.loads(Path(a.cards).read_text(encoding="utf-8"))
    times = [float(x) for x in a.still_times.split(",") if x.strip()]
    render(cards, a.total, Path(a.out), a.scale, Path(a.stills) if a.stills else None, times,
           Path(a.still_bg) if a.still_bg else None)


if __name__ == "__main__":
    main()
