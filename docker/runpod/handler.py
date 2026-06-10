"""RunPod Serverless handler for the karaoke GPU worker.

Mirrors the GPU stages of `docker/vast/server.py` but exposes them as a
JSON-in / JSON-out Serverless handler instead of an HTTP server.

Stem separation runs **BS-Roformer** via ``audio-separator`` (replacing the
old Demucs ``htdemucs``); lyrics transcription runs ``faster-whisper``. The
handler's JSON input/output contract is unchanged — the ``"demucs"`` mode
name is kept verbatim for backward compatibility with the coordinator even
though the underlying engine is now BS-Roformer.

Input (``event["input"]``)::

    {
      "audio_base64": "<base64-encoded WAV bytes>",
      "mode": "demucs" | "whisper" | "both",  # default "both"
      "align_text": "<plain lyrics to force-align>",  # optional (#55)
      "align_lang": "eng"                              # optional ISO-639-3
    }

When ``align_text`` is present (and separation ran, i.e. ``mode != "whisper"``),
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

    + optional (any mode that ran separation, when ``align_text`` was supplied
      and alignment succeeded):
      {"aligned_lrc": str, "aligned_lang": str}

Unknown modes raise ``ValueError`` so RunPod marks the job FAILED rather
than silently returning a wrong shape.
"""
from __future__ import annotations

import base64
import logging
import os
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

# Lazy-loaded audio-separator BS-Roformer model. Same lazy-load shape as the
# Whisper model so the (large) separation weights only load when a job actually
# runs separation — a whisper-only job never constructs the Separator.
_SEPARATOR: Any = None
_SEP_LOCK = threading.Lock()
# BS-Roformer checkpoint, pre-cached into the image (see Dockerfile). The model
# dir MUST match the Dockerfile pre-cache path so cold-start reuses the baked-in
# weights instead of re-downloading from the audio-separator model host.
_SEP_MODEL_DIR = os.environ.get("KARAOKE_SEP_MODEL_DIR", "/opt/audio-separator-models")
_SEP_MODEL = os.environ.get(
    "KARAOKE_SEP_MODEL", "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
)

# Lazy-loaded ctc-forced-aligner model/tokenizer (#55). Same lazy-load shape as
# the Whisper model so the aligner weights only load when a job actually needs
# force-alignment (align_text supplied). MMS-300m is multilingual + light.
_ALIGN_MODEL: Any = None
_ALIGN_TOKENIZER: Any = None
_ALIGN_LOCK = threading.Lock()
# MMS-300m forced aligner — pre-cached in the image (see Dockerfile).
_ALIGN_MODEL_ID = "MahmoudAshraf/mms-300m-1130-forced-aligner"


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


def _get_separator():
    """Lazy-load the audio-separator Separator with the BS-Roformer model.

    Mirrors ``_get_whisper``: construct once, reuse across jobs on the worker.
    The (large) checkpoint loads on first use; a whisper-only job never triggers
    this. audio-separator picks CUDA automatically when ``torch.cuda`` is
    available and falls back to CPU otherwise (used by the build smoke test).
    """
    global _SEPARATOR
    if _SEPARATOR is not None:
        return _SEPARATOR
    with _SEP_LOCK:
        if _SEPARATOR is None:
            from audio_separator.separator import Separator  # type: ignore

            log_level = getattr(
                logging,
                os.environ.get("KARAOKE_LOG_LEVEL", "INFO").upper(),
                logging.INFO,
            )
            LOG.info(
                "loading audio-separator model %s (dir=%s)",
                _SEP_MODEL,
                _SEP_MODEL_DIR,
            )
            sep = Separator(
                model_file_dir=_SEP_MODEL_DIR,
                output_format="WAV",
                log_level=log_level,
            )
            sep.load_model(model_filename=_SEP_MODEL)
            _SEPARATOR = sep
    return _SEPARATOR


def _pick_stem(paths: list[Path], token: str) -> Path | None:
    """Return the first path whose filename contains ``token`` (case-insensitive)."""
    for p in paths:
        if token in p.name.lower():
            return p
    return None


def _run_separation(input_wav: Path, out_dir: Path) -> tuple[Path, Path]:
    """Separate ``input_wav`` into (vocals.wav, instrumental.wav) via BS-Roformer.

    Uses audio-separator's BS-Roformer model (replacing Demucs htdemucs). The
    return contract is unchanged from the old ``_run_demucs``: a
    ``(vocals_path, instrumental_path)`` pair of WAV files, where
    ``instrumental_path`` is the non-vocal stem (the old ``no_vocals.wav`` role).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sep = _get_separator()
    # One job at a time (the handler holds _GPU_LOCK), so mutating output_dir on
    # the shared Separator is safe. audio-separator captures output_dir into the
    # loaded architecture instance (``model_instance``) at load_model() time, so
    # reassigning ``sep.output_dir`` alone does NOT redirect output — the
    # model_instance must be updated too, or stems land in the process CWD.
    sep.output_dir = str(out_dir)
    _model_instance = getattr(sep, "model_instance", None)
    if _model_instance is not None and hasattr(_model_instance, "output_dir"):
        _model_instance.output_dir = str(out_dir)
    # Force deterministic stem filenames so the mapping below is unambiguous.
    outputs = sep.separate(
        str(input_wav),
        custom_output_names={"Vocals": "vocals", "Instrumental": "instrumental"},
    )
    paths: list[Path] = []
    for name in outputs or []:
        p = Path(name)
        if not p.is_absolute():
            p = out_dir / p
        paths.append(p)
    # Map stems robustly: prefer the names audio-separator returned, fall back to
    # globbing the output dir if the returned shape is unexpected.
    vocals = _pick_stem(paths, "vocal")
    instrumental = _pick_stem(paths, "instrument")
    if vocals is None or instrumental is None:
        wavs = sorted(out_dir.rglob("*.wav"))
        vocals = vocals or _pick_stem(wavs, "vocal")
        instrumental = instrumental or _pick_stem(wavs, "instrument")
    if vocals is None or instrumental is None:
        raise RuntimeError(
            f"audio-separator output missing vocals/instrumental under {out_dir}; "
            f"separate() returned {[p.as_posix() for p in paths]}; "
            f"found {[p.as_posix() for p in out_dir.rglob('*.wav')]}"
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
    # (#55). Only meaningful when separation runs (we need the vocal stem).
    # Absent → behave exactly as before.
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

    with _GPU_LOCK, tempfile.TemporaryDirectory(prefix="kar-rp-") as tmp:
        tmp_path = Path(tmp)
        in_wav = tmp_path / "input.wav"
        in_wav.write_bytes(audio_bytes)

        vocals_path: Path | None = None
        instrumental_path: Path | None = None

        if mode in ("demucs", "both"):
            out_dir = tmp_path / "out"
            vocals_path, instrumental_path = _run_separation(in_wav, out_dir)
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
            lyrics_txt, lyrics_json = _transcribe(target)
            result["lyrics_txt"] = lyrics_txt
            result["lyrics_json"] = lyrics_json

    result["gpu_model"] = _gpu_model_name()
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    return result


if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler})
