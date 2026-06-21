# Conform (SMDR2) — single image for web, worker, and migrations.
# Web:     uvicorn app.main:app          (compose/k8s default command)
# Worker:  python -m app.worker_loop     (same image, different command)
# Migrate: alembic upgrade head          (k8s Job conform-migrate)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Dependency layer — cached until pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
# Migration Job needs these — without them `alembic upgrade head` dies on
# a missing alembic.ini and the whole k8s rollout never starts.
COPY alembic.ini ./
COPY alembic ./alembic

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
