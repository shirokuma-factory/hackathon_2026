# このリポジトリについて

ハッカソン提出用のリポジトリです。Claude Code が司令塔になり、ローカル GPU（RTX 5080 16GB）上の Blender（3D モデルと描画）を開発に、ComfyUI・音声をデモ動画の制作に使います。

テーマと課題: Backfull。中小の運送会社と中小の荷主のためのツールで、荷待ちの記録から協議の書面を作る、市場の帰り便の偏りを見る、帰り便をつなぐ、荷物の送り方を比べる（今の範囲は `README.md`、課題と判定は `docs/plan/2026-11-05_課題を解くツールになっているか.md`）。最初の計画は中小荷主のスポット出荷の見積比較（最初の計画資料、改訂4）で、11/3 に地図を中心に作り直した。デモ動画（ComfyUI・DaVinci Resolve・ElevenLabs）はアプリが終わってから作る。動画は3分以内・日本語。リポジトリは公開設定にする。締切は 11月8日（日）20:00、提出は 11月7日（土）に終える。

## 最初に読むもの

| ファイル | 内容 |
|---|---|
| `SPEC.md` | アプリの仕様と受入条件 |
| `docs/plan/2026-11-03_審査基準と計画の照合.md` | 公式の審査基準（配点と観点）と計画の食い違い |
| 最初の計画資料 | 物流ツールの計画資料の目次と読む順 |
| `docs/DEVLOG.md` | 何を試して何が分かったか。作業の始めに直近の記録を読む |
| `docs/LOCAL_AI.md` | ローカル AI の構成、役割分担、実測値 |
| `docs/CLAUDE_USAGE.md` | 使った Skills・MCP・サブエージェント・フックと理由 |

## 役割分担

- **Claude（本線）**: 設計、判断、仕様と受け入れ条件、最終の確認と報告。本線は自分でコードを大量に書かず、`coder` サブエージェントに渡して `reviewer` で確かめる。
- **Blender**: 3D モデル・軽い版・サムネイル・回転映像。Blender MCP か、Claude が書いた bpy の台本を画面なしで実行する（`assets3d/source/`）。
- **ComfyUI**: デモ動画の静止画・映像の生成。`local-ai` MCP の `comfy_run_workflow` で実行する。ワークフローは API 形式の JSON を `workflows/comfyui/` に置く。
- **音声**: デモ動画のナレーション（ElevenLabs）。使ったら LOCAL_AI.md に追記。
- **ローカル LLM（Ollama）**: 導入していない（11/2 の作成者の判断）。使うときは LOCAL_AI.md に追記する。

外に出せないデータ（現場の写真、個人情報）はローカルで処理し、Claude には処理結果の要約だけを渡す。どのデータをどちらで処理したかを LOCAL_AI.md に書く。

## 道具

- MCP `local-ai`（`.mcp.json`）: `comfy_status` `comfy_list_workflows` `comfy_run_workflow` `comfy_free` `ollama_list` `ollama_generate`
- MCP Blender（Blender 5.2 の拡張機能、127.0.0.1:9876）、DaVinci Resolve MCP（動画の編集）
- サブエージェント（`.claude/agents/`）: `coder`（Sonnet）、`reviewer`（Opus）、`gen-reviewer`（Sonnet、生成物の目視確認）、`researcher`（Opus）
- スキル（`.claude/skills/`）: `comfyui-run`（生成と証拠の残し方）、`route-verify`（モデルの振り分けと検証）、`rubric-loop`（採点基準を作り、新しい採点者で採点して直す反復）
- フック（`.claude/settings.json`）: `git commit` の件名を DEVLOG に追記、生成のたびに `docs/generation_log.jsonl` に追記、`.env` への書き込みを止める。手元の Windows でも動いている。ただし `git -c … commit` の形と、マージ・amend は 11/8 まで拾えなかった（`docs/CLAUDE_USAGE.md` のフックの表）

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

Python 3.13 + uv（理由は `SPEC.md` §2）。画面は地図の画面と、ファイル 1 枚で動く 2 枚のページ。

```bash
uv run pytest -n 8          # 並列（pytest-xdist）。画面の試験も各プロセスで Edge を 1 つずつ使う。1 プロセスで流すなら -n 0
uv run python tools/serve_web.py   # http://127.0.0.1:8600/（地図の画面）
# web/waiting.html と web/compare_lite.html はブラウザで直接開く
```

11/3 の最初の版（Streamlit の `app/main.py`）は 11/7 に消した（`docs/試行錯誤の記録.md`）。
