"""PostToolUse hook (Bash): after a successful `git commit`, append the commit to docs/DEVLOG.md."""
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
command = (data.get("tool_input") or {}).get("command", "")
if "git commit" not in command:
    sys.exit(0)

root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".")
try:
    line = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%h %s"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()
except (subprocess.CalledProcessError, FileNotFoundError):
    sys.exit(0)
if not line:
    sys.exit(0)

# A commit that only touches DEVLOG.md (e.g. committing these auto entries) is not logged,
# otherwise every commit of the log would leave a new uncommitted entry behind.
try:
    changed = subprocess.run(
        ["git", "-C", str(root), "show", "--name-only", "--format=", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.split()
except (subprocess.CalledProcessError, FileNotFoundError):
    changed = []
if changed == ["docs/DEVLOG.md"]:
    sys.exit(0)

devlog = root / "docs" / "DEVLOG.md"
devlog.parent.mkdir(parents=True, exist_ok=True)
text = devlog.read_text(encoding="utf-8") if devlog.exists() else "# 開発記録\n"
short_hash = line.split(" ", 1)[0]
if f"`{short_hash}`" in text:
    sys.exit(0)

marker = "## コミット（自動記録）"
stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
hash_, _, subject = line.partition(" ")
entry = f"- {stamp} `{hash_}` {subject}\n"
if marker not in text:
    text = text.rstrip("\n") + f"\n\n{marker}\n\n" + entry
else:
    text = text.rstrip("\n") + "\n" + entry
with open(devlog, "w", encoding="utf-8", newline="\n") as f:
    f.write(text)
