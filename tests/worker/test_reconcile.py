"""Boot reconcile (issue #50): requeue queued, fail out in-flight, leave terminal.

No network, no worker run — ``scheduler.schedule_job`` is monkeypatched at the
module attribute ``reconcile_jobs`` resolves at call time, so dispatch is
observed without executing a pipeline.
"""
from __future__ import annotations

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from karaoke.config import Settings
from karaoke.db.models import Base, Job, JobStatus
from karaoke.db.session import create_engine_and_sessionmaker
from karaoke.worker import scheduler
from karaoke.worker.reconcile import INTERRUPTED_ERROR, reconcile_jobs

_IN_FLIGHT = (JobStatus.downloading, JobStatus.separating, JobStatus.transcribing)
_TERMINAL = (JobStatus.completed, JobStatus.failed, JobStatus.cancelled)

# Per-status seed progress — distinct values prove fail-out preserves progress.
_SEED_PROGRESS = {
    JobStatus.queued: 0,
    JobStatus.downloading: 15,
    JobStatus.separating: 45,
    JobStatus.transcribing: 75,
    JobStatus.completed: 100,
    JobStatus.failed: 30,
    JobStatus.cancelled: 60,
}


@pytest_asyncio.fixture
async def factory(tmp_path) -> async_sessionmaker:
    url = f"sqlite+aiosqlite:///{tmp_path / 'reconcile.db'}"
    engine, fac = create_engine_and_sessionmaker(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield fac
    finally:
        await engine.dispose()


async def _seed_one_per_status(factory) -> dict[JobStatus, int]:
    """Insert exactly one job per ``JobStatus`` value; return status → id."""
    ids: dict[JobStatus, int] = {}
    async with factory() as session:
        for status in JobStatus:
            job = Job(
                job_token=f"tok-{status.value}",
                owner_subject="owner",
                source_url=f"https://example.com/{status.value}",
                status=status,
                progress=_SEED_PROGRESS[status],
            )
            if status in _IN_FLIGHT:
                job.stage_note = "GPU worker warming up"
            if status is JobStatus.completed:
                job.completed_at = dt.datetime.now(dt.UTC)
            if status is JobStatus.failed:
                job.error = "pre-existing failure"
            session.add(job)
            await session.flush()
            ids[status] = job.id
        await session.commit()
    return ids


def _snapshot(job: Job) -> dict:
    """All column values of a row, for field-by-field comparison."""
    return {c.name: getattr(job, c.name) for c in Job.__table__.columns}


async def _snapshot_all(factory) -> dict[int, dict]:
    async with factory() as session:
        jobs = (await session.scalars(select(Job).order_by(Job.id))).all()
        return {job.id: _snapshot(job) for job in jobs}


@pytest.fixture
def dispatch_calls(monkeypatch) -> list[int]:
    """Capture ``scheduler.schedule_job`` job ids without running anything."""
    calls: list[int] = []

    def fake_schedule(session_factory, job_id: int, settings) -> None:
        calls.append(job_id)

    monkeypatch.setattr(scheduler, "schedule_job", fake_schedule)
    return calls


@pytest.mark.asyncio
async def test_reconcile_requeues_fails_out_and_leaves_terminal(
    factory, dispatch_calls
):
    ids = await _seed_one_per_status(factory)
    before = await _snapshot_all(factory)
    settings = Settings()

    result = await reconcile_jobs(factory, settings)

    # queued → re-dispatched exactly once, row untouched (still queued).
    assert dispatch_calls == [ids[JobStatus.queued]]
    assert result.requeued == [ids[JobStatus.queued]]
    assert sorted(result.failed_out) == sorted(ids[s] for s in _IN_FLIGHT)

    async with factory() as session:
        # In-flight rows → failed with the exact interrupted reason; progress
        # preserved; completed_at stays null; stale stage_note cleared.
        for status in _IN_FLIGHT:
            job = await session.get(Job, ids[status])
            assert job.status == JobStatus.failed
            assert job.error == INTERRUPTED_ERROR
            assert job.progress == _SEED_PROGRESS[status]
            assert job.completed_at is None
            assert job.stage_note is None

        # Terminal rows: every column value unchanged.
        for status in _TERMINAL:
            job = await session.get(Job, ids[status])
            assert _snapshot(job) == before[ids[status]]

        # The queued row itself is unchanged (dispatch was monkeypatched).
        queued = await session.get(Job, ids[JobStatus.queued])
        assert _snapshot(queued) == before[ids[JobStatus.queued]]


@pytest.mark.asyncio
async def test_reconcile_second_run_mutates_nothing(factory, dispatch_calls):
    """Idempotency: a second pass performs no row mutations at all — rows
    already failed out are terminal now, and terminal rows stay untouched.
    ``schedule_job`` MAY fire again for the still-queued row (with dispatch
    monkeypatched it never leaves ``queued``; in a real boot reconcile runs
    exactly once, so the re-dispatch is acceptable by design)."""
    ids = await _seed_one_per_status(factory)
    settings = Settings()

    await reconcile_jobs(factory, settings)
    after_first = await _snapshot_all(factory)

    result = await reconcile_jobs(factory, settings)

    assert await _snapshot_all(factory) == after_first
    assert result.failed_out == []
    assert result.requeued == [ids[JobStatus.queued]]
    assert dispatch_calls == [ids[JobStatus.queued]] * 2


@pytest.mark.asyncio
async def test_reconcile_empty_table_is_a_clean_noop(factory, dispatch_calls):
    result = await reconcile_jobs(factory, Settings())
    assert result.requeued == []
    assert result.failed_out == []
    assert dispatch_calls == []
