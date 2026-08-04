"""Unit tests for worker dispatch + the real-pipeline failure path.

No network, no vast.ai, no yt-dlp/ffmpeg — the real worker's blocking steps are
monkeypatched. Covers:
  (d) scheduler dispatches to mock with no vast key / mock mode, and to the real
      worker when a key is configured.
  - run_real_job marks the job failed (never raises) when a stage explodes, and
    leaves no instance leaked (VastClient owns its own finally; here we assert
    the orchestrator routes errors to Job.failed).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from karaoke.config import Settings
from karaoke.db.models import Base, Job, JobStatus
from karaoke.db.session import create_engine_and_sessionmaker
from karaoke.worker import scheduler
from karaoke.worker.scheduler import _use_mock, schedule_job


# ---------------------------------------------------------------------------
# (d) dispatch decision table
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("device_mode", "vast_api_key", "expect_mock"),
    [
        ("auto", "", True),        # CI default: no key → mock
        ("auto", "  ", True),      # whitespace key → mock
        ("auto", "k-real", False),  # key present → real
        ("mock", "k-real", True),  # forced mock even with a key
        ("vast", "", False),       # forced real (will error later if no key)
        ("vast", "k-real", False),
        ("cpu-local", "", False),
    ],
)
def test_use_mock_decision(device_mode, vast_api_key, expect_mock):
    settings = Settings(device_mode=device_mode, vast_api_key=vast_api_key)
    assert _use_mock(settings) is expect_mock


@pytest_asyncio.fixture
async def factory(tmp_path) -> async_sessionmaker:
    url = f"sqlite+aiosqlite:///{tmp_path / 'sched.db'}"
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
            job_token="tok-test",
            owner_subject="owner",
            source_url="https://example.com/song",
            status=JobStatus.queued,
            progress=0,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


@pytest.mark.asyncio
async def test_schedule_dispatches_to_mock_with_no_key(factory, monkeypatch):
    called: dict[str, int] = {}
    monkeypatch.setattr(
        scheduler, "_use_mock", lambda s: True,
    )
    # Patch the mock entrypoint to observe the dispatch without running it fully.
    import karaoke.api.worker_stub as stub

    async def fake_mock(session_factory, job_id, *, sleep=0.0):
        called["mock"] = job_id

    monkeypatch.setattr(stub, "run_mock_job", fake_mock)

    job_id = await _make_job(factory)
    settings = Settings(device_mode="auto", vast_api_key="")
    task = schedule_job(factory, job_id, settings)
    await task
    assert called == {"mock": job_id}


@pytest.mark.asyncio
async def test_schedule_dispatches_to_real_with_key(factory, monkeypatch):
    called: dict[str, int] = {}

    async def fake_real(session_factory, job_id, settings):
        called["real"] = job_id

    import karaoke.worker.pipeline as pipeline

    monkeypatch.setattr(pipeline, "run_real_job", fake_real)

    job_id = await _make_job(factory)
    settings = Settings(device_mode="vast", vast_api_key="k-real")
    task = schedule_job(factory, job_id, settings)
    await task
    assert called == {"real": job_id}


# ---------------------------------------------------------------------------
# run_real_job failure path → Job.failed, never raises
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_real_job_marks_failed_on_download_error(factory, monkeypatch, tmp_path):
    import karaoke.worker.pipeline as pipeline

    def boom(*a, **k):
        raise pipeline.PipelineError("yt-dlp blew up")

    monkeypatch.setattr(pipeline, "_ytdlp_metadata", lambda url, settings=None, **_: {})
    monkeypatch.setattr(pipeline, "_download_audio", boom)

    job_id = await _make_job(factory)
    settings = Settings(
        device_mode="vast", vast_api_key="k-real", artifact_root=str(tmp_path)
    )
    # Must NOT raise — failures are recorded on the Job.
    await pipeline.run_real_job(factory, job_id, settings)

    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.failed
        assert "yt-dlp blew up" in (job.error or "")


@pytest.mark.asyncio
async def test_run_real_job_completes_with_mocked_gpu(factory, monkeypatch, tmp_path):
    """Full happy path with download/ffmpeg/VastClient all mocked — proves the
    finalize step writes artifacts + completes the job with vast bookkeeping."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker.vast_client import GpuJobResult

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

    # Mock VastClient.run to return a result pointing at on-disk fakes.
    def fake_run(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None, whisper_lang=None):
        work_dir.mkdir(parents=True, exist_ok=True)
        voc = work_dir / "vocals.wav"
        inst = work_dir / "instrumental.wav"
        ltxt = work_dir / "lyrics.txt"
        ljson = work_dir / "lyrics.json"
        for p, c in [(voc, b"v"), (inst, b"i"), (ltxt, b"la la"), (ljson, b"{}")]:
            p.write_bytes(c)
        return GpuJobResult(
            vast_instance_id=4242,
            vast_cost=0.12,
            gpu_model="RTX 4090",
            vocals_path=voc,
            instrumental_path=inst,
            lyrics_txt_path=ltxt,
            lyrics_json_path=ljson,
        )

    from karaoke.worker import vast_client

    monkeypatch.setattr(vast_client.VastClient, "run", fake_run)

    # LRCLIB lookup must never hit the network in tests: inject a source that
    # always misses, so the pipeline keeps the (mocked) Whisper transcript.
    from karaoke.worker.lyrics import LyricsSource

    monkeypatch.setattr(
        pipeline,
        "_LYRICS_SOURCE",
        LyricsSource(http=lambda *a, **k: (404, None)),
    )

    job_id = await _make_job(factory)
    settings = Settings(
        device_mode="vast", vast_api_key="k-real", artifact_root=str(tmp_path)
    )
    await pipeline.run_real_job(factory, job_id, settings)

    async with factory() as session:
        from sqlalchemy import select

        from karaoke.db.models import Artifact

        job = await session.get(Job, job_id)
        assert job.status == JobStatus.completed
        assert job.progress == 100
        assert job.completed_at is not None
        assert job.title == "My Song"
        assert job.vast_instance_id == "4242"
        assert job.vast_cost_micros == 120000  # 0.12 * 1e6
        arts = (await session.scalars(select(Artifact).where(Artifact.job_id == job_id))).all()
        kinds = sorted(a.kind for a in arts)
        # LRCLIB missed → Whisper transcript kept; no lyrics_lrc artifact.
        assert kinds == ["karaoke", "lyrics", "vocals"]
        for a in arts:
            assert a.relative_path.startswith("tok-test/exports/")

    # Exported files exist on disk.
    exports = tmp_path / "tok-test" / "exports"
    assert (exports / "karaoke.mp3").is_file()
    assert (exports / "vocals.mp3").is_file()
    assert (exports / "lyrics.txt").is_file()
    assert not (exports / "lyrics.lrc").exists()
    assert (exports / "metadata.json").is_file()
    metadata = json.loads((exports / "metadata.json").read_text())
    assert metadata["lyrics_source"] == "whisper_asr"
    assert metadata["synced"] is False
    assert metadata["instrumental"] is False
    # The kept transcript is the Whisper output.
    assert (exports / "lyrics.txt").read_text() == "la la"


@pytest.mark.asyncio
async def test_run_real_job_prefers_lrclib_synced(factory, monkeypatch, tmp_path):
    """When LRCLIB returns synced lyrics, the pipeline writes lyrics.lrc, adds a
    lyrics_lrc artifact, and records provenance — overriding the Whisper ASR."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker.vast_client import GpuJobResult

    monkeypatch.setattr(
        pipeline, "_ytdlp_metadata", lambda url, settings=None, **_: {"title": "Artist - Song"}
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

    def fake_run(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None, whisper_lang=None):
        work_dir.mkdir(parents=True, exist_ok=True)
        voc = work_dir / "vocals.wav"
        inst = work_dir / "instrumental.wav"
        ltxt = work_dir / "lyrics.txt"
        ljson = work_dir / "lyrics.json"
        for p, c in [(voc, b"v"), (inst, b"i"), (ltxt, b"whisper"), (ljson, b"{}")]:
            p.write_bytes(c)
        return GpuJobResult(
            vast_instance_id=7,
            vast_cost=0.05,
            gpu_model="L40",
            vocals_path=voc,
            instrumental_path=inst,
            lyrics_txt_path=ltxt,
            lyrics_json_path=ljson,
        )

    from karaoke.worker import vast_client

    monkeypatch.setattr(vast_client.VastClient, "run", fake_run)

    synced = "[00:10.00]synced line one\n[00:13.00]synced line two"

    def fake_http(method, url, params):
        if "/api/get" in url:
            return 200, {"syncedLyrics": synced, "plainLyrics": "p1\np2"}
        return 404, None

    from karaoke.worker.lyrics import LyricsSource

    monkeypatch.setattr(pipeline, "_LYRICS_SOURCE", LyricsSource(http=fake_http))

    job_id = await _make_job(factory)
    settings = Settings(
        device_mode="vast", vast_api_key="k-real", artifact_root=str(tmp_path)
    )
    await pipeline.run_real_job(factory, job_id, settings)

    async with factory() as session:
        from sqlalchemy import select

        from karaoke.db.models import Artifact

        arts = (
            await session.scalars(select(Artifact).where(Artifact.job_id == job_id))
        ).all()
        kinds = sorted(a.kind for a in arts)
        assert kinds == ["karaoke", "lyrics", "lyrics_lrc", "vocals"]

    exports = tmp_path / "tok-test" / "exports"
    assert (exports / "lyrics.lrc").read_text() == synced
    # Plain export prefers LRCLIB plain text, NOT the Whisper transcript.
    assert (exports / "lyrics.txt").read_text() == "p1\np2"
    metadata = json.loads((exports / "metadata.json").read_text())
    assert metadata["lyrics_source"] == "lrclib_synced"
    assert metadata["synced"] is True
    assert metadata["instrumental"] is False


# ---------------------------------------------------------------------------
# force-align (#55): LRCLIB plain-only → align_text sent → aligned LRC consumed
# ---------------------------------------------------------------------------
def _patch_io(monkeypatch, pipeline):
    """Stub yt-dlp/ffmpeg so run_real_job runs without external binaries."""
    monkeypatch.setattr(
        pipeline, "_ytdlp_metadata", lambda url, settings=None, **_: {"title": "Artist - Song"}
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


@pytest.mark.asyncio
async def test_run_real_job_forced_aligns_plain_only(factory, monkeypatch, tmp_path):
    """LRCLIB plain-only → the pipeline ships the plain text to the GPU as
    ``align_text``; the GPU returns a force-aligned LRC; finalize writes
    lyrics.lrc + a lyrics_lrc artifact with provenance ``forced_aligned``."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker import vast_client
    from karaoke.worker.lyrics import LyricsSource
    from karaoke.worker.vast_client import GpuJobResult

    _patch_io(monkeypatch, pipeline)

    aligned = "[00:01.00]plain one\n[00:02.50]plain two"
    captured: dict = {}

    def fake_run(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None, whisper_lang=None):
        captured["align_text"] = align_text
        captured["align_lang"] = align_lang
        work_dir.mkdir(parents=True, exist_ok=True)
        voc = work_dir / "vocals.wav"
        inst = work_dir / "instrumental.wav"
        ltxt = work_dir / "lyrics.txt"
        ljson = work_dir / "lyrics.json"
        for p, c in [(voc, b"v"), (inst, b"i"), (ltxt, b"whisper"), (ljson, b"{}")]:
            p.write_bytes(c)
        # The handler force-aligned align_text → an aligned LRC on disk.
        alrc = work_dir / "aligned.lrc"
        alrc.write_text(aligned, encoding="utf-8")
        return GpuJobResult(
            vast_instance_id=9,
            vast_cost=0.05,
            gpu_model="L40",
            vocals_path=voc,
            instrumental_path=inst,
            lyrics_txt_path=ltxt,
            lyrics_json_path=ljson,
            aligned_lrc_path=alrc,
        )

    monkeypatch.setattr(vast_client.VastClient, "run", fake_run)

    # LRCLIB returns plain text but NO synced LRC.
    def fake_http(method, url, params):
        if "/api/get" in url:
            return 200, {"plainLyrics": "plain one\nplain two"}
        return 404, None

    monkeypatch.setattr(pipeline, "_LYRICS_SOURCE", LyricsSource(http=fake_http))

    job_id = await _make_job(factory)
    settings = Settings(
        device_mode="vast", vast_api_key="k-real", artifact_root=str(tmp_path)
    )
    await pipeline.run_real_job(factory, job_id, settings)

    # The plain text was forwarded to the GPU as align_text.
    assert captured["align_text"] == "plain one\nplain two"
    assert captured["align_lang"] == "eng"

    async with factory() as session:
        from sqlalchemy import select

        from karaoke.db.models import Artifact

        arts = (
            await session.scalars(select(Artifact).where(Artifact.job_id == job_id))
        ).all()
        kinds = sorted(a.kind for a in arts)
        assert kinds == ["karaoke", "lyrics", "lyrics_lrc", "vocals"]

    exports = tmp_path / "tok-test" / "exports"
    assert (exports / "lyrics.lrc").read_text() == aligned
    assert (exports / "lyrics.txt").read_text() == "plain one\nplain two"
    metadata = json.loads((exports / "metadata.json").read_text())
    assert metadata["lyrics_source"] == "forced_aligned"
    assert metadata["synced"] is True
    assert metadata["instrumental"] is False


@pytest.mark.asyncio
async def test_run_real_job_plain_only_tolerates_no_aligned_lrc(
    factory, monkeypatch, tmp_path
):
    """Tolerant fallback: align_text is sent, but the GPU (old image / failed
    alignment) returns NO aligned LRC → provenance stays ``lrclib_plain`` and
    the untimed plain text is kept. The job still completes."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker import vast_client
    from karaoke.worker.lyrics import LyricsSource
    from karaoke.worker.vast_client import GpuJobResult

    _patch_io(monkeypatch, pipeline)

    captured: dict = {}

    def fake_run(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None, whisper_lang=None):
        captured["align_text"] = align_text
        work_dir.mkdir(parents=True, exist_ok=True)
        voc = work_dir / "vocals.wav"
        inst = work_dir / "instrumental.wav"
        ltxt = work_dir / "lyrics.txt"
        ljson = work_dir / "lyrics.json"
        for p, c in [(voc, b"v"), (inst, b"i"), (ltxt, b"whisper"), (ljson, b"{}")]:
            p.write_bytes(c)
        # NO aligned.lrc written → aligned_lrc_path=None (old image).
        return GpuJobResult(
            vast_instance_id=11,
            vast_cost=0.05,
            gpu_model="L40",
            vocals_path=voc,
            instrumental_path=inst,
            lyrics_txt_path=ltxt,
            lyrics_json_path=ljson,
            aligned_lrc_path=None,
        )

    monkeypatch.setattr(vast_client.VastClient, "run", fake_run)

    def fake_http(method, url, params):
        if "/api/get" in url:
            return 200, {"plainLyrics": "plain one\nplain two"}
        return 404, None

    monkeypatch.setattr(pipeline, "_LYRICS_SOURCE", LyricsSource(http=fake_http))

    job_id = await _make_job(factory)
    settings = Settings(
        device_mode="vast", vast_api_key="k-real", artifact_root=str(tmp_path)
    )
    await pipeline.run_real_job(factory, job_id, settings)

    # align_text WAS sent (the pipeline always tries) ...
    assert captured["align_text"] == "plain one\nplain two"

    async with factory() as session:
        from sqlalchemy import select

        from karaoke.db.models import Artifact

        arts = (
            await session.scalars(select(Artifact).where(Artifact.job_id == job_id))
        ).all()
        kinds = sorted(a.kind for a in arts)
        # ... but no lyrics_lrc artifact, since no synced LRC was produced.
        assert kinds == ["karaoke", "lyrics", "vocals"]

    exports = tmp_path / "tok-test" / "exports"
    assert not (exports / "lyrics.lrc").exists()
    assert (exports / "lyrics.txt").read_text() == "plain one\nplain two"
    metadata = json.loads((exports / "metadata.json").read_text())
    assert metadata["lyrics_source"] == "lrclib_plain"
    assert metadata["synced"] is False


@pytest.mark.asyncio
async def test_run_real_job_pops_perjob_cookies_even_when_cancelled(factory):
    # A cancelled / early-return job must still drop its stashed per-job
    # cookies so the in-memory registry never leaks (issue #77).
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker import job_cookies

    async with factory() as session:
        job = Job(
            job_token="tok-cancelled",
            owner_subject="owner",
            source_url="https://example.com/song",
            status=JobStatus.cancelled,
            progress=0,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    job_cookies._PENDING.clear()
    job_cookies.stash(
        job_id,
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tv\n",
    )
    settings = Settings(device_mode="vast", vast_api_key="k-real")
    # Cancelled job → run_real_job returns early, but only AFTER popping.
    await pipeline.run_real_job(factory, job_id, settings)
    assert job_cookies.pop(job_id) is None
