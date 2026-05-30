"""RunPod Serverless handler for the karaoke GPU worker.

Mirrors the GPU stages of `docker/vast/server.py` but exposes them as a
JSON-in / JSON-out Serverless handler instead of an HTTP server.

Input (``event["input"]``)::

    {
      "audio_base64": "<base64-encoded WAV bytes>",
      "mode": "demucs" | "whisper" | "both"   # default "both"
    }

Output (returned to RunPod as JSON)::

    mode == "demucs":
      {"vocals_b64": str, "instrumental_b64": str,
       "gpu_model": str, "elapsed_s": float}

    mode == "whisper":
      {"lyrics_txt": str, "lyrics_json": dict,
       "gpu_model": str, "elapsed_s": float}

    mode == "both":
      {"vocals_b64": str, "instrumental_b64": str,
       "lyrics_txt": str, "lyrics_json": dict,
       "gpu_model": str, "elapsed_s": float}

Unknown modes raise ``ValueError`` so RunPod marks the job FAILED rather
than silently returning a wrong shape.
"""
from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger("karaoke-runpod")
logging.basicConfig(
    level=os.environ.get("KARAOKE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# Single global lock — one job at a time on a single-GPU worker.
_GPU_LOCK = threading.Lock()

# Lazy-loaded faster-whisper model; mirrors server.py's _get_whisper pattern.
_WHISPER_MODEL: Any = None
_WHISPER_LOCK = threading.Lock()


def _gpu_available() -> bool:
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:  # pragma: no cover
        return False


def _gpu_model_name() -> str:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:  # pragma: no cover
        pass
    return "cpu"


def _get_whisper():
    """Lazy-load the faster-whisper large-v3-turbo model on the GPU."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            from faster_whisper import WhisperModel  # type: ignore

            device = "cuda" if _gpu_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            LOG.info(
                "loading faster-whisper large-v3-turbo on %s/%s", device, compute_type
            )
            _WHISPER_MODEL = WhisperModel(
                "large-v3-turbo", device=device, compute_type=compute_type
            )
    return _WHISPER_MODEL


def _run_demucs(input_wav: Path, out_dir: Path) -> tuple[Path, Path]:
    """Run Demucs htdemucs and return (vocals.wav, no_vocals.wav)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "/opt/karaoke-venv/bin/python",
        "-m",
        "demucs.separate",
        "-n",
        "htdemucs",
        "--two-stems",
        "vocals",
        "-o",
        str(out_dir),
        str(input_wav),
    ]
    LOG.info("demucs cmd: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"demucs failed rc={proc.returncode}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    stem = input_wav.stem
    base = out_dir / "htdemucs" / stem
    vocals = base / "vocals.wav"
    instrumental = base / "no_vocals.wav"
    if not vocals.exists() or not instrumental.exists():
        cand_v = list(out_dir.rglob("vocals.wav"))
        cand_i = list(out_dir.rglob("no_vocals.wav"))
        if cand_v and cand_i:
            vocals, instrumental = cand_v[0], cand_i[0]
        else:
            raise RuntimeError(
                f"demucs output missing under {out_dir}; found: "
                f"{[p.as_posix() for p in out_dir.rglob('*.wav')]}"
            )
    return vocals, instrumental


def _transcribe(wav_path: Path) -> tuple[str, dict[str, Any]]:
    """Run faster-whisper on `wav_path`. Returns (lyrics_txt, lyrics_json)."""
    model = _get_whisper()
    segments_iter, info = model.transcribe(
        str(wav_path),
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
    lyrics_json = {
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "segments": segments,
    }
    lyrics_txt = "\n".join(line for line in text_lines if line)
    return lyrics_txt, lyrics_json


def _put_file(path: Path, presigned_url: str, content_type: str) -> None:
    """PUT a file to a presigned URL via urllib (no boto3 dep)."""
    import urllib.request

    body = path.read_bytes()
    req = urllib.request.Request(
        presigned_url,
        data=body,
        method="PUT",
        headers={"Content-Type": content_type, "Content-Length": str(len(body))},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        if resp.status not in (200, 201, 204):
            raise RuntimeError(f"presigned PUT returned HTTP {resp.status}")


def _b64_file(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def handler(event: dict[str, Any]) -> dict[str, Any]:
    """RunPod Serverless entrypoint.

    Raises on bad input or stage failure so the platform marks the job
    FAILED instead of returning a half-filled response.
    """
    started = time.monotonic()
    job_input = event.get("input") or {}
    if not isinstance(job_input, dict):
        raise ValueError("event.input must be an object")

    audio_b64 = job_input.get("audio_base64")
    audio_url = job_input.get("audio_url")
    if not audio_url and not audio_b64:
        raise ValueError("audio_url or audio_base64 is required")
    if audio_url and not isinstance(audio_url, str):
        raise ValueError("audio_url must be a string")
    if audio_b64 and not isinstance(audio_b64, str):
        raise ValueError("audio_base64 must be a string")

    mode = job_input.get("mode", "both")
    if mode not in ("demucs", "whisper", "both"):
        raise ValueError("unknown mode")

    if audio_url:
        import urllib.request
        try:
            with urllib.request.urlopen(audio_url, timeout=120) as resp:
                audio_bytes = resp.read()
        except Exception as exc:
            raise ValueError(f"audio_url fetch failed: {exc}") from exc
        if not audio_bytes:
            raise ValueError("audio_url returned empty body")
    else:
        try:
            audio_bytes = base64.b64decode(audio_b64, validate=True)
        except Exception as exc:
            raise ValueError(f"audio_base64 is not valid base64: {exc}") from exc
        if not audio_bytes:
            raise ValueError("audio_base64 decoded to empty bytes")

    # Optional presigned PUT URLs — when present, the handler streams
    # the wav stems into them and returns only URLs (RunPod /run output
    # has a 10MB cap; raw stems are well over that).
    vocals_put_url = job_input.get("vocals_put_url")
    instrumental_put_url = job_input.get("instrumental_put_url")
    use_put = bool(vocals_put_url and instrumental_put_url)

    result: dict[str, Any] = {}

    with _GPU_LOCK, tempfile.TemporaryDirectory(prefix="kar-rp-") as tmp:
        tmp_path = Path(tmp)
        in_wav = tmp_path / "input.wav"
        in_wav.write_bytes(audio_bytes)

        vocals_path: Path | None = None
        instrumental_path: Path | None = None

        if mode in ("demucs", "both"):
            out_dir = tmp_path / "out"
            vocals_path, instrumental_path = _run_demucs(in_wav, out_dir)
            if use_put:
                _put_file(vocals_path, vocals_put_url, "audio/wav")
                _put_file(instrumental_path, instrumental_put_url, "audio/wav")
                result["vocals_uploaded"] = True
                result["instrumental_uploaded"] = True
            else:
                result["vocals_b64"] = _b64_file(vocals_path)
                result["instrumental_b64"] = _b64_file(instrumental_path)

        if mode in ("whisper", "both"):
            target = vocals_path if mode == "both" else in_wav
            assert target is not None
            lyrics_txt, lyrics_json = _transcribe(target)
            result["lyrics_txt"] = lyrics_txt
            result["lyrics_json"] = lyrics_json

    result["gpu_model"] = _gpu_model_name()
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    return result


if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler})
