"""karaoke — URL → isolated vocals + instrumental playback + lyrics.

This package is the coordinator (FastAPI API + worker) that runs on devbox.
GPU stages (Demucs separation + Whisper lyrics) are offloaded to ephemeral
vast.ai instances per job; that client lives under ``karaoke.vast`` once
implemented.
"""
from __future__ import annotations

__version__ = "0.1.0"


def main() -> int:
    """Entry point stub for the ``karaoke`` console script.

    Real CLI/server entry points land as the API and worker are scaffolded.
    """
    print(f"karaoke {__version__} — scaffold; nothing wired up yet.")
    return 0
