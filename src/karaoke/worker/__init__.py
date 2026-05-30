"""Karaoke worker package.

The real worker provisions an ephemeral vast.ai GPU instance, runs Demucs
separation + faster-whisper transcription against the GPU image's HTTP
contract, and **always** destroys the instance in a ``finally`` clause.

Public entrypoints:
- :func:`karaoke.worker.scheduler.schedule_job` — dispatches to the real
  worker or the in-process mock depending on ``settings.device_mode`` and
  whether a vast.ai key is configured.
- :func:`karaoke.worker.pipeline.run_real_job` — the async orchestrator.
- :class:`karaoke.worker.vast_client.VastClient` — the ported lifecycle.
"""
from __future__ import annotations
