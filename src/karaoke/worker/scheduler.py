"""Worker dispatch.

``schedule_job`` decides between the mock, the RunPod Serverless worker,
and the vast.ai worker based on settings:

  - ``device_mode == "mock"``                                              → mock
  - ``device_mode == "runpod"`` (or auto + runpod creds + no vast key)     → runpod
  - ``device_mode == "auto"`` and no vast_api_key set + no runpod creds    → mock (CI default)
  - otherwise                                                              → vast (real)

This keeps the existing test suite green: default test settings carry no
keys, so ``create_job`` falls back to the mock worker and the API surface
stays exercisable without touching the network.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from karaoke.config import Settings


def _has_runpod(settings: Settings) -> bool:
    return bool(
        settings.runpod_api_key.strip() and settings.runpod_endpoint_id.strip()
    )


def _use_mock(settings: Settings) -> bool:
    mode = (settings.device_mode or "auto").strip().lower()
    if mode == "mock":
        return True
    if mode == "auto":
        return not (settings.vast_api_key.strip() or _has_runpod(settings))
    return False


def _use_runpod(settings: Settings) -> bool:
    mode = (settings.device_mode or "auto").strip().lower()
    if mode == "runpod":
        return True
    if mode == "auto":
        # Prefer runpod when only runpod is configured. If both vast and
        # runpod are configured, vast wins (explicit override required).
        return _has_runpod(settings) and not settings.vast_api_key.strip()
    return False


def schedule_job(
    session_factory: async_sessionmaker,
    job_id: int,
    settings: Settings,
) -> asyncio.Task[None]:
    """Dispatch a job to the mock, runpod, or vast worker; returns the scheduled task."""
    if _use_mock(settings):
        from karaoke.api.worker_stub import schedule_mock_job

        return schedule_mock_job(session_factory, job_id)

    from karaoke.worker.pipeline import schedule_real_job

    return schedule_real_job(session_factory, job_id, settings)
