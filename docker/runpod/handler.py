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
``aligned_lrc``. Lines whose mean per-frame alignment log-prob falls below
``KARAOKE_ALIGN_MIN_AVG_LOGPROB`` (default -5.0) are dropped from the LRC
(#149): text lines absent from the audio get squeezed by monotonic CTC into
low-confidence spans rather than timed sensibly. This is purely additive: an
old handler that ignores ``align_text`` still works, and a coordinator that
does not send it sees unchanged behavior. Alignment failures NEVER fail the
job — the handler logs and omits ``aligned_lrc`` so the coordinator falls back
(plain text / Whisper).

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
      {"aligned_lrc": str, "aligned_lang": str,
       "aligned_raw_lrc": str, "aligned_diagnostics": dict}

Raw evidence includes every supplied lyric line before score/VAD filters.
For mode "both", ASR runs first and bounded no-VAD vocal-crop retries may
recover problematic lines only when independent text and timings agree.
Accepted absolute-time words are also included in lyrics_json.retry_segments.

Unknown modes raise ``ValueError`` so RunPod marks the job FAILED rather
than silently returning a wrong shape.
"""
from __future__ import annotations

import base64
import importlib
import logging
import math
import os
import re
import subprocess
import sys
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
_WHISPER_MODEL_NAME = "large-v3-turbo"

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
# Per-line confidence floor for the synthesized LRC (#149). ctc-forced-aligner
# reports each word's summed frame log-probabilities as ``score``; a line's
# mean per-frame log-prob below this drops the line from the LRC. Monotonic CTC
# alignment squeezes lyric lines that are absent from the audio (canonical
# verses cut from a video edit) into tiny, very-low-score spans — confident
# lines on separated vocals average far above this. Conservative on purpose:
# only clearly-garbage lines go; tune via env without an image rebuild.
_ALIGN_MIN_AVG_LOGPROB = float(os.environ.get("KARAOKE_ALIGN_MIN_AVG_LOGPROB", "-5.0"))
# Minimum fraction of an aligned line's window that must overlap Silero-VAD
# voiced regions (#247). Lines below it were placed on instrumental audio.
_ALIGN_MIN_VOICED_OVERLAP = float(
    os.environ.get("KARAOKE_ALIGN_MIN_VOICED_OVERLAP", "0.35")
)


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
                "loading faster-whisper %s on %s/%s",
                _WHISPER_MODEL_NAME,
                device,
                compute_type,
            )
            _WHISPER_MODEL = WhisperModel(
                _WHISPER_MODEL_NAME, device=device, compute_type=compute_type
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


# A multi-segment language detection at or above this probability is trusted
# over the coordinator's title-script hint (#260): the hint exists to break
# LOW-confidence misdetection ties, not to overrule clear audio evidence.
_LANG_DETECT_TRUST_P = 0.6


def _transcribe(
    wav_path: Path, language: str | None = None, *, vad_filter: bool = True,
    deadline: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run faster-whisper on `wav_path`. Returns (lyrics_txt, lyrics_json).

    Tuned for separated vocal stems carrying repetitive sung text (#218). The
    key fix is ``condition_on_previous_text=False``: on a chorus repeated N times
    the decoder otherwise degenerates and swallows whole repeats into one
    mega-segment — classic Whisper repetition-collapse, matching a 203 s track
    collapsing to a single 41→195 s segment on job
    ``lSsLw_MlOPb6r3V4KUW_2ulW9pY-ir82``. Alongside it we:

    - keep ``vad_filter=True`` but widen ``vad_parameters``: sung phrases have
      long intra-line gaps, so the default VAD over-merges / drops voiced
      regions on a BS-Roformer stem (breaths, separation artifacts).
      ``min_silence_duration_ms=500`` + ``speech_pad_ms=400`` keeps phrase
      boundaries without swallowing quiet vocals.
    - add the faster-whisper default temperature-fallback ladder plus
      music-tuned hallucination guards (``compression_ratio_threshold=2.4``,
      ``log_prob_threshold=-1.0``, ``no_speech_threshold=0.6``,
      ``hallucination_silence_threshold=2.0``) so a bad greedy decode retries at
      a higher temperature instead of hallucinating a mega-segment.

    ``beam_size=5`` and ``word_timestamps=True`` are kept unchanged.

    Language (#260, r10): ``language`` (ISO-639-1) is a coordinator-supplied
    HINT, not an unconditional override — single-window auto-detect misread a
    Hebrew stem as ``en`` at p=0.456 (an English adlib opened the track) and
    decoded the whole song as transliterated-Latin gibberish, but the hint is
    derived from the video TITLE script, and a translated-lyrics upload
    (native-script title over foreign-language audio) would be equally wrong
    in the other direction. So: a multi-segment ``detect_language`` probe runs
    first, and a CONFIDENT detection (p >= 0.6) wins over the hint; the hint
    decides only the low-confidence case. Without a hint,
    ``language_detection_segments=4`` averages detection over several windows
    so one anglophone intro can no longer lock the file (the earlier ``ru``
    p=0.86 evidence job stays correct either way).
    """
    model = _get_whisper()
    if language:
        try:
            detected, prob, _ = model.detect_language(
                str(wav_path),
                vad_filter=vad_filter,
                language_detection_segments=4,
            )
        except Exception:  # detection probe is best-effort; keep the hint
            detected, prob = None, 0.0
        if detected and prob >= _LANG_DETECT_TRUST_P:
            language = detected
    segments_iter, info = model.transcribe(
        str(wav_path),
        language=language,
        language_detection_segments=4,
        beam_size=5,
        word_timestamps=True,
        condition_on_previous_text=False,
        vad_filter=vad_filter,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=400,
        ),
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        hallucination_silence_threshold=2.0,
    )
    segments: list[dict[str, Any]] = []
    text_lines: list[str] = []
    for seg in segments_iter:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("crop transcription retry deadline exceeded")
        seg_dict: dict[str, Any] = {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
            "avg_logprob": getattr(seg, "avg_logprob", None),
            "no_speech_prob": getattr(seg, "no_speech_prob", None),
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


def _fmt_lrc_time(seconds: float) -> str:
    """Format ``seconds`` as a bare ``mm:ss.xx`` LRC time (2-digit centiseconds)."""
    if seconds < 0:
        seconds = 0.0
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{minutes:02d}:{rem:05.2f}"


def _fmt_lrc_timestamp(seconds: float) -> str:
    """Format ``seconds`` as an LRC ``[mm:ss.xx]`` line-start tag."""
    return f"[{_fmt_lrc_time(seconds)}]"


def _fmt_lrc_word_tag(seconds: float) -> str:
    """Format ``seconds`` as an Enhanced-LRC ``<mm:ss.xx>`` inline word tag.

    Identical time shape to :func:`_fmt_lrc_timestamp`; only the delimiter
    differs (``<>`` for inline word tags vs ``[]`` for the line-start tag), per
    the shared Enhanced LRC contract (#218).
    """
    return f"<{_fmt_lrc_time(seconds)}>"


def _voiced_regions(wav_path: Path) -> list[tuple[float, float]] | None:
    """Voiced (speech/singing) regions of ``wav_path`` via Silero VAD (#247).

    Reuses the VAD that ships with faster-whisper (already in this image).
    Returns ``[(start_s, end_s), ...]`` or ``None`` when VAD fails — callers
    must treat ``None`` as "no veto" (never drop lines on a VAD failure).
    """
    try:
        from faster_whisper.audio import decode_audio  # type: ignore
        from faster_whisper.vad import (  # type: ignore
            VadOptions,
            get_speech_timestamps,
        )

        audio = decode_audio(str(wav_path), sampling_rate=16000)
        # Chunk to 30 s windows: Silero's streaming state degrades over long
        # music files (measured: whole-file VAD missed everything past ~60 s
        # on a 203 s vocal stem; chunked found every sung region).
        sr = 16000
        chunk = 30 * sr
        regions: list[tuple[float, float]] = []
        opts = VadOptions(min_silence_duration_ms=500, speech_pad_ms=200)
        for off in range(0, len(audio), chunk):
            for ts in get_speech_timestamps(audio[off : off + chunk], opts):
                regions.append(
                    ((off + ts["start"]) / sr, (off + ts["end"]) / sr)
                )
        # Merge near-adjacent regions (chunk boundaries split them).
        merged: list[list[float]] = []
        for a, b in sorted(regions):
            if merged and a - merged[-1][1] < 0.5:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return [(a, b) for a, b in merged]
    except Exception as exc:  # noqa: BLE001 — VAD is advisory, never fatal
        LOG.warning("VAD failed (%s); skipping voiced-region veto", exc)
        return None


def _voiced_overlap(start: float, end: float, regions: list[tuple[float, float]]) -> float:
    """Fraction of ``[start, end]`` covered by ``regions`` (0.0–1.0)."""
    span = max(end - start, 1e-6)
    covered = 0.0
    for r0, r1 in regions:
        covered += max(0.0, min(end, r1) - max(start, r0))
    return covered / span


def _force_align_to_lrc(
    vocals_wav: Path, text: str, language: str,
    diagnostics: dict[str, Any] | None = None,
) -> tuple[str, list[float | None]]:
    """Force-align ``text`` against the ``vocals_wav`` stem → Enhanced LRC.

    Returns ``(lrc_body, line_scores)`` — see :func:`_word_timestamps_to_lrc`.

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

    return _word_timestamps_to_lrc(
        word_timestamps, text, stride=stride, voiced=_voiced_regions(vocals_wav),
        diagnostics=diagnostics,
    )


def _word_timestamps_to_lrc(
    word_timestamps: list[dict[str, Any]],
    text: str,
    stride: float | None = None,
    voiced: list[tuple[float, float]] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> tuple[str, list[float | None]]:
    """Build an Enhanced LRC from ctc-forced-aligner word timestamps.

    ``word_timestamps`` is a list of ``{"text", "start", "end", "score", ...}``
    in the same order as the words in ``text``. We walk the original lines,
    consuming one timestamp per word, and emit an Enhanced LRC line carrying
    word-level timing for the SPA's word highlight (#218)::

        [mm:ss.xx]<mm:ss.xx>word <mm:ss.xx>word … <mm:ss.xx>

    The leading ``[mm:ss.xx]`` line tag is the line start (first aligned word's
    ``start``) — unchanged from the pre-word-tag output. Each word is then
    prefixed with an inline ``<mm:ss.xx>`` start tag (its aligner ``start``),
    and a single trailing ``<mm:ss.xx>`` after the last word carries the line's
    sung end (last aligned word's ``end``). Consumers derive each word's
    duration from the next word's start, else the trailing end tag. A line with
    no aligned words at all stays plain (no ``<>`` tags) — a valid *mixed* file
    per the shared Enhanced LRC contract; consumers fall back to a linear line
    wipe for it.

    When ``stride`` (milliseconds per emission frame) is known, each line also
    gets a confidence check (#149): lines whose mean per-frame log-prob (summed
    word ``score`` over summed frame count) falls below
    ``_ALIGN_MIN_AVG_LOGPROB`` are dropped from the LRC — they are almost
    always canonical-text lines absent from this audio edit, which monotonic
    CTC alignment squeezed somewhere they don't belong. The confidence math is
    unchanged; only the kept lines now carry word tags. Missing scores or an
    unknown stride skip the check (never drop), so older aligner output shapes
    keep the pre-#149 behavior.

    Returns ``(lrc_body, line_scores)`` — one entry per emitted LRC line, the
    line's mean per-frame log-prob (the #149 gate metric) or ``None`` when the
    stride/scores were unavailable. The coordinator uses the distribution of
    these scores to drop *relatively* bad lines (#244): a cut/shortened
    performance forces monotonic CTC to cram the absent text somewhere, and
    those crammed lines score visibly worse than the job's own median.
    """
    lines = [ln for ln in text.splitlines()]
    out: list[str] = []
    out_scores: list[float | None] = []
    wi = 0
    n = len(word_timestamps)
    # The aligner's preprocess/romanize step can merge or split tokens, so the
    # timestamp count may drift from the original text's whitespace words. The
    # positional word↔timestamp mapping below is only trustworthy when the
    # counts match exactly — after a drifted token every tag would mark the
    # wrong word. On mismatch keep line-start timing (pre-r7 behavior) and
    # emit no word tags at all.
    total_words = sum(len(ln.split()) for ln in lines)
    emit_word_tags = n == total_words
    if not emit_word_tags:
        LOG.info(
            "aligner token drift (%d timestamps vs %d words): "
            "emitting line-level LRC without word tags",
            n,
            total_words,
        )
    evidence: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines):
        words = line.split()
        if not words:
            continue
        line_ts = word_timestamps[wi : wi + len(words)]
        wi += len(words)
        score = _line_avg_logprob(line_ts, stride) if line_ts else None
        if not line_ts:
            start = float(word_timestamps[-1].get("end") or 0.0) if n else 0.0
            raw_line = f"{_fmt_lrc_timestamp(start)}{line.strip()}"
            end = start
        else:
            start = float(line_ts[0].get("start") or 0.0)
            end = float(line_ts[-1].get("end") or start)
            raw_line = (
                _enhanced_lrc_line(words, line_ts) if emit_word_tags
                else f"{_fmt_lrc_timestamp(start)}{line.strip()}"
            )
        reasons: list[str] = []
        if _line_below_confidence(line_ts, stride):
            reasons.append("low_alignment_score")
        if voiced is not None and line_ts:
            total_span = sum(max(0.0, float(w.get("end") or 0) -
                                 float(w.get("start") or 0)) for w in line_ts)
            covered = sum(
                _voiced_overlap(float(w.get("start") or 0),
                                float(w.get("end") or 0), voiced)
                * max(0.0, float(w.get("end") or 0) - float(w.get("start") or 0))
                for w in line_ts
            )
            if total_span > 0 and covered / total_span < _ALIGN_MIN_VOICED_OVERLAP:
                reasons.append("low_voiced_overlap")
        # Keep the legacy rendering contract, but never conceal token drift
        # from the coordinator's quality gate or use drifted rows as anchors.
        timing_issues = [] if emit_word_tags else ["token_count_mismatch"]
        if not line_ts:
            timing_issues.append("no_word_timestamps")
        elif any(
            not math.isfinite(float(w.get("start") or 0))
            or not math.isfinite(float(w.get("end") or 0))
            or float(w.get("end") or 0) <= float(w.get("start") or 0)
            for w in line_ts
        ):
            timing_issues.append("invalid_word_timestamps")
        if line_ts and emit_word_tags:
            starts = [float(w.get("start") or 0) for w in line_ts]
            ends = [float(w.get("end") or 0) for w in line_ts]
            if any(b - a > 8 for a, b in zip(starts, ends, strict=True)):
                timing_issues.append("long_word_span")
            if any(starts[i] - ends[i - 1] > 4 for i in range(1, len(starts))):
                timing_issues.append("internal_word_gap")
            if len(starts) > 1:
                chars = len(" ".join(words[:-1]))
                if chars / max(starts[-1] - starts[0], 0.05) > 30:
                    timing_issues.append("implausible_word_pace")
        evidence.append({
            "line_index": line_index, "text": line.strip(), "raw_lrc": raw_line,
            "start": start, "end": end, "score": score, "kept": not reasons,
            "rejection_reasons": reasons, "timing_issues": timing_issues,
            "words": [dict(w) for w in line_ts],
        })
        if not reasons:
            out.append(raw_line)
            out_scores.append(score)
    numeric = sorted(row["score"] for row in evidence if row["score"] is not None)
    if len(numeric) >= 8:
        median = numeric[len(numeric) // 2]
        mad = sorted(abs(score - median) for score in numeric)[len(numeric) // 2]
        cutoff = median - 2 * max(mad, 0.25)
        for row in evidence:
            if row["score"] is not None and row["score"] < cutoff:
                row["timing_issues"].append("relative_score_outlier")
    if diagnostics is not None:
        diagnostics.update(schema_version=1, lines=evidence, retries=[])
        diagnostics["raw_lrc"] = "\n".join(row["raw_lrc"] for row in evidence)
    return "\n".join(out), out_scores


def _enhanced_lrc_line(words: list[str], line_ts: list[dict[str, Any]]) -> str:
    """Render one Enhanced-LRC line: ``[start]<start>word … <end>``.

    ``line_ts`` holds one aligner word-timestamp per leading word; any words
    beyond the available timestamps (rare intra-line tokenization drift) ride
    untagged so no timing is invented. The line-start ``[..]`` tag is the first
    aligned word's ``start``; the single trailing ``<..>`` tag is the last
    aligned word's ``end`` (the line's sung end).
    """
    tokens: list[str] = []
    for word, ts in zip(words, line_ts, strict=False):
        w_start = float(ts.get("start") or 0.0)
        tokens.append(f"{_fmt_lrc_word_tag(w_start)}{word}")
    # Words with no aligned timestamp (intra-line drift) ride untagged.
    tokens.extend(words[len(line_ts):])
    line_start = float(line_ts[0].get("start") or 0.0)
    line_end = float(line_ts[-1].get("end") or 0.0)
    tokens.append(_fmt_lrc_word_tag(line_end))
    return f"{_fmt_lrc_timestamp(line_start)}{' '.join(tokens)}"


def _line_avg_logprob(
    line_ts: list[dict[str, Any]], stride: float | None
) -> float | None:
    """A line's mean per-frame alignment log-prob, or ``None`` if unscoreable.

    ctc-forced-aligner's per-word ``score`` is the *sum* of frame log-probs
    over the word's span, and ``start``/``end`` are ``frame_index * stride/1000``
    — so ``(end - start) * 1000 / stride`` recovers the frame count and
    ``sum(scores) / sum(frames)`` is the line's mean per-frame log-prob.
    Tolerant by design: unknown stride, a missing/non-numeric ``score`` on any
    word, or a degenerate frame count return ``None``.
    """
    if not stride or stride <= 0:
        return None
    total_score = 0.0
    total_frames = 0.0
    for w in line_ts:
        try:
            total_score += float(w["score"])
            start = float(w.get("start") or 0.0)
            end = float(w.get("end") or 0.0)
        except (KeyError, TypeError, ValueError):
            return None
        # A word occupies at least one emission frame; clamp degenerate spans.
        total_frames += max((end - start) * 1000.0 / stride, 1.0)
    if total_frames <= 0:
        return None
    return total_score / total_frames


def _line_below_confidence(
    line_ts: list[dict[str, Any]], stride: float | None
) -> bool:
    """True when a line's mean per-frame log-prob is below the drop threshold
    (#149). ``None`` scores keep the line — the filter only drops lines it
    positively scored.
    """
    avg = _line_avg_logprob(line_ts, stride)
    return avg is not None and avg < _ALIGN_MIN_AVG_LOGPROB


# Retry limits are deliberately fixed: these are short recovery probes inside
# the existing GPU job, never another separation or a whole-track ASR pass.
_RETRY_MAX_CROPS = 3
_RETRY_MAX_CROP_SECONDS = 25.0
_RETRY_MAX_AUDIO_SECONDS = 60.0


def _norm_word(word: str) -> str:
    return re.sub(r"[\W_]", "", word.casefold())


def _asr_words(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(w) for seg in transcript.get("segments", [])
            for w in seg.get("words", []) if _norm_word(str(w.get("word", "")))]


def _matching_word_runs(text: str, words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    expected = [_norm_word(w) for w in text.split() if _norm_word(w)]
    actual = [_norm_word(str(w.get("word", ""))) for w in words]
    if not expected:
        return []
    return [words[i:i + len(expected)] for i in range(len(words) - len(expected) + 1)
            if actual[i:i + len(expected)] == expected]


def _plausible_words(words: list[dict[str, Any]], start: float, end: float) -> bool:
    """Reject degenerate, overlapping, low-confidence or out-of-crop ASR words."""
    previous_end = start
    probabilities: list[float] = []
    for word in words:
        try:
            a, b = float(word["start"]), float(word["end"])
            probability = float(word["probability"])
        except (KeyError, TypeError, ValueError):
            return False
        if not all(math.isfinite(v) for v in (a, b, probability)):
            return False
        if a < start or b > end or b - a < 0.04 or b - a > 8 or a < previous_end - 0.02:
            return False
        if probabilities and a - previous_end > 4:
            return False
        if probability < 0.3 or probability > 1:
            return False
        probabilities.append(probability)
        previous_end = b
    return bool(probabilities) and sum(probabilities) / len(probabilities) >= 0.65


def _corroborated_anchor(row: dict[str, Any], words: list[dict[str, Any]]) -> tuple[float, float] | None:
    if not row["kept"] or row.get("timing_issues"):
        return None
    for run in _matching_word_runs(row["text"], words):
        if not _plausible_words(run, 0, float("inf")):
            continue
        a, b = float(run[0]["start"]), float(run[-1]["end"])
        if abs(a - row["start"]) <= 2 and abs(b - row["end"]) <= 2:
            return a, b
    return None


def _transcribe_crop(
    vocals_wav: Path, start: float, end: float, language: str | None, deadline: float,
) -> dict[str, Any]:
    """Decode one vocal crop without VAD or a supplied lyric prompt.

    ffmpeg is already part of this image. The deadline is cooperative during
    model decoding (checked between segments); RunPod/coordinator retain the
    hard job timeout. No additional GPU job is created.
    """
    with tempfile.TemporaryDirectory(prefix="kar-retry-") as tmp:
        crop = Path(tmp) / "vocals.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(start), "-i", str(vocals_wav), "-t", str(end - start),
             "-ac", "1", "-ar", "16000", str(crop)],
            check=True, capture_output=True, timeout=max(0.1, min(10, deadline - time.monotonic())),
        )
        if time.monotonic() >= deadline:
            raise TimeoutError("crop extraction exhausted retry deadline")
        _, transcript = _transcribe(crop, language, vad_filter=False, deadline=deadline)
        return transcript


def _retry_problem_lines(
    vocals_wav: Path, diagnostics: dict[str, Any], transcript: dict[str, Any],
    original_lrc: str, original_scores: list[float | None], *, deadline: float,
) -> tuple[str, list[float | None]]:
    """Recover only independently decoded words between two trusted anchors.

    Canonical text is used solely for comparing the result. It is never fed
    into crop ASR, so a forced-input echo cannot resurrect a missing verse.
    Failure preserves the original output and remains visible in diagnostics.
    """
    rows = diagnostics["lines"]
    full_words = _asr_words(transcript)
    anchors = {i: anchor for i, row in enumerate(rows)
               if (anchor := _corroborated_anchor(row, full_words)) is not None}
    attempts = 0
    audio_seconds = 0.0
    recovered: dict[int, str] = {}
    recovered_spans: list[tuple[float, float]] = []
    diagnostics["retry_budget"] = {
        "max_crops": _RETRY_MAX_CROPS, "max_crop_seconds": _RETRY_MAX_CROP_SECONDS,
        "max_audio_seconds": _RETRY_MAX_AUDIO_SECONDS, "max_wall_seconds": 60,
    }
    for index, row in enumerate(rows):
        if row["kept"] and not row.get("timing_issues"):
            continue
        record: dict[str, Any] = {"line_index": row["line_index"], "outcome": "skipped"}
        diagnostics["retries"].append(record)
        left = max((i for i in anchors if i < index), default=None)
        right = min((i for i in anchors if i > index), default=None)
        if left is None or right is None:
            record["reason"] = "no_two_corroborated_anchors"
            continue
        lower = max(anchors[left][1], rows[left]["end"])
        upper = min(anchors[right][0], rows[right]["start"])
        start, end = max(0.0, lower - 0.25), upper + 0.25
        record.update(start=start, end=end, left_anchor=rows[left]["line_index"],
                      right_anchor=rows[right]["line_index"])
        duration = end - start
        if upper <= lower or duration > _RETRY_MAX_CROP_SECONDS:
            record["reason"] = "anchor_gap_out_of_bounds"
            continue
        if attempts >= _RETRY_MAX_CROPS or audio_seconds + duration > _RETRY_MAX_AUDIO_SECONDS:
            record["reason"] = "retry_audio_budget_exhausted"
            continue
        if time.monotonic() + 10 >= deadline:
            record["reason"] = "retry_time_budget_exhausted"
            continue
        attempts += 1
        audio_seconds += duration
        record["outcome"] = "rejected"
        try:
            crop_asr = _transcribe_crop(vocals_wav, start, end,
                                        transcript.get("language"), deadline)
            record["transcript"] = crop_asr
            # Segment guards are needed in addition to word probabilities:
            # high token confidence alone does not exclude silence hallucination.
            safe_segments = []
            for seg in crop_asr.get("segments", []):
                logprob, silence = seg.get("avg_logprob"), seg.get("no_speech_prob")
                if (isinstance(logprob, (int, float)) and math.isfinite(logprob)
                        and logprob >= -1 and isinstance(silence, (int, float))
                        and math.isfinite(silence) and 0 <= silence < 0.6):
                    safe_segments.append(seg)
                else:
                    # Do not splice across a low-confidence segment to create
                    # an apparently exact text run that was never decoded.
                    safe_segments.append({"words": [{"word": "__untrusted_segment__"}]})
            # Preserve ALL trusted observations from the crop, not only
            # the matching reference slice. Otherwise extra sung words
            # disappear from the completeness audit and can falsely produce
            # a checked result. Evidence is independent of repair acceptance.
            evidence_segments = []
            for segment in safe_segments:
                if "avg_logprob" not in segment:  # unsafe-segment barrier
                    continue
                evidence_words = [
                    dict(word, start=float(word["start"]) + start,
                         end=float(word["end"]) + start)
                    for word in segment.get("words", [])
                ]
                evidence_segments.append(dict(
                    segment,
                    start=float(segment.get("start", 0)) + start,
                    end=float(segment.get("end", duration)) + start,
                    words=evidence_words, source="independent_crop_asr",
                    line_index=row["line_index"],
                ))
            record["evidence_segments"] = evidence_segments
            if evidence_segments:
                transcript.setdefault("retry_segments", []).extend(evidence_segments)
            crop_words = _asr_words({"segments": safe_segments})
            candidates = _matching_word_runs(row["text"], crop_words)
            record["reason"] = "text_not_corroborated"
            for candidate in candidates:
                if len(candidate) != len(row["text"].split()):
                    record["reason"] = "source_word_token_mismatch"
                    continue
                if not _plausible_words(candidate, lower - start, upper - start):
                    record["reason"] = "implausible_word_timing_or_confidence"
                    continue
                shifted = [dict(w, start=float(w["start"]) + start,
                                end=float(w["end"]) + start) for w in candidate]
                a, b = shifted[0]["start"], shifted[-1]["end"]
                if any(max(a, x) < min(b, y) for x, y in recovered_spans):
                    record["reason"] = "retry_word_span_already_used"
                    continue
                # Later source rows cannot precede a previously recovered row.
                if recovered_spans and a < recovered_spans[-1][1]:
                    record["reason"] = "retry_word_order_conflict"
                    continue
                recovered[index] = _enhanced_lrc_line(row["text"].split(), shifted)
                recovered_spans.append((a, b))
                record.update(outcome="accepted", reason="independent_crop_asr_match",
                              words=shifted)
                break
        except Exception as exc:  # bounded recovery must never fail the base job
            record.update(outcome="failed", reason=type(exc).__name__)
    diagnostics["retry_budget"].update(crops_used=attempts, audio_seconds_used=audio_seconds)
    if not recovered:
        return original_lrc, original_scores
    # Rebuild in source order. Raw evidence and original rejection reasons are
    # intentionally immutable; successful recoveries live in retries[].
    lrc: list[str] = []
    scores: list[float | None] = []
    for index, row in enumerate(rows):
        if index in recovered:
            lrc.append(recovered[index])
            scores.append(None)  # ASR probability is not an alignment log-prob
        elif row["kept"]:
            lrc.append(row["raw_lrc"])
            scores.append(row["score"])
    return "\n".join(lrc), scores


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

    # Optional Whisper decode-language hint (#260, r10), ISO-639-1 ("he").
    # Absent/empty -> multi-segment auto-detect (see _transcribe).
    whisper_lang = job_input.get("whisper_lang") or None
    if whisper_lang is not None:
        if not isinstance(whisper_lang, str):
            raise ValueError("whisper_lang must be a string")
        whisper_lang = whisper_lang.strip().lower() or None
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
            lyrics_txt, lyrics_json = _transcribe(target, language=whisper_lang)
            result["lyrics_txt"] = lyrics_txt
            result["lyrics_json"] = lyrics_json

        if want_align and vocals_path is not None:
            diagnostics: dict[str, Any] = {"schema_version": 1, "lines": [], "retries": []}
            try:
                lrc, line_scores = _force_align_to_lrc(
                    vocals_path, align_text, align_lang, diagnostics=diagnostics
                )
                if mode == "both" and diagnostics.get("lines"):
                    # Reserve time for returning outputs; never extend the
                    # coordinator's existing wall/cost ceiling for retries.
                    budget = max(0.0, min(float(job_input.get("job_budget_s", 1200)), 1200))
                    deadline = min(time.monotonic() + 60, started + budget - 30)
                    try:
                        lrc, line_scores = _retry_problem_lines(
                            vocals_path, diagnostics, result["lyrics_json"],
                            lrc, line_scores, deadline=deadline,
                        )
                    except Exception as exc:  # retries cannot destroy valid baseline output
                        diagnostics["retry_error"] = type(exc).__name__
                        LOG.warning("alignment retries failed (%s); preserving baseline", exc)
                if lrc.strip():
                    result.update(aligned_lrc=lrc, aligned_lang=align_lang,
                                  aligned_line_scores=line_scores)
            except Exception as exc:
                diagnostics["alignment_error"] = type(exc).__name__
                LOG.warning("force-align failed (%s); omitting aligned_lrc", exc)
            raw = diagnostics.pop("raw_lrc", "")
            if raw:
                result["aligned_raw_lrc"] = raw
            result["aligned_diagnostics"] = diagnostics

    result["gpu_model"] = _gpu_model_name()
    result["elapsed_s"] = round(time.monotonic() - started, 3)
    return result


# --------------------------------------------------------------------------
# CPU-only image selfcheck (#279; prevents a repeat of #263).
#
# CI runs this right after pushing a GPU image tag (`docker run <tag>
# --selfcheck`, or KARAOKE_SELFCHECK=1): it eagerly imports every heavy
# dependency the worker otherwise imports lazily at job time, and verifies the
# baked model artifacts exist with plausible sizes — so a build that lost a
# model-cache layer or shipped a broken venv fails the workflow instead of
# shipping an unbootable image that only dies on the first real job.
# It MUST NOT require a GPU: torch is imported but torch.cuda is never
# initialized (only static build metadata is read).

# Modules the worker imports lazily inside functions; a broken install of any
# of them would otherwise surface only on the first real job.
_SELFCHECK_IMPORTS = (
    "torch",
    "torchaudio",
    "audio_separator.separator",
    "faster_whisper",
    "ctc_forced_aligner",
    "runpod",
)

# Size floors for the baked model artifacts — generous fractions of the real
# sizes (BS-Roformer ckpt ~639 MB, whisper model.bin ~1.6 GB, MMS-300m weights
# ~1.2 GB) so a truncated or placeholder file fails while a future smaller
# model revision still passes.
_SELFCHECK_MIN_SEP_BYTES = 100 * 1024 * 1024
_SELFCHECK_MIN_WHISPER_BYTES = 500 * 1024 * 1024
_SELFCHECK_MIN_ALIGN_BYTES = 100 * 1024 * 1024


def _hf_hub_dir() -> Path:
    """The huggingface_hub cache dir models download into (``$HF_HOME/hub``)."""
    hf_home = Path(os.environ.get("HF_HOME", "~/.cache/huggingface")).expanduser()
    return hf_home / "hub"


def _hf_repo_dir(repo_id: str) -> Path:
    """The HF hub cache dir for one repo (``models--org--name``)."""
    return _hf_hub_dir() / ("models--" + repo_id.replace("/", "--"))


def _selfcheck_whisper_dirs() -> list[Path]:
    """HF cache dirs that may hold the baked faster-whisper model.

    Prefer the exact repo faster-whisper resolves ``_WHISPER_MODEL_NAME`` to
    (private mapping — best-effort); fall back to globbing the hub for any
    ``*whisper*`` model dir, which is unambiguous inside the image (exactly one
    whisper model is baked).
    """
    try:
        from faster_whisper.utils import _MODELS  # type: ignore

        repo = _MODELS.get(_WHISPER_MODEL_NAME)
    except Exception:
        repo = None
    if repo:
        return [_hf_repo_dir(repo)]
    return sorted(_hf_hub_dir().glob("models--*whisper*"))


def _size_or_zero(path: Path) -> int:
    """``path``'s size in bytes; 0 when missing (e.g. a broken HF symlink)."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _selfcheck_file(
    label: str, path: Path, min_bytes: int, failures: list[str]
) -> None:
    """Record a failure unless ``path`` exists with at least ``min_bytes``."""
    try:
        size = path.stat().st_size
    except OSError:
        failures.append(f"{label}: missing at {path}")
        return
    if size < min_bytes:
        failures.append(
            f"{label}: {path} is {size} bytes (< {min_bytes} required)"
        )
    else:
        print(f"selfcheck: {label} ok ({size / 1e6:.0f} MB) at {path}")


def _selfcheck() -> int:
    """CPU-only boot-verify of the built image. Returns a process exit code."""
    failures: list[str] = []

    for mod_name in _SELFCHECK_IMPORTS:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the check
            failures.append(f"import {mod_name} failed: {exc!r}")
        else:
            version = getattr(mod, "__version__", "?")
            print(f"selfcheck: import {mod_name} ok ({version})")
            if mod_name == "torch":
                # Static build metadata only — must not initialize CUDA.
                print(f"selfcheck: torch cuda build: {mod.version.cuda}")

    # BS-Roformer checkpoint pre-cached by the Dockerfile into the dir the
    # handler loads from at job time.
    _selfcheck_file(
        "separator checkpoint",
        Path(_SEP_MODEL_DIR) / _SEP_MODEL,
        _SELFCHECK_MIN_SEP_BYTES,
        failures,
    )

    # faster-whisper model weights in the HF hub cache.
    whisper_dirs = _selfcheck_whisper_dirs()
    whisper_bins = [p for d in whisper_dirs for p in sorted(d.rglob("model.bin"))]
    if not whisper_bins:
        failures.append(
            "whisper model.bin: not found under "
            f"{[str(d) for d in whisper_dirs] or [str(_hf_hub_dir())]}"
        )
    else:
        best = max(whisper_bins, key=_size_or_zero)
        _selfcheck_file(
            "whisper model.bin", best, _SELFCHECK_MIN_WHISPER_BYTES, failures
        )

    # ctc-forced-aligner MMS-300m weights in the HF hub cache.
    align_dir = _hf_repo_dir(_ALIGN_MODEL_ID)
    align_weights = [
        p
        for pattern in ("*.safetensors", "pytorch_model.bin")
        for p in sorted(align_dir.rglob(pattern))
    ]
    if not align_weights:
        failures.append(f"aligner weights: none found under {align_dir}")
    else:
        best = max(align_weights, key=_size_or_zero)
        _selfcheck_file(
            "aligner weights", best, _SELFCHECK_MIN_ALIGN_BYTES, failures
        )

    if failures:
        for failure in failures:
            print(f"selfcheck: FAIL {failure}")
        print(f"selfcheck: FAILED ({len(failures)} problem(s))")
        return 1
    print("selfcheck: OK — imports and baked model files verified (CPU-only)")
    return 0


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:] or os.environ.get("KARAOKE_SELFCHECK") == "1":
        sys.exit(_selfcheck())

    import runpod

    runpod.serverless.start({"handler": handler})
