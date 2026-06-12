"""Pipeline tests for ``upload://`` jobs (#172).

Harness pattern of ``test_scheduler.py``: yt-dlp / ffmpeg / GPU / LRCLIB are
all stubbed; the uploaded source is pre-staged at ``work/source.audio`` (the
API endpoint's job) and ffprobe is mocked at the ``_run`` subprocess seam —
no ffmpeg/ffprobe binary is required by any test.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

import karaoke.worker.pipeline as pipeline
from karaoke.config import Settings
from karaoke.db.models import Base, Job, JobStatus
from karaoke.db.session import create_engine_and_sessionmaker
from karaoke.worker import vast_client
from karaoke.worker.lyrics import LyricsSource
from karaoke.worker.vast_client import GpuJobResult

JOB_TOKEN = "tok-upload"

FFPROBE_JSON = json.dumps(
    {
        "format": {
            "duration": "212.41",
            "tags": {
                "TITLE": "Get Lucky",  # mixed-case on purpose: read case-insensitively
                "Artist": "Daft Punk",
                "album": "Random Access Memories",
            },
        }
    }
)


@pytest_asyncio.fixture
async def factory(tmp_path) -> async_sessionmaker:
    url = f"sqlite+aiosqlite:///{tmp_path / 'upload.db'}"
    engine, fac = create_engine_and_sessionmaker(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield fac
    finally:
        await engine.dispose()


async def _make_upload_job(
    factory, source_url: str = "upload://tagged-song.mp3"
) -> int:
    async with factory() as session:
        job = Job(
            job_token=JOB_TOKEN,
            owner_subject="owner",
            source_url=source_url,
            status=JobStatus.queued,
            progress=0,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


def _stage_source(tmp_path: Path) -> Path:
    src = tmp_path / JOB_TOKEN / "work" / "source.audio"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"uploaded-audio-bytes")
    return src


def _patch_ytdlp_recorders(monkeypatch) -> dict:
    """Stub both yt-dlp seams; record any (forbidden) invocation."""
    called: dict[str, int] = {}

    def fake_meta(url, settings=None, **_):
        called["metadata"] = called.get("metadata", 0) + 1
        return {}

    def fake_download(url, dest, settings=None, **_):
        called["download"] = called.get("download", 0) + 1
        return dest

    monkeypatch.setattr(pipeline, "_ytdlp_metadata", fake_meta)
    monkeypatch.setattr(pipeline, "_download_audio", fake_download)
    return called


def _patch_ffmpeg(monkeypatch) -> None:
    def fake_to_wav(src, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"wav")
        return dest

    def fake_wav_to_mp3(src, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mp3")
        return dest

    monkeypatch.setattr(pipeline, "_to_wav", fake_to_wav)
    monkeypatch.setattr(pipeline, "_wav_to_mp3", fake_wav_to_mp3)


def _patch_ffprobe(monkeypatch, stdout: str = FFPROBE_JSON) -> None:
    """Mock ffprobe at the ``_run`` subprocess seam (ffmpeg calls are patched
    out above it, so ffprobe is the only command reaching ``_run``)."""

    def fake_run(cmd, *, timeout=None):
        assert cmd[0] == "ffprobe", cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(pipeline, "_run", fake_run)


def _patch_gpu(monkeypatch) -> None:
    def fake_run(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None):
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

    monkeypatch.setattr(vast_client.VastClient, "run", fake_run)


def _patch_lrclib(monkeypatch) -> list[dict]:
    """LRCLIB stub returning a synced hit; records the lookup params."""
    lookups: list[dict] = []

    def fake_http(method, url, params):
        if "/api/get" in url:
            lookups.append(dict(params or {}))
            return 200, {
                "syncedLyrics": "[00:10.00]line one",
                "plainLyrics": "line one",
            }
        return 404, None

    monkeypatch.setattr(pipeline, "_LYRICS_SOURCE", LyricsSource(http=fake_http))
    return lookups


def _spy_stages(monkeypatch) -> list[JobStatus]:
    seen: list[JobStatus] = []
    orig = pipeline._set_stage

    async def spy(factory_, job_id_, status_, progress_, **kwargs):
        seen.append(status_)
        return await orig(factory_, job_id_, status_, progress_, **kwargs)

    monkeypatch.setattr(pipeline, "_set_stage", spy)
    return seen


async def _get_job(factory, job_id: int) -> Job:
    async with factory() as session:
        return await session.get(Job, job_id)


# ---------------------------------------------------------------------------
# end-to-end: yt-dlp bypassed, ffprobe metadata, LRCLIB fed from tags
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_job_completes_without_ytdlp(factory, monkeypatch, tmp_path):
    ytdlp_calls = _patch_ytdlp_recorders(monkeypatch)
    _patch_ffmpeg(monkeypatch)
    _patch_ffprobe(monkeypatch)
    _patch_gpu(monkeypatch)
    lookups = _patch_lrclib(monkeypatch)
    stages = _spy_stages(monkeypatch)

    _stage_source(tmp_path)
    job_id = await _make_upload_job(factory)
    settings = Settings(
        device_mode="vast", vast_api_key="k-real", artifact_root=str(tmp_path)
    )
    await pipeline.run_real_job(factory, job_id, settings)

    job = await _get_job(factory, job_id)
    assert job.status is JobStatus.completed, job.error
    assert job.progress == 100

    # yt-dlp was never touched on the upload branch.
    assert ytdlp_calls == {}

    # queued (creation) → downloading → separating → transcribing → completed.
    deduped = [s for i, s in enumerate(stages) if i == 0 or stages[i - 1] != s]
    assert deduped == [
        JobStatus.downloading,
        JobStatus.separating,
        JobStatus.transcribing,
    ]

    # Tag-derived metadata (mocked ffprobe JSON) persisted on the row.
    assert job.title == "Get Lucky"
    assert job.artist == "Daft Punk"
    assert job.track == "Get Lucky"
    assert job.album == "Random Access Memories"
    assert job.duration == 212

    # LRCLIB lookup used the tag-derived artist/track (+ album/duration).
    assert lookups, "LRCLIB stub was never called"
    assert lookups[0]["artist_name"] == "Daft Punk"
    assert lookups[0]["track_name"] == "Get Lucky"

    exports = tmp_path / JOB_TOKEN / "exports"
    assert (exports / "lyrics.lrc").read_text() == "[00:10.00]line one"
    metadata = json.loads((exports / "metadata.json").read_text())
    assert metadata["lyrics_source"] == "lrclib_synced"
    assert metadata["source_url"] == "upload://tagged-song.mp3"


# ---------------------------------------------------------------------------
# corrupt / missing upload → failed with the locked stage note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_job_missing_source_fails_cleanly(factory, monkeypatch, tmp_path):
    ytdlp_calls = _patch_ytdlp_recorders(monkeypatch)
    # No source.audio staged.
    job_id = await _make_upload_job(factory)
    settings = Settings(
        device_mode="vast", vast_api_key="k-real", artifact_root=str(tmp_path)
    )
    await pipeline.run_real_job(factory, job_id, settings)

    job = await _get_job(factory, job_id)
    assert job.status is JobStatus.failed
    assert job.stage_note == "uploaded audio is missing or not decodable"
    assert "uploaded audio is missing or not decodable" in job.error
    assert ytdlp_calls == {}


@pytest.mark.asyncio
async def test_upload_job_undecodable_source_fails_cleanly(factory, monkeypatch, tmp_path):
    ytdlp_calls = _patch_ytdlp_recorders(monkeypatch)

    def boom(cmd, *, timeout=None):
        raise pipeline.PipelineError("ffprobe could not decode")

    monkeypatch.setattr(pipeline, "_run", boom)

    _stage_source(tmp_path)
    job_id = await _make_upload_job(factory)
    settings = Settings(
        device_mode="vast", vast_api_key="k-real", artifact_root=str(tmp_path)
    )
    await pipeline.run_real_job(factory, job_id, settings)

    job = await _get_job(factory, job_id)
    assert job.status is JobStatus.failed
    assert job.stage_note == "uploaded audio is missing or not decodable"
    assert "ffprobe could not decode" in job.error
    assert ytdlp_calls == {}


# ---------------------------------------------------------------------------
# _probe_upload_meta unit tests on canned ffprobe JSON
# ---------------------------------------------------------------------------


def _probe_with(monkeypatch, payload: str, fallback: str = "fallback-song.mp3") -> dict:
    def fake_run(cmd, *, timeout=None):
        assert cmd[0] == "ffprobe"
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    monkeypatch.setattr(pipeline, "_run", fake_run)
    return pipeline._probe_upload_meta(Path("/nfs/x/work/source.audio"), fallback)


def test_probe_reads_tags_case_insensitively(monkeypatch):
    meta = _probe_with(monkeypatch, FFPROBE_JSON)
    assert meta == {
        "title": "Get Lucky",
        "track": "Get Lucky",
        "artist": "Daft Punk",
        "album": "Random Access Memories",
        "duration": 212.41,
    }


def test_probe_missing_tags_falls_back_to_filename_stem(monkeypatch):
    meta = _probe_with(monkeypatch, json.dumps({"format": {"duration": "10.0"}}))
    assert meta["title"] == "fallback-song"
    assert meta["track"] is None
    assert meta["artist"] is None
    assert meta["album"] is None
    assert meta["duration"] == 10.0


def test_probe_malformed_duration_becomes_none(monkeypatch):
    payload = json.dumps(
        {"format": {"duration": "N/A", "tags": {"title": "X", "artist": "Y"}}}
    )
    meta = _probe_with(monkeypatch, payload)
    assert meta["duration"] is None
    assert meta["title"] == "X"
    assert meta["artist"] == "Y"


def test_probe_invalid_json_raises(monkeypatch):
    with pytest.raises(pipeline.PipelineError):
        _probe_with(monkeypatch, "not json {")


@pytest.mark.asyncio
async def test_upload_job_completes_with_malformed_duration(
    factory, monkeypatch, tmp_path
):
    """Malformed format.duration → duration None — the job still completes
    (the LRCLIB lookup simply runs without a duration filter)."""
    _patch_ytdlp_recorders(monkeypatch)
    _patch_ffmpeg(monkeypatch)
    _patch_ffprobe(
        monkeypatch,
        stdout=json.dumps(
            {
                "format": {
                    "duration": "garbage",
                    "tags": {"title": "Songy", "artist": "Someone"},
                }
            }
        ),
    )
    _patch_gpu(monkeypatch)
    lookups = _patch_lrclib(monkeypatch)

    _stage_source(tmp_path)
    job_id = await _make_upload_job(factory)
    settings = Settings(
        device_mode="vast", vast_api_key="k-real", artifact_root=str(tmp_path)
    )
    await pipeline.run_real_job(factory, job_id, settings)

    job = await _get_job(factory, job_id)
    assert job.status is JobStatus.completed, job.error
    assert job.duration is None
    assert lookups and "duration" not in lookups[0]
