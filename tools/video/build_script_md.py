"""docs/video/shots.json から、脚本の表と尺の検査を docs/video/脚本.md に書き出す。

使い方:
    uv run python tools/video/build_script_md.py

脚本.md の <!-- SHOTS:BEGIN --> と <!-- SHOTS:END --> の間だけを書き換える。
尺の検査: 各ショットの字幕の文字数 ÷ 秒が CHARS_PER_SEC_MAX を超えたら「速い」と出す。
合計の秒が max_seconds を超えたら終了コード 1。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "video" / "shots.json"
OUT = ROOT / "docs" / "video" / "脚本.md"
BEGIN = "<!-- SHOTS:BEGIN -->"
END = "<!-- SHOTS:END -->"
# 日本語のナレーションで聞き取りやすい上限の目安（字幕の文字、漢字かな混じり）。実測で直す。
CHARS_PER_SEC_MAX = 5.5


def cell(text: str) -> str:
    return text.replace("|", "／").replace("\n", " ")


def mmss(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def build(data: dict) -> tuple[str, bool]:
    lines = [
        "| 区間 | 秒 | 何を見せるか | ナレーション（字幕） | 字数 / 秒 | 使う MCP・道具 | 小さい注記 |",
        "|---|---:|---|---|---:|---|---|",
    ]
    ok = True
    total = 0
    for s in data["shots"]:
        dur = s["end"] - s["start"]
        total = max(total, s["end"])
        n = len(s["narration"])
        rate = n / dur
        mark = "" if rate <= CHARS_PER_SEC_MAX else " 速い"
        lines.append(
            f"| {s['id']} {cell(s['part'])}<br>{mmss(s['start'])}–{mmss(s['end'])} | {dur} | {cell(s['screen'])} "
            f"| {cell(s['narration'])} | {n} / {rate:.1f}{mark} | {cell('<br>'.join(s.get('mcp', [])))} "
            f"| {cell(s.get('caption_small', ''))}{('<br>' + cell(s['claude_badge'])) if s.get('claude_badge') else ''} |"
        )
    over = total > data["max_seconds"]
    if over:
        ok = False
    lines.append("")
    lines.append(
        f"合計 {total} 秒（上限 {data['max_seconds']} 秒）。字幕の字数の上限の目安は 1 秒 {CHARS_PER_SEC_MAX} 字で、"
        "音声を作ったあとに実測の長さで直す。"
    )
    return "\n".join(lines), ok


def main() -> int:
    data = json.loads(SHOTS.read_text(encoding="utf-8"))
    table, ok = build(data)
    md = OUT.read_text(encoding="utf-8")
    if BEGIN not in md or END not in md:
        print("脚本.md に目印がありません", file=sys.stderr)
        return 2
    head, rest = md.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    new = f"{head}{BEGIN}\n{table}\n{END}{tail}"
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    print(table.splitlines()[-1])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
