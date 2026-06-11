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
     the best candidate by title similarity + duration proximity. A best
     candidate whose duration is more than ``_DURATION_REJECT_S`` away from
     the actual audio is rejected outright (#148): it is the wrong edit/cut,
     and its synced timings would drift against our track — strictly worse
     than the Whisper ASR floor, which is timed against the real audio.

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
# Hard ceiling for /api/search candidates (#148): a best candidate further
# than this from the actual audio duration is the wrong edit/cut, so the whole
# record is rejected (text included) and the pipeline falls through to the
# Whisper ASR floor. Does not apply to /api/get — LRCLIB enforces ±2s there.
_DURATION_REJECT_S = 5

# Lyrics-source provenance values recorded in metadata.json.
SOURCE_LRCLIB_SYNCED = "lrclib_synced"
# LRCLIB plain text that we force-aligned against the vocal stem into a synced
# LRC inside the GPU job window (#55). Ranks just below native LRCLIB synced.
SOURCE_FORCED_ALIGNED = "forced_aligned"
SOURCE_LRCLIB_PLAIN = "lrclib_plain"
# Whisper ASR transcript with an approximate LRC synthesized from the Whisper
# segment timestamps (#145). Ranks below ``forced_aligned``: it only applies
# when LRCLIB missed entirely, so any LRCLIB-derived source always wins.
SOURCE_WHISPER_ASR_SYNCED = "whisper_asr_synced"
SOURCE_WHISPER_ASR = "whisper_asr"
SOURCE_INSTRUMENTAL = "instrumental"


def whisper_segments_to_lrc(segments: list[dict[str, Any]] | None) -> str:
    """Build an approximate line-level LRC from Whisper segment timestamps (#145).

    ``segments`` is the ``"segments"`` list of the GPU job's ``lyrics.json``
    (faster-whisper output): ``{"start": float, "end": float, "text": str, ...}``
    per entry. One LRC line is emitted per segment, tagged ``[mm:ss.xx]`` with
    the segment *start* time — approximate but good enough for synced highlight
    on tracks where LRCLIB has nothing.

    Pure + tolerant by design: segments are sorted by start time (out-of-order
    input yields stable output), entries that are not dicts, carry no numeric
    ``start``, or have empty/whitespace text are skipped, and a negative start
    clamps to zero. Returns ``""`` when nothing usable remains — the caller
    then keeps the untimed ASR floor.
    """
    timed: list[tuple[float, str]] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(seg["start"])
        except (KeyError, TypeError, ValueError):
            continue
        timed.append((max(start, 0.0), text))
    timed.sort(key=lambda item: item[0])
    return "\n".join(f"{_fmt_lrc_timestamp(start)}{text}" for start, text in timed)


def _fmt_lrc_timestamp(seconds: float) -> str:
    """Format ``seconds`` as an LRC ``[mm:ss.xx]`` timestamp tag."""
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"[{minutes:02d}:{rem:05.2f}]"


@dataclass(frozen=True, slots=True)
class LyricsResult:
    """Outcome of an LRCLIB lookup.

    * ``synced_lrc`` — time-synced ``.lrc`` body, or ``None``.
    * ``plain`` — plain-text lyrics, or ``None``.
    * ``instrumental`` — LRCLIB flagged the track as instrumental (no lyrics).
    * ``source`` — where the data came from for provenance / logging:
      ``"lrclib_get"``, ``"lrclib_search"``, ``"instrumental"`` or ``"none"``.
    * ``rejected`` — why a record LRCLIB *did* return was dropped anyway
      (e.g. ``"duration_mismatch (28s)"``, #148), or ``None``. Only ever set
      alongside ``source="none"``; surfaced in ``metadata.json`` provenance.
    """

    synced_lrc: str | None = None
    plain: str | None = None
    instrumental: bool = False
    source: str = "none"
    rejected: str | None = None

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


def _duration_delta(record: dict[str, Any], duration: int | None) -> float | None:
    """Absolute duration delta (seconds) between a candidate and the actual
    audio, or ``None`` when either side is unknown/unparseable."""
    rec_dur = record.get("duration")
    if duration is None or rec_dur is None:
        return None
    try:
        return abs(float(rec_dur) - float(duration))
    except (TypeError, ValueError):
        return None


def _score_candidate(
    record: dict[str, Any], track: str, duration: int | None
) -> tuple[int, float]:
    """Score a ``/api/search`` candidate. Higher is better.

    Ranks by (1) duration proximity bucket then (2) title-token overlap.
    Returns a tuple usable as a sort key (both ascending-friendly when negated).
    """
    # Duration proximity: within tolerance is best, then by absolute delta.
    delta = _duration_delta(record, duration)
    within = 1 if delta is not None and delta <= _DURATION_TOLERANCE_S else 0
    if delta is None:
        delta = float("inf")

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
        # Keep a duration-rejected result (#148) as-is: it carries the
        # rejection reason for metadata.json provenance.
        if search_result is not None and (search_result.found or search_result.rejected):
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
        # Hard reject (#148): a best candidate too far from the actual audio
        # duration is the wrong edit/cut — its synced timings would drift, and
        # its text covers the wrong cut too. Drop the entire record so the
        # pipeline falls through to the Whisper ASR floor (timed against the
        # real audio). Unknown durations (either side) are never rejected.
        delta = _duration_delta(best, duration)
        if delta is not None and delta > _DURATION_REJECT_S:
            return LyricsResult(
                source="none",
                rejected=f"duration_mismatch ({round(delta, 1):g}s)",
            )
        return _from_record(best, source="lrclib_search")
