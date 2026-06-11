"""Tests for ``karaoke.web.views`` — HTML library, share page, artifact server."""
from __future__ import annotations

import os
import secrets
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from karaoke.api.app import create_app
from karaoke.config import get_settings, reset_settings_for_tests
from karaoke.db import session as db_session
from karaoke.db.models import Artifact, Base, Job, JobStatus


@pytest.fixture(autouse=True)
def _reset_module_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> Iterator[None]:
    """Per-test isolated DB + artifacts dir."""
    db_path = tmp_path / f"karaoke-web-{secrets.token_hex(4)}.db"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    monkeypatch.setenv("KARAOKE_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("KARAOKE_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setenv("KARAOKE_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setenv("KARAOKE_DEFAULT_OWNER_SUBJECT", "lan-default")
    monkeypatch.setenv("KARAOKE_DEFAULT_OWNER_EMAIL", "lan@example.com")
    monkeypatch.setenv("KARAOKE_AUTH_TEST_MODE", "true")
    monkeypatch.setenv("KARAOKE_PUBLIC_BASE_URL", "http://test.local")

    from karaoke.api import auth as auth_module

    auth_module._JWKS_CACHE.clear()
    reset_settings_for_tests()
    db_session._engine = None
    db_session._session_factory = None
    yield
    reset_settings_for_tests()


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
async def session_factory():
    url = os.environ["KARAOKE_DATABASE_URL"]
    engine, factory = db_session.create_engine_and_sessionmaker(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield factory
    finally:
        await engine.dispose()


def _artifacts_dir() -> Path:
    return Path(get_settings().artifacts_dir)


def _wait_for_completion(client: TestClient, job_id: int, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = client.get(f"/jobs/{job_id}/status").json()
        if last.get("status") == JobStatus.completed.value:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not complete; last={last}")


def _seed_artifact_files(job_token: str) -> tuple[Path, Path, Path]:
    """Drop fake artifact files so the /artifacts endpoint can serve them."""
    root = _artifacts_dir() / job_token
    root.mkdir(parents=True, exist_ok=True)
    vocals = root / "vocals.mp3"
    karaoke = root / "karaoke.mp3"
    lyrics = root / "lyrics.txt"
    vocals.write_bytes(b"\x00\x01vocals-bytes")
    karaoke.write_bytes(b"\x00\x02karaoke-bytes")
    lyrics.write_text("la la la\n", encoding="utf-8")
    return vocals, karaoke, lyrics


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


def test_library_renders_for_lan_caller(client: TestClient) -> None:
    """LAN-trusted (TestClient) caller sees the library shell with 'Library'."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "Library" in body
    assert "<title>Library · karaoke</title>" in body


def test_library_lists_owner_jobs(client: TestClient) -> None:
    """LAN caller sees jobs across owners (machine/LAN bypass)."""
    create = client.post("/jobs", json={"url": "https://example.com/song", "title": "My Tune"})
    assert create.status_code == 201
    body = client.get("/").text
    assert "My Tune" in body
    assert "https://example.com/song" in body


# ---------------------------------------------------------------------------
# GET /share/{token}
# ---------------------------------------------------------------------------


def test_share_html_renders_with_audio_sources(client: TestClient) -> None:
    """``/share/<token>`` HTML includes audio sources pointing at /artifacts/<token>/*."""
    create = client.post("/jobs", json={"url": "https://example.com/x", "title": "Tune"})
    assert create.status_code == 201
    job = create.json()
    token = job["job_token"]

    _wait_for_completion(client, job["id"])

    response = client.get(f"/share/{token}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "Tune" in body
    # Audio sources must point at the owner-aware artifact server.
    assert f'src="/artifacts/{token}/vocals.mp3"' in body
    assert f'src="/artifacts/{token}/karaoke.mp3"' in body
    # Lyrics rendered as a <pre> with data-src for lazy fetch.
    assert f'data-src="/artifacts/{token}/lyrics.txt"' in body


def test_share_html_404_for_unknown_token(client: TestClient) -> None:
    response = client.get("/share/does-not-exist")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /artifacts/{token}/{file}
# ---------------------------------------------------------------------------


def test_artifacts_endpoint_serves_file_for_owner(client: TestClient) -> None:
    """Owner (LAN-trusted here) can fetch the artifact with correct content-type."""
    create = client.post("/jobs", json={"url": "https://example.com/x", "title": "T"})
    assert create.status_code == 201
    job = create.json()
    token = job["job_token"]
    _wait_for_completion(client, job["id"])

    _seed_artifact_files(token)

    vocals = client.get(f"/artifacts/{token}/vocals.mp3")
    assert vocals.status_code == 200
    assert vocals.headers["content-type"] == "audio/mpeg"
    assert vocals.content == b"\x00\x01vocals-bytes"

    karaoke = client.get(f"/artifacts/{token}/karaoke.mp3")
    assert karaoke.status_code == 200
    assert karaoke.headers["content-type"] == "audio/mpeg"

    lyrics = client.get(f"/artifacts/{token}/lyrics.txt")
    assert lyrics.status_code == 200
    assert lyrics.headers["content-type"].startswith("text/plain")
    assert lyrics.text == "la la la\n"


def test_artifacts_endpoint_404_for_unknown_artifact(client: TestClient) -> None:
    create = client.post("/jobs", json={"url": "https://example.com/x"})
    token = create.json()["job_token"]
    _wait_for_completion(client, create.json()["id"])
    response = client.get(f"/artifacts/{token}/does-not-exist.mp3")
    assert response.status_code == 404


def test_artifacts_endpoint_404_for_unknown_token(client: TestClient) -> None:
    response = client.get("/artifacts/no-such-token/vocals.mp3")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /submit
# ---------------------------------------------------------------------------


def test_submit_form_renders_for_authed_caller(client: TestClient) -> None:
    response = client.get("/submit")
    assert response.status_code == 200
    body = response.text
    assert "Submit" in body
    assert 'id="submit-form"' in body


# ---------------------------------------------------------------------------
# JSON share endpoint moved to /api/share
# ---------------------------------------------------------------------------


def test_json_share_endpoint_lives_under_api_share(client: TestClient) -> None:
    create = client.post("/jobs", json={"url": "https://example.com/y", "title": "X"})
    token = create.json()["job_token"]
    _wait_for_completion(client, create.json()["id"])

    response = client.get(f"/api/share/{token}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["job_token"] == token
    assert body["title"] == "X"
    kinds = {a["kind"] for a in body["artifacts"]}
    assert {"vocals", "karaoke", "lyrics"}.issubset(kinds)


# ---------------------------------------------------------------------------
# Owner isolation on /artifacts (Clerk-authed cross-owner read)
# ---------------------------------------------------------------------------


def _jwt(sub: str) -> str:
    """Forge an unsigned JWT consumed by KARAOKE_AUTH_TEST_MODE."""
    import base64
    import json

    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(
        json.dumps({"sub": sub, "email": f"{sub}@example.com", "exp": int(time.time()) + 600}).encode()
    ).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


@pytest.mark.asyncio
async def test_session_factory_bootstraps(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Sanity: the standalone session factory can write a row."""
    async with session_factory() as session:
        job = Job(
            job_token="zzz",
            owner_subject="alice",
            source_url="https://x",
            status=JobStatus.completed,
            progress=100,
        )
        session.add(job)
        await session.commit()
        session.add(
            Artifact(
                job_id=job.id,
                kind="vocals",
                relative_path="zzz/vocals.mp3",
                content_type="audio/mpeg",
            )
        )
        await session.commit()
