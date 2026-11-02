# hackathon_2026

> テーマと課題: **未定**（決まったら、誰の・どんな課題を・どう解決するかを3行で書く）

## 仕組み

Claude Code が司令塔になり、ローカル GPU（RTX 5080 16GB）上の ComfyUI・ローカル LLM・音声モデルを道具として使います。
Claude Code からは自作の MCP サーバー `local-ai`（`tools/local_ai_mcp/server.py`）を通して、ワークフローの実行・VRAM の計測・ローカル LLM での生成を行います。

## ドキュメント

| ファイル | 内容 |
|---|---|
| [docs/LOCAL_AI.md](docs/LOCAL_AI.md) | ローカル AI の構成、Claude との役割分担、使ったモデル、実測値（処理時間・VRAM） |
| [docs/CLAUDE_USAGE.md](docs/CLAUDE_USAGE.md) | 使った Skills・MCP・サブエージェント・フックと、それぞれを選んだ理由 |
| [docs/DEVLOG.md](docs/DEVLOG.md) | 試行錯誤の記録（コミットはフックで自動追記） |
| [workflows/comfyui/](workflows/comfyui/) | ComfyUI のワークフロー（API 形式の JSON） |

## 動かし方

（スタックが決まったら書く）
