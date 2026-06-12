"""Offline yt-dlp extractor matching for URL support checks (issue #180).

Answers "would the deployed yt-dlp recognise this URL with a dedicated
extractor?" entirely in-process: no network, no subprocess, no
``yt_dlp.YoutubeDL`` instantiation. The verdict is version-true because it
runs against the same installed ``yt_dlp`` package the pipeline downloads
with.

The extractor class list is static for a given yt-dlp version, so it is
built once at import. yt-dlp ships lazy extractor classes whose URL regex
compiles on the first ``suitable()`` call — a one-time ~300 ms cost across
the full list — so a warm-up sweep below moves that cost from the first
request to process start; a warm sweep takes single-digit milliseconds.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from yt_dlp.extractor import gen_extractor_classes

_EXTRACTORS = tuple(gen_extractor_classes())


@dataclass(frozen=True)
class PreflightResult:
    """Verdict of matching a URL against yt-dlp's extractors.

    * ``supported`` — a dedicated (non-Generic) extractor claims the URL.
    * ``extractor`` — that extractor's ``IE_NAME``, else ``None``.
    * ``generic_only`` — the URL is syntactically valid http(s) but only the
      catch-all Generic extractor matched (yt-dlp *might* scrape something,
      with no guarantee).
    """

    supported: bool
    extractor: str | None
    generic_only: bool


_INVALID_URL = PreflightResult(supported=False, extractor=None, generic_only=False)


def _is_http_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def match_url(url: str) -> PreflightResult:
    """Match ``url`` against the installed yt-dlp's extractors, offline.

    Invalid / non-http(s) input short-circuits to an "unsupported" verdict —
    yt-dlp's Generic extractor pattern-matches nearly any string, so it must
    not be consulted before the URL itself is known to be plausible.
    """
    if not _is_http_url(url):
        return _INVALID_URL
    generic_matched = False
    for ie in _EXTRACTORS:
        if not ie.suitable(url):
            continue
        if ie.ie_key() == "Generic":
            generic_matched = True
            continue
        return PreflightResult(supported=True, extractor=ie.IE_NAME, generic_only=False)
    return PreflightResult(supported=False, extractor=None, generic_only=generic_matched)


# Warm-up sweep: nothing dedicated matches this URL, so every extractor's
# lazy ``suitable()`` regex gets compiled exactly once, here.
match_url("https://preflight-warmup.invalid/")
