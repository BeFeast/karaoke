"""Storage + validation for the YouTube cookie jar (issue #73).

The coordinator accepts a logged-in Netscape ``cookies.txt`` from the Chrome
extension (or a machine bearer) and persists it to the path the pipeline reads
(``Settings.ytdlp_cookies_file``) so session-gated YouTube downloads keep
working without a manual re-export.

Security rules baked in here:

- **Cookies are secrets.** No function in this module logs, returns, or raises
  a cookie *value*. Validation errors carry only structural facts (line index,
  field count, flag names) — never the field contents.
- **Atomic write.** The new jar is written to a temp file in the *same*
  directory and ``os.replace``-d onto the canonical path, so a reader (the
  pipeline) never observes a half-written file. The replace target must live on
  a writable directory mount — a single-file ``:ro`` bind cannot be renamed
  over (see ``docs/cookie-rotation.md``).
- **Last-known-good.** Before replacing, the current canonical jar is copied to
  a sibling ``<name>.previous`` so a bad rotation can be rolled back. Because
  every accepted jar is validated *before* it is written, the canonical file is
  always a structurally-valid jar.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Netscape data lines have exactly 7 tab-separated fields:
#   domain  include_subdomains  path  secure  expiry  name  value
_NETSCAPE_FIELDS = 7
_HTTPONLY_PREFIX = "#HttpOnly_"
_BOOL_FIELDS = ("TRUE", "FALSE")


class CookieValidationError(ValueError):
    """Raised when an uploaded blob is not a usable Netscape cookie jar.

    The message is safe to surface to the client: it never contains a cookie
    value, only structural detail (line number, field count, flag name)."""


@dataclass(frozen=True)
class CookieStats:
    """Non-secret summary of a validated jar."""

    total: int
    youtube: int


def validate_netscape_cookies(blob: str) -> CookieStats:
    """Validate ``blob`` as a Netscape ``cookies.txt`` and return counts.

    Raises :class:`CookieValidationError` (value-free message) when the blob is
    not a usable jar. A blob is usable when it has at least one well-formed data
    line and at least one cookie scoped to ``youtube.com`` (a guard against the
    extension uploading the wrong jar)."""
    total = 0
    youtube = 0
    for index, raw_line in enumerate(blob.splitlines(), start=1):
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        # Comments are skipped, except yt-dlp's ``#HttpOnly_`` data lines.
        is_httponly = line.startswith(_HTTPONLY_PREFIX)
        if line.startswith("#") and not is_httponly:
            continue

        data_line = line[len(_HTTPONLY_PREFIX):] if is_httponly else line
        fields = data_line.split("\t")
        if len(fields) != _NETSCAPE_FIELDS:
            raise CookieValidationError(
                f"line {index}: expected {_NETSCAPE_FIELDS} tab-separated "
                f"fields, got {len(fields)}"
            )
        domain, include_sub, _path, secure, expiry, name, _value = fields
        if not domain.strip():
            raise CookieValidationError(f"line {index}: empty domain field")
        if include_sub.upper() not in _BOOL_FIELDS:
            raise CookieValidationError(
                f"line {index}: include-subdomains flag must be TRUE/FALSE"
            )
        if secure.upper() not in _BOOL_FIELDS:
            raise CookieValidationError(
                f"line {index}: secure flag must be TRUE/FALSE"
            )
        expiry_str = expiry.strip()
        if expiry_str and not expiry_str.lstrip("-").isdigit():
            raise CookieValidationError(
                f"line {index}: expiry must be an integer timestamp"
            )
        if not name.strip():
            raise CookieValidationError(f"line {index}: empty cookie name")

        total += 1
        if "youtube.com" in domain.lower():
            youtube += 1

    if total == 0:
        raise CookieValidationError("no cookie entries found")
    if youtube == 0:
        raise CookieValidationError("no youtube.com cookies present")
    return CookieStats(total=total, youtube=youtube)


def previous_path(target: Path) -> Path:
    """Sibling path holding the last-known-good jar (one-step rollback)."""
    return target.with_name(target.name + ".previous")


def write_cookies_atomically(target: Path, blob: str) -> bool:
    """Atomically persist ``blob`` to ``target``; keep the prior jar as backup.

    Returns ``True`` when an existing canonical jar was snapshotted to
    :func:`previous_path` (i.e. a rollback copy now exists), ``False`` on the
    first write. The blob is normalised to end with a trailing newline and the
    file is written ``0600``. Callers must pass a blob that already passed
    :func:`validate_netscape_cookies`."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    kept = False
    if target.exists() and target.stat().st_size > 0:
        with contextlib.suppress(OSError):
            shutil.copy2(target, previous_path(target))
            kept = True

    payload = blob if blob.endswith("\n") else blob + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".ytc-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)  # atomic within the same directory
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return kept
