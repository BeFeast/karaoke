"""Web lyric references for tracks LRCLIB does not cover (#283).

Operator decision 3 for #279: a web reference (anything that is not LRCLIB)
never replaces the transcript. The ASR stays the base, the reference only
corrects words in lines the singer was heard singing (see
:mod:`karaoke.worker.asr_reference`). This module only *finds* the text.

Cascade, first hit wins:

1. **YouTube Music lyrics** for the canonical release (``videoId`` from
   :mod:`karaoke.worker.canonical`): ``get_watch_playlist`` → lyrics
   ``browseId`` → ``get_lyrics(timestamps=True)`` (Musixmatch / LyricFind).
2. **Genius** (:class:`karaoke.worker.genius.GeniusSource`).
3. **tab4u** — found through SearXNG (``site:tab4u.com``), parsed from
   ``#songContentTPL td.song_only``.

:meth:`WebReferenceFinder.find_by_text` is the post-ASR rung (#262): a
confident transcript line, quoted, searched through SearXNG and resolved on
the same whitelisted sites.

Shironet sits behind Radware and nagnu renders lyrics client-side; both are
treated as misses. Every network failure is a miss; nothing here raises.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from karaoke.textnorm import tokens
from karaoke.worker.genius import (
    GeniusSource,
    TextHttpFn,
    _default_text_http,
    clean_text,
    extract_lyrics_html,
)

log = logging.getLogger("karaoke.worker.web_reference")

PROVIDER_YTMUSIC = "ytmusic"
PROVIDER_GENIUS = "genius"
PROVIDER_TAB4U = "tab4u"

_TAB4U_URL_RE = re.compile(r"^https?://(?:www\.)?tab4u\.com/(?:lyrics|tabs)/songs/[^?#]+\.html")
_GENIUS_URL_RE = re.compile(r"^https?://(?:www\.)?genius\.com/[A-Za-z0-9-]+-lyrics$")
_MIN_LINES = 4
_TAB4U_NOISE_RE = re.compile(r":\s*$|^\s*[x×]\s*\d+\s*$|\bx\d{3,}\b|^[A-G][#b]?\w*\d*\s*:")
_TAB4U_SLUG_RE = re.compile(r"/songs/\d+_(?P<artist>.+?)_-_(?P<title>.+?)\.html")
_MUSIXMATCH_RE = re.compile(r"musixmatch\.com/lyrics/(?P<artist>[^/]+)/(?P<title>[^/?#]+)")
_GENIUS_TITLE_RE = re.compile(r"^(?P<a>.+?)\s+[–-]\s+(?P<t>.+?)\s+Lyrics\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class WebReference:
    """Reference lyrics from the web.

    * ``text`` — plain lines (blank lines between stanzas allowed).
    * ``provider`` — ``ytmusic`` / ``genius`` / ``tab4u``.
    * ``url`` — page or ``ytmusic:<browseId>`` for provenance.
    * ``synced_lrc`` — only when the provider carried timestamps (YouTube
      Music / Musixmatch); informational, the policy does not use it.
    """

    text: str
    provider: str
    url: str | None = None
    synced_lrc: str | None = None


# (videoId) -> (plain text, synced lrc or None, browse id) — None on a miss.
YtmLyricsFn = Callable[[str], tuple[str, str | None, str] | None]

_ytm_lock = threading.Lock()
_ytm_client: Any = None


def _fmt_ms(ms: int) -> str:
    seconds = max(0, int(ms)) / 1000.0
    return f"[{int(seconds // 60):02d}:{seconds % 60:05.2f}]"


def _default_ytm_lyrics(video_id: str) -> tuple[str, str | None, str] | None:
    global _ytm_client
    try:
        from ytmusicapi import YTMusic

        with _ytm_lock:
            if _ytm_client is None:
                _ytm_client = YTMusic()
        watch = _ytm_client.get_watch_playlist(videoId=video_id)
        browse_id = (watch or {}).get("lyrics")
        if not browse_id:
            return None
        found = _ytm_client.get_lyrics(browse_id, timestamps=True) or {}
        body = found.get("lyrics")
        if not body:
            return None
        if found.get("hasTimestamps") and isinstance(body, list):
            lines = [str(getattr(ln, "text", "") or "").strip() for ln in body]
            lrc = "\n".join(
                f"{_fmt_ms(getattr(ln, 'start_time', 0))}{str(getattr(ln, 'text', '')).strip()}"
                for ln in body
                if str(getattr(ln, "text", "") or "").strip() not in ("", "♪")
            )
            text = "\n".join(ln for ln in lines if ln and ln != "♪")
            return text, lrc or None, str(browse_id)
        return str(body), None, str(browse_id)
    except Exception as exc:  # noqa: BLE001 — unofficial API; any failure is a miss
        log.info("ytmusic lyrics for %s failed: %s", video_id, exc)
        return None


class _Tab4uExtractor(HTMLParser):
    """Lyric lines from ``#songContentTPL``: each ``td.song_only`` is a line,
    a ``<br>`` between the line tables is a stanza break. Chord cells
    (``td.chords``) are skipped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0  # >0 inside #songContentTPL
        self._in_line = False
        self._buf: list[str] = []
        self.lines: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if self._depth == 0:
            if tag == "div" and attr.get("id") == "songContentTPL":
                self._depth = 1
            return
        if tag == "div":
            self._depth += 1
        elif tag == "td" and "song_only" in (attr.get("class") or "").split():
            self._in_line = True
            self._buf = []
        elif tag == "br" and not self._in_line and self.lines and self.lines[-1] != "":
            self.lines.append("")

    def handle_endtag(self, tag: str) -> None:
        if self._depth == 0:
            return
        if tag == "td" and self._in_line:
            self._in_line = False
            line = " ".join("".join(self._buf).split())
            if line:
                self.lines.append(line)
        elif tag == "div":
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_line:
            self._buf.append(data.replace("\xa0", " "))


def extract_tab4u(html: str) -> str:
    parser = _Tab4uExtractor()
    parser.feed(html)
    parser.close()
    # Section labels ("פתיחה:", "המשך פתיחה:"), repeat marks ("x5") and
    # tablature rows ("A47: x02030") are page structure, not lyrics.
    lines = [ln for ln in parser.lines if ln == "" or not _TAB4U_NOISE_RE.search(ln)]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _enough(text: str | None) -> bool:
    return bool(text) and len([ln for ln in text.splitlines() if ln.strip()]) >= _MIN_LINES


def _title_evidence(page_title: str, track: str, lang: str | None) -> bool:
    want = set(tokens(track, lang))
    return bool(want) and bool(want & set(tokens(page_title, lang)))


class WebReferenceFinder:
    """The cascade above, with injectable transports for tests."""

    def __init__(
        self,
        *,
        ytm_lyrics: YtmLyricsFn | None = None,
        genius: GeniusSource | None = None,
        http: TextHttpFn | None = None,
        searxng_url: str | None = None,
    ) -> None:
        self._ytm = ytm_lyrics or _default_ytm_lyrics
        self._http = http or _default_text_http
        self._genius = genius or GeniusSource(http=self._http)
        self._searxng = (searxng_url or "").rstrip("/") or None

    # -- cascade ---------------------------------------------------------------
    def find(
        self,
        *,
        artist: str | None,
        track: str | None,
        video_id: str | None = None,
        latin_artist: str | None = None,
        lang: str | None = None,
    ) -> WebReference | None:
        if video_id:
            got = self._ytm(video_id)
            if got is not None and _enough(got[0]):
                return WebReference(text=got[0], provider=PROVIDER_YTMUSIC,
                                    url=f"ytmusic:{got[2]}", synced_lrc=got[1])
        if track:
            for who in dict.fromkeys(a for a in (latin_artist, artist) if a):
                hit = self._genius.fetch(who, track, lang=lang)
                if hit is not None and _enough(hit.text):
                    return WebReference(text=hit.text, provider=PROVIDER_GENIUS, url=hit.url)
            if self._searxng:
                query = " ".join(p for p in (artist, track) if p)
                ref = self._searx_sites(f"{query} site:tab4u.com", track, lang)
                if ref is not None:
                    return ref
        return None

    def find_by_text(self, line: str, *, lang: str | None = None) -> WebReference | None:
        """Post-ASR rung (#262): search a confident transcript line, quoted.
        The page must contain the line (normalised) to count."""
        if not self._searxng or len(line.split()) < 4:
            return None
        suffix = " מילים" if lang == "he" else " lyrics"
        return self._searx_sites(f'"{line}"{suffix}', None, lang, must_contain=line)

    def identify_by_text(self, line: str, *, lang: str | None = None, limit: int = 3) -> list[tuple[str, str]]:
        """Post-ASR (#262): ``(artist, title)`` candidates for the song a
        transcript line comes from, read off search-result URLs / titles
        (tab4u and Musixmatch slugs, Genius page titles). LRCLIB cannot
        search lyric text, but it can search the title this finds."""
        if not self._searxng or len(line.split()) < 4:
            return []
        suffix = " מילים" if lang == "he" else " lyrics"
        status, body = self._http("GET", f"{self._searxng}/search", {"q": f'"{line}"{suffix}', "format": "json"})
        if status != 200 or not body:
            return []
        try:
            results = json.loads(body).get("results") or []
        except ValueError:
            return []
        found: list[tuple[str, str]] = []
        for result in results[:10]:
            url = urllib.parse.unquote(str(result.get("url") or ""))
            title = str(result.get("title") or "")
            match = _TAB4U_SLUG_RE.search(url) or _MUSIXMATCH_RE.search(url)
            pair = None
            if match:
                pair = (match["artist"].replace("_", " ").replace("-", " "),
                        match["title"].replace("_", " ").replace("-", " "))
            elif "genius.com" in url and (g := _GENIUS_TITLE_RE.match(title)):
                pair = (g["a"], g["t"])
            if pair and pair[1].strip() and pair not in found:
                found.append((pair[0].strip(), pair[1].strip()))
            if len(found) >= limit:
                break
        return found

    # -- internals ---------------------------------------------------------------
    def _searx_sites(
        self, query: str, track: str | None, lang: str | None, *, must_contain: str | None = None
    ) -> WebReference | None:
        status, body = self._http("GET", f"{self._searxng}/search", {"q": query, "format": "json"})
        if status != 200 or not body:
            return None
        try:
            results = json.loads(body).get("results") or []
        except ValueError:
            return None
        for result in results[:10]:
            url = str(result.get("url") or "")
            title = str(result.get("title") or "")
            if track and not _title_evidence(title, track, lang) and not _title_evidence(
                urllib.parse.unquote(url), track, lang
            ):
                continue
            ref = self._fetch_site(url)
            if ref is None:
                continue
            if must_contain and " ".join(tokens(must_contain, lang)) not in " ".join(tokens(ref.text, lang)):
                continue
            return ref
        return None

    def _fetch_site(self, url: str) -> WebReference | None:
        if _TAB4U_URL_RE.match(url):
            lyrics_url = url.replace("/tabs/songs/", "/lyrics/songs/").split("?")[0]
            status, html = self._http("GET", lyrics_url, None)
            text = extract_tab4u(html) if status == 200 and html else ""
            return WebReference(text=text, provider=PROVIDER_TAB4U, url=lyrics_url) if _enough(text) else None
        if _GENIUS_URL_RE.match(url):
            status, html = self._http("GET", url, None)
            text = clean_text(extract_lyrics_html(html)) if status == 200 and html else ""
            return WebReference(text=text, provider=PROVIDER_GENIUS, url=url) if _enough(text) else None
        return None
