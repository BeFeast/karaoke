#!/usr/bin/env bash
# karaoke entrypoint — sources Infisical-rendered secrets, runs alembic, then execs the CMD.
set -euo pipefail

# 1. Source rendered secrets (Infisical Agent writes /secrets/karaoke.env on a TTL).
#    Last-known-good survives Infisical outages so the API still boots.
if [[ -r /secrets/karaoke.env ]]; then
  echo "[entrypoint] sourcing /secrets/karaoke.env"
  set -a
  # shellcheck disable=SC1091
  . /secrets/karaoke.env
  set +a
else
  echo "[entrypoint] /secrets/karaoke.env not found; falling back to env_file (.env)" >&2
fi

# 2. Run alembic migrations against the configured DB.
if [[ -n "${KARAOKE_DATABASE_URL:-}" ]]; then
  echo "[entrypoint] running alembic upgrade head"
  uv run alembic upgrade head
else
  echo "[entrypoint] WARN: KARAOKE_DATABASE_URL not set; skipping alembic" >&2
fi

# 3. Exec the original CMD.
exec "$@"
