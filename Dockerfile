# Cloud Run 用（docs/DEPLOY.md）。標準ライブラリだけで動くので、パッケージの追加はしない。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SHIPCOMPARE_PUBLIC=1

WORKDIR /app
COPY src/ ./src/
COPY tools/serve_web.py ./tools/serve_web.py
COPY data/ ./data/
COPY web/ ./web/

# 実行中はファイルを書かない。root 以外で動かす
RUN useradd --no-create-home --uid 10001 app
USER app

# Cloud Run が環境変数 PORT を渡す（公開版の既定は 8080）
CMD ["python", "tools/serve_web.py", "--public"]
