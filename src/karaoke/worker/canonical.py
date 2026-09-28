"""Canonical track metadata via YouTube Music search (#281).

Uploaded videos carry the *upload's* metadata: a native-script artist, a
title with producer credits, and the video's duration (skit intro, live
cut). LRCLIB curates the *audio release*: Latin ``artistName``, clean
title, release duration. YouTube Music's song index bridges the two — an
unauthenticated ``search(query, filter="songs")`` returns the release's
artist names, title, ``videoId`` and ``duration_seconds``.

Best-effort by design: ``ytmusicapi`` parses an unofficial InnerTube
surface, so every failure path returns ``None`` and the caller proceeds with
the parsed metadata alone (today's behaviour). The search callable is
injectable so tests never touch the network.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from karaoke.textnorm import has_hebrew, tokens

log = logging.getLogger("karaoke.worker.canonical")

# A release whose length is further than this from the source video is a
# different recording (live, remix); the parsed metadata is safer.
_MAX_DURATION_DELTA_S = 60
_SEARCH_LIMIT = 5

SearchFn = Callable[[str], list[dict[str, Any]]]

_client_lock = threading.Lock()
_client: Any = None


def _default_search(query: str) -> list[dict[str, Any]]:
    global _client
    try:
        from ytmusicapi import YTMusic

        with _client_lock:
            if _client is None:
                _client = YTMusic()
        return list(_client.search(query, filter="songs", limit=_SEARCH_LIMIT) or [])
    except Exception as exc:  # noqa: BLE001 — unofficial API; any failure is a miss
        log.info("ytmusic search %r failed: %s", query, exc)
        return []


@dataclass(frozen=True, slots=True)
class CanonicalMeta:
    """Audio-release metadata for a track.

    * ``artist`` — primary artist as YouTube Music spells it (Latin for most
      Israeli releases: ``"Noa Kirel"`` for ``"נועה קירל"``).
    * ``artists`` — every credited artist, primary first.
    * ``title`` — release title (may be a different script than the upload).
    * ``duration`` — release length in seconds, or ``None``.
    * ``video_id`` — the release's ATV video id, for diagnostics.
    """

    artist: str
    artists: tuple[str, ...]
    title: str
    duration: int | None
    video_id: str | None


def _lang(text: str) -> str | None:
    return "he" if has_hebrew(text) else None


def _candidate(item: dict[str, Any]) -> CanonicalMeta | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    names = tuple(
        str(a.get("name") or "").strip()
        for a in item.get("artists") or []
        if isinstance(a, dict) and str(a.get("name") or "").strip()
    )
    if not title or not names:
        return None
    duration: int | None = None
    raw = item.get("duration_seconds")
    if raw is not None:
        try:
            duration = int(raw)
        except (TypeError, ValueError):
            duration = None
    return CanonicalMeta(
        artist=names[0],
        artists=names,
        title=title,
        duration=duration,
        video_id=str(item.get("videoId") or "") or None,
    )


def _plausible(
    cand: CanonicalMeta, artist: str | None, track: str, duration: int | None
) -> tuple[bool, float]:
    """``(accept, rank)`` — a candidate is plausible when its title, one of
    its artists, or its duration corroborates the upload, and its duration
    is not wildly off. Ranked by title overlap only; ties keep YouTube
    Music's own relevance order (the artist's release outranks covers and
    TV performances that carry the artist's name as a credit)."""
    track_tokens = set(tokens(track, _lang(track)))
    title_tokens = set(tokens(cand.title, _lang(cand.title)))
    title_overlap = len(track_tokens & title_tokens) / len(track_tokens) if track_tokens else 0.0
    artist_tokens = set(tokens(artist or "", _lang(artist or "")))
    cand_artist_tokens = set(tokens(" ".join(cand.artists), _lang(" ".join(cand.artists))))
    artist_overlap = (
        len(artist_tokens & cand_artist_tokens) / len(artist_tokens) if artist_tokens else 0.0
    )
    delta = (
        abs(cand.duration - duration)
        if cand.duration is not None and duration is not None
        else None
    )
    duration_ok = delta is not None and delta <= _MAX_DURATION_DELTA_S
    accept = title_overlap > 0 or artist_overlap > 0 or duration_ok
    if delta is not None and delta > _MAX_DURATION_DELTA_S:
        accept = False
    return accept, title_overlap


class CanonicalResolver:
    """Resolve ``(artist, track, duration)`` to release metadata, caching hits."""

    def __init__(self, *, search: SearchFn | None = None) -> None:
        self._search = search or _default_search
        self._cache: dict[tuple[str, str], CanonicalMeta] = {}
        self._lock = threading.Lock()

    def resolve(
        self, artist: str | None, track: str | None, duration: int | None = None
    ) -> CanonicalMeta | None:
        track = (track or "").strip()
        if not track:
            return None
        artist = (artist or "").strip() or None
        key = ((artist or "").casefold(), track.casefold())
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        query = f"{artist} {track}" if artist else track
        results = self._search(query)
        best: tuple[float, int, CanonicalMeta] | None = None
        for index, item in enumerate(results[:_SEARCH_LIMIT]):
            cand = _candidate(item)
            if cand is None:
                continue
            accept, rank = _plausible(cand, artist, track, duration)
            if accept and (best is None or (rank, -index) > (best[0], -best[1])):
                best = (rank, index, cand)
        if best is None:
            log.info("ytmusic: no plausible release for %r", query)
            return None
        with self._lock:
            self._cache[key] = best[2]
        return best[2]
