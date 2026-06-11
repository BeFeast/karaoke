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
from karaoke.worker import job_cookies
from karaoke.worker.lyrics import (
    SOURCE_FORCED_ALIGNED,
    SOURCE_INSTRUMENTAL,
    SOURCE_LRCLIB_PLAIN,
    SOURCE_LRCLIB_SYNCED,
    SOURCE_WHISPER_ASR,
    LyricsResult,
    LyricsSource,
)

_log = logging.getLogger(__name__)

# Module-level LRCLIB client so its in-process cache survives across jobs.
_LYRICS_SOURCE = LyricsSource()

# yt-dlp player-client chain (mirrors scribe's downloader; android_vr is the
# token-free workhorse, web clients need the EJS/deno JS solver in the image).
_PLAYER_CLIENTS = "mweb,web_safari,android_vr,web_embedded"

# Matches an LRC line timestamp tag, e.g. "[01:23.45]" / "[01:23]", including
# repeated tags on one line. Used to derive a plain-text export from synced LRC.
_LRC_TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")

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
    submitting client supplied with the job. When present it is written to a
    fresh ``0600`` temp and used as ``--cookies`` for THIS invocation only,
    taking precedence over the shared/central ``ytdlp_cookies_file`` jar. The
    temp is deleted on context exit (success or failure); the blob is never
    persisted and never logged.

    - ``--remote-components <ytdlp_remote_components>`` enables the external
      n-sig / signature solver (run under deno). Without it YouTube hands back
      only storyboard formats and the download fails with "Requested format is
      not available". Defaults to ``ejs:github``; empty disables.
    - ``--cookies <copy>`` is emitted only when ``ytdlp_cookies_file`` points at
      an existing non-empty file. yt-dlp rotates the cookie jar and writes it
      back on close, so we hand it a *per-call writable copy* in a temp file
      (the mounted secret is read-only and shared) and delete the copy on exit.
      No cookies file → public videos still download with the solver alone.

    ``settings`` may be ``None`` (legacy callers / tests), in which case we fall
    back to the same defaults as :class:`karaoke.config.Settings`.
    """
    args: list[str] = []
    if settings is not None:
        remote = str(getattr(settings, "ytdlp_remote_components", "") or "").strip()
        cookies_src = str(getattr(settings, "ytdlp_cookies_file", "") or "").strip()
    else:
        remote, cookies_src = "ejs:github", ""
    if remote:
        args += ["--remote-components", remote]
    tmp_cookies: Path | None = None
    try:
        if (cookies_blob or "").strip():
            # Per-job cookies (issue #77) win over the central jar: a client
            # that supplied its own logged-in session for THIS job must use
            # exactly that, not the shared operator jar.
            fd, name = tempfile.mkstemp(prefix="ytc-job-", suffix=".txt")
            os.close(fd)
            tmp_cookies = Path(name)
            payload = cookies_blob if cookies_blob.endswith("\n") else cookies_blob + "\n"
            tmp_cookies.write_text(payload, encoding="utf-8")
            os.chmod(tmp_cookies, 0o600)
            args += ["--cookies", str(tmp_cookies)]
        elif cookies_src:
            src = Path(cookies_src)
            if src.is_file() and src.stat().st_size > 0:
                fd, name = tempfile.mkstemp(prefix="ytc-", suffix=".txt")
                os.close(fd)
                tmp_cookies = Path(name)
                tmp_cookies.write_bytes(src.read_bytes())
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
                            "export a logged-in YouTube cookies.txt and point "
                            "KARAOKE_YTDLP_COOKIES_FILE at it. (A genuine HTTP 429 "
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
    session_factory: async_sessionmaker, job_id: int, error: str
) -> None:
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        # Never resurrect a job the user already cancelled (or one already
        # failed) into a fresh 'failed' — that would clobber a terminal state.
        if job is None or job.status in {JobStatus.cancelled, JobStatus.failed}:
            return
        job.status = JobStatus.failed
        job.stage_note = None
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
) -> dict[str, object]:
    """Apply the lyrics precedence and write the chosen export files.

    Precedence (highest first):
      1. LRCLIB synced        → write ``exports/lyrics.lrc`` (+ ``lyrics.txt``).
      2. LRCLIB plain + force-aligned LRC → write the GPU-synthesized
         ``exports/lyrics.lrc`` (provenance ``forced_aligned``) + the LRCLIB
         plain text as ``lyrics.txt``.
      3. LRCLIB plain (no usable aligned LRC) → write ``exports/lyrics.txt``
         (untimed) from LRCLIB.
      4. LRCLIB instrumental  → no lyrics; mark the job instrumental.
      5. LRCLIB miss          → keep the Whisper transcript (``lyrics.txt``).

    ``aligned_lrc_path`` is the optional GPU-produced force-aligned LRC (#55).
    It is only consulted in the plain-only branch — synced LRCLIB always wins,
    and we never force-align when there's nothing to align.

    Returns a mapping with provenance for ``metadata.json`` and a flag for
    whether an ``.lrc`` was written::

        {"lyrics_source": str, "synced": bool, "instrumental": bool,
         "lrc_written": bool}
    """
    lyrics_txt = exports_dir / "lyrics.txt"
    lyrics_lrc = exports_dir / "lyrics.lrc"

    if lyrics.synced_lrc:
        lyrics_lrc.write_text(lyrics.synced_lrc, encoding="utf-8")
        # Also keep a plain-text export so the inline/share text path renders.
        plain_text = lyrics.plain or _lrc_to_plain(lyrics.synced_lrc)
        lyrics_txt.write_text(plain_text, encoding="utf-8")
        return {
            "lyrics_source": SOURCE_LRCLIB_SYNCED,
            "synced": True,
            "instrumental": False,
            "lrc_written": True,
        }

    if lyrics.plain:
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

    # LRCLIB miss → keep the Whisper transcript.
    lyrics_txt.write_bytes(whisper_lyrics_txt.read_bytes())
    return {
        "lyrics_source": SOURCE_WHISPER_ASR,
        "synced": False,
        "instrumental": False,
        "lrc_written": False,
    }


def _lrc_to_plain(lrc: str) -> str:
    """Strip ``[mm:ss.xx]`` timestamps from an LRC body for the plain export."""
    out: list[str] = []
    for line in lrc.splitlines():
        stripped = _LRC_TIMESTAMP_RE.sub("", line).strip()
        if stripped:
            out.append(stripped)
    return "\n".join(out)


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
    if not body.strip() or not _LRC_TIMESTAMP_RE.search(body):
        return None
    return body


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

    try:
        # --- downloading ----------------------------------------------------
        if not await _set_stage(session_factory, job_id, JobStatus.downloading, 15):
            return
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

        raw_audio = work_dir / "source.audio"
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
        # Force-align only when LRCLIB returned plain text but NO synced LRC
        # (synced already wins; instrumental/miss have nothing to align).
        align_text: str | None = None
        align_lang: str | None = None
        if lyrics.plain and not lyrics.synced_lrc and not lyrics.instrumental:
            align_text = lyrics.plain
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
        # LRCLIB synced > LRCLIB plain + force-aligned LRC (→ synced) >
        # LRCLIB plain (untimed) > Whisper ASR (floor). Tolerant: if the GPU
        # returned no usable aligned LRC (old image / alignment failed), we
        # degrade to LRCLIB plain text; if LRCLIB missed entirely, we keep the
        # Whisper transcript. Never fails the job over alignment.
        lyrics_prov = _resolve_lyrics(
            lyrics, exports_dir, gpu.lyrics_txt_path, gpu.aligned_lrc_path
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
