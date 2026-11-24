"""Exercise the pilot admission and import with an isolated DB and fake providers."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import wave
from argparse import Namespace
from array import array
from pathlib import Path

import pytest
from sqlalchemy import func, select
from yt_dlp import YoutubeDL

from karaoke import catalog_pilot
from karaoke.catalog import PILOT_DOWNLOAD_GATE_NOTE, pilot_job_root
from karaoke.catalog_auto_check import Thresholds
from karaoke.catalog_identity import PILOT_OWNER
from karaoke.catalog_pilot import (
    COOLDOWN,
    SeedManifest,
    admission,
    import_seeds,
    reservation,
    unique_seeds,
)
from karaoke.config import Settings
from karaoke.db.models import (
    Base,
    CatalogAsset,
    CatalogPilotState,
    CatalogRecording,
    CatalogSource,
    CatalogTrack,
    Job,
    JobStatus,
)
from karaoke.db.session import create_engine_and_sessionmaker
from karaoke.worker import pipeline

NOW = dt.datetime(2026, 11, 24, 12, tzinfo=dt.UTC)


def test_duplicate_manifest_and_repeat_import(tmp_path):
    # Handled by the async test below as well; this assertion also exercises
    # the checked-in manifest constraint independently of the database.
    path = Path(__file__).parents[1] / "catalog" / "pilot-2026-11-24.json"
    manifest = SeedManifest.model_validate_json(path.read_text())
    assert len(unique_seeds(manifest)) == 30
    assert {song.language for song in manifest.songs} == {"en", "he"}
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
        finished_at=NOW - COOLDOWN * 2, reserved_micros=950_000, charged_micros=930_000,
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
    assert check(attempts=[prior] * 5, reserve=350_000) == "eligible"
    assert check(attempts=[prior] * 5, reserve=350_001) == "waiting_for_budget"
    prior.download_started_at = NOW.replace(hour=0) - dt.timedelta(minutes=10)
    prior.finished_at = NOW.replace(hour=0) + dt.timedelta(minutes=1)
    assert check(attempts=[prior]) == "waiting_for_budget"


def test_reservation_uses_observed_charges_and_stays_within_caps():
    observed = CatalogAsset(
        charged_micros=79_021, state="retired",
        job=Job(status=JobStatus.completed, vast_cost_micros=12_354),
    )
    uncertain = CatalogAsset(
        charged_micros=333_334, job=Job(status=JobStatus.failed),
    )
    assert reservation([], 1) == 50_000
    assert reservation([observed, uncertain], 2) == 118_532
    assert admission(
        now=NOW, attempts=[CatalogAsset(
            source_id=1, charged_micros=900_000,
            download_started_at=NOW - COOLDOWN * 3,
            finished_at=NOW - COOLDOWN * 2, state="review",
        )], foreground=False, paused=False, reserve=reservation([observed], 2),
    ) == "waiting_for_budget"


def _stem(path, *, backing=False, silent=False, short=False):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        pcm = array("h", (
            (0 if silent or (not backing and sample // 8000 not in (1, 5, 9)) else
             9_000 if sample % (8 if backing else 20) < (4 if backing else 10) else -9_000)
            for sample in range(8000 * (8 if short else 12))
        ))
        wav.writeframes(pcm.tobytes())


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="requires coordinator ffmpeg")
@pytest.mark.parametrize(("failure", "reason"), [
    (None, None), ("missing", "karaoke_missing"), ("broken", "karaoke_decode"),
    ("silent", "karaoke_rms"), ("short", "karaoke_duration"), ("leakage", "vocal_band_leakage"),
    ("missing_lrc", "synced_lyrics_missing"), ("unordered", "lyric_timestamps"),
    ("coverage", "lyrics_coverage"), ("gap", "lyrics_gap"), ("offset", "lyric_onset_offset"),
])
async def test_auto_check_publishes_only_on_all_checks(tmp_path, failure, reason):
    manifest = SeedManifest.model_validate_json(
        (Path(__file__).parents[1] / "catalog" / "pilot-2026-11-24.json").read_text()
    )
    manifest.songs = manifest.songs[:1]
    settings = Settings(artifact_root=str(tmp_path))
    engine, factory = create_engine_and_sessionmaker(f"sqlite+aiosqlite:///{tmp_path / 'pilot.db'}")
    limits = Thresholds(lyric_line_window_s=1.25, lyric_gap_max_s=0.5)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            [source] = await import_seeds(session, manifest)
            job = Job(
                job_token="pilot-fixture", owner_subject=PILOT_OWNER, duration=12,
                status=JobStatus.completed, source_url="https://youtu.be/" + source.external_id,
            )
            asset = CatalogAsset(
                job=job, source=source, pipeline_version="test",
                download_started_at=NOW, reserved_micros=100_000, state="review",
            )
            session.add(asset)
            await session.commit()
            asset_id = asset.id
        root = pilot_job_root(tmp_path, "pilot-fixture")
        exports = root / "exports"
        exports.mkdir(parents=True)
        (root / "work").mkdir()
        _stem(exports / "vocals.mp3")
        if failure == "broken":
            (exports / "karaoke.mp3").write_bytes(b"not audio")
        elif failure != "missing":
            _stem(
                exports / "karaoke.mp3", backing=failure != "leakage",
                silent=failure == "silent", short=failure == "short",
            )
        lyrics = {
            "unordered": "[00:05.00] five\n[00:01.00] one",
            "coverage": "[00:01.00] one\n[00:01.20] partial",
            "gap": "[00:01.00] one\n[00:09.00] nine",
            "offset": "[00:00.20] one\n[00:04.20] five\n[00:08.20] nine",
        }.get(failure, "[00:01.00] one\n[00:05.00] five\n[00:09.00] nine")
        if failure != "missing_lrc":
            (exports / "lyrics.lrc").write_text(lyrics, encoding="utf-8")
        args = Namespace(auto_check=asset_id, thresholds=limits)
        await catalog_pilot._execute_locked(args, manifest, settings, factory)
        async with factory() as session:
            asset = await session.get(CatalogAsset, asset_id)
            audit = json.loads((await session.get(Job, asset.job_id)).stage_note)
            assert audit["who"] == "auto-check"
            assert audit["result"] == asset.state
            assert audit["values"]["vocals_decodes"]
            if reason:
                assert reason in audit["reasons"]
                assert asset.state == "review"
                assert (root / "work").is_dir()
            else:
                assert audit["reasons"] == []
                assert asset.state == "ready"
                assert len(asset.lyric_revision) == 64
                assert not (root / "work").exists()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_import_retires_excluded_assets_without_erasing_the_ledger(tmp_path):
    manifest = SeedManifest.model_validate_json(
        (Path(__file__).parents[1] / "catalog" / "pilot-2026-11-24.json").read_text()
    )
    settings = Settings(artifact_root=str(tmp_path))
    engine, factory = create_engine_and_sessionmaker(f"sqlite+aiosqlite:///{tmp_path / 'pilot.db'}")
    root = pilot_job_root(tmp_path, "retired-fixture")
    (root / "exports").mkdir(parents=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            source = CatalogSource(
                provider="youtube", external_id="4e6s1b4G35Y",
                available=True, metadata_allowed=True, playback_allowed=True,
                recording=CatalogRecording(
                    track=CatalogTrack(title="Retired", artist="Fixture"), version="studio",
                    language="ru",
                ),
            )
            asset = CatalogAsset(
                job=Job(
                    job_token="retired-fixture", owner_subject=PILOT_OWNER,
                    status=JobStatus.completed, source_url="https://youtu.be/4e6s1b4G35Y",
                    vast_cost_micros=12_354,
                ),
                source=source, pipeline_version="test", state="review",
                download_started_at=NOW, finished_at=NOW, charged_micros=79_021,
                reserved_micros=333_334,
            )
            session.add(asset)
            await session.commit()
            asset_id, source_id = asset.id, source.id
        await catalog_pilot._execute_locked(Namespace(import_only=True), manifest, settings, factory)
        async with factory() as session:
            asset = await session.get(CatalogAsset, asset_id)
            source = await session.get(CatalogSource, source_id)
            assert not (source.available or source.metadata_allowed or source.playback_allowed)
            assert asset.charged_micros == 79_021
            assert asset.state == "retired"
            assert not root.exists()
    finally:
        await engine.dispose()


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


def test_pilot_info_json_path_uses_yt_dlp_filename_rules(monkeypatch, tmp_path):
    dest = tmp_path / "source.audio"
    metadata = {"availability": "public", "id": "1TAuiaxqJTY", "ext": "m4a"}

    def provider(cmd, **_kwargs):
        template = next(arg.removeprefix("infojson:") for arg in cmd if arg.startswith("infojson:"))
        with YoutubeDL({"quiet": True, "outtmpl": {"infojson": template}}) as ydl:
            sidecar = Path(ydl.prepare_filename(metadata, "infojson"))
        sidecar.write_text(json.dumps(metadata), encoding="utf-8")
        dest.write_bytes(b"audio")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(pipeline.subprocess, "run", provider)
    result = pipeline._download_audio("https://youtu.be/1TAuiaxqJTY", dest, pilot=True)
    assert pipeline._read_json_object(result.with_suffix(".info.json")) == metadata
