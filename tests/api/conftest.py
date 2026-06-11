"""Pytest fixtures for the karaoke API tests."""
from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from karaoke.api.app import create_app
from karaoke.config import get_settings, reset_settings_for_tests
from karaoke.db import session as db_session
from karaoke.db.models import Base


@pytest.fixture(autouse=True)
def _reset_module_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> Iterator[None]:
    """Each test gets a fresh on-disk SQLite + clean settings cache.

    We use a file-backed DB (not in-memory) so the test thread can open a
    parallel ``sqlite3`` connection to seed rows that the live API engine
    (running on its own event loop inside ``TestClient``) will see.
    """
    db_path = tmp_path / f"karaoke-test-{secrets.token_hex(4)}.db"
    monkeypatch.setenv(
        "KARAOKE_DATABASE_URL",
        f"sqlite+aiosqlite:///{db_path}",
    )
    monkeypatch.setenv("KARAOKE_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setenv("KARAOKE_DEFAULT_OWNER_SUBJECT", "lan-default")
    monkeypatch.setenv("KARAOKE_DEFAULT_OWNER_EMAIL", "lan@example.com")
    monkeypatch.setenv("KARAOKE_AUTH_TEST_MODE", "true")
    monkeypatch.setenv("KARAOKE_PUBLIC_BASE_URL", "http://test.local")

    # Clear caches.
    from karaoke.api import auth as auth_module
    from karaoke.api import ws as ws_module

    auth_module._JWKS_CACHE.clear()
    ws_module.reset_hub_for_tests()

    reset_settings_for_tests()
    db_session._engine = None
    db_session._session_factory = None
    yield
    ws_module.reset_hub_for_tests()
    reset_settings_for_tests()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A FastAPI ``TestClient`` driving a fresh app + on-disk SQLite DB."""
    app = create_app()
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Standalone session factory bound to the same DB the app uses."""
    url = os.environ["KARAOKE_DATABASE_URL"]
    engine, factory = db_session.create_engine_and_sessionmaker(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    return asyncio.DefaultEventLoopPolicy()
