"""Helpers for the audio-file upload flow (``POST /jobs/upload``, issue #172).

An uploaded job is represented with ZERO schema changes: ``jobs.source_url``
carries the sentinel ``upload://<safe filename>`` instead of a real URL. The
server names the stored file (``{artifact_root}/{job_token}/work/source.audio``
— the exact path the URL flow's downloader produces), so the client filename
is display-only; it is still sanitized here so no path separators, control
characters, or absurd lengths ever reach the DB or a rendered page.

Pure string work with no I/O — importable from both the API and the worker.
"""
from __future__ import annotations

import re

UPLOAD_PREFIX = "upload://"

# Extensions accepted by ``POST /jobs/upload``. The pipeline ffmpeg-normalizes
# whatever it gets, so this is a UX guard, not a security boundary: content is
# NOT sniffed at upload time — the worker's ffprobe is authoritative, and a
# garbage file fails the job in seconds with a clear stage note.
ALLOWED_UPLOAD_EXTENSIONS = frozenset({".mp3", ".m4a", ".wav", ".flac", ".ogg"})

# Cap on the sanitized filename kept inside the sentinel (display-only).
MAX_UPLOAD_FILENAME_CHARS = 160

# Control chars EXCEPT \t..\r (\x09-\x0d), which are whitespace and must
# survive until the whitespace collapse below turns them into single spaces.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")


def is_upload_source(source_url: str | None) -> bool:
    """Whether a job's ``source_url`` is an ``upload://`` sentinel."""
    return bool(source_url) and source_url.startswith(UPLOAD_PREFIX)


def upload_display_name(source_url: str) -> str:
    """The filename carried by an ``upload://`` sentinel; other URLs pass
    through unchanged (display fallback for titles, share pages, ...)."""
    if is_upload_source(source_url):
        return source_url[len(UPLOAD_PREFIX):]
    return source_url


def sanitize_upload_filename(raw: str | None) -> str:
    """Reduce a client-supplied filename to a safe display name.

    Backslashes normalize to ``/`` and only the last path component survives,
    control characters are stripped, whitespace collapses to single spaces,
    and the result is capped at ~160 chars (extension preserved). A name with
    no usable stem (empty, ``.mp3``, ...) falls back to ``audio[.<ext>]`` —
    the extension allowlist is the caller's job.
    """
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_CHARS_RE.sub("", name)
    name = _WHITESPACE_RE.sub(" ", name).strip()
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        ext = ext.strip()
    else:
        stem, ext = name, ""
    stem = stem.strip() or "audio"
    if len(stem) + len(ext) + 1 > MAX_UPLOAD_FILENAME_CHARS:
        stem = stem[: max(1, MAX_UPLOAD_FILENAME_CHARS - len(ext) - 1)].rstrip()
    return f"{stem}.{ext}" if ext else stem
