"""Genius lyrics as a second reference source (#281).

Genius has the cleanest plain text for fresh non-Latin releases that LRCLIB
either lacks or carries with typos. Two unofficial surfaces, both token-free:

* ``GET https://genius.com/api/search/multi?q=…`` — JSON with ``sections``
  of ``hits``; song hits carry ``result.url`` / ``title`` / ``artist_names``.
* the song page — lyrics live in ``div[data-lyrics-container="true"]``
  blocks; ``data-exclude-from-selection`` subtrees (contributor header,
  translation notes) are not lyrics.

Both can change or start blocking without notice, so every entry point
degrades to ``None`` and the caller carries on without a second source. The
HTTP transport is an injectable callable (same pattern as
:mod:`karaoke.worker.lyrics`) so tests never touch the network.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import httpx

from karaoke.textnorm import tokens

log = logging.getLogger("karaoke.worker.genius")

GENIUS_BASE = "https://genius.com"
# Genius serves an interstitial to obviously non-browser clients.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36"
)
_TIMEOUT_S = 10.0
_VOID_TAGS = frozenset({"br", "img", "hr", "input", "meta", "link", "wbr", "source"})
_SECTION_HEADER_RE = re.compile(r"^\s*\[[^\]]*\]\s*$")
_BIDI_RE = re.compile("[‎‏‪-‮⁦-⁩؜﻿]")
# "קנאית ,אובססיבית" — a comma glued to the *next* word by RTL editing.
_STRAY_COMMA_RE = re.compile(r"\s+([,;:!?.])(?=\S)")

# (method, url, params) -> (status, body_text). Network failures → (0, "").
TextHttpFn = Callable[[str, str, dict[str, Any] | None], tuple[int, str]]


def _default_text_http(
    method: str, url: str, params: dict[str, Any] | None
) -> tuple[int, str]:
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html,application/json"}
    try:
        resp = httpx.request(
            method, url, params=params, headers=headers, timeout=_TIMEOUT_S,
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return 0, ""
    return resp.status_code, resp.text


class _LyricsExtractor(HTMLParser):
    """Collect text from lyrics containers, skipping excluded subtrees."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[bool, bool]] = []  # (opens_container, opens_excluded)
        self._container = 0
        self._excluded = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            if tag == "br" and self._container and not self._excluded:
                self.parts.append("\n")
            return
        attr = dict(attrs)
        opens_container = attr.get("data-lyrics-container") == "true"
        opens_excluded = "data-exclude-from-selection" in attr
        if opens_container:
            if self._container == 0 and self.parts:
                self.parts.append("\n")
            self._container += 1
        if opens_excluded:
            self._excluded += 1
        self._stack.append((opens_container, opens_excluded))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and self._container and not self._excluded:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS or not self._stack:
            return
        opens_container, opens_excluded = self._stack.pop()
        if opens_container:
            self._container -= 1
        if opens_excluded:
            self._excluded -= 1

    def handle_data(self, data: str) -> None:
        if self._container and not self._excluded:
            self.parts.append(data)


def extract_lyrics_html(html: str) -> str:
    """Raw lyrics text from a Genius song page (section headers kept)."""
    parser = _LyricsExtractor()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)


def clean_text(raw: str) -> str:
    """Drop ``[Verse]``-style headers and RTL editing artefacts; collapse
    blank runs to a single empty line."""
    lines: list[str] = []
    for line in _BIDI_RE.sub("", raw).splitlines():
        line = _STRAY_COMMA_RE.sub(r"\1 ", line).strip()
        line = re.sub(r"\s{2,}", " ", line)
        if _SECTION_HEADER_RE.match(line):
            continue
        if not line and (not lines or not lines[-1]):
            continue
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class GeniusHit:
    url: str
    title: str
    artist_names: str


@dataclass(frozen=True, slots=True)
class GeniusResult:
    text: str
    url: str
    title: str
    artist_names: str


def _score_hit(hit: GeniusHit, track_tokens: set[str], artist_tokens: set[str], lang: str | None) -> float:
    title_tokens = set(tokens(hit.title, lang))
    if not track_tokens & title_tokens:
        return 0.0
    track_overlap = len(track_tokens & title_tokens) / len(track_tokens)
    artist_overlap = (
        len(artist_tokens & set(tokens(hit.artist_names, lang))) / len(artist_tokens)
        if artist_tokens
        else 0.0
    )
    return track_overlap + 0.5 * artist_overlap


class GeniusSource:
    """Search + fetch client with a success-only in-process cache."""

    def __init__(self, *, http: TextHttpFn | None = None, base_url: str = GENIUS_BASE) -> None:
        self._http = http or _default_text_http
        self._base = base_url.rstrip("/")
        self._cache: dict[tuple[str, str], GeniusResult] = {}
        self._lock = threading.Lock()

    def search(self, artist: str | None, track: str, *, lang: str | None = None) -> GeniusHit | None:
        """Best song hit for ``artist track``, or ``None`` when nothing shares
        a title token with ``track`` (wrong-song guard)."""
        query = " ".join(p for p in (artist, track) if p)
        status, body = self._http("GET", f"{self._base}/api/search/multi", {"q": query})
        if status != 200 or not body:
            log.info("genius search %r: HTTP %s", query, status)
            return None
        try:
            payload = json.loads(body)
        except ValueError:
            return None
        hits: list[GeniusHit] = []
        seen: set[str] = set()
        for section in (payload.get("response") or {}).get("sections") or []:
            for hit in section.get("hits") or []:
                result = hit.get("result") or {}
                url = result.get("url")
                if hit.get("type") != "song" or not url or url in seen:
                    continue
                seen.add(url)
                hits.append(
                    GeniusHit(
                        url=url,
                        title=str(result.get("title") or ""),
                        artist_names=str(result.get("artist_names") or ""),
                    )
                )
        track_tokens = set(tokens(track, lang))
        artist_tokens = set(tokens(artist or "", lang))
        if not track_tokens or not hits:
            return None
        scored = [(_score_hit(h, track_tokens, artist_tokens, lang), -i, h) for i, h in enumerate(hits)]
        score, _, best = max(scored)
        return best if score > 0 else None

    def fetch_url(self, url: str) -> str | None:
        """Cleaned lyrics text from a song page URL, or ``None``."""
        status, body = self._http("GET", url, None)
        if status != 200 or not body:
            log.info("genius page %s: HTTP %s", url, status)
            return None
        text = clean_text(extract_lyrics_html(body))
        return text or None

    def fetch(self, artist: str | None, track: str, *, lang: str | None = None) -> GeniusResult | None:
        key = ((artist or "").strip().casefold(), track.strip().casefold())
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        hit = self.search(artist, track, lang=lang)
        if hit is None:
            return None
        text = self.fetch_url(hit.url)
        if text is None:
            return None
        result = GeniusResult(text=text, url=hit.url, title=hit.title, artist_names=hit.artist_names)
        with self._lock:
            self._cache[key] = result
        return result
