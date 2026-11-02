# ComfyUI ワークフロー

ここには **API 形式** の JSON を置きます。ComfyUI の画面でワークフローを開き、メニューの「Export (API)」で保存したものです。画面用の JSON（`nodes` と `links` を持つ形）は API からは実行できません。

- ファイル名がそのまま `comfy_run_workflow` の `workflow` 引数になります（例: `txt2img_base.json` → `"txt2img_base"`）。
- 実行のたびに変える値（seed、プロンプト、入力画像）は JSON を書き換えず、`overrides` で渡します。例: `{"3.inputs.seed": 42, "6.inputs.text": "..."}`
- `comfy_list_workflows` で各ノードの ID と種類を確認できます。
- 置いたら `docs/LOCAL_AI.md` の「ComfyUI ワークフロー」の表に1行足します。
