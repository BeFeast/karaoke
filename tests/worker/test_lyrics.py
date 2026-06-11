"""Tests for LRCLIB lyrics sourcing.

All HTTP is mocked through the ``http`` injection seam — never touches the
network. Covers the cases issue #54 calls out:

  * exact synced hit (``/api/get``)
  * fuzzy fallback (``/api/get`` misses → ``/api/search`` best candidate)
  * plain-only (no synced lyrics)
  * ``instrumental: true``
  * no-match (both endpoints miss → empty result; caller keeps Whisper)
  * in-process caching by (artist, track, duration)
"""
from __future__ import annotations

from typing import Any

from karaoke.worker.lyrics import LyricsSource, whisper_segments_to_lrc

SYNCED_BODY = "[00:12.00]line one\n[00:15.50]line two"
PLAIN_BODY = "line one\nline two"


class _Recorder:
    """Replays a scripted sequence of HTTP responses; records every call."""

    def __init__(self, script: list[dict]) -> None:
        self.script = script
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, url: str, params: dict[str, Any] | None):
        self.calls.append((method, url, params))
        if not self.script:
            raise AssertionError(f"unscripted HTTP call: {method} {url} params={params!r}")
        step = self.script.pop(0)
        if step.get("expect_in") and step["expect_in"] not in url:
            raise AssertionError(f"expected url to contain {step['expect_in']!r}, got {url}")
        return step["code"], step["body"]


# ---------------------------------------------------------------------------
# 1. exact synced hit via /api/get
# ---------------------------------------------------------------------------
def test_get_returns_synced_lyrics():
    rec = _Recorder([
        {
            "expect_in": "/api/get",
            "code": 200,
            "body": {
                "syncedLyrics": SYNCED_BODY,
                "plainLyrics": PLAIN_BODY,
                "instrumental": False,
            },
        },
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track="Song", duration=200)

    assert result.synced_lrc == SYNCED_BODY
    assert result.plain == PLAIN_BODY
    assert result.instrumental is False
    assert result.source == "lrclib_get"
    assert result.found is True
    # /api/get carried the duration for the ±2s match.
    assert rec.calls[0][2]["duration"] == 200
    assert rec.calls[0][2]["artist_name"] == "Artist"
    # No fallback search was needed.
    assert len(rec.calls) == 1


# ---------------------------------------------------------------------------
# 2. fuzzy fallback — /api/get misses (404), /api/search picks best candidate
# ---------------------------------------------------------------------------
def test_search_fallback_picks_best_candidate():
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {
            "expect_in": "/api/search",
            "code": 200,
            "body": [
                # Wrong duration, no synced — should lose.
                {
                    "trackName": "Song",
                    "duration": 999,
                    "syncedLyrics": None,
                    "plainLyrics": "wrong",
                },
                # Right duration + synced — should win.
                {
                    "trackName": "Song",
                    "duration": 201,
                    "syncedLyrics": SYNCED_BODY,
                    "plainLyrics": PLAIN_BODY,
                },
            ],
        },
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track="Song", duration=200)

    assert result.synced_lrc == SYNCED_BODY
    assert result.source == "lrclib_search"
    assert result.found is True
    assert [c[1].rsplit("/", 1)[-1] for c in rec.calls] == ["get", "search"]


# ---------------------------------------------------------------------------
# 3. plain-only — record has plainLyrics but no syncedLyrics
# ---------------------------------------------------------------------------
def test_get_returns_plain_only():
    rec = _Recorder([
        {
            "expect_in": "/api/get",
            "code": 200,
            "body": {
                "syncedLyrics": None,
                "plainLyrics": PLAIN_BODY,
                "instrumental": False,
            },
        },
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track="Song", duration=200)

    assert result.synced_lrc is None
    assert result.plain == PLAIN_BODY
    assert result.source == "lrclib_get"
    assert result.found is True


# ---------------------------------------------------------------------------
# 4. instrumental: true
# ---------------------------------------------------------------------------
def test_get_instrumental_flag():
    rec = _Recorder([
        {
            "expect_in": "/api/get",
            "code": 200,
            "body": {
                "syncedLyrics": None,
                "plainLyrics": None,
                "instrumental": True,
            },
        },
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track="Song", duration=200)

    assert result.instrumental is True
    assert result.synced_lrc is None
    assert result.plain is None
    assert result.source == "instrumental"
    assert result.found is True


# ---------------------------------------------------------------------------
# 5. no match — both endpoints miss → empty result (caller keeps Whisper)
# ---------------------------------------------------------------------------
def test_no_match_returns_empty():
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track="Song", duration=200)

    assert result.found is False
    assert result.synced_lrc is None
    assert result.plain is None
    assert result.instrumental is False
    assert result.source == "none"


def test_network_failure_returns_empty():
    """A transport failure surfaces as (0, None) → miss, not an exception."""

    def boom(method, url, params):
        return 0, None

    src = LyricsSource(http=boom)
    result = src.fetch(artist="Artist", track="Song", duration=200)
    assert result.found is False
    assert result.source == "none"


# ---------------------------------------------------------------------------
# 6. caching — repeated fetch for same (artist, track, duration) hits no HTTP
# ---------------------------------------------------------------------------
def test_results_are_cached():
    rec = _Recorder([
        {
            "expect_in": "/api/get",
            "code": 200,
            "body": {"syncedLyrics": SYNCED_BODY, "plainLyrics": PLAIN_BODY},
        },
    ])
    src = LyricsSource(http=rec)
    first = src.fetch(artist="Artist", track="Song", duration=200)
    # Second call: scripted list is now empty — any HTTP call would raise.
    second = src.fetch(artist="Artist", track="Song", duration=200)

    assert first == second
    assert len(rec.calls) == 1, f"second fetch must be cached; calls={rec.calls!r}"


def test_cache_key_is_case_insensitive_on_names():
    rec = _Recorder([
        {
            "expect_in": "/api/get",
            "code": 200,
            "body": {"syncedLyrics": SYNCED_BODY, "plainLyrics": PLAIN_BODY},
        },
    ])
    src = LyricsSource(http=rec)
    src.fetch(artist="Artist", track="Song", duration=200)
    src.fetch(artist="ARTIST", track="song", duration=200)
    assert len(rec.calls) == 1


# ---------------------------------------------------------------------------
# 7. missing track / artist edge cases
# ---------------------------------------------------------------------------
def test_no_track_does_no_http():
    rec = _Recorder([])  # any call fails
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track=None, duration=200)
    assert result.found is False
    assert rec.calls == []


def test_no_artist_skips_get_and_uses_search():
    """Without an artist, /api/get is skipped (LRCLIB requires artist_name);
    we go straight to /api/search."""
    rec = _Recorder([
        {
            "expect_in": "/api/search",
            "code": 200,
            "body": [
                {
                    "trackName": "Song",
                    "duration": 200,
                    "syncedLyrics": SYNCED_BODY,
                    "plainLyrics": PLAIN_BODY,
                }
            ],
        },
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist=None, track="Song", duration=200)
    assert result.source == "lrclib_search"
    assert [c[1].rsplit("/", 1)[-1] for c in rec.calls] == ["search"]


# ---------------------------------------------------------------------------
# whisper_segments_to_lrc (#145): approximate LRC from Whisper segment stamps
# ---------------------------------------------------------------------------
def test_segments_to_lrc_formats_and_orders():
    segments = [
        {"start": 12.0, "end": 14.0, "text": " line one "},
        {"start": 75.345, "end": 78.0, "text": "line two"},
    ]
    assert whisper_segments_to_lrc(segments) == (
        "[00:12.00]line one\n[01:15.34]line two"
    )


def test_segments_to_lrc_sorts_out_of_order_input():
    shuffled = [
        {"start": 30.0, "text": "third"},
        {"start": 1.5, "text": "first"},
        {"start": 10.0, "text": "second"},
    ]
    body = whisper_segments_to_lrc(shuffled)
    assert body == "[00:01.50]first\n[00:10.00]second\n[00:30.00]third"
    # Stable: re-running on a differently ordered copy yields the same output.
    assert whisper_segments_to_lrc(list(reversed(shuffled))) == body


def test_segments_to_lrc_skips_unusable_segments():
    segments = [
        {"start": 1.0, "text": "   "},          # whitespace-only text
        {"start": 2.0, "text": ""},              # empty text
        {"start": 3.0},                           # no text at all
        {"text": "no start"},                    # no timestamp
        {"start": "abc", "text": "bad start"},  # non-numeric timestamp
        "not a dict",                             # not a segment
        {"start": -0.4, "text": "clamped"},     # negative start clamps to 0
        {"start": 4.0, "text": "kept"},
    ]
    assert whisper_segments_to_lrc(segments) == "[00:00.00]clamped\n[00:04.00]kept"


def test_segments_to_lrc_empty_inputs():
    assert whisper_segments_to_lrc(None) == ""
    assert whisper_segments_to_lrc([]) == ""
    assert whisper_segments_to_lrc([{"start": 1.0, "text": "  "}]) == ""
