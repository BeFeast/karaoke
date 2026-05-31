"""Real karaoke worker — async orchestrator.

Lifecycle (mirrors the mock's status transitions so the existing ``/ws`` poller
in ``routes.py`` keeps working):

  queued
    → downloading   yt-dlp the source_url → audio; ffmpeg → working wav
                    (coordinator-side; runs on devbox's residential IP).
    → separating    provision a vast.ai GPU instance, POST /demucs
                    → vocals.wav + instrumental.wav.
    → transcribing  reuse the SAME instance window, POST /whisper on vocals.wav
                    → lyrics.txt + lyrics.json.
    → completed     ffmpeg-encode instrumental→karaoke.mp3, vocals→vocals.mp3;
                    write lyrics.txt + metadata.json under
                    {artifact_root}/{job_token}/...; insert Artifact rows; set
                    Job completed/100/completed_at/vast_instance_id/vast_cost.

On any exception the Job is marked failed (with ``error``) and the vast instance
is guaranteed destroyed (``VastClient.run`` owns a ``finally`` teardown).

yt-dlp / ffmpeg are invoked as subprocesses assumed present in the worker image;
they are NOT imported, so unit tests never need them installed.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import subprocess
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from karaoke.db.models import Artifact, Job, JobStatus
from karaoke.titles import derive_metadata

# yt-dlp player-client chain (mirrors scribe's downloader; android_vr is the
# token-free workhorse, web clients need the EJS/deno JS solver in the image).
_PLAYER_CLIENTS = "mweb,web_safari,android_vr,web_embedded"


class PipelineError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# coordinator-side subprocess steps (download + normalize + encode)
# ---------------------------------------------------------------------------
def _run(cmd: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise PipelineError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )
    return proc


def _ytdlp_metadata(source_url: str) -> dict:
    """Best-effort yt-dlp metadata dump; returns {} on any failure."""
    try:
        proc = subprocess.run(
            [
                "yt-dlp", "--no-playlist", "--skip-download",
                "--dump-single-json",
                "--extractor-args", f"youtube:player_client={_PLAYER_CLIENTS}",
                source_url,
            ],
            text=True, capture_output=True, timeout=120,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except Exception:
        return {}
    return {}


def _download_audio(source_url: str, dest: Path) -> Path:
    """yt-dlp the best audio stream to ``dest`` (no postprocessing)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "yt-dlp", "--no-playlist",
            "--extractor-args", f"youtube:player_client={_PLAYER_CLIENTS}",
            "-f", "ba/bestaudio/best",
            "-o", str(dest),
            source_url,
        ],
        timeout=900,
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise PipelineError(f"yt-dlp produced no audio at {dest}")
    return dest


def _to_wav(src: Path, dest: Path) -> Path:
    """Normalize ``src`` to a 44.1 kHz stereo wav for separation."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-vn", "-ar", "44100", "-ac", "2", str(dest),
        ],
        timeout=600,
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise PipelineError(f"ffmpeg produced no wav at {dest}")
    return dest


def _wav_to_mp3(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-vn", "-codec:a", "libmp3lame", "-q:a", "2",
            str(dest),
        ],
        timeout=600,
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise PipelineError(f"ffmpeg produced no mp3 at {dest}")
    return dest


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
async def _set_stage(
    session_factory: async_sessionmaker,
    job_id: int,
    status: JobStatus,
    progress: int,
    *,
    title: str | None = None,
    metadata: dict | None = None,
) -> bool:
    """Advance the job to ``status``/``progress``. Returns False if the job is
    gone or already terminal (cancelled/failed) — the caller should stop.

    When ``metadata`` is given (a ``derive_metadata`` mapping), persist any
    of ``artist``/``track``/``album``/``duration`` that we don't already have;
    we never overwrite an existing value so a later, coarser source can't clobber
    a better one."""
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status in {JobStatus.cancelled, JobStatus.failed}:
            return False
        job.status = status
        job.progress = progress
        if title and not job.title:
            job.title = title
        if metadata:
            for field in ("artist", "track", "album", "duration"):
                value = metadata.get(field)
                if value is not None and getattr(job, field) is None:
                    setattr(job, field, value)
        await session.commit()
    return True


async def _prior_24h_cost_micros(session_factory: async_sessionmaker) -> int:
    """Sum vast_cost_micros over jobs completed in the last 24h (rolling cap)."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=24)
    async with session_factory() as session:
        rows = await session.scalars(
            select(Job.vast_cost_micros).where(
                Job.completed_at.is_not(None),
                Job.completed_at >= cutoff,
                Job.vast_cost_micros.is_not(None),
            )
        )
        return sum(int(v) for v in rows if v is not None)


async def _mark_failed(
    session_factory: async_sessionmaker, job_id: int, error: str
) -> None:
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.failed
        job.error = error[:4000]
        await session.commit()


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------
async def run_real_job(
    session_factory: async_sessionmaker,
    job_id: int,
    settings,
) -> None:
    """Run a real karaoke job end-to-end. Always leaves the Job in a terminal
    state (completed / failed) and never leaks a vast instance."""
    # Import here so the module is importable in CI without the worker deps.
    from karaoke.worker.scheduler import _use_runpod
    from karaoke.worker.vast_client import VastClient

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status in {JobStatus.cancelled, JobStatus.failed}:
            return
        source_url = job.source_url
        job_token = job.job_token

    artifact_root = Path(settings.artifact_root)
    job_root = artifact_root / job_token
    work_dir = job_root / "work"
    exports_dir = job_root / "exports"

    try:
        # --- downloading ----------------------------------------------------
        if not await _set_stage(session_factory, job_id, JobStatus.downloading, 15):
            return
        meta = await asyncio.to_thread(_ytdlp_metadata, source_url)
        title = (meta.get("title") or "").strip() or None
        source_meta = derive_metadata(meta)
        if title or any(source_meta.values()):
            await _set_stage(
                session_factory,
                job_id,
                JobStatus.downloading,
                20,
                title=title,
                metadata=source_meta,
            )

        raw_audio = work_dir / "source.audio"
        await asyncio.to_thread(_download_audio, source_url, raw_audio)
        mix_wav = work_dir / "mix.wav"
        await asyncio.to_thread(_to_wav, raw_audio, mix_wav)

        # --- separating (provision + /demucs) -------------------------------
        if not await _set_stage(session_factory, job_id, JobStatus.separating, 45):
            return
        prior = await _prior_24h_cost_micros(session_factory)
        if _use_runpod(settings):
            from karaoke.worker.runpod_client import RunpodClient

            client = RunpodClient(settings, prior_24h_cost_micros=prior)
        else:
            client = VastClient(settings, prior_24h_cost_micros=prior)

        # VastClient.run is synchronous (urllib + ssh + httpx); offload it. It
        # runs BOTH /demucs (separating) and /whisper (transcribing) in one
        # instance window, then destroys the instance in its own finally.
        # We flip the visible status to "transcribing" right before the call so
        # the WS poller reflects the GPU phase; the single window covers both.
        if not await _set_stage(session_factory, job_id, JobStatus.transcribing, 75):
            return
        gpu = await asyncio.to_thread(client.run, mix_wav, work_dir)

        # --- finalize -------------------------------------------------------
        exports_dir.mkdir(parents=True, exist_ok=True)
        karaoke_mp3 = exports_dir / "karaoke.mp3"
        vocals_mp3 = exports_dir / "vocals.mp3"
        await asyncio.to_thread(_wav_to_mp3, gpu.instrumental_path, karaoke_mp3)
        await asyncio.to_thread(_wav_to_mp3, gpu.vocals_path, vocals_mp3)

        lyrics_txt = exports_dir / "lyrics.txt"
        lyrics_txt.write_bytes(gpu.lyrics_txt_path.read_bytes())

        metadata = {
            "title": title,
            "artist": source_meta.get("artist"),
            "track": source_meta.get("track"),
            "album": source_meta.get("album"),
            "duration": source_meta.get("duration"),
            "source_url": source_url,
            "device": "vast",
            "gpu_model": gpu.gpu_model,
            "vast_instance_id": gpu.vast_instance_id,
            "vast_cost": round(gpu.vast_cost, 6),
        }
        (exports_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        artifacts = [
            ("vocals", "vocals.mp3", vocals_mp3, "audio/mpeg"),
            ("karaoke", "karaoke.mp3", karaoke_mp3, "audio/mpeg"),
            ("lyrics", "lyrics.txt", lyrics_txt, "text/plain"),
        ]

        async with session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in {JobStatus.cancelled, JobStatus.failed}:
                return
            for kind, fname, path, ctype in artifacts:
                size = path.stat().st_size if path.is_file() else None
                session.add(
                    Artifact(
                        job_id=job.id,
                        kind=kind,
                        relative_path=f"{job_token}/exports/{fname}",
                        size_bytes=size,
                        content_type=ctype,
                    )
                )
            job.status = JobStatus.completed
            job.progress = 100
            job.completed_at = dt.datetime.now(dt.UTC)
            job.vast_instance_id = str(gpu.vast_instance_id)
            job.vast_cost_micros = round(gpu.vast_cost * 1_000_000)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — surface as a failed job, never crash the loop
        await _mark_failed(session_factory, job_id, f"{type(exc).__name__}: {exc}")


def schedule_real_job(
    session_factory: async_sessionmaker,
    job_id: int,
    settings,
) -> asyncio.Task[None]:
    """Schedule :func:`run_real_job` on the running event loop."""
    return asyncio.create_task(run_real_job(session_factory, job_id, settings))
