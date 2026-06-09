"""Single-invocation GPU worker entrypoint for the vast.ai fallback image.

The coordinator downloads and normalizes audio on devbox, then invokes this
container with one local audio file and an output directory. This script runs
Demucs first, transcribes the separated vocals with faster-whisper, and writes
the stable artifact names expected by the coordinator:

    vocals.mp3
    no_vocals.mp3
    lyrics.txt
    lyrics.json
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

LOG = logging.getLogger("karaoke-vast-entrypoint")
logging.basicConfig(
    level="INFO",
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    LOG.info("running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, check=False, text=True)


def _require_gpu() -> str:
    """Fail fast on CPU-only hosts before loading large models."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        raise RuntimeError("nvidia-smi is not present; this image requires an NVIDIA GPU")

    smi = _run([nvidia_smi, "--query-gpu=name,driver_version", "--format=csv,noheader"])
    if smi.returncode != 0:
        raise RuntimeError(
            "nvidia-smi failed; run the container with NVIDIA GPU access\n"
            f"stdout:\n{smi.stdout}\nstderr:\n{smi.stderr}"
        )

    import torch  # type: ignore

    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() is false; refusing CPU fallback")
    return smi.stdout.strip() or torch.cuda.get_device_name(0)


def _run_demucs(input_audio: Path, work_dir: Path) -> tuple[Path, Path]:
    demucs_out = work_dir / "demucs"
    cmd = [
        sys.executable,
        "-m",
        "demucs.separate",
        "-n",
        "htdemucs",
        "--two-stems",
        "vocals",
        "-o",
        str(demucs_out),
        str(input_audio),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(
            f"demucs failed rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

    base = demucs_out / "htdemucs" / input_audio.stem
    vocals = base / "vocals.wav"
    no_vocals = base / "no_vocals.wav"
    if vocals.exists() and no_vocals.exists():
        return vocals, no_vocals

    vocals_candidates = list(demucs_out.rglob("vocals.wav"))
    no_vocals_candidates = list(demucs_out.rglob("no_vocals.wav"))
    if vocals_candidates and no_vocals_candidates:
        return vocals_candidates[0], no_vocals_candidates[0]

    found = [p.as_posix() for p in demucs_out.rglob("*.wav")]
    raise RuntimeError(f"demucs output missing vocals/no_vocals under {demucs_out}; found={found}")


def _encode_mp3(input_wav: Path, output_mp3: Path) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_wav),
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(output_mp3),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg mp3 encode failed rc={proc.returncode}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def _transcribe(vocals_wav: Path) -> tuple[str, dict[str, Any]]:
    from faster_whisper import WhisperModel  # type: ignore

    model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    segments_iter, info = model.transcribe(
        str(vocals_wav),
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )
    segments: list[dict[str, Any]] = []
    text_lines: list[str] = []
    for seg in segments_iter:
        seg_dict: dict[str, Any] = {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
        }
        if seg.words:
            seg_dict["words"] = [
                {
                    "start": w.start,
                    "end": w.end,
                    "word": w.word,
                    "probability": w.probability,
                }
                for w in seg.words
            ]
        segments.append(seg_dict)
        if seg.text:
            text_lines.append(seg.text.strip())

    return "\n".join(line for line in text_lines if line), {
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "segments": segments,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run karaoke GPU stages once.")
    parser.add_argument("input_audio", type=Path, help="Input WAV/MP3 produced by the coordinator")
    parser.add_argument(
        "output_dir",
        type=Path,
        help="Directory where vocals.mp3, no_vocals.mp3, lyrics.txt, lyrics.json are written",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    input_audio = args.input_audio.resolve()
    output_dir = args.output_dir.resolve()
    if not input_audio.is_file():
        raise FileNotFoundError(f"input audio does not exist: {input_audio}")

    gpu = _require_gpu()
    LOG.info("GPU ready: %s", gpu)

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="karaoke-vast-") as tmp:
        work_dir = Path(tmp)
        vocals_wav, no_vocals_wav = _run_demucs(input_audio, work_dir)
        _encode_mp3(vocals_wav, output_dir / "vocals.mp3")
        _encode_mp3(no_vocals_wav, output_dir / "no_vocals.mp3")

        lyrics_txt, lyrics_json = _transcribe(vocals_wav)
        (output_dir / "lyrics.txt").write_text(lyrics_txt, encoding="utf-8")
        (output_dir / "lyrics.json").write_text(
            json.dumps(lyrics_json, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    LOG.info("wrote karaoke GPU artifacts to %s", output_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        LOG.exception("karaoke vast entrypoint failed: %s", exc)
        raise SystemExit(1) from exc
