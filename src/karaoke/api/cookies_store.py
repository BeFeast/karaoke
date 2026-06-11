"""Validation for client-supplied Netscape cookie blobs (issue #77).

A client (Chrome extension on desktop, native app on mobile) may attach the
user's logged-in YouTube cookies to a job (``POST /jobs`` ``youtube_cookies``).
This module checks that such a blob is a usable Netscape ``cookies.txt`` before
the job is accepted; the blob itself stays in memory only (see
``karaoke.worker.job_cookies``) and is consumed once by the worker's download
stage.

Security rule baked in here: **cookies are secrets.** No function in this
module logs, returns, or raises a cookie *value*. Validation errors carry only
structural facts (line index, field count, flag names) — never the field
contents.
"""
from __future__ import annotations

from dataclasses import dataclass

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
