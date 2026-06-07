"""Tests for transient RunPod GPU-capacity auto-retry in the worker pipeline.

A ``RunpodCapacityError`` (job stuck IN_QUEUE past the queue ceiling, then
cancelled before any compute ran) is transient and free to retry. The pipeline
must re-submit with backoff instead of permanently failing the job — that was
the red flag behind job #41 "Russian Titanik" (failed on capacity; the manual
retry #42 completed). No network, no GPU: ``RunpodClient.run`` is monkeypatched.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from karaoke.config import Settings
from karaoke.db.models import Base, Job, JobStatus
from karaoke.db.session import create_engine_and_sessionmaker


@pytest_asyncio.fixture
async def factory(tmp_path) -> async_sessionmaker:
    url = f"sqlite+aiosqlite:///{tmp_path / 'cap.db'}"
    engine, fac = create_engine_and_sessionmaker(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield fac
    finally:
        await engine.dispose()


async def _make_job(factory) -> int:
    async with factory() as session:
        job = Job(
            job_token="tok-cap",
            owner_subject="owner",
            source_url="https://example.com/song",
            status=JobStatus.queued,
            progress=0,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


def _patch_pipeline_io(pipeline, monkeypatch, delays):
    """Stub yt-dlp/ffmpeg/LRCLIB and make backoff instantaneous (recorded)."""
    monkeypatch.setattr(
        pipeline, "_ytdlp_metadata", lambda url, settings=None, **_: {"title": "My Song"}
    )

    def fake_download(url, dest: Path, settings=None, **_):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"audio")
        return dest

    def fake_to_wav(src, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"wav")
        return dest

    def fake_wav_to_mp3(src, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mp3")
        return dest

    monkeypatch.setattr(pipeline, "_download_audio", fake_download)
    monkeypatch.setattr(pipeline, "_to_wav", fake_to_wav)
    monkeypatch.setattr(pipeline, "_wav_to_mp3", fake_wav_to_mp3)

    from karaoke.worker.lyrics import LyricsSource

    monkeypatch.setattr(
        pipeline, "_LYRICS_SOURCE", LyricsSource(http=lambda *a, **k: (404, None))
    )

    async def fake_asleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(pipeline, "_asleep", fake_asleep)


def _gpu_result(work_dir: Path):
    from karaoke.worker.vast_client import GpuJobResult

    work_dir.mkdir(parents=True, exist_ok=True)
    voc = work_dir / "vocals.wav"
    inst = work_dir / "instrumental.wav"
    ltxt = work_dir / "lyrics.txt"
    ljson = work_dir / "lyrics.json"
    for p, c in [(voc, b"v"), (inst, b"i"), (ltxt, b"la la"), (ljson, b"{}")]:
        p.write_bytes(c)
    return GpuJobResult(
        vast_instance_id="rp-7",
        vast_cost=0.016,
        gpu_model="RTX 4090",
        vocals_path=voc,
        instrumental_path=inst,
        lyrics_txt_path=ltxt,
        lyrics_json_path=ljson,
    )


def _runpod_settings(tmp_path, **over) -> Settings:
    base = dict(
        device_mode="runpod",
        runpod_api_key="k-real",
        runpod_endpoint_id="ep-real",
        artifact_root=str(tmp_path),
    )
    base.update(over)
    return Settings(**base)


@pytest.mark.asyncio
async def test_capacity_stall_retries_then_completes(factory, monkeypatch, tmp_path):
    """Two capacity stalls, then success → job completes; backoff slept twice."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker.runpod_client import RunpodCapacityError

    delays: list[float] = []
    _patch_pipeline_io(pipeline, monkeypatch, delays)

    calls = {"n": 0}

    def flaky_run(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RunpodCapacityError("runpod job stuck in queue — GPU capacity busy")
        return _gpu_result(work_dir)

    from karaoke.worker.runpod_client import RunpodClient

    monkeypatch.setattr(RunpodClient, "run", flaky_run)

    job_id = await _make_job(factory)
    await pipeline.run_real_job(factory, job_id, _runpod_settings(tmp_path, runpod_capacity_retries=5))

    assert calls["n"] == 3, "should have re-submitted twice then succeeded"
    assert delays == [20.0, 40.0], "two capped-exponential backoffs"
    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.completed
        assert job.progress == 100
        assert (job.error or "") == ""


@pytest.mark.asyncio
async def test_capacity_stall_exhausts_retries_then_fails(factory, monkeypatch, tmp_path):
    """Persistent capacity outage → fails only after exhausting retries, with a
    clear capacity message; re-submitted exactly retries+1 times."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker.runpod_client import RunpodCapacityError

    delays: list[float] = []
    _patch_pipeline_io(pipeline, monkeypatch, delays)

    calls = {"n": 0}

    def always_busy(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None):
        calls["n"] += 1
        raise RunpodCapacityError(
            "runpod job stuck in queue 480s > 480s — GPU capacity busy, retry shortly"
        )

    from karaoke.worker.runpod_client import RunpodClient

    monkeypatch.setattr(RunpodClient, "run", always_busy)

    job_id = await _make_job(factory)
    await pipeline.run_real_job(factory, job_id, _runpod_settings(tmp_path, runpod_capacity_retries=2))

    assert calls["n"] == 3, "initial attempt + 2 retries"
    assert delays == [20.0, 40.0]
    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.failed
        assert "capacity busy" in (job.error or "")


@pytest.mark.asyncio
async def test_capacity_retry_disabled_fails_fast(factory, monkeypatch, tmp_path):
    """runpod_capacity_retries=0 restores legacy one-shot fail-fast (no backoff)."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker.runpod_client import RunpodCapacityError

    delays: list[float] = []
    _patch_pipeline_io(pipeline, monkeypatch, delays)

    calls = {"n": 0}

    def always_busy(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None):
        calls["n"] += 1
        raise RunpodCapacityError("GPU capacity busy")

    from karaoke.worker.runpod_client import RunpodClient

    monkeypatch.setattr(RunpodClient, "run", always_busy)

    job_id = await _make_job(factory)
    await pipeline.run_real_job(factory, job_id, _runpod_settings(tmp_path, runpod_capacity_retries=0))

    assert calls["n"] == 1, "no retries when disabled"
    assert delays == []
    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.failed


@pytest.mark.asyncio
async def test_cancel_during_capacity_wait_stops_retry_and_keeps_cancelled(
    factory, monkeypatch, tmp_path
):
    """If the user cancels the job while we wait for GPU capacity, we stop
    retrying and the job stays 'cancelled' — never resurrected to 'failed'."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker.runpod_client import RunpodCapacityError

    delays: list[float] = []
    _patch_pipeline_io(pipeline, monkeypatch, delays)

    job_id = await _make_job(factory)

    # The backoff is where a concurrent cancel lands: flip the job to cancelled
    # mid-wait, then the next attempt's cancellation check must abort the loop.
    async def cancelling_asleep(seconds: float) -> None:
        delays.append(seconds)
        async with factory() as session:
            job = await session.get(Job, job_id)
            job.status = JobStatus.cancelled
            await session.commit()

    monkeypatch.setattr(pipeline, "_asleep", cancelling_asleep)

    calls = {"n": 0}

    def always_busy(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None):
        calls["n"] += 1
        raise RunpodCapacityError("GPU capacity busy")

    from karaoke.worker.runpod_client import RunpodClient

    monkeypatch.setattr(RunpodClient, "run", always_busy)

    await pipeline.run_real_job(
        factory, job_id, _runpod_settings(tmp_path, runpod_capacity_retries=5)
    )

    assert calls["n"] == 2, "one real attempt, one post-cancel attempt that aborts"
    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.cancelled, "must NOT be flipped to failed"
