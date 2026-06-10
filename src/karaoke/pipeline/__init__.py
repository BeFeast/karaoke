"""CPU-local karaoke pipeline used by the development CLI."""
from __future__ import annotations

from karaoke.pipeline.local import LocalPipelineConfig, LocalPipelineResult, run_local_pipeline

__all__ = ["LocalPipelineConfig", "LocalPipelineResult", "run_local_pipeline"]
