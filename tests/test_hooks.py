"""Claude Code のフック（.claude/hooks/）の回帰試験。

guard_secrets: 秘密情報のファイルへの書き込みを止める（exit 2）。Windows で同じファイルを指す書き方もそろえて止める。
log_generation: 生成の記録に prompt の本文を残さない。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".claude" / "hooks" / "guard_secrets.py"
LOG = ROOT / ".claude" / "hooks" / "log_generation.py"


def run_guard(payload: bytes) -> int:
    return subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True).returncode


BLOCK = [".env", "/a/.env.", "C:\\x\\.env::$DATA", ".env ", "./.env/", ".envrc", "k.pem", "server.key",
         "gcp-credentials.json", ".env.local", "dir/.ENV.production", "id_rsa", "id_ed25519"]
ALLOW = [".env.example", ".env.sample", "README.md", "docs/env.md", "src/keys.py", "tools/x.json", "web/data/meta.json"]


@pytest.mark.parametrize("path", BLOCK)
def test_guard_blocks_secret_files(path):
    assert run_guard(json.dumps({"tool_input": {"file_path": path}}).encode()) == 2


@pytest.mark.parametrize("path", ALLOW)
def test_guard_allows_ordinary_files(path):
    assert run_guard(json.dumps({"tool_input": {"file_path": path}}).encode()) == 0


def test_guard_checks_notebook_path_and_fails_closed():
    assert run_guard(json.dumps({"tool_input": {"notebook_path": "x/.env"}}).encode()) == 2
    assert run_guard(b"not json") == 2


def test_settings_route_writes_through_the_guard():
    s = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    matchers = [h["matcher"] for h in s["hooks"]["PreToolUse"]]
    assert any(set("Write|Edit|MultiEdit|NotebookEdit".split("|")) <= set(m.split("|")) for m in matchers)
    assert "enableAllProjectMcpServers" not in s


def test_generation_log_does_not_keep_the_prompt(tmp_path):
    payload = {"tool_name": "mcp__local-ai__ollama_generate", "session_id": "s",
               "tool_input": {"model": "m", "prompt": "外に出せない中身"}, "tool_response": {"ok": True}}
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    r = subprocess.run([sys.executable, str(LOG)], input=json.dumps(payload).encode(), env=env, capture_output=True)
    assert r.returncode == 0, r.stderr
    line = (tmp_path / "docs" / "generation_log.jsonl").read_text(encoding="utf-8")
    assert "外に出せない中身" not in line and "記録しない" in line
