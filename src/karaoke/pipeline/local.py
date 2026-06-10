"""CPU-local end-to-end karaoke pipeline.

This is the M0 development runner: coordinator-side yt-dlp + ffmpeg, local
Demucs two-stem separation, local faster-whisper transcription, and the PRD
artifact layout. Production API jobs continue to use ``karaoke.worker.pipeline``
and ephemeral GPU runtimes.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Protocol

from karaoke.config import Settings
from karaoke.titles import derive_metadata
from karaoke.worker.pipeline import (
    PipelineError,
    _download_audio,
    _to_wav,
    _wav_to_mp3,
    _ytdlp_metadata,
)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class StageCallback(Protocol):
    def __call__(self, stage: str, message: str) -> None: ...


@dataclasses.dataclass(frozen=True)
class LocalPipelineConfig:
    """Configuration for one CPU-local CLI run."""

    output_dir: Path
    device: str = "cpu-local"
    demucs_model: str = "htdemucs"
    whisper_model: str = "small"
    ytdlp_timeout_s: int = 900
    ffmpeg_timeout_s: int = 600
    demucs_timeout_s: int = 3600
    whisper_timeout_s: int = 3600
    cookies_blob: str | None = None

    def validate(self) -> None:
        if self.device != "cpu-local":
            raise PipelineError("local pipeline only supports device=cpu-local")


@dataclasses.dataclass(frozen=True)
class LocalPipelineResult:
    """Paths produced by a CPU-local run."""

    job_root: Path
    source_mp3: Path
    vocals_stem_mp3: Path
    no_vocals_stem_mp3: Path
    karaoke_mp3: Path
    vocals_mp3: Path
    lyrics_txt: Path
    metadata_json: Path
    worker_log: Path


def run_local_pipeline(
    source_url: str,
    config: LocalPipelineConfig,
    settings: Settings | None = None,
    *,
    heartbeat: StageCallback | None = None,
) -> LocalPipelineResult:
    """Run one CPU-local karaoke job and return the artifact paths.

    Heavy tools are invoked at runtime as local prerequisites:
    ``yt-dlp``, ``ffmpeg``, ``demucs``, and the ``faster_whisper`` Python
    package. Tests monkeypatch the stage helpers so CI never downloads media or
    imports model packages.
    """
    config.validate()
    settings = settings or Settings(device_mode=config.device)
    job_root = config.output_dir
    source_dir = job_root / "source"
    stems_dir = job_root / "stems" / config.demucs_model / "source"
    exports_dir = job_root / "exports"
    lyrics_dir = job_root / "lyrics"
    logs_dir = job_root / "logs"
    work_dir = job_root / "work"
    for directory in (source_dir, stems_dir, exports_dir, lyrics_dir, logs_dir, work_dir):
        directory.mkdir(parents=True, exist_ok=True)

    worker_log = logs_dir / "worker.log"
    logger = _job_logger(worker_log)
    started = dt.datetime.now(dt.UTC)
    timings: dict[str, float] = {}

    def stage(name: str, message: str) -> None:
        logger.info("%s: %s", name, message)
        if heartbeat is not None:
            heartbeat(name, message)

    try:
        stage("metadata", "reading source metadata")
        meta = _time_stage(
            timings,
            "metadata",
            lambda: _ytdlp_metadata(source_url, settings, cookies_blob=config.cookies_blob),
        )
        source_meta = derive_metadata(meta)

        stage("download", "downloading source audio with yt-dlp")
        raw_audio = work_dir / "source.audio"
        _time_stage(
            timings,
            "download",
            lambda: _download_audio(
                source_url, raw_audio, settings, cookies_blob=config.cookies_blob
            ),
        )

        stage("normalize", "normalizing source audio with ffmpeg")
        source_mp3 = source_dir / "source.mp3"
        source_wav = source_dir / "source.wav"
        _time_stage(timings, "source_mp3", lambda: _wav_to_mp3(raw_audio, source_mp3))
        _time_stage(timings, "source_wav", lambda: _to_wav(source_mp3, source_wav))

        stage("separate", f"running Demucs {config.demucs_model} on CPU")
        wav_vocals, wav_no_vocals = _time_stage(
            timings,
            "demucs",
            lambda: _run_demucs_cpu(source_wav, job_root / "stems", config),
        )
        vocals_stem_mp3 = stems_dir / "vocals.mp3"
        no_vocals_stem_mp3 = stems_dir / "no_vocals.mp3"
        _time_stage(timings, "vocals_stem_mp3", lambda: _wav_to_mp3(wav_vocals, vocals_stem_mp3))
        _time_stage(
            timings,
            "no_vocals_stem_mp3",
            lambda: _wav_to_mp3(wav_no_vocals, no_vocals_stem_mp3),
        )

        stage("transcribe", f"transcribing vocals with faster-whisper {config.whisper_model}")
        lyrics_text = _time_stage(
            timings,
            "whisper",
            lambda: _transcribe_vocals(wav_vocals, config),
        )
        lyrics_txt = lyrics_dir / "lyrics.txt"
        lyrics_txt.write_text(lyrics_text.strip() + "\n", encoding="utf-8")

        stage("export", "writing browser playback MP3 exports")
        karaoke_mp3 = exports_dir / "karaoke.mp3"
        vocals_mp3 = exports_dir / "vocals.mp3"
        shutil.copyfile(no_vocals_stem_mp3, karaoke_mp3)
        shutil.copyfile(vocals_stem_mp3, vocals_mp3)

        metadata = {
            "source_url": source_url,
            "title": (meta.get("title") or "").strip() or None,
            "artist": source_meta.get("artist"),
            "track": source_meta.get("track"),
            "album": source_meta.get("album"),
            "duration": source_meta.get("duration"),
            "device": config.device,
            "demucs_model": config.demucs_model,
            "whisper_model": config.whisper_model,
            "started_at": started.isoformat(),
            "completed_at": dt.datetime.now(dt.UTC).isoformat(),
            "timings_s": {k: round(v, 3) for k, v in timings.items()},
            "artifacts": {
                "source": "source/source.mp3",
                "vocals_stem": "stems/htdemucs/source/vocals.mp3",
                "no_vocals_stem": "stems/htdemucs/source/no_vocals.mp3",
                "karaoke": "exports/karaoke.mp3",
                "vocals": "exports/vocals.mp3",
                "lyrics": "lyrics/lyrics.txt",
                "worker_log": "logs/worker.log",
            },
        }
        metadata_json = job_root / "metadata.json"
        metadata_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        stage("completed", f"artifacts ready in {job_root}")

        return LocalPipelineResult(
            job_root=job_root,
            source_mp3=source_mp3,
            vocals_stem_mp3=vocals_stem_mp3,
            no_vocals_stem_mp3=no_vocals_stem_mp3,
            karaoke_mp3=karaoke_mp3,
            vocals_mp3=vocals_mp3,
            lyrics_txt=lyrics_txt,
            metadata_json=metadata_json,
            worker_log=worker_log,
        )
    except Exception:
        logger.exception("pipeline failed")
        raise


def _run_demucs_cpu(source_wav: Path, stems_root: Path, config: LocalPipelineConfig) -> tuple[Path, Path]:
    """Run the prototype remove-vocals wrapper logic through Python.

    The original shell wrapper drove Demucs with the ``htdemucs`` model and
    ``--two-stems vocals`` so Demucs emits ``vocals`` and ``no_vocals`` stems.
    Keeping it in Python gives tests direct access to the command and avoids a
    second shell entrypoint.
    """
    cmd = [
        "demucs",
        "--two-stems",
        "vocals",
        "-n",
        config.demucs_model,
        "--device",
        "cpu",
        "-o",
        str(stems_root),
        str(source_wav),
    ]
    _run_cmd(cmd, timeout=config.demucs_timeout_s)
    stem_dir = stems_root / config.demucs_model / source_wav.stem
    vocals = stem_dir / "vocals.wav"
    no_vocals = stem_dir / "no_vocals.wav"
    if not vocals.is_file() or not no_vocals.is_file():
        raise PipelineError(f"Demucs did not produce expected stems under {stem_dir}")
    return vocals, no_vocals


def _transcribe_vocals(vocals_wav: Path, config: LocalPipelineConfig) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depends on local dev machine
        raise PipelineError(
            "faster-whisper is required for CPU-local transcription; install it "
            "in the local environment before running `karaoke run`"
        ) from exc

    model = WhisperModel(config.whisper_model, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(vocals_wav), vad_filter=True)
    lines = [seg.text.strip() for seg in segments if seg.text and seg.text.strip()]
    return "\n".join(lines) or ""


def _run_cmd(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise PipelineError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )
    return proc


def _time_stage(timings: dict[str, float], name: str, func):
    start = time.monotonic()
    try:
        return func()
    finally:
        timings[name] = time.monotonic() - start


def _job_logger(worker_log: Path) -> logging.Logger:
    logger = logging.getLogger(f"karaoke.pipeline.local.{worker_log}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(worker_log, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(handler)
    return logger
