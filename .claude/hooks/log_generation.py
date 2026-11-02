"""PostToolUse hook (local-ai MCP): append one JSON line per generation to docs/generation_log.jsonl.

The file is the raw data for the timing / VRAM tables in docs/LOCAL_AI.md.
"""
import datetime
import json
import os
import sys
from pathlib import Path


def _parse(response):
    # MCP tool results arrive as text content; the local-ai server returns JSON text.
    if isinstance(response, dict) and "content" in response:
        response = response["content"]
    if isinstance(response, list):
        texts = [c.get("text", "") for c in response if isinstance(c, dict)]
        response = "".join(texts)
    if isinstance(response, str):
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {"raw": response[:2000]}
    return response


data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".")
result = _parse(data.get("tool_response"))
if isinstance(result, dict):
    result.pop("response", None)  # long LLM text is not needed in the log

entry = {
    "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    "session": data.get("session_id"),
    "tool": data.get("tool_name", "").removeprefix("mcp__local-ai__"),
    "input": data.get("tool_input"),
    "result": result,
}
log = root / "docs" / "generation_log.jsonl"
log.parent.mkdir(parents=True, exist_ok=True)
with open(log, "a", encoding="utf-8", newline="\n") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
