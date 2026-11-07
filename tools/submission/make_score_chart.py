"""採点の推移を SVG の折れ線にする（GitHub の README でそのまま表示できる形）。

使い方: uv run python tools/submission/make_score_chart.py
出力: docs/assets/2026-11-07_score_history.svg

値の出典は docs/reviews/2026-11-05_筋書き_採点の推移.md と docs/video/脚本の採点の推移.md。
採点者が 2 人の回（第 6〜8 部）は 2 人の平均を描く。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "assets" / "2026-11-07_score_history.svg"


def avg(pairs: list[tuple[int, int]]) -> list[float]:
    return [(a + b) / 2 for a, b in pairs]


SERIES: list[tuple[str, list[float]]] = [
    ("第1部 筋書き（荷主）", [48, 51, 68, 62]),
    ("第2部 筋書き（運送会社）", [39, 60, 58, 68]),
    ("第3部 課題を解くか", [28, 47, 62, 55, 60]),
    ("第4部 論理の筋", [38, 63, 69, 72]),
    ("第5部 思想と公開版", [64, 68, 87, 89]),
    ("第6部 道具として", avg([(70, 60), (77, 72), (84, 73), (78, 75), (79, 74), (85, 83), (81, 87), (86, 84), (85, 86)])),
    ("第7部 実務の効き目", avg([(34, 40), (43, 50), (45, 61), (60, 55), (55, 40), (52, 66), (73, 58)])),
    ("第8部 画面の出来", avg([(45, 42), (65, 63), (60, 73), (64, 74)])),
    ("動画の脚本", [60, 68, 89, 88, 86, 90, 90, 96, 95, 93]),
]

W, H = 960, 520
L, R, T, B = 60, 230, 30, 50
MAX_ROUND = max(len(v) for _, v in SERIES)
PALETTE = ["#4e79a7", "#59a14f", "#9c755f", "#76b7b2", "#edc948",
           "#e15759", "#f28e2b", "#b07aa1", "#555555"]


def x(i: int) -> float:
    return L + (W - L - R) * i / (MAX_ROUND - 1)


def y(v: float) -> float:
    return T + (H - T - B) * (100 - v) / 100


def main() -> None:
    font = "font-family=\"'Noto Sans JP','Yu Gothic','Meiryo',sans-serif\""
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" {font} font-size="13">',
             f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    for v in range(0, 101, 20):
        parts.append(f'<line x1="{L}" y1="{y(v):.1f}" x2="{W - R}" y2="{y(v):.1f}" stroke="#e5e5e5"/>')
        parts.append(f'<text x="{L - 8}" y="{y(v) + 4:.1f}" text-anchor="end" fill="#666">{v}</text>')
    parts.append(f'<line x1="{L}" y1="{y(95):.1f}" x2="{W - R}" y2="{y(95):.1f}" stroke="#999" stroke-dasharray="4 4"/>')
    parts.append(f'<text x="{W - R - 4}" y="{y(95) - 6:.1f}" text-anchor="end" fill="#666">合格の線 95 点</text>')
    for i in range(MAX_ROUND):
        parts.append(f'<text x="{x(i):.1f}" y="{H - B + 20}" text-anchor="middle" fill="#666">r{i + 1}</text>')
    parts.append(f'<text x="{(L + W - R) / 2:.1f}" y="{H - 8}" text-anchor="middle" fill="#666">採点の回（毎回、前の点を知らない新しい Opus が採点）</text>')

    ends: list[tuple[float, str, str]] = []
    for (name, vals), color in zip(SERIES, PALETTE):
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.2"/>')
        for i, v in enumerate(vals):
            parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3" fill="{color}"><title>{name} r{i + 1}: {v:g} 点</title></circle>')
        ends.append((y(vals[-1]), f"{name} {vals[0]:g}→{vals[-1]:g}", color))

    # 右の名札は重ならないよう 18px ずつ離す
    ends.sort()
    last = -1e9
    for yy, label, color in ends:
        yy = max(yy, last + 18)
        last = yy
        parts.append(f'<text x="{W - R + 10}" y="{yy + 4:.1f}" fill="{color}">{label}</text>')
    parts.append("</svg>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts) + "\n")
    print(OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
