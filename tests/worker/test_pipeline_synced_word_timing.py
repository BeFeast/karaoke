"""End-to-end wiring for LRCLIB-synced word timing (issue #222).

Drives :func:`karaoke.worker.pipeline.run_real_job` with a *synced* LRCLIB
result and a stubbed GPU, asserting the two coordinator-level behaviors the
unit tests can't reach:

  * the coordinator sends ``lrc_to_plain(synced_lrc)`` as ``align_text`` so the
    GPU force-aligns the curated text in the same job window;
  * finalize merges the aligner word tags into the curated LRC, keeps provenance
    ``lrclib_synced``, and records ``lyrics_word_timing: forced_aligned`` in
    ``metadata.json``.

No network, no GPU, no ffmpeg: every I/O boundary is stubbed.
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
from karaoke.worker.lyrics import LyricsResult, lrc_to_plain

SYNCED = "[00:12.00]hello world\n[00:15.50]second line"


@pytest_asyncio.fixture
async def factory(tmp_path) -> async_sessionmaker:
    url = f"sqlite+aiosqlite:///{tmp_path / 'wt.db'}"
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
            job_token="tok-wt",
            owner_subject="owner",
            source_url="https://example.com/song",
            status=JobStatus.queued,
            progress=0,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


class _SyncedSource:
    """Stub ``_LYRICS_SOURCE`` that always returns the same synced record."""

    def fetch(self, **_kw) -> LyricsResult:
        return LyricsResult(
            synced_lrc=SYNCED, plain="hello world\nsecond line", source="lrclib_get"
        )


def _patch_io(pipeline, monkeypatch):
    monkeypatch.setattr(
        pipeline, "_ytdlp_metadata", lambda url, settings=None, **_: {"title": "My Song"}
    )

    def fake_download(url, dest: Path, settings=None, **_):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"audio")
        return dest

    def fake_wav(src, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"wav")
        return dest

    monkeypatch.setattr(pipeline, "_download_audio", fake_download)
    monkeypatch.setattr(pipeline, "_to_wav", fake_wav)
    monkeypatch.setattr(pipeline, "_wav_to_mp3", fake_wav)
    monkeypatch.setattr(pipeline, "_LYRICS_SOURCE", _SyncedSource())


def _runpod_settings(tmp_path) -> Settings:
    return Settings(
        device_mode="runpod",
        runpod_api_key="k-real",
        runpod_endpoint_id="ep-real",
        artifact_root=str(tmp_path),
    )


@pytest.mark.asyncio
async def test_synced_job_sends_align_text_and_merges_word_timing(
    factory, monkeypatch, tmp_path
):
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker.runpod_client import RunpodClient
    from karaoke.worker.vast_client import GpuJobResult

    captured: dict[str, object] = {}

    def fake_run(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None, whisper_lang=None):
        # Record what the coordinator chose to align.
        captured["align_text"] = align_text
        captured["align_lang"] = align_lang
        work_dir.mkdir(parents=True, exist_ok=True)
        voc = work_dir / "vocals.wav"
        inst = work_dir / "instrumental.wav"
        ltxt = work_dir / "lyrics.txt"
        ljson = work_dir / "lyrics.json"
        aligned = work_dir / "aligned.lrc"
        for p, c in [(voc, b"v"), (inst, b"i"), (ltxt, b"la la"), (ljson, b"{}")]:
            p.write_bytes(c)
        # The GPU force-aligned the SAME curated text into an Enhanced LRC.
        aligned.write_text(
            "[00:12.05]<00:12.05>hello <00:12.9>world <00:13.4>\n"
            "[00:15.55]<00:15.55>second <00:16.1>line <00:16.8>",
            encoding="utf-8",
        )
        return GpuJobResult(
            vast_instance_id="rp-7",
            vast_cost=0.016,
            gpu_model="RTX 4090",
            vocals_path=voc,
            instrumental_path=inst,
            lyrics_txt_path=ltxt,
            lyrics_json_path=ljson,
            aligned_lrc_path=aligned,
        )

    _patch_io(pipeline, monkeypatch)
    monkeypatch.setattr(RunpodClient, "run", fake_run)

    job_id = await _make_job(factory)
    await pipeline.run_real_job(factory, job_id, _runpod_settings(tmp_path))

    # 1. Coordinator aligned the curated text (line tags stripped).
    assert captured["align_text"] == lrc_to_plain(SYNCED) == "hello world\nsecond line"
    assert captured["align_lang"] == "eng"

    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job.status == JobStatus.completed

    exports = tmp_path / "tok-wt" / "exports"
    # 2. Merged LRC: curated LRCLIB line tags + aligner word tags.
    lrc = (exports / "lyrics.lrc").read_text(encoding="utf-8")
    assert lrc == (
        "[00:12.00]<00:12.05>hello <00:12.90>world <00:13.40>\n"
        "[00:15.50]<00:15.55>second <00:16.10>line <00:16.80>"
    )
    assert lrc_to_plain(lrc) == lrc_to_plain(SYNCED)  # text byte-identical

    # 3. Provenance unchanged; merge flag recorded.
    meta = json.loads((exports / "metadata.json").read_text(encoding="utf-8"))
    assert meta["lyrics_source"] == "lrclib_synced"
    assert meta["synced"] is True
    assert meta["lyrics_word_timing"] == "forced_aligned"


@pytest.mark.asyncio
async def test_synced_job_without_aligned_lrc_stays_plain(factory, monkeypatch, tmp_path):
    """Old image / alignment failure → no aligned LRC → curated LRC verbatim, no
    word-timing flag. Never fatal (#222)."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker.runpod_client import RunpodClient
    from karaoke.worker.vast_client import GpuJobResult

    def fake_run(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None, whisper_lang=None):
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
            aligned_lrc_path=None,  # nothing came back
        )

    _patch_io(pipeline, monkeypatch)
    monkeypatch.setattr(RunpodClient, "run", fake_run)

    job_id = await _make_job(factory)
    await pipeline.run_real_job(factory, job_id, _runpod_settings(tmp_path))

    exports = tmp_path / "tok-wt" / "exports"
    assert (exports / "lyrics.lrc").read_text(encoding="utf-8") == SYNCED
    meta = json.loads((exports / "metadata.json").read_text(encoding="utf-8"))
    assert meta["lyrics_source"] == "lrclib_synced"
    assert "lyrics_word_timing" not in meta
