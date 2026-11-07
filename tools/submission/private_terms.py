"""作成者の呼び名・アカウント名など、リポジトリに書きたくない検索語を読む。

読み先（どちらも追跡しない）:
  - tools/submission/private_terms.txt（1 行に 1 つの正規表現。空行と # で始まる行は無視）
  - 環境変数 BACKFULL_PRIVATE_TERMS（改行か ; 区切りの正規表現）
どちらも無ければ空のリストを返し、呼び出し側はその確認を飛ばす。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

FILE = Path(__file__).resolve().parent / "private_terms.txt"
SKIP_NOTE = "（private_terms.txt が無いので飛ばした）"


def load() -> list[str]:
    raw: list[str] = []
    if FILE.is_file():
        raw += FILE.read_text(encoding="utf-8").splitlines()
    env = os.environ.get("BACKFULL_PRIVATE_TERMS", "")
    raw += re.split(r"[\n;]", env)
    return [t.strip() for t in raw if t.strip() and not t.strip().startswith("#")]


def combined() -> re.Pattern[str] | None:
    terms = load()
    return re.compile("|".join(f"(?:{t})" for t in terms)) if terms else None
