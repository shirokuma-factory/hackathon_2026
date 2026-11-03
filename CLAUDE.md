# このリポジトリについて

ハッカソン提出用のリポジトリです。Claude Code が司令塔になり、ローカル GPU（RTX 5080 16GB）上の ComfyUI・ローカル LLM・音声モデルを道具として使います。

テーマと課題: 中小荷主のスポット出荷を支援するツール。条件整理、見積依頼、回答比較、選択理由の保存までを開発3日で完成させ、その後2日でデモ動画（ComfyUI・DaVinci Resolve・ElevenLabs）を作る。計画は `docs/plan/logistics-review-20261103/`（改訂4）で、08_作り込む範囲の判断 と 12_デモ動画制作とClaude活用 を先に読む。

## 最初に読むもの

| ファイル | 内容 |
|---|---|
| `docs/plan/logistics-review-20261103/README.md` | 物流ツールの計画資料の目次と読む順 |
| `docs/DEVLOG.md` | 何を試して何が分かったか。作業の始めに直近の記録を読む |
| `docs/LOCAL_AI.md` | ローカル AI の構成、役割分担、実測値 |
| `docs/CLAUDE_USAGE.md` | 使った Skills・MCP・サブエージェント・フックと理由 |

## 役割分担

- **Claude（本線）**: 設計、判断、仕様と受け入れ条件、最終の確認と報告。本線は自分でコードを大量に書かず、`coder` サブエージェントに渡して `reviewer` で確かめる。
- **ローカル LLM（Ollama）**: 下書き、大量の分類・要約、外に出せないデータの処理。
- **ComfyUI**: 画像・動画の生成。`local-ai` MCP の `comfy_run_workflow` で実行する。ワークフローは API 形式の JSON を `workflows/comfyui/` に置く。
- **音声**: Whisper で文字起こし、日本語 TTS でナレーション（導入したら LOCAL_AI.md に追記）。

外に出せないデータ（現場の写真、個人情報）はローカルで処理し、Claude には処理結果の要約だけを渡す。どのデータをどちらで処理したかを LOCAL_AI.md に書く。

## 道具

- MCP `local-ai`（`.mcp.json`）: `comfy_status` `comfy_list_workflows` `comfy_run_workflow` `comfy_free` `ollama_list` `ollama_generate`
- サブエージェント（`.claude/agents/`）: `coder`（Sonnet）、`reviewer`（Opus）、`gen-reviewer`（Sonnet、生成物の目視確認）、`researcher`（Opus）
- スキル（`.claude/skills/`）: `comfyui-run`（生成と証拠の残し方）、`route-verify`（モデルの振り分けと検証）
- フック（`.claude/settings.json`）: `git commit` の件名を DEVLOG に追記、生成のたびに `docs/generation_log.jsonl` に追記、`.env` への書き込みを止める

新しい Skill・MCP・サブエージェント・フックを足したら、同じコミットで `docs/CLAUDE_USAGE.md` に1行足す。

## 約束ごと

- 文書・コメント・コミット本文は日本語。コミット件名は短い英語でもよい。
- 改行は LF（`.gitattributes` で固定）。Python からファイルを書くときは `newline="\n"`。
- `.env` はコミットしない。値は `.env.example` に項目名だけ。
- PC 固有の絶対パスをコードに書かない。環境変数か設定ファイルにする。
- 生成物（`outputs/`）とモデルはコミットしない。採用したものだけ `docs/assets/` に小さくして置く。
- 「動いた」と書く前に証拠を残す。テストの結果、スクリーンショット、コンタクトシート、実測値。
- 作業の始めに `git fetch`、終わりに push。

## 確認の手順

テーマが決まり、スタックを選んだらここに書く（型検査・テスト・ビルド・E2E のコマンド）。
