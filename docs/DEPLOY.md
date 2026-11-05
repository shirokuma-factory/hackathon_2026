# Cloud Run への公開手順

Google Cloud Shell（ブラウザで使うターミナル）から、このリポジトリを取得して Cloud Run に公開します。手元に Docker は要りません。Cloud Build がリポジトリの `Dockerfile` からイメージを作ります。

公開するのは `tools/serve_web.py --public`（SPEC §15.1「公開版」）です。手元の版との違いは次のとおりです。

- OSRM の公開デモサーバーには問い合わせません（利用規約が多数の公開利用を禁じているため）。道路の経路は、リポジトリにある計算済みのデータだけで表示します。それ以外の組は直線の弧と「経路を取得できませんでした」を出します。
- 「空きを探す・つなぐ」の仮予約などの状態は、来訪者のブラウザごとに分けてメモリに持ちます。
- P31（再配布できないデータ）は画像に入れません。施設の欄に「物流拠点（国土数値情報 P31）は再配布できないため、この版では表示しません」と出ます。

## 事前に必要なもの

- 課金を有効にした Google Cloud のプロジェクト 1 つ（Cloud Run、Cloud Build、Artifact Registry の API は、初回のデプロイ時に有効化するかを聞かれます。`y` で進めます）
- このリポジトリが GitHub で公開されていること

## 公開する

Cloud Shell を開き、次を順に実行します。`PROJECT_ID` は自分のプロジェクト ID に置き換えます。

```bash
gcloud config set project PROJECT_ID

git clone https://github.com/shirokuma-factory/hackathon_2026
cd hackathon_2026

gcloud run deploy backfull-demo \
  --source . \
  --region asia-northeast1 \
  --allow-unauthenticated \
  --set-env-vars SHIPCOMPARE_PUBLIC=1 \
  --memory 512Mi \
  --cpu 1 \
  --max-instances 1 \
  --concurrency 40 \
  --timeout 60 \
  --session-affinity
```

終わると `Service URL: https://backfull-demo-xxxxx-an.a.run.app` が表示されます。この URL がデモの公開先です。

- `--max-instances 1` は同時に動くコンテナを 1 つまでに制限します。課金の上限をここで決めます。仮予約の状態はコンテナのメモリにあるので、1 つにすると食い違いが起きません。
- `--memory 512Mi` は、圧縮済みの静的ファイル（約 3 MB）と来訪者ごとの状態（最大 300 人分）に十分な大きさです。
- `--session-affinity` は、同じブラウザの通信をなるべく同じコンテナに送ります（コンテナが入れ替わると仮予約の状態は初期に戻ります）。
- `--allow-unauthenticated` は、誰でも URL を開ける設定です。デモは架空のデータだけで、個人情報は扱いません。
- 最小インスタンスは 0（既定）です。使われていない間は料金がかからず、しばらくぶりの最初のアクセスだけ起動に数秒かかります。デモ動画の撮影や審査の直前に開いて温めておくと安心です。

## 更新する

リポジトリを更新して push したあと、Cloud Shell で次を実行します。同じコマンドで同じサービスが新しい版に入れ替わります。

```bash
cd hackathon_2026
git pull
gcloud run deploy backfull-demo --source . --region asia-northeast1
```

（`--set-env-vars` などの設定は前回の値が引き継がれます。）

## 動作の確認

```bash
URL=$(gcloud run services describe backfull-demo --region asia-northeast1 --format 'value(status.url)')
curl -s -o /dev/null -w "%{http_code}\n" $URL/index.html
curl -s $URL/api/scenarios | head -c 200
curl -s -o /dev/null -w "%{http_code}\n" $URL/data/local/p31.json   # 404 になれば正常
```

ブラウザで `$URL/index.html?check=1` を開くと、画面の動作確認も出ます。

## 削除する（課金を止める）

```bash
gcloud run services delete backfull-demo --region asia-northeast1
```

Cloud Build が作ったイメージは Artifact Registry の `cloud-run-source-deploy` に残ります。保存料金がかかるので、不要なら削除します。

```bash
gcloud artifacts repositories delete cloud-run-source-deploy --location asia-northeast1
```

Cloud Build が使う Cloud Storage のバケット（`PROJECT_ID_cloudbuild` など）にもアップロードしたソースが残ります。コンソールの Cloud Storage から確認して削除します。
