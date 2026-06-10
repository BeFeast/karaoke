"""Command line entry points for karaoke."""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from karaoke import __version__
from karaoke.config import Settings
from karaoke.pipeline import LocalPipelineConfig, run_local_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="karaoke")
    parser.add_argument("--version", action="version", version=f"karaoke {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run one CPU-local karaoke job")
    run.add_argument("url", help="yt-dlp-supported source URL")
    run.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="job artifact directory (default: ./artifacts/local-<timestamp>)",
    )
    run.add_argument("--device", default="cpu-local", help="must be cpu-local for this CLI")
    run.add_argument("--demucs-model", default="htdemucs")
    run.add_argument("--whisper-model", default="small")

    args = parser.parse_args(argv)
    if args.command != "run":
        parser.print_help()
        return 0

    output_dir = args.output_dir or _default_output_dir()
    config = LocalPipelineConfig(
        output_dir=output_dir,
        device=args.device,
        demucs_model=args.demucs_model,
        whisper_model=args.whisper_model,
    )
    settings = Settings(device_mode=args.device, artifact_root=str(output_dir))

    def heartbeat(stage: str, message: str) -> None:
        print(f"[{stage}] {message}", flush=True)

    result = run_local_pipeline(args.url, config, settings, heartbeat=heartbeat)
    print(result.job_root)
    return 0


def _default_output_dir() -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("artifacts") / f"local-{stamp}"
