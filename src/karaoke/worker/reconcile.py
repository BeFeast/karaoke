"""Boot-time reconcile for jobs abandoned by a coordinator restart (issue #50).

Jobs run as bare in-process ``asyncio.Task``s (see ``scheduler.schedule_job``),
so a container restart — and since v0.2.0 every merge auto-deploys, i.e.
recreates the stack — kills the task mid-flight while the DB row sits in a
non-terminal status forever (observed live: job #23 stuck at ``transcribing 75``
after a redeploy). This pass, run from the app lifespan right after the engine
comes up, makes persisted job state truthful and actionable again:

* ``queued`` rows lost only their dispatch — nothing had run yet — so they are
  re-dispatched through ``scheduler.schedule_job`` with no work lost. Note the
  per-job YouTube cookie stash (issue #77) is in-memory and gone after a
  restart: a re-dispatched gated-video job falls back to the central jar or
  fails with the normal gated-video error, which is acceptable by design.
* ``downloading``/``separating``/``transcribing`` rows lost in-flight work we
  cannot resume (the old container's task, temp files, and GPU poll loop died
  with it) — they are marked ``failed`` with an explicit interrupted reason so
  the SPA's existing retry flow (re-submit) covers recovery. ``progress`` is
  preserved for context; ``completed_at`` stays null.
* Terminal rows (``completed``/``failed``/``cancelled``) are never touched,
  which also makes the pass idempotent.

Live re-attach to a still-running RunPod job (re-poll ``/status`` + finalize)
is an explicit non-goal of this v1 — fail-with-reason is the agreed policy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from karaoke.config import Settings
from karaoke.db.models import Job, JobStatus
from karaoke.worker import scheduler

_log = logging.getLogger(__name__)

# Exact, user-visible reason recorded on jobs whose in-flight work the restart
# destroyed. The SPA surfaces ``error`` verbatim; "resubmit to retry" matches
# its retry affordance.
INTERRUPTED_ERROR = "interrupted: coordinator restarted mid-job — resubmit to retry"

# Rows that lost only their dispatch: safe to re-run from scratch.
_REQUEUE_STATUSES = (JobStatus.queued,)

# Rows that lost unrecoverable in-flight work: fail out with a clear reason.
_INTERRUPTED_STATUSES = (
    JobStatus.downloading,
    JobStatus.separating,
    JobStatus.transcribing,
)


@dataclass
class ReconcileResult:
    """Outcome of one boot reconcile pass (job ids, in id order)."""

    requeued: list[int] = field(default_factory=list)
    failed_out: list[int] = field(default_factory=list)


async def reconcile_jobs(
    session_factory: async_sessionmaker,
    settings: Settings,
) -> ReconcileResult:
    """Reconcile every non-terminal job left behind by the previous process.

    Re-dispatch is unconditional for ``queued`` rows and goes through
    ``scheduler.schedule_job`` (the same seam ``POST /jobs`` uses), AFTER the
    fail-out commit so a re-queued worker can never race this pass's writes.
    """
    result = ReconcileResult()
    async with session_factory() as session:
        jobs = (
            await session.scalars(
                select(Job)
                .where(Job.status.in_(_REQUEUE_STATUSES + _INTERRUPTED_STATUSES))
                .order_by(Job.id)
            )
        ).all()
        for job in jobs:
            if job.status in _INTERRUPTED_STATUSES:
                job.status = JobStatus.failed
                job.stage_note = None
                job.error = INTERRUPTED_ERROR
                result.failed_out.append(job.id)
            else:
                result.requeued.append(job.id)
        await session.commit()

    for job_id in result.requeued:
        scheduler.schedule_job(session_factory, job_id, settings)

    # One summary line per boot — the requeued ids make an unexpected boot-time
    # dispatch visible in container logs (the GET /jobs canary cannot see it).
    _log.info(
        "boot reconcile: requeued %d job(s) %s, failed out %d interrupted job(s) %s",
        len(result.requeued),
        result.requeued,
        len(result.failed_out),
        result.failed_out,
    )
    return result
