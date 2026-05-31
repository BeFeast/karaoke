"""LRCLIB synced-lyrics sourcing (Track 1).

The coordinator (devbox, residential IP) tries to source *real* lyrics for a
job from `LRCLIB <https://lrclib.net>`_ — a free, key-less community lyrics
database that returns time-synced ``.lrc`` lyrics when available. This runs on
the coordinator, **never** inside the GPU handler: LRCLIB is a plain HTTP API
and the lookup keys (artist / track / album / duration) are already known from
the yt-dlp metadata captured during download (see :mod:`karaoke.titles`).

Precedence the pipeline applies with this module's result:

    LRCLIB synced  >  LRCLIB plain  >  Whisper ASR (the GPU transcript)

Lookup strategy (mirrors LRCLIB's own client guidance):

  1. ``GET /api/get`` with ``artist_name`` / ``track_name`` / ``album_name`` /
     ``duration`` — an exact match; LRCLIB matches duration within ±2s.
  2. On a 404 (or any miss), fall back to ``GET /api/search`` (fuzzy) and pick
     the best candidate by title similarity + duration proximity.

Results are cached in-process by ``(artist, track, duration)`` so re-running a
job (or a retry) does not re-hit LRCLIB.

A descriptive ``User-Agent`` is sent as LRCLIB requests (their docs ask clients
to identify themselves with a link back to the project).

The HTTP call is injectable (``http=...``) so unit tests never touch the
network — the same test seam shape the RunPod/vast clients use.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

LRCLIB_BASE = "https://lrclib.net"
USER_AGENT = "karaoke/1.0 (+https://github.com/BeFeast/karaoke)"
# LRCLIB matches the supplied duration within this many seconds for /api/get;
# we reuse it to score /api/search candidates.
_DURATION_TOLERANCE_S = 2

# Lyrics-source provenance values recorded in metadata.json.
SOURCE_LRCLIB_SYNCED = "lrclib_synced"
SOURCE_LRCLIB_PLAIN = "lrclib_plain"
SOURCE_WHISPER_ASR = "whisper_asr"
SOURCE_INSTRUMENTAL = "instrumental"


@dataclass(frozen=True, slots=True)
class LyricsResult:
    """Outcome of an LRCLIB lookup.

    * ``synced_lrc`` — time-synced ``.lrc`` body, or ``None``.
    * ``plain`` — plain-text lyrics, or ``None``.
    * ``instrumental`` — LRCLIB flagged the track as instrumental (no lyrics).
    * ``source`` — where the data came from for provenance / logging:
      ``"lrclib_get"``, ``"lrclib_search"``, ``"instrumental"`` or ``"none"``.
    """

    synced_lrc: str | None = None
    plain: str | None = None
    instrumental: bool = False
    source: str = "none"

    @property
    def found(self) -> bool:
        """True when LRCLIB returned usable info (lyrics or an instrumental flag)."""
        return bool(self.synced_lrc or self.plain or self.instrumental)


# Type of the injectable HTTP callable: (method, url, params) -> (status, json).
HttpFn = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]


def _default_http(
    method: str, url: str, params: dict[str, Any] | None
) -> tuple[int, Any]:
    """Real HTTP via ``httpx`` (sync — the pipeline calls us in a worker thread).

    Returns ``(status_code, parsed_json_or_None)``. Network / decode failures
    surface as ``(0, None)`` so the caller treats them as a miss and keeps the
    Whisper transcript rather than failing the whole job over a lyrics lookup.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        resp = httpx.request(
            method, url, params=params, headers=headers, timeout=10.0
        )
    except httpx.HTTPError:
        return 0, None
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    return resp.status_code, payload


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _from_record(record: dict[str, Any], source: str) -> LyricsResult:
    """Build a :class:`LyricsResult` from an LRCLIB record dict."""
    if record.get("instrumental"):
        return LyricsResult(instrumental=True, source="instrumental")
    synced = _clean(record.get("syncedLyrics"))
    plain = _clean(record.get("plainLyrics"))
    if not synced and not plain:
        return LyricsResult(source="none")
    return LyricsResult(synced_lrc=synced, plain=plain, source=source)


def _score_candidate(
    record: dict[str, Any], track: str, duration: int | None
) -> tuple[int, float]:
    """Score a ``/api/search`` candidate. Higher is better.

    Ranks by (1) duration proximity bucket then (2) title-token overlap.
    Returns a tuple usable as a sort key (both ascending-friendly when negated).
    """
    # Duration proximity: within tolerance is best, then by absolute delta.
    within = 0
    delta = float("inf")
    rec_dur = record.get("duration")
    if duration is not None and rec_dur is not None:
        try:
            delta = abs(float(rec_dur) - float(duration))
        except (TypeError, ValueError):
            delta = float("inf")
        within = 1 if delta <= _DURATION_TOLERANCE_S else 0

    # Title-token overlap (cheap fuzzy match, no extra deps).
    want = {t for t in track.lower().split() if t}
    got = {t for t in str(record.get("trackName") or "").lower().split() if t}
    overlap = len(want & got)

    # Prefer candidates that actually carry synced lyrics.
    has_synced = 1 if _clean(record.get("syncedLyrics")) else 0

    # Sort key: maximize within-tolerance, then synced availability, then token
    # overlap, then minimize duration delta (negated so larger = better).
    return (within + has_synced + overlap, -delta)


class LyricsSource:
    """LRCLIB lookup client with an in-process cache.

    Test seam: pass ``http`` to inject a callable with the same shape as
    :func:`_default_http` so unit tests drive the lookup without the network.
    """

    def __init__(self, *, http: HttpFn | None = None, base_url: str = LRCLIB_BASE) -> None:
        self._http = http or _default_http
        self._base = base_url.rstrip("/")
        self._cache: dict[tuple[str, str, int | None], LyricsResult] = {}
        self._lock = threading.Lock()

    def _cache_key(
        self, artist: str | None, track: str | None, duration: int | None
    ) -> tuple[str, str, int | None]:
        return ((artist or "").strip().lower(), (track or "").strip().lower(), duration)

    def fetch(
        self,
        *,
        artist: str | None,
        track: str | None,
        album: str | None = None,
        duration: int | None = None,
    ) -> LyricsResult:
        """Look up lyrics for ``(artist, track[, album, duration])``.

        Tries the exact ``/api/get`` endpoint first, then the fuzzy
        ``/api/search`` fallback. Returns an empty :class:`LyricsResult`
        (``source="none"``) when LRCLIB has nothing — the caller then keeps the
        Whisper transcript. Results are cached by ``(artist, track, duration)``.
        """
        track = _clean(track)
        if not track:
            # Without at least a track name there is nothing to look up.
            return LyricsResult(source="none")
        artist = _clean(artist)
        album = _clean(album)

        key = self._cache_key(artist, track, duration)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        result = self._lookup(artist, track, album, duration)

        with self._lock:
            self._cache[key] = result
        return result

    # -- internals ----------------------------------------------------------
    def _lookup(
        self,
        artist: str | None,
        track: str,
        album: str | None,
        duration: int | None,
    ) -> LyricsResult:
        get_result = self._try_get(artist, track, album, duration)
        if get_result is not None and get_result.found:
            return get_result
        search_result = self._try_search(artist, track, duration)
        if search_result is not None and search_result.found:
            return search_result
        return LyricsResult(source="none")

    def _try_get(
        self,
        artist: str | None,
        track: str,
        album: str | None,
        duration: int | None,
    ) -> LyricsResult | None:
        """Exact match via ``GET /api/get``. ``artist_name``/``track_name`` are
        required by LRCLIB; we only call it when an artist is known."""
        if not artist:
            return None
        params: dict[str, Any] = {"artist_name": artist, "track_name": track}
        if album:
            params["album_name"] = album
        if duration is not None:
            params["duration"] = duration
        status, body = self._http("GET", f"{self._base}/api/get", params)
        if status == 200 and isinstance(body, dict):
            return _from_record(body, source="lrclib_get")
        return None

    def _try_search(
        self, artist: str | None, track: str, duration: int | None
    ) -> LyricsResult | None:
        """Fuzzy match via ``GET /api/search``; pick the best candidate."""
        params: dict[str, Any] = {"track_name": track}
        if artist:
            params["artist_name"] = artist
        status, body = self._http("GET", f"{self._base}/api/search", params)
        if status != 200 or not isinstance(body, list) or not body:
            return None
        candidates = [c for c in body if isinstance(c, dict)]
        if not candidates:
            return None
        best = max(candidates, key=lambda c: _score_candidate(c, track, duration))
        return _from_record(best, source="lrclib_search")
