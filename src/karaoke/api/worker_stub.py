"""Mocked vast.ai worker.

The real worker provisions an ephemeral vast.ai instance, runs Demucs +
faster-whisper, uploads artifacts to the TrueNAS NFS share, and **always**
destroys the instance in a ``finally`` clause. This stub does none of that
— it advances the job through the lifecycle and writes synthetic artifact
rows so the API surface is exercisable end-to-end.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import secrets

from sqlalchemy.ext.asyncio import async_sessionmaker

from karaoke.db.models import Artifact, Job, JobStatus

# Synthetic artifacts produced by the mock worker; mirrors the real shape.
_MOCK_ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("vocals", "vocals.mp3", "audio/mpeg"),
    ("karaoke", "karaoke.mp3", "audio/mpeg"),
    ("lyrics", "lyrics.txt", "text/plain"),
)


async def run_mock_job(
    session_factory: async_sessionmaker,
    job_id: int,
    *,
    sleep: float = 0.0,
) -> None:
    """Walk the job through queued → … → completed with synthetic artifacts.

    Tests use ``sleep=0`` so the full lifecycle resolves synchronously
    after a single ``await``. Production stubs may pass a small delay.
    """
    stages = (
        JobStatus.downloading,
        JobStatus.separating,
        JobStatus.transcribing,
    )
    for stage in stages:
        async with session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in {JobStatus.cancelled, JobStatus.failed}:
                return
            job.status = stage
            job.progress = {
                JobStatus.downloading: 25,
                JobStatus.separating: 60,
                JobStatus.transcribing: 90,
            }[stage]
            await session.commit()
        if sleep:
            await asyncio.sleep(sleep)

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status in {JobStatus.cancelled, JobStatus.failed}:
            return
        job.status = JobStatus.completed
        job.progress = 100
        job.completed_at = dt.datetime.now(dt.UTC)
        # Mock vast.ai bookkeeping.
        job.vast_instance_id = f"mock-{secrets.token_hex(4)}"
        job.vast_cost_micros = 0
        for kind, rel, ctype in _MOCK_ARTIFACTS:
            session.add(
                Artifact(
                    job_id=job.id,
                    kind=kind,
                    relative_path=f"{job.job_token}/{rel}",
                    size_bytes=0,
                    content_type=ctype,
                )
            )
        await session.commit()


def schedule_mock_job(
    session_factory: async_sessionmaker,
    job_id: int,
    *,
    sleep: float = 0.0,
) -> asyncio.Task[None]:
    """Schedule :func:`run_mock_job` on the running event loop."""
    return asyncio.create_task(run_mock_job(session_factory, job_id, sleep=sleep))
