"""Lifespan boot reconcile (issue #50).

Covers the wiring (``create_app`` boot invokes ``reconcile_jobs`` exactly once,
asserted via monkeypatching the seam ``app.py`` resolves at call time — the
``TestClient`` runs lifespan on its own loop) and a real end-to-end boot over a
pre-seeded DB: a stuck in-flight row is failed out with the interrupted reason
and a queued row is re-dispatched.
"""
from __future__ import annotations

import os
import secrets
import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from karaoke.api.app import create_app
from karaoke.db.models import Base, JobStatus
from karaoke.worker.reconcile import INTERRUPTED_ERROR, ReconcileResult


def test_lifespan_invokes_reconcile_on_boot(monkeypatch):
    """Boot calls reconcile_jobs once and comes up clean on an empty table."""
    from karaoke.api import app as app_module

    calls: list[tuple] = []

    async def fake_reconcile(session_factory, settings):
        calls.append((session_factory, settings))
        return ReconcileResult()

    monkeypatch.setattr(app_module, "reconcile_jobs", fake_reconcile)

    app = create_app()
    with TestClient(app) as tc:
        assert tc.get("/health").status_code == 200
        assert calls and calls[0][0] is not None and calls[0][1] is not None
    assert len(calls) == 1


def _db_path() -> str:
    return os.environ["KARAOKE_DATABASE_URL"].split("///", 1)[1]


def _seed_pre_boot_job(db_path: str, status: str, progress: int) -> tuple[int, str]:
    """Insert a job row BEFORE the app ever boots (simulating the DB a restarted
    coordinator wakes up to). Tables don't exist yet at that point, so create
    them first with a parallel sync engine — lifespan's own ``create_all`` is
    idempotent and will no-op over them."""
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    token = secrets.token_urlsafe(16)
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA busy_timeout=3000")
        con.execute(
            "INSERT INTO jobs "
            "(job_token, owner_subject, source_url, status, progress, "
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (
                token,
                "lan-default",
                "https://example.com/seed",
                status,
                progress,
                "2026-05-31 00:00:00+00:00",
                "2026-05-31 00:00:00+00:00",
            ),
        )
        con.commit()
        row = con.execute("SELECT id FROM jobs WHERE job_token=?", (token,)).fetchone()
        return int(row[0]), token
    finally:
        con.close()


def test_boot_fails_out_stuck_job_and_requeues_queued(monkeypatch):
    """Real reconcile over a pre-seeded DB: the live-observed scenario (a job
    abandoned at ``transcribing 75`` by a redeploy) is failed out on boot, and
    a queued row is re-dispatched through the scheduler seam."""
    from karaoke.worker import scheduler

    db_path = _db_path()
    stuck_id, _ = _seed_pre_boot_job(db_path, JobStatus.transcribing.value, 75)
    queued_id, _ = _seed_pre_boot_job(db_path, JobStatus.queued.value, 0)

    dispatched: list[int] = []
    monkeypatch.setattr(
        scheduler,
        "schedule_job",
        lambda session_factory, job_id, settings: dispatched.append(job_id),
    )

    app = create_app()
    with TestClient(app) as tc:
        jobs = {j["id"]: j for j in tc.get("/jobs").json()}

    stuck = jobs[stuck_id]
    assert stuck["status"] == JobStatus.failed.value
    assert stuck["error"] == INTERRUPTED_ERROR
    assert stuck["progress"] == 75

    # The queued row was re-dispatched (dispatch monkeypatched → still queued).
    assert dispatched == [queued_id]
    assert jobs[queued_id]["status"] == JobStatus.queued.value
