"""画面の左下に出す出典の行を、透明な動画（1920x1080、30fps、qtrle の .mov）にする。

入力: --cards の JSON。[{"start": 秒, "end": 秒, "text": "出典 …"}, ...]
出力: --out の .mov（--total 秒の長さ）。文字は Noto Sans JP 22px（太さ 500）、#c9d3de の約 80%、
      箱は無く、柔らかい暗い影だけ。左端から 60px、下の線（ベースライン）は下端から約 26px。
      出入りは 0.25 秒のフェード。同じ文字の札は 1 回だけ描き、concat で並べる。

使い方:
  uv run python tools/video/make_sources.py --cards sources_cards.json --total 176 --out outputs/video/subs/sources.mov
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

FFMPEG = "C:/ai/bin/ffmpeg.exe"
FONT = "C:/Windows/Fonts/NotoSansJP-VF.ttf"
W, H, FPS = 1920, 1080, 30
SIZE, WEIGHT = 16, 500  # 11/7 作成者「注釈はもっと小さく」で 22 から 16 に
COLOR = (0xC9, 0xD3, 0xDE)
OPACITY = 0.8
LEFT, BASE_FROM_BOTTOM = 60, 18
FADE = 0.25


def card_image(text: str, font, alpha: float):
    """alpha（0〜1）の分だけ薄くした 1 枚。"""
    from PIL import Image, ImageDraw, ImageFilter
    base_y = H - BASE_FROM_BOTTOM
    asc = font.getmetrics()[0]
    y = base_y - asc
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((LEFT, y + 2), text, font=font, fill=(0, 0, 0, int(200 * alpha)),
                            stroke_width=3, stroke_fill=(0, 0, 0, int(200 * alpha)))
    sh = sh.filter(ImageFilter.GaussianBlur(4))
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((LEFT, y), text, font=font, fill=COLOR + (int(255 * OPACITY * alpha),))
    return Image.alpha_composite(sh, im)


def render(cards: list[dict], total: float, out: Path) -> None:
    from PIL import Image, ImageFont
    font = ImageFont.truetype(FONT, SIZE)
    try:
        font.set_variation_by_axes([WEIGHT])
    except Exception:
        pass
    cards = sorted(cards, key=lambda c: c["start"])
    fr = 1 / FPS
    nfade = max(1, round(FADE * FPS))
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(d / "blank.png")
        made: dict[tuple[str, int], str] = {}

        def img(text: str, k: int) -> str:
            """k = 0 は全部の濃さ、1〜nfade は薄い段階（同じ文字は 1 回だけ描く）。"""
            key = (text, k)
            if key not in made:
                made[key] = f"t{len(made):03d}.png"
                a = 1.0 if k == 0 else k / (nfade + 1)
                card_image(text, font, a).save(d / made[key])
            return made[key]

        lst, t = [], 0.0
        for i, c in enumerate(cards):
            start = max(c["start"], t)
            end = min(c["end"], total)
            if i + 1 < len(cards):
                end = min(end, cards[i + 1]["start"])
            if end - start < fr * (2 * nfade + 1):
                end = start + fr * (2 * nfade + 1)
            if start > t:
                lst.append(("blank.png", start - t))
            for k in range(nfade):
                lst.append((img(c["text"], k + 1), fr))
            lst.append((img(c["text"], 0), end - start - fr * 2 * nfade))
            for k in range(nfade):
                lst.append((img(c["text"], nfade - k), fr))
            t = end
        if total > t:
            lst.append(("blank.png", total - t))
        with open(d / "list.txt", "w", encoding="utf-8", newline="\n") as fh:
            for n, dur in lst:
                fh.write(f"file '{n}'\nduration {dur:.4f}\n")
            fh.write(f"file '{lst[-1][0]}'\n")
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(d / "list.txt"),
                        "-vf", f"fps={FPS}", "-t", f"{total}", "-c:v", "qtrle", "-pix_fmt", "argb", str(out)], check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", required=True, help="[{start, end, text}] の JSON")
    ap.add_argument("--total", type=float, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cards = json.loads(Path(a.cards).read_text(encoding="utf-8"))
    render(cards, a.total, Path(a.out))
    print(f"出典 {len(cards)} 枚 -> {a.out}")


if __name__ == "__main__":
    main()
