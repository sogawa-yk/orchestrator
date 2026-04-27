# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# uv インストール
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /usr/local/bin/uv

WORKDIR /app

# 依存解決を先にして層キャッシュを効かせる
COPY pyproject.toml uv.lock* README.md ./
RUN uv sync --no-dev --no-install-project --frozen || uv sync --no-dev --no-install-project

# 本体コピー & ビルド
COPY src/ src/
COPY .chainlit/ .chainlit/
RUN uv sync --no-dev --frozen || uv sync --no-dev


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

RUN useradd -u 10001 -m orch
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src/ /app/src/
COPY .chainlit/ /app/.chainlit/

# Chainlit が書き込む temp 用ディレクトリ
RUN mkdir -p /app/.files && chown -R orch:orch /app

USER orch
EXPOSE 8000

# Chainlit ヘッドレス起動 (-h は CLI ガイド表示を抑止)
CMD ["chainlit", "run", "src/orchestrator/app.py", "-h", "--host", "0.0.0.0", "--port", "8000"]
