"""local-ai MCP server: lets Claude Code drive ComfyUI and Ollama running on this PC.

Started by .mcp.json via `uv run --with "mcp>=1.2,<2" python tools/local_ai_mcp/server.py`.
Uses only the standard library besides the `mcp` package. Pinned below 2.0 because mcp 2.x
renamed FastMCP to MCPServer.

Environment:
  COMFYUI_URL          default http://127.0.0.1:8188
  OLLAMA_URL           default http://127.0.0.1:11434
  COMFY_WORKFLOW_DIR   default <repo>/workflows/comfyui  (API-format JSON files)
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

REPO = Path(__file__).resolve().parents[2]
COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
WORKFLOW_DIR = Path(os.environ.get("COMFY_WORKFLOW_DIR", REPO / "workflows" / "comfyui"))

mcp = FastMCP("local-ai")


def _comfy_org_key() -> str:
    """有料の API ノード（MiniMax など）用の Comfy.org の API キー。環境変数か <repo>/.env の COMFY_ORG_API_KEY。値は返り値やログに出さない。"""
    v = os.environ.get("COMFY_ORG_API_KEY", "").strip()
    if v:
        return v
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("COMFY_ORG_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _http(method: str, url: str, body: Any = None, timeout: float = 30) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read()
    return json.loads(raw) if raw else {}


def _vram_used_mib() -> int | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return int(out.strip().splitlines()[0])
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None


class _VramSampler:
    """Samples GPU memory every second while a job runs and keeps the peak."""

    def __init__(self) -> None:
        self.peak: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            used = _vram_used_mib()
            if used is not None and (self.peak is None or used > self.peak):
                self.peak = used
            self._stop.wait(1.0)

    def __enter__(self) -> "_VramSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=3)


def _resolve_workflow(name: str) -> Path:
    # workflows/comfyui/ の中の .json だけを読む（絶対パスや .. でリポジトリの外のファイルを読ませない）
    path = (WORKFLOW_DIR / Path(name).name).with_suffix(".json")
    try:
        path.resolve().relative_to(WORKFLOW_DIR.resolve())
    except ValueError:
        raise FileNotFoundError(f"workflow must be inside {WORKFLOW_DIR.name}/: {name}") from None
    if not path.exists():
        raise FileNotFoundError(f"workflow not found: {path.name}")
    return path


def _apply_overrides(graph: dict, overrides: dict[str, Any]) -> dict:
    graph = copy.deepcopy(graph)
    for key, value in overrides.items():
        node_id, _, rest = key.partition(".")
        if node_id not in graph:
            raise KeyError(f"node {node_id} is not in the workflow (key {key})")
        target = graph[node_id]
        parts = rest.split(".") if rest else []
        if not parts:
            raise KeyError(f"override key needs a field after the node id: {key}")
        if parts[0] == "inputs" and len(parts) > 2:
            # 入力名に「.」を含むノード（例 MiniMax H3 の "model.prompt"）は、inputs の後ろを 1 つの名前として扱う
            parts = ["inputs", ".".join(parts[1:])]
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return graph


def _collect_outputs(history: dict) -> list[dict]:
    files = []
    for node_id, node_out in (history.get("outputs") or {}).items():
        for kind in ("images", "gifs", "videos", "audio"):
            for item in node_out.get(kind, []) or []:
                files.append({
                    "node": node_id,
                    "kind": kind,
                    "filename": item.get("filename"),
                    "subfolder": item.get("subfolder", ""),
                    "type": item.get("type", "output"),
                })
    return files


@mcp.tool()
def comfy_status() -> dict:
    """ComfyUI のサーバー状態（GPU、VRAM、キューの件数）を返す。生成の前に必ず呼ぶ。"""
    try:
        stats = _http("GET", f"{COMFYUI_URL}/system_stats", timeout=5)
        queue = _http("GET", f"{COMFYUI_URL}/queue", timeout=5)
    except (urllib.error.URLError, TimeoutError) as e:
        return {"running": False, "url": COMFYUI_URL, "error": str(e)}
    return {
        "running": True,
        "url": COMFYUI_URL,
        "devices": stats.get("devices"),
        "queue_running": len(queue.get("queue_running", [])),
        "queue_pending": len(queue.get("queue_pending", [])),
    }


@mcp.tool()
def comfy_list_workflows() -> list[dict]:
    """workflows/comfyui/ にある API 形式のワークフロー JSON と、各ノードの種類を返す。"""
    result = []
    for path in sorted(WORKFLOW_DIR.glob("*.json")):
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
            nodes = {nid: n.get("class_type") for nid, n in graph.items() if isinstance(n, dict)}
            api_format = all("class_type" in n for n in graph.values() if isinstance(n, dict))
        except (json.JSONDecodeError, AttributeError) as e:
            nodes, api_format = {"error": str(e)}, False
        result.append({"name": path.stem, "api_format": api_format, "nodes": nodes})
    return result


@mcp.tool()
def comfy_run_workflow(
    workflow: str,
    overrides: dict[str, Any] | None = None,
    wait: bool = True,
    timeout_s: int = 1800,
) -> dict:
    """API 形式のワークフローを ComfyUI に投入する。

    workflow: workflows/comfyui/ のファイル名（拡張子なし可）。フォルダーの外のファイルは読まない。
    overrides: {"ノードID.inputs.項目名": 値}。例 {"3.inputs.seed": 42, "6.inputs.text": "a cat"}。
    wait: True なら完了まで待ち、処理時間・VRAM の最大値・出力ファイルを返す。
    """
    path = _resolve_workflow(workflow)
    raw = path.read_bytes()
    graph = _apply_overrides(json.loads(raw), overrides or {})
    client_id = uuid.uuid4().hex
    vram_before = _vram_used_mib()
    started = time.time()
    with _VramSampler() as sampler:
        body: dict[str, Any] = {"prompt": graph, "client_id": client_id}
        org_key = _comfy_org_key()
        if org_key:
            body["extra_data"] = {"api_key_comfy_org": org_key}
        queued = _http("POST", f"{COMFYUI_URL}/prompt", body)
        prompt_id = queued["prompt_id"]
        result: dict[str, Any] = {
            "prompt_id": prompt_id,
            "workflow": path.name,
            "workflow_sha256": hashlib.sha256(raw).hexdigest(),
            "overrides": overrides or {},
        }
        if not wait:
            return result | {"status": "queued"}
        history: dict = {}
        while time.time() - started < timeout_s:
            entry = _http("GET", f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
            if prompt_id in entry:
                history = entry[prompt_id]
                break
            time.sleep(1.0)
    elapsed = round(time.time() - started, 2)
    if not history:
        return result | {"status": "timeout", "elapsed_s": elapsed}
    status = history.get("status", {})
    return result | {
        "status": status.get("status_str", "unknown"),
        "elapsed_s": elapsed,
        "vram_before_mib": vram_before,
        "vram_peak_mib": sampler.peak,
        "outputs": _collect_outputs(history),
        "messages": [m[0] for m in status.get("messages", []) if m and m[0] == "execution_error"],
    }


@mcp.tool()
def comfy_free(unload_models: bool = True) -> dict:
    """ComfyUI が抱えているモデルと VRAM を解放する。計測の前や、学習・別の GPU 作業の前に使う。"""
    _http("POST", f"{COMFYUI_URL}/free", {"unload_models": unload_models, "free_memory": True})
    time.sleep(2)
    return {"freed": True, "vram_used_mib": _vram_used_mib()}


@mcp.tool()
def ollama_list() -> list[dict]:
    """Ollama に入っているローカル LLM の一覧（名前、サイズ）を返す。"""
    try:
        tags = _http("GET", f"{OLLAMA_URL}/api/tags", timeout=5)
    except (urllib.error.URLError, TimeoutError) as e:
        return [{"error": f"Ollama に接続できません ({OLLAMA_URL}): {e}"}]
    return [
        {"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 2), "family": m.get("details", {}).get("family")}
        for m in tags.get("models", [])
    ]


@mcp.tool()
def ollama_generate(
    model: str,
    prompt: str,
    system: str | None = None,
    json_mode: bool = False,
    temperature: float = 0.2,
) -> dict:
    """ローカル LLM で文章を生成する。外に出せないデータの要約・分類、大量の下書きに使う。

    返り値に処理時間と tokens/s を含む（docs/LOCAL_AI.md の実測に使う）。
    """
    body: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": temperature}}
    if system:
        body["system"] = system
    if json_mode:
        body["format"] = "json"
    with _VramSampler() as sampler:
        res = _http("POST", f"{OLLAMA_URL}/api/generate", body, timeout=600)
    eval_count = res.get("eval_count") or 0
    eval_ns = res.get("eval_duration") or 0
    return {
        "model": model,
        "response": res.get("response", ""),
        "total_s": round((res.get("total_duration") or 0) / 1e9, 2),
        "load_s": round((res.get("load_duration") or 0) / 1e9, 2),
        "prompt_tokens": res.get("prompt_eval_count"),
        "output_tokens": eval_count,
        "tokens_per_s": round(eval_count / (eval_ns / 1e9), 1) if eval_ns else None,
        "vram_peak_mib": sampler.peak,
    }


if __name__ == "__main__":
    mcp.run()
