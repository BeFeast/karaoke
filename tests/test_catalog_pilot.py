"""Exercise the pilot admission and import with an isolated DB and fake providers."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import func, select

from karaoke.catalog_pilot import (
    COOLDOWN,
    SeedManifest,
    admission,
    download_blocked,
    import_seeds,
    settle,
    unique_seeds,
)
from karaoke.config import Settings
from karaoke.db.models import Base, CatalogAsset, CatalogPilotState, CatalogSource, Job, JobStatus
from karaoke.db.session import create_engine_and_sessionmaker

NOW = dt.datetime(2026, 11, 24, 12, tzinfo=dt.UTC)


def test_duplicate_manifest_and_repeat_import(tmp_path):
    # Handled by the async test below as well; this assertion also exercises
    # the checked-in manifest constraint independently of the database.
    path = Path(__file__).parents[1] / "catalog" / "pilot-2026-11-24.json"
    manifest = SeedManifest.model_validate_json(path.read_text())
    assert len(unique_seeds(manifest)) == 30
    duplicate = manifest.model_copy(update={"songs": [*manifest.songs, manifest.songs[0]]})
    assert len(unique_seeds(duplicate)) == 30


@pytest.mark.asyncio
async def test_idempotent_metadata_import_without_jobs(tmp_path):
    path = Path(__file__).parents[1] / "catalog" / "pilot-2026-11-24.json"
    manifest = SeedManifest.model_validate_json(path.read_text())
    first = manifest.songs[0]
    manifest.songs = [first, first.model_copy(update={"title": "Updated title"})]
    engine, factory = create_engine_and_sessionmaker(f"sqlite+aiosqlite:///{tmp_path / 'pilot.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            await import_seeds(session, manifest)
            await session.commit()
            [source] = await import_seeds(session, manifest)
            await session.commit()
            assert source.recording.track.title == "Updated title"
            assert source.playback_allowed is bool(first.playback_basis)
            assert await session.scalar(select(func.count()).select_from(CatalogSource)) == 1
            assert await session.scalar(select(func.count()).select_from(Job)) == 0
    finally:
        await engine.dispose()


def test_priority_cooldown_and_reservations():
    def check(**kwargs):
        return admission(**({"now": NOW, "attempts": [], "foreground": False, "paused": False, "reserve": 100_000} | kwargs))

    prior = CatalogAsset(
        source_id=1, state="ready", download_started_at=NOW - COOLDOWN * 3,
        finished_at=NOW - COOLDOWN * 2, reserved_micros=350_000, charged_micros=300_000,
    )
    assert check(foreground=True) == "waiting_for_foreground"
    assert check(paused=True) == "paused"
    assert check(attempts=[prior]) == "waiting_for_budget"
    assert check(attempts=[prior], reserve=50_000) == "eligible"
    prior.finished_at = NOW - dt.timedelta(seconds=299)
    assert check(attempts=[prior], reserve=1) == "waiting_for_cooldown"
    prior.state = "processing"
    assert check(attempts=[prior]) == "waiting_for_active"
    prior.state = "ready"
    prior.finished_at = NOW - dt.timedelta(days=1)
    prior.download_started_at = prior.finished_at - COOLDOWN
    assert check(attempts=[prior]) == "eligible"
    assert check(attempts=[prior] * 4) == "waiting_for_budget"
    prior.download_started_at = NOW.replace(hour=0) - dt.timedelta(minutes=10)
    prior.finished_at = NOW.replace(hour=0) + dt.timedelta(minutes=1)
    assert check(attempts=[prior]) == "waiting_for_budget"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    "HTTP Error 429: Too Many Requests",
    "HTTP Error 403: Forbidden",
    "Sign in to confirm that you’re not a bot",
])
async def test_yt_dlp_gate_stops_and_pauses_without_gpu(error, tmp_path):
    class Session:
        async def commit(self):
            pass

    asset = CatalogAsset(
        reserved_micros=100_000,
        state="processing",
        job=Job(status=JobStatus.failed, progress=15, error=error),
    )
    state = CatalogPilotState(paused=False)
    assert download_blocked(error)
    await settle(Session(), asset, state, Settings(artifact_root=str(tmp_path)))
    assert state.paused and asset.charged_micros == 0 and asset.state == "failed"


def test_pilot_ytdlp_never_retries(monkeypatch, tmp_path):
    from karaoke.worker import pipeline

    calls = []

    def provider(cmd, **_kwargs):
        calls.append(cmd)
        raise pipeline.PipelineError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(pipeline, "_run", provider)
    with pytest.raises(pipeline.PipelineError, match="429"):
        pipeline._download_audio("https://youtu.be/1TAuiaxqJTY", tmp_path / "source.audio", pilot=True)
    assert len(calls) == 1
    command = calls[0]
    assert "--cookies" not in command
    assert command[command.index("--retries") + 1] == "0"
    assert command[command.index("--extractor-retries") + 1] == "0"
    assert command[command.index("--fragment-retries") + 1] == "0"
