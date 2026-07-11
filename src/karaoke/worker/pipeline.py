"""Real karaoke worker — async orchestrator.

Lifecycle (mirrors the mock's status transitions so the existing ``/ws`` poller
in ``routes.py`` keeps working):

  queued
    → downloading   yt-dlp the source_url → audio; ffmpeg → working wav
                    (coordinator-side; runs on devbox's residential IP).
    → separating    provision a vast.ai GPU instance, POST /demucs
                    → vocals.wav + instrumental.wav.
    → transcribing  reuse the SAME instance window, POST /whisper on vocals.wav
                    → lyrics.txt + lyrics.json.
    → completed     ffmpeg-encode instrumental→karaoke.mp3, vocals→vocals.mp3;
                    write lyrics.txt + metadata.json under
                    {artifact_root}/{job_token}/...; insert Artifact rows; set
                    Job completed/100/completed_at/vast_instance_id/vast_cost.

On any exception the Job is marked failed (with ``error``) and the vast instance
is guaranteed destroyed (``VastClient.run`` owns a ``finally`` teardown).

yt-dlp / ffmpeg are invoked as subprocesses assumed present in the worker image;
they are NOT imported, so unit tests never need them installed.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import functools
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from karaoke.api import ws as ws_events
from karaoke.db.models import Artifact, Job, JobStatus
from karaoke.titles import derive_metadata
from karaoke.uploads import UPLOAD_PREFIX, upload_display_name
from karaoke.worker import job_cookies
from karaoke.worker.lyrics import (
    LRC_TIMESTAMP_RE,
    SOURCE_FORCED_ALIGNED,
    SOURCE_INSTRUMENTAL,
    SOURCE_LRCLIB_PLAIN,
    SOURCE_LRCLIB_SYNCED,
    SOURCE_WHISPER_ASR,
    SOURCE_WHISPER_ASR_SYNCED,
    LyricsResult,
    LyricsSource,
    aligned_text_agreement,
    lrc_to_plain,
    merge_lrclib_word_tags,
    repair_aligned_lrc,
    whisper_segments_to_lrc,
)

_log = logging.getLogger(__name__)

# Module-level LRCLIB client so its in-process cache survives across jobs.
_LYRICS_SOURCE = LyricsSource()

# Word-merge coverage gate (#237): when the force-aligner fit fewer than this
# fraction of a curated record's lines (with at least this many candidate
# lines, so the ratio is meaningful), the record's TEXT does not match the
# audio — a different performance/edit that slipped the ±5 s duration gate —
# and the whole record is rejected in favor of the Whisper ASR floor.
_ALIGN_COVERAGE_MIN_RATIO = 0.3
_ALIGN_COVERAGE_MIN_LINES = 10

# yt-dlp player-client chain (mirrors scribe's downloader; android_vr is the
# token-free workhorse, web clients need the EJS/deno JS solver in the image).
_PLAYER_CLIENTS = "mweb,web_safari,android_vr,web_embedded"

# YouTube soft-ban / bot-check fingerprints. When yt-dlp prints one of these we
# back off and retry rather than failing immediately — the devbox residential
# IP gets transiently flagged under load and recovers after a short cooldown.
# Matched case-insensitively against the combined stdout+stderr of the failed
# invocation (issue #68).
_BOT_CHECK_RE = re.compile(
    r"sign in to confirm (?:you|that you)(?:'| a)?re not a bot"
    r"|confirm you'?re not a bot"
    r"|not a bot"
    r"|this video is not available|HTTP Error 429|too many requests",
    re.IGNORECASE,
)

# Backoff schedule (seconds) for bot-check retries. Exponential with a small,
# bounded number of attempts so a flagged IP gets a brief cooldown without the
# job hanging for many minutes. The download itself already has a 900s timeout
# per attempt; these sleeps sit between attempts.
_BOT_CHECK_BACKOFF_S = (15.0, 45.0, 120.0)


def _is_bot_check(message: str) -> bool:
    """True when a yt-dlp failure message looks like a YouTube bot-check / rate
    limit, i.e. the kind of soft-ban that a short cooldown + retry can clear."""
    return bool(_BOT_CHECK_RE.search(message or ""))


class PipelineError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# coordinator-side subprocess steps (download + normalize + encode)
# ---------------------------------------------------------------------------
def _run(cmd: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise PipelineError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )
    return proc


def _pot_base_url(settings) -> str:
    """Normalized bgutil PO-token provider base URL (no trailing slash), or ""
    when unset. ``settings`` may be ``None`` (legacy callers / tests)."""
    raw = getattr(settings, "pot_provider_base_url", "") if settings else ""
    return str(raw or "").strip().rstrip("/")


def _ytdlp_extractor_args(settings) -> list[str]:
    """Build the ``--extractor-args`` flags for a yt-dlp invocation.

    Always sets the ``youtube`` player-client chain. When a bgutil PO-token
    provider base URL is configured, also points the ``bgutil-ytdlp-pot-provider``
    plugin at it via its own extractor-args namespace
    (``youtubepot-bgutilhttp:base_url``). These are *different* namespaces, so
    yt-dlp needs two separate ``--extractor-args`` flags — they cannot be merged
    into one (issue #68). The default ``http://karaoke-pot:4416`` matches the
    documented sidecar; pointing at a dead/absent provider degrades gracefully
    (the plugin just fails to fetch a token), so this is safe in dev/CI too.
    """
    args = ["--extractor-args", f"youtube:player_client={_PLAYER_CLIENTS}"]
    base_url = _pot_base_url(settings)
    if base_url:
        args += ["--extractor-args", f"youtubepot-bgutilhttp:base_url={base_url}"]
    return args


@contextlib.contextmanager
def _ytdlp_aux_args(settings, *, cookies_blob: str | None = None):
    """Yield the yt-dlp flags shared by every YouTube invocation: the EJS
    JS-challenge solver and ``--cookies`` for session-gated videos (issue #68).

    ``cookies_blob`` (issue #77) is an optional per-job Netscape cookie jar the
    submitting client supplied with the job — the **only** cookie source (#132
    retired the central jar). When present it is written to a fresh ``0600``
    temp and used as ``--cookies`` for THIS invocation only. The temp is
    deleted on context exit (success or failure); the blob is never persisted
    and never logged. No blob → no ``--cookies`` at all; public videos still
    download with the solver alone.

    - ``--remote-components <ytdlp_remote_components>`` enables the external
      n-sig / signature solver (run under deno). Without it YouTube hands back
      only storyboard formats and the download fails with "Requested format is
      not available". Defaults to ``ejs:github``; empty disables.

    ``settings`` may be ``None`` (legacy callers / tests), in which case we fall
    back to the same defaults as :class:`karaoke.config.Settings`.
    """
    args: list[str] = []
    if settings is not None:
        remote = str(getattr(settings, "ytdlp_remote_components", "") or "").strip()
    else:
        remote = "ejs:github"
    if remote:
        args += ["--remote-components", remote]
    tmp_cookies: Path | None = None
    try:
        if (cookies_blob or "").strip():
            fd, name = tempfile.mkstemp(prefix="ytc-job-", suffix=".txt")
            os.close(fd)
            tmp_cookies = Path(name)
            payload = cookies_blob if cookies_blob.endswith("\n") else cookies_blob + "\n"
            tmp_cookies.write_text(payload, encoding="utf-8")
            os.chmod(tmp_cookies, 0o600)
            args += ["--cookies", str(tmp_cookies)]
        yield args
    finally:
        if tmp_cookies is not None:
            with contextlib.suppress(OSError):
                tmp_cookies.unlink()


def _ytdlp_metadata(source_url: str, settings=None, *, cookies_blob: str | None = None) -> dict:
    """Best-effort yt-dlp metadata dump; returns {} on any failure.

    ``cookies_blob`` is the optional per-job Netscape jar (issue #77): gated
    videos need a logged-in session for the player API even to read metadata.
    """
    try:
        with _ytdlp_aux_args(settings, cookies_blob=cookies_blob) as aux:
            proc = subprocess.run(
                [
                    "yt-dlp", "--no-playlist", "--skip-download",
                    "--dump-single-json",
                    *_ytdlp_extractor_args(settings),
                    *aux,
                    source_url,
                ],
                text=True, capture_output=True, timeout=120,
            )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
    except Exception:
        return {}
    return {}


def _download_audio(source_url: str, dest: Path, settings=None, *, cookies_blob: str | None = None) -> Path:
    """yt-dlp the best audio stream to ``dest`` (no postprocessing).

    Retries with exponential backoff when yt-dlp fails with a YouTube bot-check
    / rate-limit fingerprint (issue #68): the residential coordinator IP gets
    transiently soft-banned under load and clears after a short cooldown. Any
    other failure (private video, network error, etc.) is raised immediately —
    we only burn the backoff budget on errors a cooldown can actually fix.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    max_attempts = len(_BOT_CHECK_BACKOFF_S) + 1
    with _ytdlp_aux_args(settings, cookies_blob=cookies_blob) as aux:
        cmd = [
            "yt-dlp", "--no-playlist",
            *_ytdlp_extractor_args(settings),
            *aux,
            "-f", "ba/bestaudio/best",
            "-o", str(dest),
            source_url,
        ]
        for attempt in range(1, max_attempts + 1):
            try:
                _run(cmd, timeout=900)
                break
            except PipelineError as exc:
                if not _is_bot_check(str(exc)) or attempt == max_attempts:
                    if _is_bot_check(str(exc)):
                        raise PipelineError(
                            "yt-dlp hit a YouTube bot-check and did not recover "
                            f"after {max_attempts} attempts. This is usually a "
                            "per-video logged-in-session requirement (NOT an IP "
                            "ban — other videos download fine from the same IP): "
                            "resubmit this video via the Chrome extension from a "
                            "browser signed in to YouTube, so the job carries "
                            "your logged-in cookies. (A genuine HTTP 429 "
                            "rate-limit clears on its own after a cooldown.) "
                            f"Last error:\n{exc}"
                        ) from exc
                    raise
                delay = _BOT_CHECK_BACKOFF_S[attempt - 1]
                _log.warning(
                    "yt-dlp bot-check on attempt %d/%d for %s; backing off %.0fs then retrying",
                    attempt, max_attempts, source_url, delay,
                )
                _sleep(delay)
    if not dest.is_file() or dest.stat().st_size == 0:
        raise PipelineError(f"yt-dlp produced no audio at {dest}")
    return dest


def _sleep(seconds: float) -> None:
    """Indirection over ``time.sleep`` so tests can patch out the real backoff."""
    time.sleep(seconds)


# Terminal stage note (and error) for an upload job whose pre-staged
# ``work/source.audio`` is absent or undecodable. No retry: re-uploading is
# the fix, and yt-dlp has nothing to offer an upload:// source.
UPLOAD_BAD_SOURCE_NOTE = "uploaded audio is missing or not decodable"


def _probe_upload_meta(path: Path, fallback_name: str) -> dict:
    """ffprobe an uploaded source file into a yt-dlp-info-shaped dict (#172).

    Returns ``{"title", "track", "artist", "album", "duration"}`` ready for
    :func:`derive_metadata`: container tags are read case-insensitively from
    ``format.tags``, the title falls back to the upload's filename stem, and a
    missing/malformed ``format.duration`` becomes ``None`` rather than failing
    the job. Raises :class:`PipelineError` when ffprobe cannot decode the file
    at all — the caller fails the job with :data:`UPLOAD_BAD_SOURCE_NOTE`.
    """
    proc = _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(path)],
        timeout=120,
    )
    try:
        fmt = json.loads(proc.stdout or "{}").get("format") or {}
    except ValueError as exc:
        raise PipelineError(f"ffprobe returned invalid JSON for {path}") from exc
    raw_tags = fmt.get("tags") if isinstance(fmt, dict) else None
    tags = {
        str(k).lower(): str(v).strip()
        for k, v in (raw_tags or {}).items()
        if v is not None
    }
    duration: float | None
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        duration = None
    tag_title = tags.get("title") or None
    return {
        "title": tag_title or Path(fallback_name).stem or None,
        "track": tag_title,
        "artist": tags.get("artist") or None,
        "album": tags.get("album") or None,
        "duration": duration,
    }


async def _asleep(seconds: float) -> None:
    """Async indirection over ``asyncio.sleep`` so tests can patch the backoff."""
    await asyncio.sleep(seconds)


# Capped exponential backoff between RunPod re-submissions when GPU capacity
# is busy. Index = retry attempt (0-based). Beyond the table we clamp to the
# last value, so a large ``runpod_capacity_retries`` just keeps polling slowly.
_CAPACITY_BACKOFF_S = (20.0, 40.0, 80.0, 120.0, 120.0)


def _capacity_backoff(attempt: int) -> float:
    """Backoff (seconds) before re-submitting after capacity stall ``attempt``."""
    if attempt < 0:
        attempt = 0
    idx = min(attempt, len(_CAPACITY_BACKOFF_S) - 1)
    return _CAPACITY_BACKOFF_S[idx]


def _to_wav(src: Path, dest: Path) -> Path:
    """Normalize ``src`` to a 44.1 kHz stereo wav for separation."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-vn", "-ar", "44100", "-ac", "2", str(dest),
        ],
        timeout=600,
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise PipelineError(f"ffmpeg produced no wav at {dest}")
    return dest


def _wav_to_mp3(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-vn", "-codec:a", "libmp3lame", "-q:a", "2",
            str(dest),
        ],
        timeout=600,
    )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise PipelineError(f"ffmpeg produced no mp3 at {dest}")
    return dest


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
async def _set_stage(
    session_factory: async_sessionmaker,
    job_id: int,
    status: JobStatus,
    progress: int,
    *,
    title: str | None = None,
    metadata: dict | None = None,
    stage_note: str | None = None,
) -> bool:
    """Advance the job to ``status``/``progress``. Returns False if the job is
    gone or already terminal (cancelled/failed) — the caller should stop.

    When ``metadata`` is given (a ``derive_metadata`` mapping), persist any
    of ``artist``/``track``/``album``/``duration`` that we don't already have;
    we never overwrite an existing value so a later, coarser source can't clobber
    a better one."""
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status in {JobStatus.cancelled, JobStatus.failed}:
            return False
        job.status = status
        job.progress = progress
        job.stage_note = stage_note
        if title and not job.title:
            job.title = title
        if metadata:
            for field in ("artist", "track", "album", "duration"):
                value = metadata.get(field)
                if value is not None and getattr(job, field) is None:
                    setattr(job, field, value)
        await session.commit()
    # WS push on every stage transition (issue #8) — the hard rule: WebSocket
    # is the canonical progress channel; /status polling is the fallback.
    ws_events.publish_stage(job_id, status, progress, stage_note=stage_note)
    return True


async def _prior_24h_cost_micros(session_factory: async_sessionmaker) -> int:
    """Sum vast_cost_micros over jobs completed in the last 24h (rolling cap)."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=24)
    async with session_factory() as session:
        rows = await session.scalars(
            select(Job.vast_cost_micros).where(
                Job.completed_at.is_not(None),
                Job.completed_at >= cutoff,
                Job.vast_cost_micros.is_not(None),
            )
        )
        return sum(int(v) for v in rows if v is not None)


async def _mark_failed(
    session_factory: async_sessionmaker,
    job_id: int,
    error: str,
    *,
    stage_note: str | None = None,
) -> None:
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        # Never resurrect a job the user already cancelled (or one already
        # failed) into a fresh 'failed' — that would clobber a terminal state.
        if job is None or job.status in {JobStatus.cancelled, JobStatus.failed}:
            return
        job.status = JobStatus.failed
        # Cleared by default; an explicit note survives as the short operator/
        # UI hint next to the full ``error`` (upload jobs use this, #172).
        job.stage_note = stage_note
        job.error = error[:4000]
        progress = job.progress
        await session.commit()
    # WS: an explicit ``error`` event plus the terminal stage (issue #8).
    ws_events.publish_error(job_id, error[:4000])
    ws_events.publish_stage(job_id, JobStatus.failed, progress, error=error[:4000])


# ---------------------------------------------------------------------------
# lyrics resolution (LRCLIB synced > LRCLIB plain > Whisper ASR)
# ---------------------------------------------------------------------------
def _resolve_lyrics(
    lyrics: LyricsResult,
    exports_dir: Path,
    whisper_lyrics_txt: Path,
    aligned_lrc_path: Path | None = None,
    whisper_lyrics_json: Path | None = None,
) -> dict[str, object]:
    """Apply the lyrics precedence and write the chosen export files.

    Precedence (highest first):
      1. LRCLIB synced        → write ``exports/lyrics.lrc`` (+ ``lyrics.txt``).
         The curated line text + line tags stay authoritative; when the GPU
         force-aligned the same text, its word ``<>`` tags are spliced into each
         line (#222) and ``"lyrics_word_timing": "forced_aligned"`` is recorded.
      2. LRCLIB plain + force-aligned LRC → write the GPU-synthesized
         ``exports/lyrics.lrc`` (provenance ``forced_aligned``) + the LRCLIB
         plain text as ``lyrics.txt``.
      3. LRCLIB plain (no usable aligned LRC) → write ``exports/lyrics.txt``
         (untimed) from LRCLIB.
      4. LRCLIB instrumental  → no lyrics; mark the job instrumental.
      5. Duration-rejected LRCLIB text + force-aligned LRC (#149) → write the
         GPU-synthesized ``exports/lyrics.lrc`` (provenance ``forced_aligned``)
         + the salvaged text as ``lyrics.txt``: right text, timings measured
         from the actual audio. The mapping carries the trigger under
         ``"lyrics_align_reason"`` (e.g. ``"lrclib_duration_mismatch (28s)"``).
      6. LRCLIB miss          → keep the Whisper transcript (``lyrics.txt``);
         when the GPU job's ``lyrics.json`` segment timestamps are usable, also
         synthesize an approximate ``exports/lyrics.lrc`` from them (provenance
         ``whisper_asr_synced``, #145) so the ASR floor still gets synced
         highlight.

    ``aligned_lrc_path`` is the optional GPU-produced force-aligned LRC (#55).
    In the synced branch it supplies word ``<>`` tags merged into the curated
    LRCLIB lines (#222); in the plain-only and rejected-text branches it becomes
    the synced export outright. Known limitation of the rejected-text branch:
    the canonical text may carry lines absent from this audio edit; the handler
    drops low-confidence lines (per-line aligner scores), and a wholly-garbage
    alignment is rejected by :func:`_read_aligned_lrc`, falling to the Whisper
    ASR floor.

    ``whisper_lyrics_json`` is the GPU job's faster-whisper ``lyrics.json``
    (segment timestamps). It is only consulted in the floor branch; a
    missing/unreadable/segment-less file degrades silently to the untimed
    ``whisper_asr`` floor.

    Returns a mapping with provenance for ``metadata.json`` and a flag for
    whether an ``.lrc`` was written::

        {"lyrics_source": str, "synced": bool, "instrumental": bool,
         "lrc_written": bool}

    When the LRCLIB lookup *had* a record but dropped it (duration hard-reject,
    #148) and no usable aligned LRC came back, the floor-branch mapping
    additionally carries the reason under ``"lyrics_lrclib_rejected"`` for
    ``metadata.json`` debuggability. The synced branch adds
    ``"lyrics_word_timing": "forced_aligned"`` when the word-tag merge (#222)
    landed on at least one line.
    """
    lyrics_txt = exports_dir / "lyrics.txt"
    lyrics_lrc = exports_dir / "lyrics.lrc"

    align_coverage_reject: str | None = None
    if lyrics.synced_lrc:
        # Curated LRCLIB synced timing wins, but it is line-level only. Splice
        # the GPU force-aligner's word ``<>`` tags into each line (#222) while
        # keeping LRCLIB's line text + tags byte-identical; provenance stays
        # ``lrclib_synced``. Tolerant: a missing/garbage aligned LRC or any
        # unmergeable line degrades to the plain LRCLIB line exactly.
        aligned = _read_aligned_lrc(aligned_lrc_path)
        merged_lrc, word_timing, eligible, matched_n = merge_lrclib_word_tags(
            lyrics.synced_lrc, aligned
        )
        # Match-quality gate (#237): the aligner tried to fit the curated text
        # onto the actual vocal and almost nothing landed — the record is the
        # wrong text for this audio (a different performance/edit that slipped
        # the duration gate). Fall through to the Whisper ASR floor instead of
        # shipping lyrics that do not match what is sung. Only when alignment
        # actually ran (aligned present) and the record is big enough for the
        # ratio to mean anything.
        if aligned and eligible >= _ALIGN_COVERAGE_MIN_LINES:
            coverage = matched_n / eligible
            if coverage < _ALIGN_COVERAGE_MIN_RATIO:
                align_coverage_reject = f"align_coverage_low ({matched_n}/{eligible})"
                _log.warning(
                    "rejecting lrclib_synced: word-merge coverage %s below %.0f%%",
                    align_coverage_reject,
                    _ALIGN_COVERAGE_MIN_RATIO * 100,
                )
        # The curated TIMING does not fit this audio, but the aligner already
        # timed the same TEXT against the actual vocal stem — a different
        # performance/arrangement of the same song (#241: «Конь» TV cut runs
        # +6…+22 s vs the studio LRC). Ship the aligned LRC (right text,
        # measured timings) instead of falling to the ASR floor. Gate on
        # text_matched (in-order text agreement between the aligner output and
        # the curated lines, drift ignored): a degenerate or wrong-text
        # alignment cannot pass, and both ratio sides count the same lines.
        agreed_n, agree_eligible = (
            aligned_text_agreement(lyrics.synced_lrc, aligned)
            if align_coverage_reject is not None
            else (0, 0)
        )
        if (
            align_coverage_reject is not None
            and aligned
            and agree_eligible > 0
            and agreed_n >= 0.5 * agree_eligible
        ):
            lyrics_lrc.write_text(aligned, encoding="utf-8")
            plain_text = lyrics.plain or lrc_to_plain(lyrics.synced_lrc)
            lyrics_txt.write_text(plain_text, encoding="utf-8")
            return {
                "lyrics_source": SOURCE_FORCED_ALIGNED,
                "synced": True,
                "instrumental": False,
                "lrc_written": True,
                "lyrics_align_reason": align_coverage_reject,
            }
        if align_coverage_reject is None:
            lyrics_lrc.write_text(merged_lrc, encoding="utf-8")
            # Also keep a plain-text export so the inline/share text path renders.
            plain_text = lyrics.plain or lrc_to_plain(lyrics.synced_lrc)
            lyrics_txt.write_text(plain_text, encoding="utf-8")
            prov: dict[str, object] = {
                "lyrics_source": SOURCE_LRCLIB_SYNCED,
                "synced": True,
                "instrumental": False,
                "lrc_written": True,
            }
            if word_timing:
                prov["lyrics_word_timing"] = SOURCE_FORCED_ALIGNED
            return prov

    if lyrics.plain and align_coverage_reject is None:
        # Always keep the LRCLIB plain text as the plain-text export.
        lyrics_txt.write_text(lyrics.plain, encoding="utf-8")
        # Promote to a synced export if the GPU force-aligned the plain text
        # into a usable LRC (#55). Tolerant: a missing/empty/garbage aligned
        # file degrades silently to the untimed plain-only branch.
        aligned = _read_aligned_lrc(aligned_lrc_path)
        if aligned:
            lyrics_lrc.write_text(aligned, encoding="utf-8")
            return {
                "lyrics_source": SOURCE_FORCED_ALIGNED,
                "synced": True,
                "instrumental": False,
                "lrc_written": True,
            }
        return {
            "lyrics_source": SOURCE_LRCLIB_PLAIN,
            "synced": False,
            "instrumental": False,
            "lrc_written": False,
        }

    if lyrics.instrumental:
        # Instrumental: no lyrics. Drop any Whisper transcript that was staged.
        with contextlib.suppress(OSError):
            lyrics_txt.unlink()
        return {
            "lyrics_source": SOURCE_INSTRUMENTAL,
            "synced": False,
            "instrumental": True,
            "lrc_written": False,
        }

    # Duration-rejected record whose text was salvaged (#149): when the GPU
    # force-aligned that text into a usable LRC, export it — right text,
    # timings measured from the actual audio. Source stays ``forced_aligned``;
    # ``lyrics_align_reason`` records why alignment (not native synced) was
    # used. No usable aligned LRC → fall through to the Whisper floor below,
    # which records the rejection as before (#148 unchanged).
    if lyrics.rejected_text:
        aligned = _read_aligned_lrc(aligned_lrc_path)
        if aligned:
            lyrics_lrc.write_text(aligned, encoding="utf-8")
            lyrics_txt.write_text(lyrics.rejected_text, encoding="utf-8")
            return {
                "lyrics_source": SOURCE_FORCED_ALIGNED,
                "synced": True,
                "instrumental": False,
                "lrc_written": True,
                "lyrics_align_reason": f"lrclib_{lyrics.rejected}",
            }

    # LRCLIB miss → keep the Whisper transcript (the ASR floor). When the GPU
    # job's segment timestamps are usable, also emit an approximate LRC so the
    # floor still gets synced highlight (#145). Tolerant: a missing/unreadable
    # lyrics.json or one that yields no timed lines degrades to untimed ASR.
    lyrics_txt.write_bytes(whisper_lyrics_txt.read_bytes())
    asr_lrc = whisper_segments_to_lrc(_read_whisper_segments(whisper_lyrics_json))
    if asr_lrc:
        lyrics_lrc.write_text(asr_lrc, encoding="utf-8")
        prov = {
            "lyrics_source": SOURCE_WHISPER_ASR_SYNCED,
            "synced": True,
            "instrumental": False,
            "lrc_written": True,
        }
    else:
        prov = {
            "lyrics_source": SOURCE_WHISPER_ASR,
            "synced": False,
            "instrumental": False,
            "lrc_written": False,
        }
    if lyrics.rejected:
        prov["lyrics_lrclib_rejected"] = lyrics.rejected
    elif align_coverage_reject:
        prov["lyrics_lrclib_rejected"] = align_coverage_reject
    return prov


# Minimal ISO-639-1 → ISO-639-3 map for the languages we realistically see in
# YouTube music metadata. ctc-forced-aligner's MMS-300m takes ISO-639-3; an
# unknown code falls back to English, which the aligner tolerates (romanize=True
# normalizes the text regardless). This is intentionally tiny — we only need a
# best-effort hint, never a complete language database.
_ISO1_TO_ISO3 = {
    "en": "eng", "es": "spa", "fr": "fra", "de": "deu", "it": "ita",
    "pt": "por", "ru": "rus", "uk": "ukr", "pl": "pol", "nl": "nld",
    "ja": "jpn", "ko": "kor", "zh": "zho", "ar": "ara", "he": "heb",
    "tr": "tur", "hi": "hin", "sv": "swe", "fi": "fin", "no": "nor",
    "cs": "ces", "el": "ell", "ro": "ron", "hu": "hun", "id": "ind",
    "vi": "vie", "th": "tha",
}


def _align_lang(source_meta: dict) -> str:
    """Best-effort ISO-639-3 code for force-alignment from job metadata.

    Accepts either an ISO-639-1 (``"en"``) or already-639-3 (``"eng"``) value
    under ``language``/``lang``; defaults to English when unknown/absent.
    """
    raw = source_meta.get("language") or source_meta.get("lang")
    code = str(raw or "").strip().lower()
    if len(code) == 3:
        return code
    return _ISO1_TO_ISO3.get(code[:2], "eng")


def _read_aligned_lrc(aligned_lrc_path: Path | None) -> str | None:
    """Read a GPU force-aligned LRC (#55), validating it looks like an LRC.

    Tolerant by design: returns ``None`` on a missing/empty/unreadable file or
    a body that carries no ``[mm:ss.xx]`` timestamp tag — so the pipeline falls
    back to LRCLIB plain text rather than emitting a bogus ``.lrc``.
    """
    if aligned_lrc_path is None:
        return None
    try:
        body = aligned_lrc_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not body.strip() or not LRC_TIMESTAMP_RE.search(body):
        return None
    # Repair CTC boundary-absorption (leading/trailing silence swallowed by
    # the first word / sung-end tag) before any drift checks or export (#241).
    return repair_aligned_lrc(body)


def _read_whisper_segments(lyrics_json_path: Path | None) -> list[dict] | None:
    """Read the ``segments`` list from the GPU job's ``lyrics.json`` (#145).

    Tolerant by design: returns ``None`` on a missing/unreadable file, invalid
    JSON, or a body without a ``segments`` list — the pipeline then keeps the
    untimed Whisper transcript instead of failing the job over an LRC nicety.
    """
    if lyrics_json_path is None:
        return None
    try:
        body = json.loads(lyrics_json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    segments = body.get("segments")
    return segments if isinstance(segments, list) else None


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------
async def run_real_job(
    session_factory: async_sessionmaker,
    job_id: int,
    settings,
) -> None:
    """Run a real karaoke job end-to-end. Always leaves the Job in a terminal
    state (completed / failed) and never leaks a vast instance."""
    # Import here so the module is importable in CI without the worker deps.
    from karaoke.worker.scheduler import _use_runpod
    from karaoke.worker.vast_client import VastClient

    # Per-job ephemeral cookies (issue #77): claim the in-memory blob the
    # request handler stashed for this job, if any. Popped FIRST (before any
    # early return) so a cancelled/missing job never leaves a blob lingering
    # in the registry. Used only for the download stage below, then dropped.
    cookies_blob = job_cookies.pop(job_id)

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status in {JobStatus.cancelled, JobStatus.failed}:
            return
        source_url = job.source_url
        job_token = job.job_token

    artifact_root = Path(settings.artifact_root)
    job_root = artifact_root / job_token
    work_dir = job_root / "work"
    exports_dir = job_root / "exports"

    is_upload = source_url.startswith(UPLOAD_PREFIX)

    try:
        # --- downloading ----------------------------------------------------
        if not await _set_stage(
            session_factory,
            job_id,
            JobStatus.downloading,
            15,
            stage_note="reading uploaded audio" if is_upload else None,
        ):
            return
        raw_audio = work_dir / "source.audio"
        if is_upload:
            # Upload job (#172): POST /jobs/upload already staged the audio at
            # work/source.audio, so yt-dlp (metadata + download) and the
            # per-job cookies are no-ops on this branch. Metadata comes from
            # the file's container tags via ffprobe; a missing or undecodable
            # file fails the job immediately — nothing a retry could fix.
            cookies_blob = None
            if not raw_audio.is_file() or raw_audio.stat().st_size == 0:
                await _mark_failed(
                    session_factory,
                    job_id,
                    UPLOAD_BAD_SOURCE_NOTE,
                    stage_note=UPLOAD_BAD_SOURCE_NOTE,
                )
                return
            try:
                meta = await asyncio.to_thread(
                    _probe_upload_meta, raw_audio, upload_display_name(source_url)
                )
            except Exception as exc:  # noqa: BLE001 — any probe failure is terminal here
                await _mark_failed(
                    session_factory,
                    job_id,
                    f"{UPLOAD_BAD_SOURCE_NOTE} ({type(exc).__name__}: {exc})",
                    stage_note=UPLOAD_BAD_SOURCE_NOTE,
                )
                return
        else:
            meta = await asyncio.to_thread(
                _ytdlp_metadata, source_url, settings, cookies_blob=cookies_blob
            )
        title = (meta.get("title") or "").strip() or None
        source_meta = derive_metadata(meta)
        if title or any(source_meta.values()):
            await _set_stage(
                session_factory,
                job_id,
                JobStatus.downloading,
                20,
                title=title,
                metadata=source_meta,
            )

        if not is_upload:
            await asyncio.to_thread(
                _download_audio, source_url, raw_audio, settings, cookies_blob=cookies_blob
            )
        mix_wav = work_dir / "mix.wav"
        await asyncio.to_thread(_to_wav, raw_audio, mix_wav)
        # Download stage done — drop the per-job cookies from memory. The
        # per-invocation temp files were already deleted by ``_ytdlp_aux_args``;
        # nothing past this point needs the cookies (issue #77).
        cookies_blob = None

        # --- lyrics lookup (BEFORE GPU dispatch) ----------------------------
        # We resolve LRCLIB *before* the GPU window so that, when LRCLIB has the
        # text but no synced timing, we can ship that plain text to the GPU as
        # ``align_text`` and force-align it against the vocal stem in the SAME
        # job (#55). Best-effort: any network error → empty result → no
        # align_text → unchanged behavior (Whisper floor). Runs on the
        # coordinator, never on the GPU.
        lyrics = await asyncio.to_thread(
            _LYRICS_SOURCE.fetch,
            artist=source_meta.get("artist"),
            track=source_meta.get("track"),
            album=source_meta.get("album"),
            duration=source_meta.get("duration"),
        )
        # Send text to force-align inside the same GPU window whenever LRCLIB
        # gave us words to time against OUR vocal stem:
        #   * synced LRC (#222) — curated, but line-level only; align its own
        #     text to obtain word timings we splice back in at finalize while
        #     keeping the curated line text + tags authoritative;
        #   * plain text with no synced LRC (#55) — the words fit but have no
        #     timing;
        #   * duration hard-reject (#148) salvaged text (#149) — the words fit,
        #     only the timings belonged to the wrong edit.
        # Instrumental / miss have nothing to align.
        align_text: str | None = None
        align_lang: str | None = None
        if lyrics.synced_lrc:
            align_text = lrc_to_plain(lyrics.synced_lrc)
            align_lang = _align_lang(source_meta)
        elif lyrics.plain and not lyrics.instrumental:
            align_text = lyrics.plain
            align_lang = _align_lang(source_meta)
        elif lyrics.rejected_text:
            align_text = lyrics.rejected_text
            align_lang = _align_lang(source_meta)

        # --- separating (provision + /demucs) -------------------------------
        if not await _set_stage(session_factory, job_id, JobStatus.separating, 45):
            return
        prior = await _prior_24h_cost_micros(session_factory)
        if _use_runpod(settings):
            from karaoke.worker.runpod_client import RunpodClient

            # RunPod Serverless has no provisioning moment (no instance we
            # own); the teardown cost_update after the GPU window covers it.
            client = RunpodClient(settings, prior_24h_cost_micros=prior)
        else:
            client = VastClient(
                settings,
                prior_24h_cost_micros=prior,
                # ``client.run`` executes inside ``asyncio.to_thread``, so the
                # provisioning cost_update must hop back to the app loop.
                on_instance_created=_provision_cost_publisher(job_id),
            )

        # VastClient.run is synchronous (urllib + ssh + httpx); offload it. It
        # runs BOTH /demucs (separating) and /whisper (transcribing) in one
        # instance window, then destroys the instance in its own finally.
        # We flip the visible status to "transcribing" right before the call so
        # the WS poller reflects the GPU phase; the single window covers both.
        # When ``align_text`` is set, the RunPod handler additionally force-
        # aligns it against the vocal stem and returns a synced LRC (#55).
        if not await _set_stage(session_factory, job_id, JobStatus.transcribing, 75):
            return
        gpu = await _run_gpu_with_capacity_retry(
            session_factory,
            job_id,
            settings,
            client,
            mix_wav,
            work_dir,
            align_text=align_text,
            align_lang=align_lang,
        )
        # GPU window closed (the client destroyed the instance in its own
        # ``finally``): push the final cost, then the WS-only ``finalizing``
        # stage while we encode + write exports (issue #8). The Job row stays
        # ``transcribing`` — ``finalizing`` has no DB enum value.
        ws_events.publish_cost(
            job_id,
            gpu.vast_cost,
            vast_instance_id=str(gpu.vast_instance_id),
            phase="teardown",
        )
        ws_events.publish_stage(job_id, ws_events.STAGE_FINALIZING, 95)

        # --- finalize -------------------------------------------------------
        exports_dir.mkdir(parents=True, exist_ok=True)
        karaoke_mp3 = exports_dir / "karaoke.mp3"
        vocals_mp3 = exports_dir / "vocals.mp3"
        await asyncio.to_thread(_wav_to_mp3, gpu.instrumental_path, karaoke_mp3)
        await asyncio.to_thread(_wav_to_mp3, gpu.vocals_path, vocals_mp3)

        # --- lyrics resolution: precedence + chosen exports -----------------
        # LRCLIB synced (duration OK) > force-aligned LRCLIB text (plain #55,
        # or duration-rejected #149) > LRCLIB plain (untimed) > Whisper ASR
        # (floor). Tolerant: if the GPU returned no usable aligned LRC (old
        # image / alignment failed), we degrade to LRCLIB plain text or — for
        # rejected text — the Whisper floor; if LRCLIB missed entirely, we keep
        # the Whisper transcript — synthesizing an approximate LRC from its
        # segment timestamps when usable (#145). Never fails the job over
        # alignment.
        lyrics_prov = _resolve_lyrics(
            lyrics,
            exports_dir,
            gpu.lyrics_txt_path,
            gpu.aligned_lrc_path,
            gpu.lyrics_json_path,
        )

        lyrics_txt = exports_dir / "lyrics.txt"
        lyrics_lrc = exports_dir / "lyrics.lrc"

        metadata = {
            "title": title,
            "artist": source_meta.get("artist"),
            "track": source_meta.get("track"),
            "album": source_meta.get("album"),
            "duration": source_meta.get("duration"),
            "source_url": source_url,
            "device": "vast",
            "gpu_model": gpu.gpu_model,
            "vast_instance_id": gpu.vast_instance_id,
            "vast_cost": round(gpu.vast_cost, 6),
            "lyrics_source": lyrics_prov["lyrics_source"],
            "synced": lyrics_prov["synced"],
            "instrumental": lyrics_prov["instrumental"],
        }
        # Why an LRCLIB record was dropped (duration hard-reject, #148) — only
        # present when it happened, so normal jobs keep a stable metadata shape.
        if lyrics_prov.get("lyrics_lrclib_rejected"):
            metadata["lyrics_lrclib_rejected"] = lyrics_prov["lyrics_lrclib_rejected"]
        # Why a forced alignment was used over native synced timings (#149) —
        # only present when rejected LRCLIB text was successfully re-aligned.
        if lyrics_prov.get("lyrics_align_reason"):
            metadata["lyrics_align_reason"] = lyrics_prov["lyrics_align_reason"]
        # Word timing spliced into the curated LRCLIB synced LRC (#222) — only
        # present when the force-aligner merge added word tags to at least one
        # line; provenance stays ``lrclib_synced``.
        if lyrics_prov.get("lyrics_word_timing"):
            metadata["lyrics_word_timing"] = lyrics_prov["lyrics_word_timing"]
        # Which cleaned track variant hit via the #230 fallback ladder — only
        # present when the parsed (artist, track) missed and a variant matched.
        if lyrics.match_variant:
            metadata["lyrics_match_variant"] = lyrics.match_variant
        (exports_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        artifacts = [
            ("vocals", "vocals.mp3", vocals_mp3, "audio/mpeg"),
            ("karaoke", "karaoke.mp3", karaoke_mp3, "audio/mpeg"),
        ]
        # Plain-text lyrics exist unless the track is instrumental.
        if lyrics_txt.is_file():
            artifacts.append(("lyrics", "lyrics.txt", lyrics_txt, "text/plain"))
        # Synced LRC is a distinct artifact kind served at /share/.../lyrics.lrc.
        if lyrics_prov["lrc_written"] and lyrics_lrc.is_file():
            artifacts.append(
                ("lyrics_lrc", "lyrics.lrc", lyrics_lrc, "text/plain")
            )

        async with session_factory() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in {JobStatus.cancelled, JobStatus.failed}:
                return
            for kind, fname, path, ctype in artifacts:
                size = path.stat().st_size if path.is_file() else None
                session.add(
                    Artifact(
                        job_id=job.id,
                        kind=kind,
                        relative_path=f"{job_token}/exports/{fname}",
                        size_bytes=size,
                        content_type=ctype,
                    )
                )
            job.status = JobStatus.completed
            job.progress = 100
            job.stage_note = None
            job.completed_at = dt.datetime.now(dt.UTC)
            job.vast_instance_id = str(gpu.vast_instance_id)
            job.vast_cost_micros = round(gpu.vast_cost * 1_000_000)
            await session.commit()
        ws_events.publish_stage(job_id, JobStatus.completed, 100)
    except Exception as exc:  # noqa: BLE001 — surface as a failed job, never crash the loop
        await _mark_failed(session_factory, job_id, f"{type(exc).__name__}: {exc}")


def _provision_cost_publisher(job_id: int):
    """Callback for ``VastClient(on_instance_created=...)``: announce that a
    vast instance now exists (cost accrual starts) over WS. Runs on the
    ``asyncio.to_thread`` worker thread, hence the thread-safe publish."""

    def _publish(instance_id: int) -> None:
        ws_events.publish_cost_threadsafe(
            job_id, 0.0, vast_instance_id=str(instance_id), phase="provisioned"
        )

    return _publish


async def _run_gpu_with_capacity_retry(
    session_factory: async_sessionmaker,
    job_id: int,
    settings,
    client,
    mix_wav: Path,
    work_dir: Path,
    *,
    align_text: str | None,
    align_lang: str | None,
):
    """Run the GPU stage, auto-retrying transient RunPod capacity stalls.

    A ``RunpodCapacityError`` means the job sat IN_QUEUE past the queue
    ceiling and was cancelled before any compute ran — no cost, idempotent.
    Re-submitting after a short backoff almost always clears it (a busy GPU
    pool frees up within seconds-to-minutes). Only after exhausting
    ``settings.runpod_capacity_retries`` do we let the error propagate and
    fail the job. Any other error (real GPU failure, budget, wall backstop)
    is NOT retried. Honours user cancellation while we wait between attempts.
    """
    from karaoke.worker.runpod_client import RunpodCapacityError, RunpodColdStartError

    retries = max(0, int(getattr(settings, "runpod_capacity_retries", 0) or 0))
    attempt = 0
    cold_start_waits = 0
    while attempt <= retries:
        try:
            async with session_factory() as session:
                job = await session.get(Job, job_id)
                if job is None or job.status in {
                    JobStatus.cancelled,
                    JobStatus.failed,
                }:
                    raise PipelineError("job cancelled before RunPod retry")
                job.status = JobStatus.transcribing
                job.progress = 75
                await session.commit()
            return await asyncio.to_thread(
                functools.partial(
                    client.run, mix_wav, work_dir,
                    align_text=align_text, align_lang=align_lang,
                )
            )
        except RunpodColdStartError as exc:
            cold_start_waits += 1
            if not await _set_stage(
                session_factory,
                job_id,
                JobStatus.transcribing,
                75,
                stage_note=(
                    "GPU worker warming up (image pull) -- may take 10-30 min "
                    "on first run"
                ),
            ):
                raise
            delay = max(
                float(getattr(settings, "runpod_queue_ceiling_s", 480.0) or 480.0),
                _capacity_backoff(min(cold_start_waits - 1, 5)),
            )
            _log.warning(
                "RunPod GPU worker warming for job %s (workers.initializing=%s; "
                "capacity attempt %d/%d unchanged): %s; waiting %.0fs then "
                "re-submitting",
                job_id,
                exc.workers_initializing,
                attempt + 1,
                retries + 1,
                exc,
                delay,
            )
            await _asleep(delay)
            continue
        except RunpodCapacityError as exc:
            if attempt >= retries:
                # Out of retries — surface the capacity error so the job
                # fails with a clear, actionable message (Retry still works).
                raise
            # Stop retrying if the user cancelled the job while we waited.
            async with session_factory() as session:
                job = await session.get(Job, job_id)
                if job is None or job.status in {
                    JobStatus.cancelled,
                    JobStatus.failed,
                }:
                    raise
                job.stage_note = None
                await session.commit()
            delay = _capacity_backoff(attempt)
            _log.warning(
                "RunPod GPU capacity busy for job %s (attempt %d/%d): %s; "
                "backing off %.0fs then re-submitting",
                job_id, attempt + 1, retries + 1, exc, delay,
            )
            attempt += 1
            await _asleep(delay)
    # Unreachable: the loop either returns a result or raises.
    raise RuntimeError("capacity retry loop exited without result")


def schedule_real_job(
    session_factory: async_sessionmaker,
    job_id: int,
    settings,
) -> asyncio.Task[None]:
    """Schedule :func:`run_real_job` on the running event loop."""
    return asyncio.create_task(run_real_job(session_factory, job_id, settings))
