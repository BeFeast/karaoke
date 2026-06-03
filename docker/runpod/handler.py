"""RunPod Serverless handler for the karaoke GPU worker.

Mirrors the GPU stages of `docker/vast/server.py` but exposes them as a
JSON-in / JSON-out Serverless handler instead of an HTTP server.

Input (``event["input"]``)::

    {
      "audio_base64": "<base64-encoded WAV bytes>",
      "mode": "demucs" | "whisper" | "both",  # default "both"
      "align_text": "<plain lyrics to force-align>",  # optional (#55)
      "align_lang": "eng"                              # optional ISO-639-3
    }

When ``align_text`` is present (and Demucs ran, i.e. ``mode != "whisper"``),
the handler force-aligns that text against the separated vocal stem with
``ctc-forced-aligner`` (MMS-300m) and returns a synthesized line-level LRC in
``aligned_lrc``. This is purely additive: an old handler that ignores
``align_text`` still works, and a coordinator that does not send it sees
unchanged behavior. Alignment failures NEVER fail the job — the handler logs
and omits ``aligned_lrc`` so the coordinator falls back (plain text / Whisper).

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

    + optional (any mode that ran Demucs, when ``align_text`` was supplied
      and alignment succeeded):
      {"aligned_lrc": str, "aligned_lang": str}

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

# Lazy-loaded ctc-forced-aligner model/tokenizer (#55). Same lazy-load shape as
# the Whisper model so the aligner weights only load when a job actually needs
# force-alignment (align_text supplied). MMS-300m is multilingual + light.
_ALIGN_MODEL: Any = None
_ALIGN_TOKENIZER: Any = None
_ALIGN_LOCK = threading.Lock()
# MMS-300m forced aligner — pre-cached in the image (see Dockerfile).
_ALIGN_MODEL_ID = "MahmoudAshraf/mms-300m-1130-forced-aligner"


def _gpu_used_mb() -> int | None:
    """Return total GPU memory currently used by device 0, if nvidia-smi exists."""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
                "-i",
                "0",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    first = (proc.stdout or "").splitlines()[0:1]
    if not first:
        return None
    try:
        return int(first[0].strip())
    except ValueError:
        return None


class _StageGpuMeter:
    """Sample wall time and device-level VRAM for one sequential GPU stage."""

    def __init__(self, stage: str, *, interval_s: float = 0.1) -> None:
        self.stage = stage
        self.interval_s = interval_s
        self.started = 0.0
        self.elapsed_s = 0.0
        self.start_vram_mb: int | None = None
        self.peak_vram_mb: int | None = None
        self.end_vram_mb: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _StageGpuMeter:
        self.started = time.monotonic()
        self.start_vram_mb = _gpu_used_mb()
        self.peak_vram_mb = self.start_vram_mb
        self._thread = threading.Thread(
            target=self._sample_loop,
            name=f"gpu-meter-{self.stage}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self.end_vram_mb = _gpu_used_mb()
        self._record(self.end_vram_mb)
        self.elapsed_s = time.monotonic() - self.started

    def _record(self, value: int | None) -> None:
        if value is None:
            return
        if self.peak_vram_mb is None or value > self.peak_vram_mb:
            self.peak_vram_mb = value

    def _sample_loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._record(_gpu_used_mb())

    def snapshot(self) -> dict[str, float | int | None]:
        return {
            "elapsed_s": round(self.elapsed_s, 3),
            "start_vram_mb": self.start_vram_mb,
            "peak_vram_mb": self.peak_vram_mb,
            "end_vram_mb": self.end_vram_mb,
        }


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


def _get_aligner():
    """Lazy-load the ctc-forced-aligner MMS-300m model + tokenizer.

    Mirrors ``_get_whisper``: load once, reuse across jobs on the worker.
    Returns ``(model, tokenizer, device, dtype)``.
    """
    global _ALIGN_MODEL, _ALIGN_TOKENIZER
    if _ALIGN_MODEL is not None and _ALIGN_TOKENIZER is not None:
        return _ALIGN_MODEL, _ALIGN_TOKENIZER, _align_device(), _align_dtype()
    with _ALIGN_LOCK:
        if _ALIGN_MODEL is None or _ALIGN_TOKENIZER is None:
            from ctc_forced_aligner import (  # type: ignore
                load_alignment_model,
            )

            device = _align_device()
            dtype = _align_dtype()
            LOG.info("loading ctc-forced-aligner %s on %s/%s", _ALIGN_MODEL_ID, device, dtype)
            _ALIGN_MODEL, _ALIGN_TOKENIZER = load_alignment_model(
                device,
                model_path=_ALIGN_MODEL_ID,
                dtype=dtype,
            )
    return _ALIGN_MODEL, _ALIGN_TOKENIZER, _align_device(), _align_dtype()


def _align_device() -> str:
    return "cuda" if _gpu_available() else "cpu"


def _align_dtype():
    import torch  # type: ignore

    return torch.float16 if _gpu_available() else torch.float32


def _fmt_lrc_timestamp(seconds: float) -> str:
    """Format ``seconds`` as an LRC ``[mm:ss.xx]`` timestamp tag."""
    if seconds < 0:
        seconds = 0.0
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"[{minutes:02d}:{rem:05.2f}]"


def _force_align_to_lrc(
    vocals_wav: Path, text: str, language: str
) -> str:
    """Force-align ``text`` against the ``vocals_wav`` stem → line-level LRC.

    Uses ctc-forced-aligner (MMS-300m). Produces one ``[mm:ss.xx]line`` per
    source lyric line, timed at the start of the first aligned token of that
    line. Raises on any failure; the caller swallows it so alignment is never
    fatal to the job.
    """
    from ctc_forced_aligner import (  # type: ignore
        generate_emissions,
        get_alignments,
        get_spans,
        load_audio,
        postprocess_results,
        preprocess_text,
    )

    model, tokenizer, device, dtype = _get_aligner()

    audio_waveform = load_audio(str(vocals_wav), model.dtype, model.device)
    emissions, stride = generate_emissions(model, audio_waveform, batch_size=1)

    tokens_starred, text_starred = preprocess_text(
        text,
        romanize=True,
        language=language,
    )
    segments, scores, blank_token = get_alignments(
        emissions,
        tokens_starred,
        tokenizer,
    )
    spans = get_spans(tokens_starred, segments, blank_token)
    word_timestamps = postprocess_results(text_starred, spans, stride, scores)

    return _word_timestamps_to_lrc(word_timestamps, text)


def _word_timestamps_to_lrc(
    word_timestamps: list[dict[str, Any]], text: str
) -> str:
    """Build a line-level LRC from ctc-forced-aligner word timestamps.

    ``word_timestamps`` is a list of ``{"text", "start", "end", ...}`` in the
    same order as the words in ``text``. We walk the original lines, consuming
    one timestamp per word, and tag each non-empty line with the start time of
    its first word.
    """
    lines = [ln for ln in text.splitlines()]
    out: list[str] = []
    wi = 0
    n = len(word_timestamps)
    for line in lines:
        words = line.split()
        if not words:
            continue
        # The start of this line = start of its first aligned word.
        if wi < n:
            start = float(word_timestamps[wi].get("start") or 0.0)
        else:
            start = float(word_timestamps[-1].get("end") or 0.0) if n else 0.0
        out.append(f"{_fmt_lrc_timestamp(start)}{line.strip()}")
        wi += len(words)
    return "\n".join(out)


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

    # Optional force-alignment of supplied plain lyrics against the vocal stem
    # (#55). Only meaningful when Demucs runs (we need the vocal stem). Absent →
    # behave exactly as before.
    align_text = job_input.get("align_text")
    if align_text is not None and not isinstance(align_text, str):
        raise ValueError("align_text must be a string")
    align_lang = job_input.get("align_lang") or "eng"  # ISO-639-3
    if not isinstance(align_lang, str):
        raise ValueError("align_lang must be a string")
    want_align = bool(align_text and align_text.strip()) and mode in ("demucs", "both")

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
    metrics: dict[str, Any] = {
        "stages": {},
        "isolation": {
            "demucs": "subprocess",
            "whisper": "in-process-lazy-model",
        },
    }

    with _GPU_LOCK, tempfile.TemporaryDirectory(prefix="kar-rp-") as tmp:
        tmp_path = Path(tmp)
        in_wav = tmp_path / "input.wav"
        in_wav.write_bytes(audio_bytes)

        vocals_path: Path | None = None
        instrumental_path: Path | None = None

        if mode in ("demucs", "both"):
            out_dir = tmp_path / "out"
            with _StageGpuMeter("demucs") as meter:
                vocals_path, instrumental_path = _run_demucs(in_wav, out_dir)
            metrics["stages"]["demucs"] = meter.snapshot()
            # Force-align supplied plain lyrics against the vocal stem (#55).
            # Best-effort: a failure here must NOT fail the job — the
            # coordinator falls back to the plain text / Whisper transcript.
            if want_align:
                try:
                    lrc = _force_align_to_lrc(vocals_path, align_text, align_lang)
                    if lrc.strip():
                        result["aligned_lrc"] = lrc
                        result["aligned_lang"] = align_lang
                    else:
                        LOG.warning("force-align produced empty LRC; omitting")
                except Exception as exc:  # noqa: BLE001 — never fatal
                    LOG.warning("force-align failed (%s); omitting aligned_lrc", exc)
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
            with _StageGpuMeter("whisper") as meter:
                lyrics_txt, lyrics_json = _transcribe(target)
            metrics["stages"]["whisper"] = meter.snapshot()
            result["lyrics_txt"] = lyrics_txt
            result["lyrics_json"] = lyrics_json

    result["gpu_model"] = _gpu_model_name()
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    metrics["total_elapsed_s"] = result["elapsed_s"]
    result["metrics"] = metrics
    return result


if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler})
