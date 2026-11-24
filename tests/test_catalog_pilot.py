"""Exercise the pilot admission and import with an isolated DB and fake providers."""
from __future__ import annotations

import datetime as dt
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest
from sqlalchemy import func, select

from karaoke import catalog_pilot
from karaoke.catalog import PILOT_DOWNLOAD_GATE_NOTE
from karaoke.catalog_pilot import (
    COOLDOWN,
    SeedManifest,
    admission,
    import_seeds,
    unique_seeds,
)
from karaoke.config import Settings
from karaoke.db.models import Base, CatalogAsset, CatalogPilotState, CatalogSource, Job, JobStatus
from karaoke.db.session import create_engine_and_sessionmaker
from karaoke.worker import pipeline

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
@pytest.mark.parametrize(("exit_code", "gate"), [
    (0, "HTTP Error 403: Forbidden"),
    (1, "HTTP Error 403: Forbidden"),
    (1, "HTTP Error 429: Too Many Requests"),
    (1, "Sign in to confirm that you’re not a bot"),
])
async def test_yt_dlp_gate_stops_and_pauses_without_gpu(exit_code, gate, tmp_path, monkeypatch, capsys):
    path = Path(__file__).parents[1] / "catalog" / "pilot-2026-11-24.json"
    manifest = SeedManifest.model_validate_json(path.read_text())
    manifest.songs = [
        song.model_copy(update={"playback_basis": "approved test fixture"})
        for song in manifest.songs[:2]
    ]
    engine, factory = create_engine_and_sessionmaker(f"sqlite+aiosqlite:///{tmp_path / 'pilot.db'}")
    settings = Settings(
        artifact_root=str(tmp_path), device_mode="runpod",
        runpod_api_key="stub", runpod_endpoint_id="stub",
    )
    args = Namespace(
        import_only=False, publish=None, usd_per_hour_ceiling=1.0,
        resume=False, wait=False, limit=2,
    )
    calls = []

    def provider(cmd, **_kwargs):
        calls.append(cmd)
        stderr = f"Early diagnostic\nWARNING: {gate}\n" + ("ordinary download progress " * 150)
        return subprocess.CompletedProcess(cmd, exit_code, "", stderr)

    async def interrupted_after_worker(*_args):
        raise RuntimeError("interrupted before settling the budget")

    monkeypatch.setattr(pipeline.subprocess, "run", provider)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Leave only the persisted worker failure. The next runner must
        # recover the structured note and stop before a second download.
        with monkeypatch.context() as patch:
            patch.setattr(catalog_pilot, "settle", interrupted_after_worker)
            with pytest.raises(RuntimeError, match="interrupted"):
                await catalog_pilot._execute_locked(args, manifest, settings, factory)
        async with factory() as session:
            job = await session.scalar(select(Job))
            assert job.status == JobStatus.failed
            assert job.stage_note == PILOT_DOWNLOAD_GATE_NOTE
            assert "Early diagnostic" in job.error
            assert gate in job.error
            assert job.progress < 75
            assert job.vast_instance_id is None
            assert not (await session.get(CatalogPilotState, 1)).paused

        for _ in range(2):
            await catalog_pilot._execute_locked(args, manifest, settings, factory)
            assert "paused" in capsys.readouterr().out
        assert len(calls) == 1
        async with factory() as session:
            assert (await session.get(CatalogPilotState, 1)).paused
            [asset] = list(await session.scalars(select(CatalogAsset)))
            assert asset.state == "failed"
            assert asset.charged_micros == 0
    finally:
        await engine.dispose()


def test_pilot_ytdlp_never_retries(monkeypatch, tmp_path):
    calls = []

    def provider(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(pipeline.subprocess, "run", provider)
    with pytest.raises(pipeline.PilotDownloadGate, match="429"):
        pipeline._download_audio("https://youtu.be/1TAuiaxqJTY", tmp_path / "source.audio", pilot=True)
    assert len(calls) == 1
    command = calls[0]
    assert "--cookies" not in command
    assert command[command.index("--retries") + 1] == "0"
    assert command[command.index("--extractor-retries") + 1] == "0"
    assert command[command.index("--fragment-retries") + 1] == "0"
