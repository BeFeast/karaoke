# karaoke coordinator image (FastAPI API + in-process worker).
# Built by Dockhand at /opt/stacks/karaoke; image: karaoke:local.
# GPU stages run on ephemeral vast.ai — never bake CUDA/Demucs/Whisper here.

# ---------------------------------------------------------------------------
# Stage: build the Submitter SPA (Vite + React) → web/spa/dist.
# Uses bun (matches the repo's Node toolchain convention).
# ---------------------------------------------------------------------------
FROM oven/bun:1-slim AS spa
WORKDIR /spa
# Install deps first for cacheability (lockfile committed).
COPY web/spa/package.json web/spa/bun.lock ./
RUN bun install --frozen-lockfile
# Build.
COPY web/spa/ ./
RUN bun run build

# ---------------------------------------------------------------------------
# Stage: Python runtime.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_SYSTEM_PYTHON=1

# yt-dlp needs a Node JS runtime + the EJS remote-component solver at runtime.
# ffmpeg normalises the downloaded media before the GPU stage uploads it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        openssh-client \
        nodejs \
        npm \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python resolver) — matches the dev workflow.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Resolve deps first for cacheability.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# App code.
COPY src ./src
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

RUN uv sync --frozen --no-dev

# Built Submitter SPA → matches settings.spa_dist_path default
# (KARAOKE_SPA_DIST_PATH overrides). create_app() mounts it at /app only when
# this directory exists.
COPY --from=spa /spa/dist /app/web/spa/dist

# Entrypoint:
#   1. Source /secrets/karaoke.env if Infisical-Agent rendered it (compose mounts it).
#   2. Run alembic upgrade head against KARAOKE_DATABASE_URL.
#   3. Exec uvicorn.
COPY docker/api/entrypoint.sh /usr/local/bin/karaoke-entrypoint
RUN chmod +x /usr/local/bin/karaoke-entrypoint

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/karaoke-entrypoint"]
CMD ["uv", "run", "uvicorn", "karaoke.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
