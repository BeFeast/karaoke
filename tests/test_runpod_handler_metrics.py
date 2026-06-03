"""Tests for RunPod handler telemetry helpers.

The handler is loaded by file path so tests do not need the runpod SDK or GPU
runtime. nvidia-smi is monkeypatched through subprocess.run.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_HANDLER = Path(__file__).resolve().parents[1] / "docker" / "runpod" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location("runpod_handler", _HANDLER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gpu_used_mb_parses_nvidia_smi(monkeypatch):
    handler = _load_handler()

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout="1234\n", stderr="")

    monkeypatch.setattr(handler.subprocess, "run", fake_run)

    assert handler._gpu_used_mb() == 1234


def test_gpu_used_mb_returns_none_without_nvidia_smi(monkeypatch):
    handler = _load_handler()

    def fake_run(*_args, **_kwargs):
        raise OSError("nvidia-smi missing")

    monkeypatch.setattr(handler.subprocess, "run", fake_run)

    assert handler._gpu_used_mb() is None


def test_stage_gpu_meter_tracks_peak(monkeypatch):
    handler = _load_handler()
    samples = iter([100, 150, 125])

    monkeypatch.setattr(handler, "_gpu_used_mb", lambda: next(samples))

    meter = handler._StageGpuMeter("unit", interval_s=10)
    with meter:
        meter._record(250)

    assert meter.snapshot()["start_vram_mb"] == 100
    assert meter.snapshot()["peak_vram_mb"] == 250
    assert meter.snapshot()["end_vram_mb"] == 150
    assert meter.snapshot()["elapsed_s"] >= 0
