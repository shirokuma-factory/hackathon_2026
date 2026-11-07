"""提出前のリポジトリを機械で確かめる（モデルに渡さずに判定できるものだけ）。

使い方: uv run python tools/submission/check_ready.py [--online]
  --online  README の http(s) のリンクに実際に問い合わせる（Cloud Run は初回に十数秒かかる）

調べること:
  1. README.md と docs/README.md のリンク先のファイル・画像があるか
  2. 必要なファイル（LICENSE、動画のリンク、画面写真）があるか
  3. 追跡するファイルに PC 固有のパス・呼び名・秘密の形が無いか
  4. 一番上の階層に作業用のファイルが残っていないか
  5. README に、作成禁止の例と誤解される記述（Streamlit を今の画面とする書き方）が無いか
結果は 1 行ずつ ○ / × で出し、× が 1 つでもあれば終了コード 1。
"""
from __future__ import annotations

import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ONLINE = "--online" in sys.argv
results: list[tuple[bool, str]] = []


def check(ok: bool, msg: str) -> None:
    results.append((ok, msg))


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-co", "--exclude-standard", "-z"],
                         capture_output=True).stdout.decode("utf-8", "replace")
    return [p for p in out.split("\0") if p]


LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|<img[^>]+src=\"([^\"]+)\"")


def check_links(md: Path) -> None:
    if not md.exists():
        check(False, f"{md.relative_to(ROOT)} が無い")
        return
    text = md.read_text(encoding="utf-8")
    bad, urls = [], []
    for m in LINK.finditer(text):
        target = m.group(1) or m.group(2)
        if target.startswith(("http://", "https://")):
            urls.append(target)
            continue
        if target.startswith(("#", "mailto:")):
            continue
        path = urllib.parse.unquote(target.split("#")[0])
        if path and not (md.parent / path).exists():
            bad.append(target)
    check(not bad, f"{md.relative_to(ROOT)} のファイルへのリンク切れ: {bad or 'なし'}")
    if ONLINE:
        dead = []
        for u in sorted(set(urls)):
            try:
                req = urllib.request.Request(u, method="GET", headers={"User-Agent": "repo-check"})
                with urllib.request.urlopen(req, timeout=40) as r:
                    if r.status >= 400:
                        dead.append(f"{u} {r.status}")
            except Exception as e:  # noqa: BLE001
                dead.append(f"{u} {type(e).__name__}")
        check(not dead, f"{md.relative_to(ROOT)} の URL の応答: {dead or 'すべて 200 番台'}")


def main() -> int:
    readme = ROOT / "README.md"
    check_links(readme)
    check_links(ROOT / "docs" / "README.md")

    text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    head = text[:4000]
    check((ROOT / "LICENSE").exists(), "LICENSE がある")
    check(bool(re.search(r"youtu\.?be|loom\.com|vimeo\.com", head)), "README の冒頭に動画のリンクがある")
    check(len(re.findall(r"!\[|<img", text)) >= 3, "README に画像が 3 枚以上ある")
    check("Streamlit" not in text, "README に Streamlit の記述が無い（作成禁止の例と誤解されないため）")
    check(not (ROOT / "app" / "main.py").exists(), "app/main.py（Streamlit の最初の版）を消した")
    check("期間中" in text or "キックオフ" in text, "README に、期間中に作ったものと前からの道具の区別がある")

    files = tracked_files()
    pat_path = re.compile(r"(?i)C:[\\/]+Users[\\/]+[^\\/\s]+")
    pat_name = re.compile("\u304a\u305f\u3064|tatsu" + "jpn")  # この行が自分に当たらないよう、エスケープと連結で書く
    pat_secret = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}|\bsk_[a-f0-9]{40,}\b|AIza[0-9A-Za-z_-]{35}|"
                            r"\b(ghp|gho|github_pat)_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
    hit_path, hit_name, hit_secret = [], [], []
    for f in files:
        p = ROOT / f
        if not p.is_file() or p.stat().st_size > 5_000_000 or p.suffix.lower() in (".jpg", ".png", ".glb", ".mp4", ".webm", ".wav", ".mp3"):
            continue
        try:
            s = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pat_path.search(s):
            hit_path.append(f)
        if pat_name.search(s):
            hit_name.append(f)
        if pat_secret.search(s):
            hit_secret.append(f)
    check(not hit_secret, f"秘密の形: {hit_secret or 'なし'}")
    check(not hit_path, f"PC 固有のパス: {hit_path[:15] or 'なし'}{' ほか' if len(hit_path) > 15 else ''}（{len(hit_path)} 個）")
    check(not hit_name, f"作成者の呼び名・アカウント名: {hit_name or 'なし'}")

    junk = [f for f in files if "/" not in f and re.search(r"\.whl$|\.tar\.gz$|^\.edit_.*\.py$|^\.runtests\.sh$", f)]
    junk += [f for f in files if f.startswith("docs/assets6")]
    check(not junk, f"作業用のファイル: {junk or 'なし'}")
    check(not any(f.startswith(".claude/worktrees/") for f in files), ".claude/worktrees/ が .gitignore で外れている")

    for ok, msg in results:
        print(("○ " if ok else "× ") + msg)
    n_bad = sum(1 for ok, _ in results if not ok)
    print(f"\n× {n_bad} 件 / {len(results)} 件")
    return 1 if n_bad else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
