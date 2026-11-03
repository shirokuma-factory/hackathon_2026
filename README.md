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

## 起動方法

```
uv run streamlit run app/main.py
```

ブラウザで http://localhost:8501 が開きます。画面は 1920×1080 の横長表示を想定しています。試験は `uv run pytest -q` です。

デモの操作順:

1. 「1 出荷条件」で場面 S1 を選び、「この条件で確定」を押す
2. 「2 目安額」で手段ごとの目安額と出典を見る
3. 「3 見積依頼文」で不足項目と依頼文を見る
4. 「4 見積回答」で見積を2件入れ、順位と差額を見る
5. 「1 出荷条件」で集荷を持込に変えて確定し、「4 見積回答」で2件が条件変更前に移ることを見る
6. 「5 判断と保存」で回答と理由を入れ、JSON を保存する
