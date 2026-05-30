"""Worker dispatch.

``schedule_job`` decides between the real vast.ai worker and the in-process
mock based on settings:

  - ``device_mode == "mock"``                         → mock
  - ``device_mode == "auto"`` and no vast_api_key set → mock (CI default)
  - otherwise                                          → real worker

This keeps the existing test suite green: default test settings carry no
vast key, so ``create_job`` transparently falls back to the mock worker and
the API surface stays exercisable without touching the network.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from karaoke.config import Settings


def _use_mock(settings: Settings) -> bool:
    mode = (settings.device_mode or "auto").strip().lower()
    if mode == "mock":
        return True
    has_key = bool(settings.vast_api_key.strip())
    if mode == "auto":
        return not has_key
    # "vast" / "cpu-local": real worker (it will error clearly if no key).
    return False


def schedule_job(
    session_factory: async_sessionmaker,
    job_id: int,
    settings: Settings,
) -> asyncio.Task[None]:
    """Dispatch a job to the mock or the real worker; returns the scheduled task."""
    if _use_mock(settings):
        from karaoke.api.worker_stub import schedule_mock_job

        return schedule_mock_job(session_factory, job_id)

    from karaoke.worker.pipeline import schedule_real_job

    return schedule_real_job(session_factory, job_id, settings)
