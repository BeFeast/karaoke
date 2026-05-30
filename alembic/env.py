"""Alembic environment for karaoke.

Reads the database URL from ``KARAOKE_DATABASE_URL`` at runtime so we
never bake a production DSN into the alembic.ini.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from karaoke.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolved_url() -> str:
    env_url = os.getenv("KARAOKE_DATABASE_URL", "").strip()
    if env_url:
        # Alembic itself wants a sync URL; normalise the asyncpg/aiosqlite drivers.
        return (
            env_url.replace("+asyncpg", "")
            .replace("+aiosqlite", "")
        )
    return config.get_main_option("sqlalchemy.url") or "sqlite:///./karaoke.db"


def run_migrations_offline() -> None:
    context.configure(
        url=_resolved_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolved_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
