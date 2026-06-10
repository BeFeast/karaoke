"""karaoke — URL → isolated vocals + instrumental playback + lyrics.

This package is the coordinator (FastAPI API + worker) that runs on devbox.
GPU stages (Demucs separation + Whisper lyrics) are offloaded to ephemeral
vast.ai instances per job; that client lives under ``karaoke.vast`` once
implemented.
"""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Deploy-truth: track the installed package version (driven by pyproject.toml)
    # so /health reports the released tag without any source edit.
    __version__ = _pkg_version("karaoke")
except PackageNotFoundError:  # pragma: no cover - editable/uninstalled fallback
    __version__ = "0.0.0+unknown"


def main() -> int:
    """Console script entrypoint."""
    from karaoke.cli import main as cli_main

    if "pytest" in sys.modules:
        return cli_main([])
    return cli_main()
