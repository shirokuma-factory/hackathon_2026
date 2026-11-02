"""PreToolUse hook: block Claude from writing real secret files (.env, .env.local, credentials)."""
import json
import re
import sys

sys.stderr.reconfigure(encoding="utf-8")

ALLOWED_SUFFIXES = (".example", ".sample", ".template")
BLOCKED = re.compile(r"(^|[\\/])(\.env(\.[^\\/]+)?|\.credentials\.json|id_rsa|id_ed25519)$", re.I)

data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
path = (data.get("tool_input") or {}).get("file_path", "")
if BLOCKED.search(path) and not path.lower().endswith(ALLOWED_SUFFIXES):
    print(
        f"{path} は秘密情報のファイルです。値はユーザーが手で書きます。"
        " 項目を増やす場合は .env.example を編集してください。",
        file=sys.stderr,
    )
    sys.exit(2)
