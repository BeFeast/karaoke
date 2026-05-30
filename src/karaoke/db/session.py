"""Async SQLAlchemy engine + session factory."""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from karaoke.config import get_settings
from karaoke.db.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine_and_sessionmaker(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build a fresh engine + session factory (used by tests with isolated DBs)."""
    engine = create_async_engine(database_url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def init_engine(database_url: str | None = None) -> None:
    """Initialise the process-wide engine and create tables for SQLite dev/test."""
    global _engine, _session_factory
    url = database_url or get_settings().database_url
    _engine, _session_factory = create_engine_and_sessionmaker(url)
    # For sqlite (dev/test) we auto-create tables; for Postgres production
    # Alembic is the source of truth.
    if url.startswith("sqlite"):
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


async def shutdown_engine() -> None:
    """Dispose of the engine on app shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("DB session factory not initialised; call init_engine() first")
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
