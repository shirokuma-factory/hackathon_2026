# ローカル AI の使い方

Claude Code が判断と設計を担当し、ローカル GPU 上のモデルが生成・大量処理・外に出せないデータの処理を担当します。

## ハードウェア

| 項目 | 値 |
|---|---|
| GPU | NVIDIA GeForce RTX 5080（VRAM 16GB） |
| RAM | 64GB |
| OS | Windows 11 |
| ComfyUI | Comfy Desktop（127.0.0.1:8188）、ComfyUI 0.43.2、フロントエンド 1.57.4 |
| Python（ComfyUI） | 3.13.12 |
| torch / CUDA | 2.12.1+cu130 |
| Ollama | 導入していない（11/2 の作成者の判断）。LM Studio に gemma-4-12b-qat（7.2GB）・gemma-4-26b-a4b-qat（15.6GB）・qwen3.8-27b（17.7GB）・nomic-embed があるが使っていない |
| Blender | 5.2.2（Cycles、OptiX、OptiX Denoiser） |

## 役割分担

```mermaid
flowchart LR
  U[作成者] --> C[Claude Code 本線<br>設計・判断・採点基準・確認]
  C -->|仕様| S[coder<br>Sonnet]
  S -->|変更| R[reviewer・採点者<br>Opus]
  C -->|調査| Q[researcher<br>Opus]
  C -->|Blender MCP / bpy の台本| B[Blender<br>3D モデル・描画 RTX 5080]
  B -->|glTF| A[地図の画面]
  C -.->|local-ai MCP（デモ動画で使う）| G[ComfyUI<br>静止画・映像]
  C -.->|デモ動画で使う| W[ElevenLabs / Resolve]
```

点線はデモ動画の制作で使う予定のもの。Ollama（ローカル LLM）は導入していません。

| 処理 | 担当 | 理由 |
|---|---|---|
| 仕様・判断・採点基準・最終の確認 | Claude（本線、Opus） | |
| 実装 | coder（Sonnet） | |
| 3D モデルの形づくり、軽い版、glTF の書き出し | Blender（Claude が bpy の台本を書き、Blender MCP か画面なしで実行） | 手元の GPU で何度でも同じ結果を作れる |
| サムネイル・回転映像の描画 | Blender Cycles（RTX 5080、OptiX） | |
| デモ動画の説明の静止画・映像 | ComfyUI（`local-ai` MCP の `comfy_run_workflow`）。デモ動画の制作で使う | |
| 荷待ちの記録（運送会社の業務記録）の計算 | 利用者のブラウザの中（`web/waiting.html` は通信しない） | 記録を外に出さない |
| 自社の運行の列の対応の提案 | Claude（開発のセッション）。送ったのは列名と値の形の説明だけで、実際の値・氏名・荷主名・地点名は送っていない（SPEC §22 G71） | 外に出せない値をクラウドに送らない |

アプリの開発で使ったデータは公開の統計・告示と架空のサンプルだけで、外に出せないデータ（現場の写真、個人情報）は扱っていません。

## 使ったモデルと選んだ理由

| 用途 | モデル | 形式・量子化 | VRAM | ライセンス | 選んだ理由 |
|---|---|---|---|---|---|
| 3D の描画 | Blender Cycles（モデルではなくレンダラー） | — | 未測定 | GPL（Blender） | 手元の RTX 5080 で OptiX が使え、glTF をそのまま地図に載せられる |
| デモ動画の静止画・映像 | デモ動画の制作のときに書く（参考値の Z-Image Turbo、Wan2.2 I2V を候補にする） | | | | |

## ComfyUI ワークフロー

API 形式の JSON は `workflows/comfyui/` に置きます。デモ動画の制作で使うときに、この表に 1 行ずつ足します（今は 0 本）。

| ファイル | 内容 | 主なノード |
|---|---|---|
| `backfull_t2i_zimage.json` | デモ動画の冒頭の静止画（1344×768、28 steps、cfg 4） | UNETLoader（z_image_bf16、fp8）、CLIPLoader（qwen_3_4b）、ModelSamplingAuraFlow、KSampler |
| `backfull_i2v_wan22.json` | 静止画から 4 秒の動き（832×480、65 コマ、Lightning 4 steps） | UnetLoaderGGUF（Wan2.2 I2V A14B Q5_K_M）、LoraLoaderModelOnly（lightx2v）、WanImageToVideo、KSamplerAdvanced 2 段 |

## 実測値

### Blender（2026-11-04、RTX 5080）

作ったもの: 乗り物と施設のモデル 18 種（7 種は Blender MCP、11 種は画面なしの Blender）、小物 17 種（T11 パレット、カゴ台車、宅配箱 60〜160、フォークリフト、LD3、20ft・31ft・12ft 冷凍コンテナ、コキ、ドックレベラーほか、各 60KB 以下）、軽い版 18 種（`web/models/lo/`）。台本は `assets3d/source/`。

| 処理 | 条件 | 時間 | VRAM 最大 |
|---|---|---|---|
| サムネイル 35 枚 | 640×400、64 サンプル → 320×200 の透過 WebP | 1 枚 0.4 秒、計 13 秒 | 未測定 |
| 回転映像 18 本 | 1920×1080、96 サンプル、24fps、計 2,544 コマ | 1 コマ 1.35〜2.11 秒（平均 1.87 秒）、計 約 4,760 秒（約 79 分。見積 51 分より、ほかの GPU の負荷で伸びた） | 未測定 |
| 4K の並び画像 | 192 サンプル | 16 秒 | 未測定 |
| 軽い版の作成 | 三角形を元の 13〜25%、ファイル約 3 分の 1、1 材質 + 128×4 のパレット画像 | — | — |

分かったこと: 回転映像の床に照明の反射の帯が出た（直すと再描画に約 80 分かかるので残した）。並び画像は実寸の比ではない（トラック 3.4 倍、RORO 船 0.36 倍など）。高詳細版の材質の統合は 200KB を超えるので見送った。軽い版の数字は `web/models/lod.json`。

### 地図の画面（RTX 5080、1440×900、10 秒）

| 場面 | fps | 1 コマの時間 |
|---|---|---|
| 市場を見る | 122 | p95 12.4 ms |
| 比べる | 88〜102 | 最長 62〜114 ms |
| 拡大 | 113〜119 | 最長 88〜137 ms |

内蔵 GPU とヘッドレス Edge での測定は `docs/DEVLOG.md` と SPEC §19.5・§19.5b。

### ComfyUI

`docs/generation_log.jsonl` から転記します（デモ動画の制作から）。1回目はモデルの読み込みを含みます。

| 処理 | 条件（解像度・コマ数・steps） | 1回目 | 2回目以降 | VRAM 最大 |
|---|---|---|---|---|
| Z-Image（base、fp8）で冒頭の静止画 6 枚（2026-11-06） | 1344×768、28 steps | 66.6 秒 | 50.5〜85.2 秒 | 15.6GB（全体の使用量。録画の Edge と同時） |
| Wan2.2 I2V（2026-11-06） | 832×480、65 コマ | 30 分を超えても終わらず中止 | — | VRAM の空きが約 3.5GB（ComfyUI 以外のアプリが 12.5GB を使用）で、モデルの一部を外に逃がして動いていた。冒頭の動きは静止画への寄り（ffmpeg の zoompan）に替えた |

参考（2026-09 にこの PC で測った値）:

| 処理 | 条件 | 時間 | VRAM 最大 |
|---|---|---|---|
| Z-Image Turbo | 1024×1024、8 steps | 冷 14.7秒 / 温 5.0秒 | |
| Wan2.2 I2V A14B（GGUF Q5_K_M + Lightning 4step） | 832×480、81コマ | 108秒 | 14.3GB |

## 環境の注意

- モデルの置き場所は `C:\ai\models` と `ComfyUI-Shared\models` の2か所。落とす前に両方を確認する。
- RTX 50 系では、CUDA 版のホイールと torch のタグを厳密に合わせる。
- 学習やほかの GPU 作業の前に `comfy_free` で VRAM を空ける。
- Hugging Face からの大容量ダウンロードは止まりやすい。`curl -C -` で再開する。

## トラブルと対処

### 2026-11-03 ComfyUI が起動直後に落ちる（chardet のネイティブ拡張）

- 症状: Comfy Desktop は起動するが、サーバーが `0xC0000005`（access violation）で終了し、8188 番が開かない。
- 原因: スタックトレースは `comfyui_manager` → `requests` → `chardet\detector.py` の 23 行目。chardet に同梱されたコンパイル済み拡張の読み込みで落ちていた。GPU・モデル・ComfyUI 本体の問題ではない。
- 対処: ComfyUI の venv の chardet を、ネイティブ拡張を含まない 5.2.0 に入れ替えた。
  ```powershell
  uv pip install --python <ComfyUI の .venv>\Scripts\python.exe "chardet==5.2.0"
  ```
- 確認: 再起動後に `/system_stats` が応答し、RTX 5080 を認識（VRAM 空き 15.6GB）。
- 再発の条件: ComfyUI Manager や Comfy Desktop の更新で chardet が新しい版に戻ると再発しうる。同じ症状が出たら同じ対処をする。

## MiniMax H3 と SeedVR2 の実測（2026-11-06）

冒頭の C1・C2 を、実写の写真（C1 はぱくたその A1、C2 は Pexels の B15）を始めのコマにして作りました。ナンバーはぼかし、標識の文字は塗りつぶしています。ライセンスの地域制限は作成者が把握したうえで、H3 を使うと決めました（11/6）。動画の中に機械生成の表示を入れます。

| 段 | ワークフロー | 設定 | 処理時間 | VRAM の最大 |
|---|---|---|---|---|
| H3 画像から動画（v1.0 LoRA、試し） | backfull_i2v_minimax_h3 | 1344×768、124 コマ、8 steps | 297 秒 | 15.6 GB |
| H3 画像から動画（v1.1 768p LoRA、shift 6/3） | backfull_i2v_minimax_h3_v11 | 同上 | 248〜267 秒 | 15.6 GB |
| SeedVR2 3B int8 で拡大 | backfull_upscale_seedvr2 | 1344×768 → 1922×1098（×1.43）、時間方向は自動で分割 | 256〜269 秒 | 9.9 GB |

- ぼかした文字を H3 が読めない字として描き直すことがありました。消したい文字は、ぼかさずに塗りつぶします。
- C1 は 3.9 秒を過ぎると路面に赤い筋が出たので、0〜3.8 秒を 4 秒に伸ばして使います。
- 生成物: `outputs/video/comfy/C1_final_4s.mp4`、`C2_final_4s.mp4`。採否は作成者が決めます（未定）。
