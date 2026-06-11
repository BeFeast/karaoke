"""Tests for LRCLIB lyrics sourcing.

All HTTP is mocked through the ``http`` injection seam — never touches the
network. Covers the cases issue #54 calls out:

  * exact synced hit (``/api/get``)
  * fuzzy fallback (``/api/get`` misses → ``/api/search`` best candidate)
  * plain-only (no synced lyrics)
  * ``instrumental: true``
  * no-match (both endpoints miss → empty result; caller keeps Whisper)
  * in-process caching by (artist, track, duration)
  * duration hard-reject of the best ``/api/search`` candidate (#148)
  * text salvage from a duration-rejected candidate (``rejected_text``, #149)
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
# 8. duration hard-reject on the /api/search path (#148)
# ---------------------------------------------------------------------------
def _search_script(candidates: list[dict]) -> list[dict]:
    return [
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": candidates},
    ]


def test_search_rejects_best_candidate_on_duration_mismatch():
    """Best (and only) candidate is the wrong edit (delta > 5 s): the ENTIRE
    record is dropped — synced AND plain — so the pipeline falls through to
    the Whisper ASR floor, whose timings track the actual audio (#148/job #64)."""
    rec = _Recorder(_search_script([
        {
            # The canonical EP cut vs the 229 s official-video edit.
            "trackName": "Song",
            "duration": 257,
            "syncedLyrics": SYNCED_BODY,
            "plainLyrics": PLAIN_BODY,
        },
    ]))
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track="Song", duration=229)

    assert result.found is False
    assert result.source == "none"
    assert result.synced_lrc is None
    assert result.plain is None
    assert result.rejected == "duration_mismatch (28s)"
    # The candidate's text is salvaged for force-alignment (#149).
    assert result.rejected_text == PLAIN_BODY


def test_search_reject_drops_instrumental_flag_too():
    """A wrong-edit record's ``instrumental`` flag must not silence lyrics."""
    rec = _Recorder(_search_script([
        {"trackName": "Song", "duration": 400, "instrumental": True},
    ]))
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track="Song", duration=229)

    assert result.instrumental is False
    assert result.source == "none"
    assert result.rejected == "duration_mismatch (171s)"


def test_search_accepts_candidate_at_reject_threshold():
    """Delta == 5 s (the threshold itself) is still accepted — only > 5 rejects."""
    rec = _Recorder(_search_script([
        {
            "trackName": "Song",
            "duration": 234,
            "syncedLyrics": SYNCED_BODY,
            "plainLyrics": PLAIN_BODY,
        },
    ]))
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track="Song", duration=229)

    assert result.source == "lrclib_search"
    assert result.synced_lrc == SYNCED_BODY
    assert result.rejected is None


def test_search_keeps_candidate_when_duration_unknown():
    """No duration on the candidate, or none for the actual audio → the edit
    can't be judged, so missing data never rejects (today's behavior)."""
    # Candidate carries no duration.
    rec = _Recorder(_search_script([
        {"trackName": "Song", "syncedLyrics": SYNCED_BODY, "plainLyrics": PLAIN_BODY},
    ]))
    result = LyricsSource(http=rec).fetch(artist="Artist", track="Song", duration=229)
    assert result.source == "lrclib_search"
    assert result.rejected is None

    # Actual duration unknown; candidate duration wildly off would-be-rejected.
    rec = _Recorder(_search_script([
        {
            "trackName": "Song",
            "duration": 999,
            "syncedLyrics": SYNCED_BODY,
            "plainLyrics": PLAIN_BODY,
        },
    ]))
    result = LyricsSource(http=rec).fetch(artist="Artist", track="Song", duration=None)
    assert result.source == "lrclib_search"
    assert result.rejected is None


# ---------------------------------------------------------------------------
# 9. text salvage from a duration-rejected candidate (#149)
# ---------------------------------------------------------------------------
def test_reject_salvages_plain_text_as_is():
    """Plain text on the rejected record passes through verbatim."""
    rec = _Recorder(_search_script([
        {
            "trackName": "Song",
            "duration": 257,
            "syncedLyrics": SYNCED_BODY,
            "plainLyrics": "  line one\nline two  ",
        },
    ]))
    result = LyricsSource(http=rec).fetch(artist="Artist", track="Song", duration=229)

    assert result.rejected == "duration_mismatch (28s)"
    assert result.rejected_text == "line one\nline two"
    # #148 reject semantics unchanged: still a miss for precedence purposes.
    assert result.found is False
    assert result.source == "none"


def test_reject_salvages_synced_only_with_timestamps_stripped():
    """A synced-only rejected record yields its text with timestamps stripped —
    the timings belong to the wrong edit; only the words are worth keeping."""
    rec = _Recorder(_search_script([
        {"trackName": "Song", "duration": 257, "syncedLyrics": SYNCED_BODY},
    ]))
    result = LyricsSource(http=rec).fetch(artist="Artist", track="Song", duration=229)

    assert result.rejected == "duration_mismatch (28s)"
    assert result.rejected_text == PLAIN_BODY
    assert "[" not in result.rejected_text


def test_reject_without_text_salvages_nothing():
    """A rejected record with no lyrics (e.g. instrumental-flagged) has nothing
    to salvage — ``rejected_text`` stays None and the floor applies."""
    rec = _Recorder(_search_script([
        {"trackName": "Song", "duration": 400, "instrumental": True},
    ]))
    result = LyricsSource(http=rec).fetch(artist="Artist", track="Song", duration=229)

    assert result.rejected == "duration_mismatch (171s)"
    assert result.rejected_text is None


def test_accepted_candidate_has_no_rejected_text():
    """Within the duration threshold nothing is rejected, so nothing is salvaged."""
    rec = _Recorder(_search_script([
        {
            "trackName": "Song",
            "duration": 231,
            "syncedLyrics": SYNCED_BODY,
            "plainLyrics": PLAIN_BODY,
        },
    ]))
    result = LyricsSource(http=rec).fetch(artist="Artist", track="Song", duration=229)

    assert result.source == "lrclib_search"
    assert result.rejected is None
    assert result.rejected_text is None


def test_get_path_unaffected_by_duration_reject():
    """/api/get already matches duration ±2 s server-side; the client-side
    hard reject applies only to /api/search candidates."""
    rec = _Recorder([
        {
            "expect_in": "/api/get",
            "code": 200,
            "body": {
                "duration": 999,  # whatever the record claims, /api/get wins
                "syncedLyrics": SYNCED_BODY,
                "plainLyrics": PLAIN_BODY,
                "instrumental": False,
            },
        },
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track="Song", duration=229)

    assert result.source == "lrclib_get"
    assert result.synced_lrc == SYNCED_BODY
    assert result.rejected is None
    assert len(rec.calls) == 1


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
