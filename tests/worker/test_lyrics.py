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

from karaoke.titles import parse_artist_track
from karaoke.worker.lyrics import (
    LRC_WORD_TAG_RE,
    _MAX_LADDER_QUERIES,
    LyricsSource,
    lrc_to_plain,
    merge_lrclib_word_tags,
    whisper_segments_to_lrc,
)

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
# 10. fallback cleanup ladder + artist-free duration-gated retry (#230)
# ---------------------------------------------------------------------------
# The job-#126 title: yt-dlp parsed artist="Little Big", track="«Конь». …",
# so both /api/get and the artist-scoped /api/search miss. The ladder cleans
# the track to "Конь" and retries artist-free — LRCLIB curates it under Любэ,
# duration 202.59 s vs the 203 s source (an exact-duration synced hit).
_KON_TITLE = "Little Big — «Конь». Голубой Ургант. Фрагмент выпуска от 30.12.2018"
_KON_RECORD = {
    "trackName": "Конь",
    "artistName": "Любэ",
    "duration": 202.59,
    "syncedLyrics": SYNCED_BODY,
    "plainLyrics": PLAIN_BODY,
}


def test_kon_title_resolves_via_artist_free_ladder():
    """The exact job-#126 title → an LRCLIB synced hit through the ladder."""
    parsed = parse_artist_track(_KON_TITLE)
    assert parsed.artist == "Little Big"
    assert parsed.track.startswith("«Конь»")

    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        # Artist-scoped search still misses (wrong artist for this cut).
        {"expect_in": "/api/search", "code": 200, "body": []},
        # Artist-free q=Конь finds the curated Любэ record — duration matches.
        {"expect_in": "/api/search", "code": 200, "body": [_KON_RECORD]},
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist=parsed.artist, track=parsed.track, duration=203)

    assert result.source == "lrclib_search"
    assert result.synced_lrc == SYNCED_BODY
    assert result.found is True
    # The winning variant is recorded for metadata.json debuggability.
    assert result.match_variant == "Конь"
    # The retry was artist-free and carried the cleaned track as ``q``.
    assert rec.calls[-1][2] == {"q": "Конь"}
    # Bounded: /api/get + artist search + exactly one ladder query.
    assert len(rec.calls) == 3


def test_ladder_miss_falls_through_to_floor():
    """Every variant misses (wrong-duration artist-free candidate is rejected) →
    empty result, so the pipeline keeps today's Whisper ASR floor."""
    parsed = parse_artist_track(_KON_TITLE)
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        # A same-titled but wrong-duration record must NOT be trusted artist-free.
        {
            "expect_in": "/api/search",
            "code": 200,
            "body": [{**_KON_RECORD, "duration": 999}],
        },
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist=parsed.artist, track=parsed.track, duration=203)

    assert result.found is False
    assert result.source == "none"
    assert result.match_variant is None
    assert len(rec.calls) == 3


def test_ladder_is_bounded_and_queries_each_variant():
    """A track yielding two cleaned variants issues one artist-free query each —
    ≤ _MAX_LADDER_QUERIES extra HTTP calls, in ladder order."""
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/search", "code": 200, "body": []},
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="X", track="«Song. Extra». Tail", duration=200)

    assert result.found is False
    ladder_calls = [c[2] for c in rec.calls if c[2] and "q" in c[2]]
    assert ladder_calls == [{"q": "Song. Extra"}, {"q": "Song"}]
    assert len(ladder_calls) <= _MAX_LADDER_QUERIES


def test_ladder_skipped_without_duration():
    """Artist-free matches rely solely on the duration gate; with no known audio
    duration the ladder is skipped entirely (no extra HTTP calls)."""
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="X", track="«Конь». Голубой Ургант", duration=None)

    assert result.found is False
    assert len(rec.calls) == 2  # get + artist search only; no ladder


def test_ladder_not_run_when_primary_search_hits():
    """A direct artist-scoped hit returns before the ladder — even when the
    track has cleanable variants — so no extra queries are issued."""
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {
            "expect_in": "/api/search",
            "code": 200,
            "body": [
                {
                    "trackName": "«Song». Live",
                    "duration": 201,
                    "syncedLyrics": SYNCED_BODY,
                    "plainLyrics": PLAIN_BODY,
                }
            ],
        },
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="Artist", track="«Song». Live at X", duration=200)

    assert result.source == "lrclib_search"
    assert result.match_variant is None
    assert len(rec.calls) == 2  # a 3rd (ladder) call would raise "unscripted"


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


# ---------------------------------------------------------------------------
# Enhanced LRC word tags + mega-segment splitting (#219)
# ---------------------------------------------------------------------------
def test_segments_to_lrc_emits_word_tags():
    """A segment with per-word timings → one Enhanced-LRC line: inline
    ``<start>word`` tags plus a trailing ``<end>`` sung-end tag. The
    faster-whisper leading space in ``word`` is stripped."""
    segments = [
        {
            "start": 12.0,
            "end": 15.5,
            "text": "hello world",
            "words": [
                {"start": 12.0, "end": 12.8, "word": " hello", "probability": 0.9},
                {"start": 13.0, "end": 15.5, "word": " world", "probability": 0.8},
            ],
        }
    ]
    assert whisper_segments_to_lrc(segments) == (
        "[00:12.00]<00:12.00>hello <00:13.00>world <00:15.50>"
    )


def test_segments_to_lrc_splits_mega_segment_on_word_gaps():
    """A degenerate segment spanning 40 s → 190 s of four tight word clusters
    separated by long silences splits into one line per cluster: each cluster is
    < 12 s while the whole segment is > 12 s (bug 2)."""

    def cluster(base: int) -> list[dict]:
        # Four words 2 s apart, each 1.8 s long → 0.2 s intra-cluster gaps,
        # a 7.8 s cluster span; the inter-cluster silences are > 1 s.
        return [
            {"start": base + 2 * i, "end": base + 2 * i + 1.8, "word": f" w{base}_{i}"}
            for i in range(4)
        ]

    words = cluster(40) + cluster(90) + cluster(140) + cluster(182)
    # Pin the reported 40 → 190 s span exactly at the edges.
    words[0]["start"] = 40.0
    words[-1]["end"] = 190.0
    seg = {"start": 40.0, "end": 190.0, "text": "forty words", "words": words}

    lines = whisper_segments_to_lrc([seg]).split("\n")
    assert len(lines) == 4
    # Each sub-line's line tag is its cluster's first-word start.
    assert [ln[:10] for ln in lines] == [
        "[00:40.00]",
        "[01:30.00]",
        "[02:20.00]",
        "[03:02.00]",
    ]
    # First line carries word tags + the cluster's sung-end tag.
    assert lines[0] == (
        "[00:40.00]<00:40.00>w40_0 <00:42.00>w40_1 "
        "<00:44.00>w40_2 <00:46.00>w40_3 <00:47.80>"
    )
    # Last line ends on the pinned 190 s (03:10.00) sung-end tag.
    assert lines[3].endswith("<03:10.00>")


def test_segments_to_lrc_long_segment_without_wide_gaps_stays_one_line():
    """Over-long span but every inter-word gap < 1 s → nothing to split on, so a
    single (long) Enhanced-LRC line is emitted (split precondition unmet)."""
    words = [{"start": float(i), "end": i + 0.9, "word": f" x{i}"} for i in range(16)]
    seg = {"start": 0.0, "end": 15.9, "text": "long", "words": words}

    body = whisper_segments_to_lrc([seg])
    assert "\n" not in body  # 15.9 s span, but no gap >= 1 s to break on
    assert body.startswith("[00:00.00]<00:00.00>x0 ")
    assert body.endswith("<00:15.90>")


def test_segments_to_lrc_short_segment_not_split_despite_wide_gaps():
    """A wide gap alone never splits: a <= 12 s segment stays one line even with
    a multi-second inter-word silence."""
    seg = {
        "start": 0.0,
        "end": 6.0,
        "text": "a b",
        "words": [
            {"start": 0.0, "end": 1.0, "word": " a"},
            {"start": 5.0, "end": 6.0, "word": " b"},  # 4 s gap, span only 6 s
        ],
    }
    assert whisper_segments_to_lrc([seg]) == (
        "[00:00.00]<00:00.00>a <00:05.00>b <00:06.00>"
    )


def test_segments_to_lrc_mixed_word_and_wordless_segments():
    """Word-tagged and plain lines coexist in one body (mixed files are valid),
    and plain-text derivation drops both tag kinds."""
    segments = [
        {"start": 0.0, "end": 2.0, "text": "no words here"},  # word-less → plain
        {
            "start": 10.0,
            "end": 12.0,
            "text": "with words",
            "words": [
                {"start": 10.0, "end": 10.5, "word": " with"},
                {"start": 11.0, "end": 12.0, "word": " words"},
            ],
        },
    ]
    body = whisper_segments_to_lrc(segments)
    assert body == (
        "[00:00.00]no words here\n"
        "[00:10.00]<00:10.00>with <00:11.00>words <00:12.00>"
    )
    assert lrc_to_plain(body) == "no words here\nwith words"


def test_segments_to_lrc_malformed_words_fall_back_to_plain_line():
    """Any malformed ``words`` entry demotes the whole segment to today's plain
    line at ``seg["start"]`` rather than raising."""
    bad_word_sets = [
        [{"start": 5.0, "word": " a"}],                      # missing "end"
        [{"start": "x", "end": 6.0, "word": " a"}],          # non-numeric start
        [{"start": float("nan"), "end": 6.0, "word": " a"}],  # NaN start
        [{"start": 5.0, "end": float("inf"), "word": " a"}],  # infinite end
        [{"start": "nan", "end": 6.0, "word": " a"}],        # "nan" string
        [{"start": 5.0, "end": 6.0, "word": "   "}],         # empty word text
        [{"start": 5.0, "end": 6.0, "word": " a"}, "nope"],  # non-dict entry
        [],                                                   # empty words list
        "not a list",                                        # words not a list
    ]
    for words in bad_word_sets:
        seg = {"start": 5.0, "end": 6.0, "text": "fallback", "words": words}
        assert whisper_segments_to_lrc([seg]) == "[00:05.00]fallback", words


def test_segments_to_lrc_non_finite_segment_start_is_skipped():
    """A word-less (or words-demoted) segment with a NaN/Infinity ``start``
    is skipped instead of reaching timestamp formatting."""
    segs = [
        {"start": float("nan"), "end": 6.0, "text": "gone"},
        {"start": float("inf"), "end": 6.0, "text": "gone too"},
        {
            "start": float("nan"),
            "end": 6.0,
            "text": "demoted",
            "words": [{"bad": 1}],
        },
        {"start": 1.0, "end": 2.0, "text": "kept"},
    ]
    assert whisper_segments_to_lrc(segs) == "[00:01.00]kept"


def test_segments_to_lrc_malformed_words_without_start_are_skipped():
    """Malformed words demote to the word-less path, which then skips the
    segment entirely when it also lacks a numeric ``start`` (today's rule)."""
    seg = {"end": 6.0, "text": "gone", "words": [{"bad": 1}]}
    assert whisper_segments_to_lrc([seg]) == ""


def test_segments_to_lrc_word_tags_clamp_negative_times():
    """Negative word/segment times clamp to zero in both line and word tags."""
    seg = {
        "start": -1.0,
        "end": 2.0,
        "text": "clamp",
        "words": [
            {"start": -0.5, "end": 0.5, "word": " clamp"},
            {"start": 1.0, "end": 2.0, "word": " me"},
        ],
    }
    assert whisper_segments_to_lrc([seg]) == (
        "[00:00.00]<00:00.00>clamp <00:01.00>me <00:02.00>"
    )


def test_lrc_to_plain_strips_word_tags():
    """``lrc_to_plain`` removes Enhanced-LRC ``<..>`` word tags as well as
    ``[..]`` line tags, for both pure and mixed bodies."""
    enhanced = "[00:12.00]<00:12.00>hello <00:13.00>world <00:15.50>"
    assert lrc_to_plain(enhanced) == "hello world"
    mixed = "[00:00.00]plain line\n" + enhanced
    assert lrc_to_plain(mixed) == "plain line\nhello world"


# ---------------------------------------------------------------------------
# merge_lrclib_word_tags (#222): splice aligner word tags into curated LRCLIB
# ---------------------------------------------------------------------------
def test_merge_splices_word_tags_keeps_lrclib_line_tags():
    """Aligner word ``<>`` tags are spliced into each LRCLIB line; the curated
    LRCLIB *line* tag is kept verbatim (not the aligner's), and the flag is set."""
    synced = "[00:12.34]hello world\n[00:15.00]second line"
    aligned = (
        "[00:12.30]<00:12.30>hello <00:12.90>world <00:13.40>\n"
        "[00:15.05]<00:15.05>second <00:15.60>line <00:16.10>"
    )
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    assert merged == (
        "[00:12.34]<00:12.30>hello <00:12.90>world <00:13.40>\n"
        "[00:15.00]<00:15.05>second <00:15.60>line <00:16.10>"
    )


def test_merge_line_text_byte_identical_to_lrclib():
    """Stripping all tags from the merge reproduces the LRCLIB line text
    byte-for-byte — including irregular internal whitespace."""
    synced = "[00:10.00]keep   the    spacing\n[00:20.00]and this"
    aligned = (
        "[00:10.02]<00:10.02>keep <00:10.5>the <00:11.0>spacing <00:11.5>\n"
        "[00:20.01]<00:20.01>and <00:20.5>this <00:21.0>"
    )
    merged, _ = merge_lrclib_word_tags(synced, aligned)
    assert lrc_to_plain(merged) == lrc_to_plain(synced)
    assert "keep   the    spacing" in lrc_to_plain(merged)


def test_merge_drift_over_tolerance_leaves_line_plain():
    """A line whose aligner start drifts > 2 s from the LRCLIB tag stays plain
    (curated timing wins), while an in-tolerance line still merges."""
    synced = "[00:10.00]alpha beta\n[00:20.00]gamma delta"
    aligned = (
        "[00:12.50]<00:12.50>alpha <00:13.0>beta <00:13.5>\n"  # drift 2.5 s > 2
        "[00:20.10]<00:20.10>gamma <00:20.6>delta <00:21.0>"   # drift 0.1 s
    )
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    lines = merged.split("\n")
    assert lines[0] == "[00:10.00]alpha beta"  # plain — bad alignment rejected
    assert lines[1] == "[00:20.00]<00:20.10>gamma <00:20.60>delta <00:21.00>"


def test_merge_drift_at_tolerance_boundary_merges():
    """Drift of exactly the tolerance (2 s) still merges (``<=`` boundary)."""
    synced = "[00:10.00]alpha beta"
    aligned = "[00:12.00]<00:12.00>alpha <00:12.5>beta <00:13.0>"
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    assert merged == "[00:10.00]<00:12.00>alpha <00:12.50>beta <00:13.00>"


def test_merge_word_count_drift_leaves_line_plain():
    """A word-count mismatch between the LRCLIB line and the aligner line leaves
    that line plain — no partial / misaligned tagging."""
    synced = "[00:10.00]one two three"
    aligned = "[00:10.00]<00:10.00>one <00:10.5>two <00:11.0>"  # 2 words vs 3
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is False
    assert merged == synced


def test_merge_tolerates_dropped_line_and_resyncs():
    """A line the aligner dropped (#149 low-confidence) stays plain and the
    aligner cursor re-syncs onto the following line by order + text."""
    synced = (
        "[00:10.00]first line\n"
        "[00:20.00]dropped line\n"
        "[00:30.00]third line"
    )
    aligned = (
        "[00:10.02]<00:10.02>first <00:10.6>line <00:11.0>\n"
        "[00:30.05]<00:30.05>third <00:30.6>line <00:31.0>"
    )
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    lines = merged.split("\n")
    assert lines[0].startswith("[00:10.00]<00:10.02>first")
    assert lines[1] == "[00:20.00]dropped line"  # dropped → plain
    assert lines[2].startswith("[00:30.00]<00:30.05>third")


def test_merge_plain_aligner_line_leaves_line_plain():
    """An aligner line that carries no word tags (aligner token drift) leaves the
    matching LRCLIB line plain rather than inventing timing."""
    synced = "[00:10.00]tricky line here"
    aligned = "[00:10.02]tricky line here"  # matched text, but no <> tags
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is False
    assert merged == synced


def test_merge_no_aligned_returns_lrclib_verbatim():
    """No aligned LRC (missing / empty / whitespace) → LRCLIB body byte-exact,
    flag False. Never fatal."""
    synced = "[00:12.00]line one\n[00:15.50]line two"
    for aligned in (None, "", "   \n  "):
        merged, word_timing = merge_lrclib_word_tags(synced, aligned)
        assert merged == synced
        assert word_timing is False


def test_merge_unmergeable_aligned_returns_lrclib_verbatim():
    """A well-formed but wholly non-matching aligned LRC merges nothing and
    returns the LRCLIB body byte-exact (trailing newline preserved)."""
    synced = "[00:12.00]line one\n[00:15.50]line two\n"
    aligned = "[00:40.00]<00:40.00>totally <00:41.0>different <00:42.0>"
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert merged == synced  # byte-exact, including the trailing newline
    assert word_timing is False


def test_merge_passes_through_bare_tag_and_untagged_lines():
    """Bare line tags (instrumental breaks) and non-tag lines pass through
    untouched and do not consume the aligner cursor."""
    synced = "[00:05.00]\n[00:10.00]real words here\nno tag line"
    aligned = "[00:10.03]<00:10.03>real <00:10.5>words <00:11.0>here <00:11.5>"
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    lines = merged.split("\n")
    assert lines[0] == "[00:05.00]"          # bare tag untouched
    assert lines[1].startswith("[00:10.00]<00:10.03>real")
    assert lines[2] == "no tag line"          # untagged line untouched


def test_merge_result_roundtrips_through_word_parser():
    """The merged Enhanced LRC parses cleanly into per-word timings via the API
    word-tag parser — proving the shared contract holds end-to-end."""
    from karaoke.api.routes import _parse_lrc_lines

    synced = "[00:10.00]alpha beta gamma"
    aligned = "[00:10.05]<00:10.05>alpha <00:10.6>beta <00:11.2>gamma <00:11.8>"
    merged, _ = merge_lrclib_word_tags(synced, aligned)
    (line,) = _parse_lrc_lines(merged)
    assert line.t == 10.0  # curated LRCLIB line tag, not the aligner's 10.05
    assert line.text == "alpha beta gamma"
    assert [w.text for w in line.words] == ["alpha", "beta", "gamma"]
    assert line.end == 11.8


def test_merge_multitag_line_consumes_aligner_entry_and_stays_plain():
    """A multi-tag LRCLIB line ([t1][t2]chorus) stays plain, but its aligner
    entry is consumed so the cursor does not wedge for following lines."""
    synced = (
        "[00:10.00][00:50.00]chorus line\n"
        "[00:20.00]verse words"
    )
    aligned = (
        "[00:10.02]<00:10.02>chorus <00:10.5>line <00:11.0>\n"
        "[00:20.05]<00:20.05>verse <00:20.6>words <00:21.0>"
    )
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    lines = merged.split("\n")
    assert lines[0] == "[00:10.00][00:50.00]chorus line"  # plain, untouched
    assert lines[1].startswith("[00:20.00]<00:20.05>verse")  # cursor not wedged


def test_merge_repeated_line_defers_entry_to_matching_occurrence():
    """When the aligner dropped the FIRST occurrence of a repeated line but
    kept the second, the single aligner entry (near the second tag) is not
    burned on the first occurrence — the second occurrence merges."""
    synced = "[00:10.00]chorus\n[00:30.00]chorus"
    aligned = "[00:30.05]<00:30.05>chorus <00:31.0>"
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    lines = merged.split("\n")
    assert lines[0] == "[00:10.00]chorus"  # dropped occurrence stays plain
    assert lines[1] == "[00:30.00]<00:30.05>chorus <00:31.00>"


def test_merge_preserves_final_newline():
    """A trailing newline on the LRCLIB body survives a successful merge."""
    synced = "[00:10.00]alpha beta\n"
    aligned = "[00:10.05]<00:10.05>alpha <00:10.6>beta <00:11.0>"
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    assert merged.endswith("\n")
    assert merged.rstrip("\n").startswith("[00:10.00]<00:10.05>alpha")


def test_merge_preserves_trailing_whitespace_on_merged_line():
    """Trailing spaces on a curated line survive the splice byte-for-byte
    (the sung-end tag rides after them without adding a double space)."""
    synced = "[00:10.00]alpha beta  "
    aligned = "[00:10.05]<00:10.05>alpha <00:10.6>beta <00:11.0>"
    merged, word_timing = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    assert merged == "[00:10.00]<00:10.05>alpha <00:10.60>beta  <00:11.00>"
    # stripping tags reproduces the curated line text byte-for-byte
    stripped = LRC_WORD_TAG_RE.sub("", merged)[len("[00:10.00]"):]
    assert stripped == "alpha beta  "


def test_merge_nonmonotonic_aligner_tags_leave_line_plain():
    """Aligner lines with decreasing word starts, or an end before the last
    start, carry no usable word timing — the curated line stays plain instead
    of producing negative word durations downstream."""
    synced = "[00:10.00]alpha beta"
    for aligned in (
        "[00:10.05]<00:12.00>alpha <00:10.5>beta <00:13.0>",  # decreasing starts
        "[00:10.05]<00:10.05>alpha <00:10.6>beta <00:10.2>",  # end < last start
    ):
        merged, word_timing = merge_lrclib_word_tags(synced, aligned)
        assert word_timing is False, aligned
        assert merged == synced
