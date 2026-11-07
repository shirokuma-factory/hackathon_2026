"""PreToolUse hook: Claude が秘密情報のファイル（.env、鍵、認証情報）に書くのを止める。

止める形（大文字小文字は区別しない）: .env / .env.* / .envrc / *.pem / *.key / id_rsa / id_ed25519 /
*credentials*.json。.example・.sample・.template で終わるものは通す。
Windows で同じファイルを指す書き方（末尾の . や空白、::$DATA などの ADS、末尾の区切り）もそろえてから判定する。
読めない入力が来たときは、通さずに止める（exit 2）。
"""
import json
import re
import sys

try:
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ALLOWED_SUFFIXES = (".example", ".sample", ".template")
BLOCKED_NAME = re.compile(
    r"\.env(\..+)?|\.envrc|.*\.pem|.*\.key|id_rsa|id_ed25519|.*credentials.*\.json", re.I
)


def basename(path: str) -> str:
    p = path.strip().rstrip("\\/ .")
    name = re.split(r"[\\/]", p)[-1]
    name = name.split(":")[0]  # NTFS の代替データストリーム（.env::$DATA）
    return name.rstrip(" .").lower()


def main() -> int:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        ti = data.get("tool_input") or {}
        paths = [ti.get("file_path") or "", ti.get("notebook_path") or ""]
    except Exception as e:  # 読めない入力は通さない
        print(f"guard_secrets: 入力を読めないため止めました（{e}）", file=sys.stderr)
        return 2
    for path in paths:
        if not path:
            continue
        name = basename(path)
        if BLOCKED_NAME.fullmatch(name) and not name.endswith(ALLOWED_SUFFIXES):
            print(
                f"{path} は秘密情報のファイルです。値はユーザーが手で書きます。"
                " 項目を増やす場合は .env.example を編集してください。",
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
