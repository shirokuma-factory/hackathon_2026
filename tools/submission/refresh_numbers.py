"""README・試行錯誤の記録・CLAUDE_USAGE の数字（コミット数、日別のコミット、試験の件数、採点の記録の数、日数）を実物から数え直して書き換える。

使い方: uv run python tools/submission/refresh_numbers.py
提出の直前にもう一度流す。書き換えた所を 1 行ずつ出す。何度流しても同じ結果になる。
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8", "replace")


def facts() -> dict:
    commits = int(run("git", "rev-list", "--count", "HEAD").strip())
    days = Counter(run("git", "log", "HEAD", "--date=format-local:%m-%d", "--pretty=%ad").split())
    days = dict(sorted(days.items()))
    # 公開されるもの（git が追跡している試験ファイル）だけを数える。未追跡の試験ファイルは --ignore で外す
    untracked = [x for x in run("git", "-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard", "tests").splitlines()
                 if x.endswith(".py")]
    out = run(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--collect-only",
              *[f"--ignore={x}" for x in untracked])
    m = re.search(r"(\d+) tests? collected", out)
    tests = int(m.group(1)) if m else None
    # 公開されるもの（git が追跡しているファイル）だけを数える。未コミットの記録は数えない
    reviews = len([x for x in run("git", "-c", "core.quotepath=false", "ls-files", "docs/reviews").splitlines() if x.endswith(".md")])
    plans = len([x for x in run("git", "-c", "core.quotepath=false", "ls-files", "docs/plan").splitlines() if x.endswith(".md") and x.count("/") == 2])
    return {"commits": commits, "days": days, "tests": tests, "reviews": reviews, "ndays": len(days), "plans": plans}


def fmt(n: int) -> str:
    return f"{n:,}"


def edit(path: str, pairs: list[tuple[str, str]]) -> None:
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    t = s
    for pat, rep in pairs:
        t = re.sub(pat, rep, t)
    if t != s:
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
        print("書き換えた:", path)


def main() -> None:
    import datetime
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    f = facts()
    c, tests, rv, nd = f["commits"], fmt(f["tests"]), f["reviews"], f["ndays"]
    days = [(d, n) for d, n in f["days"].items()]
    last = days[-1][0].replace("11-0", "11-").replace("-", "/") if days else ""
    head = "| 日 | " + " | ".join(d.lstrip("0").replace("-0", "/").replace("-", "/") for d, _ in days) + " |"
    sep = "|---|" + "---:|" * len(days)
    row = "| コミット | " + " | ".join(str(n) for _, n in days) + " |"
    print(f"コミット {c}、試験 {tests}、採点の記録 {rv}、日数 {nd}（{', '.join(f'{d}:{n}' for d, n in days)}）")

    edit("README.md", [
        (r"\d+ 日間で [\d,]+ 件のコミットと [\d,]+ 件の自動試験", f"{nd} 日間で {c} 件のコミットと {tests} 件の自動試験"),
        (r"（採点・監査の記録 \d+ 件）", f"（採点・監査の記録 {rv} 件）"),
        (r"自動試験 [\d,]+ 件。GitHub Actions", f"自動試験 {tests} 件。GitHub Actions"),
        (r"\| 日 \|( \d+/\d+ \|)+\n\|---\|(---:\|)+\n\| コミット \|( \d+ \|)+", f"{head}\n{sep}\n{row}"),
    ])
    edit("docs/試行錯誤の記録.md", [
        (r"## 数字で見る \d+ 日間", f"## 数字で見る {nd} 日間"),
        (r"\| コミット（提出のブランチ） \| \d+ \|", f"| コミット（提出のブランチ） | {c} |"),
        (r"\| 採点・監査の記録 \| \d+ 件 \|", f"| 採点・監査の記録 | {rv} 件 |"),
        (r"\| 計画・判断の記録 \| \d+ 件 \|", f"| 計画・判断の記録 | {f['plans']} 件 |"),
        (r"(\| 自動試験 \| 127 → 727 → 1,442 → )[\d,]+( 件 \|)", rf"\g<1>{tests}\g<2>"),
        (r"`git rev-list --count HEAD`（[^）]*の時点）", f"`git rev-list --count HEAD`（{now} の時点）"),
    ])
    # 試行錯誤の記録の日別の表: コミット数を数え直し、無い日の行を足す（その日の試験の欄は、いまの件数）
    p = ROOT / "docs" / "試行錯誤の記録.md"
    s = p.read_text(encoding="utf-8")
    t = s
    for d, n in days:
        label = d.lstrip("0").replace("-0", "/").replace("-", "/")
        if re.search(rf"^\| {label} \| \d+ \|", t, re.M):
            t = re.sub(rf"^(\| {label} \| )\d+( \|)", rf"\g<1>{n}\g<2>", t, flags=re.M)
            if (d, n) == days[-1]:  # 最後の日（今日）の試験の欄は、いまの件数にそろえる
                t = re.sub(rf"^(\| {label} \| \d+ \| )[\d,]+（集めた数） \|", rf"\g<1>{tests}（集めた数） |", t, flags=re.M)
        else:
            prev = list(re.finditer(r"^\| \d+/\d+ \| \d+ \| [^\n]*\|$", t, re.M))
            if prev:
                at = prev[-1].end()
                t = t[:at] + f"\n| {label} | {n} | {tests}（集めた数） |" + t[at:]
    if t != s:
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(t)
        print("書き換えた: docs/試行錯誤の記録.md（日別の表）")
    edit("docs/CLAUDE_USAGE.md", [
        (r"\d+ 日間（2026-11-03〜11-\d+）", f"{nd} 日間（2026-11-03〜{last.replace('/', '-').replace('11-', '11-0') if len(last) == 4 else last}）"),
        (r"\| コミット \| \d+ \|", f"| コミット | {c} |"),
        (r"\| 自動試験 \| [\d,]+ 件 \|", f"| 自動試験 | {tests} 件 |"),
        (r"\| 採点・監査の記録 \| \d+ 件 \|", f"| 採点・監査の記録 | {rv} 件 |"),
        (r"\d+ 日で、試験 [\d,]+ 件つきのアプリを作る", f"{nd} 日で、試験 {tests} 件つきのアプリを作る"),
    ])


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
