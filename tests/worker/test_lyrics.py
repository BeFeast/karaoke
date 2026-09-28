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

import pytest

from karaoke.titles import parse_artist_track
from karaoke.worker.lyrics import (
    _MAX_LADDER_QUERIES,
    LRC_WORD_TAG_RE,
    LyricsSource,
    aligned_text_agreement,
    drop_unreliable_aligned_lines,
    lrc_to_plain,
    merge_lrclib_word_tags,
    repair_aligned_lrc,
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
    # Latin track -> the #260 raw-track rung does NOT fire (see its guard).
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


# The #260 raw-track artist-free fallback query — fires only for a NON-LATIN
# track name after a clean artist-scoped miss (never over a #148 rejection) —
# scripted as a miss in the tests below.
_RAW_TRACK_MISS = {"expect_in": "/api/search", "code": 200, "body": []}


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


def test_ladder_editions_expansion_finds_hidden_edition():
    """q= returns only wrong-duration editions of the right song; the ladder
    follows up with artist+track and accepts the in-tolerance edition (#233)."""
    wrong_edition = {
        "trackName": "Конь",
        "artistName": "Любэ",
        "duration": 217.0,          # gate-rejected
        "syncedLyrics": SYNCED_BODY,
        "plainLyrics": PLAIN_BODY,
    }
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        # ladder q=Конь: only the 217 s edition is visible
        {"expect_in": "/api/search", "code": 200, "body": [wrong_edition]},
        # editions follow-up artist+track: the 202.59 s edition appears
        {"expect_in": "/api/search", "code": 200, "body": [wrong_edition, _KON_RECORD]},
    ])
    src = LyricsSource(http=rec)
    parsed = parse_artist_track(_KON_TITLE)
    result = src.fetch(artist=parsed.artist, track=parsed.track, duration=203)
    assert result.found is True
    assert result.synced_lrc == SYNCED_BODY
    assert result.match_variant == "Конь"
    # follow-up call used artist_name+track_name from the title-matched record
    assert rec.calls[-1][2] == {"artist_name": "Любэ", "track_name": "Конь"}


def test_ladder_editions_expansion_tries_all_title_matched_artists():
    """Multiple artists share the exact title: the first artist's editions all
    fail the gate, a later artist's edition is in tolerance — it wins."""
    cover_wrong = {
        "trackName": "Конь",
        "artistName": "Cover Band",
        "duration": 300.0,
        "syncedLyrics": SYNCED_BODY,
        "plainLyrics": PLAIN_BODY,
    }
    lube_wrong = {**_KON_RECORD, "duration": 217.0}
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        # ladder q=Конь: two artists, both editions wrong-duration
        {"expect_in": "/api/search", "code": 200, "body": [cover_wrong, lube_wrong]},
        # follow-up 1 (Cover Band): still nothing in tolerance
        {"expect_in": "/api/search", "code": 200, "body": [cover_wrong]},
        # follow-up 2 (Любэ): the 202.59 s edition appears
        {"expect_in": "/api/search", "code": 200, "body": [lube_wrong, _KON_RECORD]},
    ])
    src = LyricsSource(http=rec)
    parsed = parse_artist_track(_KON_TITLE)
    result = src.fetch(artist=parsed.artist, track=parsed.track, duration=203)
    assert result.found is True
    assert result.synced_lrc == SYNCED_BODY
    assert rec.calls[-1][2] == {"artist_name": "Любэ", "track_name": "Конь"}


def test_ladder_editions_expansion_requires_title_match():
    """Gate-failed candidates whose track name differs from the query do NOT
    trigger the editions follow-up — the song itself is unconfirmed."""
    unrelated = {
        "trackName": "Конь-Огонь",
        "artistName": "Калинов Мост",
        "duration": 219.0,
        "syncedLyrics": SYNCED_BODY,
        "plainLyrics": PLAIN_BODY,
    }
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/search", "code": 200, "body": [unrelated]},
        _RAW_TRACK_MISS,
    ])
    src = LyricsSource(http=rec)
    parsed = parse_artist_track(_KON_TITLE)
    result = src.fetch(artist=parsed.artist, track=parsed.track, duration=203)
    assert result.found is False
    # get + artist search + one ladder query (no editions follow-up for the
    # unconfirmed title) + the #260 raw-track fallback
    assert len(rec.calls) == 4


def test_ladder_duration_gate_runs_before_ranking():
    """A higher-scoring wrong-duration candidate must not shadow a valid
    in-tolerance one — the gate filters BEFORE ranking picks the best."""
    wrong_duration = {
        "trackName": "Конь",          # exact title -> top similarity score
        "artistName": "Любэ",
        "duration": 300.0,             # 97 s off -> hard-rejected
        "syncedLyrics": SYNCED_BODY,
        "plainLyrics": PLAIN_BODY,
    }
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/search", "code": 200, "body": [wrong_duration, _KON_RECORD]},
    ])
    src = LyricsSource(http=rec)
    parsed = parse_artist_track(_KON_TITLE)
    result = src.fetch(artist=parsed.artist, track=parsed.track, duration=203)
    assert result.found is True
    assert result.synced_lrc == SYNCED_BODY
    assert result.match_variant == "Конь"


def test_ladder_rejects_artist_free_instrumental_match():
    """An artist-free /api/search?q= record flagged instrumental is NOT
    accepted — duration alone is too weak to mark a lyrical track
    instrumental (that would drop the transcript entirely)."""
    instrumental = {
        "trackName": "Конь",
        "artistName": "Любэ",
        "duration": 202.59,
        "instrumental": True,
        "syncedLyrics": "",
        "plainLyrics": "",
    }
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/search", "code": 200, "body": [instrumental]},
        # #260 raw-track fallback query also misses.
        {"expect_in": "/api/search", "code": 200, "body": []},
    ])
    src = LyricsSource(http=rec)
    parsed = parse_artist_track(_KON_TITLE)
    result = src.fetch(artist=parsed.artist, track=parsed.track, duration=203)
    assert result.found is False
    assert result.instrumental is False
    assert result.match_variant is None


def test_ladder_miss_falls_through_to_floor():
    """Every variant misses (wrong-duration artist-free candidate is rejected;
    its title match triggers ONE editions follow-up (#233) that also fails the
    gate; the #260 raw-track fallback misses too) → empty result, so the
    pipeline keeps today's Whisper ASR floor."""
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
        # #233 editions follow-up: still nothing in tolerance → gate holds.
        {
            "expect_in": "/api/search",
            "code": 200,
            "body": [{**_KON_RECORD, "duration": 999}],
        },
        # #260 raw-track fallback query: nothing either.
        {"expect_in": "/api/search", "code": 200, "body": []},
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist=parsed.artist, track=parsed.track, duration=203)

    assert result.found is False
    assert result.source == "none"
    assert result.match_variant is None
    assert len(rec.calls) == 5


def test_ladder_is_bounded_and_queries_each_variant():
    """A track yielding two cleaned variants issues one artist-free query each —
    ≤ _MAX_LADDER_QUERIES extra HTTP calls, in ladder order. A Latin track
    does NOT get the #260 raw-track fallback appended."""
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


def test_cross_script_artist_resolves_via_raw_track_fallback():
    """The job-#187 shape (#260): LRCLIB curates Israeli artists under a LATIN
    artistName with a Hebrew trackName and its search is conjunctive, so every
    artist-scoped rung zeroes out on the Hebrew artist. The track has no
    cleanup variants, so only the raw-track artist-free query can find it."""
    record = {
        "trackName": "כולם באילת",
        "artistName": "Eden Ben Zaken",
        "duration": 199.44,
        "syncedLyrics": SYNCED_BODY,
        "plainLyrics": PLAIN_BODY,
    }
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        # Artist-scoped search: Hebrew artist token → conjunctive miss.
        {"expect_in": "/api/search", "code": 200, "body": []},
        # #260 raw-track artist-free query finds the Latin-artist record.
        {"expect_in": "/api/search", "code": 200, "body": [record]},
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="עדן בן זקן", track="כולם באילת", duration=199)

    assert result.source == "lrclib_search"
    assert result.synced_lrc == SYNCED_BODY
    assert result.match_variant == "כולם באילת"
    assert rec.calls[-1][2] == {"q": "כולם באילת"}
    assert len(rec.calls) == 3


def test_raw_track_fallback_never_shadows_rejection():
    """A #148 duration-reject salvaged the RIGHT song's text — the #260
    raw-track rung must not run after it: a duration-coincident artist-free
    hit could attach the WRONG song's lyrics over the salvage."""
    wrong_edit = {
        "trackName": "כולם באילת",
        "artistName": "Eden Ben Zaken",
        "duration": 400,
        "plainLyrics": PLAIN_BODY,
    }
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": [wrong_edit]},
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist="עדן בן זקן", track="כולם באילת", duration=199)

    assert result.rejected is not None
    assert result.rejected_text == PLAIN_BODY
    assert len(rec.calls) == 2  # no raw-track query after the rejection


def test_raw_track_fallback_skipped_without_artist():
    """With no artist the primary search was already artist-free — the #260
    raw-track query would duplicate it, so the ladder issues nothing."""
    rec = _Recorder([
        # No /api/get (needs an artist); track-only /api/search misses.
        {"expect_in": "/api/search", "code": 200, "body": []},
    ])
    src = LyricsSource(http=rec)
    result = src.fetch(artist=None, track="כולם באילת", duration=199)

    assert result.found is False
    assert len(rec.calls) == 1


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
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
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
    merged, _, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
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
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    lines = merged.split("\n")
    assert lines[0] == "[00:10.00]alpha beta"  # plain — bad alignment rejected
    assert lines[1] == "[00:20.00]<00:20.10>gamma <00:20.60>delta <00:21.00>"


def test_merge_drift_at_tolerance_boundary_merges():
    """Drift of exactly the tolerance (2 s) still merges (``<=`` boundary)."""
    synced = "[00:10.00]alpha beta"
    aligned = "[00:12.00]<00:12.00>alpha <00:12.5>beta <00:13.0>"
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    assert merged == "[00:10.00]<00:12.00>alpha <00:12.50>beta <00:13.00>"


def test_merge_word_count_drift_leaves_line_plain():
    """A word-count mismatch between the LRCLIB line and the aligner line leaves
    that line plain — no partial / misaligned tagging."""
    synced = "[00:10.00]one two three"
    aligned = "[00:10.00]<00:10.00>one <00:10.5>two <00:11.0>"  # 2 words vs 3
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
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
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
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
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is False
    assert merged == synced


def test_merge_no_aligned_returns_lrclib_verbatim():
    """No aligned LRC (missing / empty / whitespace) → LRCLIB body byte-exact,
    flag False. Never fatal."""
    synced = "[00:12.00]line one\n[00:15.50]line two"
    for aligned in (None, "", "   \n  "):
        merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
        assert merged == synced
        assert word_timing is False


def test_merge_unmergeable_aligned_returns_lrclib_verbatim():
    """A well-formed but wholly non-matching aligned LRC merges nothing and
    returns the LRCLIB body byte-exact (trailing newline preserved)."""
    synced = "[00:12.00]line one\n[00:15.50]line two\n"
    aligned = "[00:40.00]<00:40.00>totally <00:41.0>different <00:42.0>"
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
    assert merged == synced  # byte-exact, including the trailing newline
    assert word_timing is False


def test_merge_passes_through_bare_tag_and_untagged_lines():
    """Bare line tags (instrumental breaks) and non-tag lines pass through
    untouched and do not consume the aligner cursor."""
    synced = "[00:05.00]\n[00:10.00]real words here\nno tag line"
    aligned = "[00:10.03]<00:10.03>real <00:10.5>words <00:11.0>here <00:11.5>"
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
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
    merged, _, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
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
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
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
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    lines = merged.split("\n")
    assert lines[0] == "[00:10.00]chorus"  # dropped occurrence stays plain
    assert lines[1] == "[00:30.00]<00:30.05>chorus <00:31.00>"


def test_merge_preserves_final_newline():
    """A trailing newline on the LRCLIB body survives a successful merge."""
    synced = "[00:10.00]alpha beta\n"
    aligned = "[00:10.05]<00:10.05>alpha <00:10.6>beta <00:11.0>"
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
    assert word_timing is True
    assert merged.endswith("\n")
    assert merged.rstrip("\n").startswith("[00:10.00]<00:10.05>alpha")


def test_merge_preserves_trailing_whitespace_on_merged_line():
    """Trailing spaces on a curated line survive the splice byte-for-byte
    (the sung-end tag rides after them without adding a double space)."""
    synced = "[00:10.00]alpha beta  "
    aligned = "[00:10.05]<00:10.05>alpha <00:10.6>beta <00:11.0>"
    merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
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
        merged, word_timing, _elig, _merged = merge_lrclib_word_tags(synced, aligned)
        assert word_timing is False, aligned
        assert merged == synced


# ---------------------------------------------------------------------------
# repair_aligned_lrc (#241): CTC boundary-absorption repair
# ---------------------------------------------------------------------------

def test_repair_pulls_in_silence_absorbed_first_word():
    """The Creep case: first word spans the instrumental gap (2.4 s to word 2
    vs ~0.2 s median gap) — its start is pulled to median-gap before word 2,
    and the line tag follows."""
    body = "[01:34.42]<01:34.42>I <01:36.82>want <01:37.04>a <01:37.16>perfect <01:37.78>soul <01:37.98>"
    out = repair_aligned_lrc(body)
    assert out.startswith("[01:36.")
    assert "<01:34.42>" not in out
    # word 2 onward untouched
    assert "<01:36.82>want" in out and "<01:37.78>soul" in out


def test_repair_pulls_in_silence_absorbed_end_tag():
    """Trailing silence absorbed into the sung-end tag is pulled back to
    median-gap after the last word."""
    body = "[00:10.00]<00:10.00>la <00:10.20>la <00:10.40>la <00:10.60>la <00:20.00>"
    out = repair_aligned_lrc(body)
    assert "<00:20.00>" not in out
    assert out.endswith("<00:10.80>")


def test_repair_leaves_normal_lines_untouched():
    body = "[00:10.00]<00:10.00>steady <00:10.40>pace <00:10.80>words <00:11.20>"
    assert repair_aligned_lrc(body) == body


def test_repair_passes_through_short_and_plain_lines():
    for body in (
        "[00:10.00]plain line no tags",
        "[00:10.00]<00:10.00>one <00:11.00>",  # < 3 word tags
        "free text",
    ):
        assert repair_aligned_lrc(body) == body


# ---------------------------------------------------------------------------
# drop_unreliable_aligned_lines (#244): crammed-tail filtering
# ---------------------------------------------------------------------------

def test_drop_crammed_line_by_pace():
    """Job-133 shape: all word starts packed into half a second (the sung-end
    tag absorbing the rest) is an implausible pace — the line is dropped."""
    body = (
        "[00:10.00]<00:10.00>Выйду <00:10.40>ночью <00:11.00>в <00:11.20>поле <00:12.00>\n"
        "[03:17.36]<03:17.36>Мы <03:17.42>идём <03:17.52>с <03:17.56>конём <03:17.68>по <03:17.74>полю <03:17.88>вдвоём <03:22.88>"
    )
    filtered, dropped = drop_unreliable_aligned_lines(body, None)
    assert dropped == 1
    assert "идём" not in filtered
    assert "Выйду" in filtered


def test_drop_score_outlier_self_calibrating():
    """With r8 per-line scores, a line far below the job's own median−2×MAD
    is dropped even at a plausible pace."""
    lines = [
        f"[00:{10 + i * 5:02d}.00]<00:{10 + i * 5:02d}.00>слово <00:{11 + i * 5:02d}.00>ещё <00:{12 + i * 5:02d}.00>тут <00:{13 + i * 5:02d}.00>"
        for i in range(9)
    ]
    body = "\n".join(lines)
    scores = [-1.0] * 8 + [-9.0]  # last line is a gross outlier
    filtered, dropped = drop_unreliable_aligned_lines(body, scores)
    assert dropped == 1
    assert len(filtered.splitlines()) == 8


def test_drop_requires_enough_scored_lines():
    """< 8 scored lines: the distribution is noise — no score-based drops."""
    lines = [
        f"[00:{10 + i * 5:02d}.00]<00:{10 + i * 5:02d}.00>слово <00:{11 + i * 5:02d}.00>ещё <00:{12 + i * 5:02d}.00>"
        for i in range(4)
    ]
    body = "\n".join(lines)
    filtered, dropped = drop_unreliable_aligned_lines(body, [-1.0, -1.0, -1.0, -9.0])
    assert dropped == 0
    assert filtered == body


def test_drop_keeps_plain_and_none_scored_lines():
    """Plain lines (no word tags) and None-scored lines always pass."""
    body = "[00:10.00]plain line here\n[00:15.00]<00:15.00>a <00:15.40>b <00:15.80>"
    filtered, dropped = drop_unreliable_aligned_lines(body, [None, None])
    assert dropped == 0
    assert filtered == body


def test_drop_score_guard_skips_plain_lines():
    """Plain aligned lines (token-drift output, no word tags) are never
    score-dropped — their positional timing is untrusted either way."""
    tagged = [
        f"[00:{10 + i * 5:02d}.00]<00:{10 + i * 5:02d}.00>слово <00:{11 + i * 5:02d}.00>ещё <00:{12 + i * 5:02d}.00>тут <00:{13 + i * 5:02d}.00>"
        for i in range(8)
    ]
    body = "\n".join([*tagged, "[01:00.00]plain drifted line"])
    scores = [-1.0] * 8 + [-9.0]  # outlier score belongs to the plain line
    filtered, dropped = drop_unreliable_aligned_lines(body, scores)
    assert dropped == 0
    assert "plain drifted line" in filtered


@pytest.mark.parametrize(
    ("curated", "aligned", "expected"),
    [
        # Dropping an early repeat must not match the later repeat first and
        # strand the retained middle lines behind a greedy cursor.
        (["repeat", "middle one", "middle two", "repeat"],
         ["middle one", "middle two", "repeat"], 3),
        (["first", "second"], ["unrelated", "different"], 0),
        (["first", "second", "third"], ["first", "different", "third"], 2),
        (["first", "second", "third"], ["third", "second", "first"], 1),
        (["repeat"], ["repeat", "repeat", "repeat"], 1),
        (["repeat", "repeat", "repeat"], ["repeat"], 1),
        (["MiXeD CASE", "Straße"], ["mixed case", "STRASSE"], 2),
    ],
)
def test_aligned_text_agreement_preserves_order_and_line_multiplicity(
    curated, aligned, expected
):
    def lrc(lines):
        return "\n".join(f"[00:{i:02d}.00]{text}" for i, text in enumerate(lines))

    assert aligned_text_agreement(lrc(curated), lrc(aligned)) == (
        expected, len(curated), len(aligned)
    )


def test_aligned_text_agreement_counts_only_single_tag_nonempty_curated_lines():
    curated = (
        "[ar:Example]\n"
        "untimed line\n"
        "[00:01.00]  \n"
        "[00:02.00][00:03.00]repeat\n"
        "[00:04.00]<00:04.00> Hello   <00:05.00>WORLD <00:06.00>\n"
    )
    aligned = "[01:00.00]repeat\n[01:01.00]hello world"
    assert aligned_text_agreement(curated, aligned) == (1, 1, 2)


@pytest.mark.parametrize("aligned", [None, "", "  \n"])
def test_aligned_text_agreement_without_alignment(aligned):
    assert aligned_text_agreement("[00:01.00]words", aligned) == (0, 0, 0)


# ---------------------------------------------------------------------------
# get_by_id (#280 bench tooling): direct record fetch, no ladder, no cache
# ---------------------------------------------------------------------------
def test_get_by_id_fetches_record_directly():
    rec = _Recorder([
        {"expect_in": "/api/get/38711481", "code": 200,
         "body": {"syncedLyrics": SYNCED_BODY, "plainLyrics": PLAIN_BODY}},
        {"expect_in": "/api/get/1", "code": 404, "body": {"message": "not found"}},
    ])
    src = LyricsSource(http=rec)
    hit = src.get_by_id(38711481)
    assert hit.found and hit.synced_lrc == SYNCED_BODY and hit.source == "lrclib_get"
    assert not src.get_by_id(1).found
    assert [c[2] for c in rec.calls] == [None, None]


# ---------------------------------------------------------------------------
# #281: retry on transient LRCLIB failures; transient misses are not cached
# ---------------------------------------------------------------------------
def test_retries_503_then_succeeds():
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 503, "body": {"message": "ServerOverloaded"}},
        {"expect_in": "/api/get", "code": 503, "body": None},
        {"expect_in": "/api/get", "code": 200, "body": {"syncedLyrics": SYNCED_BODY}},
    ])
    src = LyricsSource(http=rec, retry_delays=(0, 0))
    hit = src.fetch(artist="A", track="T", duration=100)
    assert hit.found and hit.source == "lrclib_get"
    assert len(rec.calls) == 3


def test_transient_miss_is_not_cached_but_404_miss_is():
    transient = [
        {"expect_in": "/api/get", "code": 503, "body": None},
        {"expect_in": "/api/get", "code": 0, "body": None},
        {"expect_in": "/api/get", "code": 503, "body": None},
        {"expect_in": "/api/search", "code": 503, "body": None},
        {"expect_in": "/api/search", "code": 503, "body": None},
        {"expect_in": "/api/search", "code": 503, "body": None},
    ]
    rec = _Recorder(list(transient) + [
        # second fetch: LRCLIB is back and answers a clean 404 miss
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
    ])
    src = LyricsSource(http=rec, retry_delays=(0, 0))
    assert not src.fetch(artist="A", track="T").found
    assert len(rec.calls) == 6
    assert not src.fetch(artist="A", track="T").found
    assert len(rec.calls) == 8  # re-queried: the transient miss was not cached
    assert not src.fetch(artist="A", track="T").found
    assert len(rec.calls) == 8  # the 404 miss is cached


# ---------------------------------------------------------------------------
# #281: canonical release metadata rungs + dual duration gate
# ---------------------------------------------------------------------------
def _record(artist, track, duration, synced=SYNCED_BODY):
    return {"artistName": artist, "trackName": track, "duration": duration,
            "syncedLyrics": synced, "plainLyrics": PLAIN_BODY, "instrumental": False}


def test_canonical_artist_get_rung_lands_hebrew_upload():
    """Parsed (Hebrew artist, track, video 227 s) misses; the canonical
    (Latin artist, track, audio 179 s) /api/get rung hits."""
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/get", "code": 200, "body": _record("Noa Kirel", "לאב סונג", 179)},
    ])
    src = LyricsSource(http=rec, retry_delays=(0,))
    hit = src.fetch(
        artist="נועה קירל", track="לאב סונג", duration=227,
        canonical_artist="Noa Kirel", canonical_track="לאב סונג", canonical_duration=179,
    )
    assert hit.found and hit.synced_lrc == SYNCED_BODY
    assert hit.match_variant == "canonical:Noa Kirel"
    params = rec.calls[2][2]
    assert params["artist_name"] == "Noa Kirel" and params["duration"] == 179


def test_canonical_title_rung_when_release_title_differs():
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/get", "code": 200, "body": _record("Static & Ben El", "Tudo bom", 190)},
    ])
    src = LyricsSource(http=rec, retry_delays=(0,))
    hit = src.fetch(
        artist="Static and Ben El", track="טודו בום", duration=200,
        canonical_artist="Static", canonical_track="Tudo bom", canonical_duration=190,
    )
    assert hit.found and hit.match_variant == "canonical:Static/Tudo bom"
    assert rec.calls[4][2]["track_name"] == "Tudo bom"


def test_search_gate_accepts_canonical_duration_for_longer_video():
    """The video is 48 s longer than the release; the only search candidate
    matches the canonical audio duration, so it is accepted (not #148-rejected)
    and tagged with the duration that admitted it."""
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": [_record("Noa Kirel", "לאב סונג", 179)]},
    ])
    src = LyricsSource(http=rec, retry_delays=(0,))
    hit = src.fetch(
        artist="Noa Kirel", track="לאב סונג", duration=227,
        canonical_artist="Noa Kirel", canonical_duration=179,
    )
    assert hit.found and hit.source == "lrclib_search"
    assert hit.duration_gate == "canonical"
    assert hit.match_variant is None


def test_search_gate_names_source_duration_when_video_matches():
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": [_record("A", "T", 200)]},
    ])
    hit = LyricsSource(http=rec, retry_delays=(0,)).fetch(
        artist="A", track="T", duration=201, canonical_artist="A", canonical_duration=150
    )
    assert hit.found and hit.duration_gate == "source"


def test_search_still_rejects_when_neither_duration_matches():
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": [_record("A", "T", 300)]},
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": [_record("A", "T", 300)]},
    ])
    hit = LyricsSource(http=rec, retry_delays=(0,)).fetch(
        artist="A", track="T", duration=200, canonical_artist="B", canonical_duration=190
    )
    assert not hit.found and hit.rejected and hit.rejected.startswith("duration_mismatch")
    assert hit.rejected_text == PLAIN_BODY


def test_canonical_fields_are_part_of_the_cache_key():
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/get", "code": 404, "body": {"code": 404}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/get", "code": 200, "body": _record("Latin", "T", 100)},
    ])
    src = LyricsSource(http=rec, retry_delays=(0,))
    assert not src.fetch(artist="A", track="T", duration=100).found
    assert not src.fetch(artist="A", track="T", duration=100).found  # cached miss
    hit = src.fetch(artist="A", track="T", duration=100, canonical_artist="Latin", canonical_duration=100)
    assert hit.found and len(rec.calls) == 5


# ---------------------------------------------------------------------------
# #281 follow-up: a line whose leading words were pinned to untranscribed
# speech (music-video skit) keeps only its sung start and loses word tags.
# ---------------------------------------------------------------------------
def test_repair_drops_word_tags_of_a_line_stretched_over_speech():
    body = (
        "[00:05.78]<00:05.78>תדע <00:42.42>זה <00:56.66>פשע <00:57.14>\n"
        "[00:57.94]<00:57.94>מישהו <00:58.42>צריך <00:59.00>להתערב <00:59.98>\n"
    )
    repaired = repair_aligned_lrc(body)
    assert repaired.splitlines()[0] == "[00:56.66]תדע זה פשע"
    assert repaired.splitlines()[1] == "[00:57.94]<00:57.94>מישהו <00:58.42>צריך <00:59.00>להתערב <00:59.98>"
    assert repaired.endswith("\n")


def test_repair_keeps_the_dense_tail_when_two_leading_words_strayed():
    body = "[00:05.00]<00:05.00>a <00:20.00>b <00:40.00>c <00:40.40>d <00:41.30>\n"
    assert repair_aligned_lrc(body).splitlines()[0] == "[00:40.00]a b c d"


def test_repair_single_strayed_first_word_still_uses_the_pull_in_rule():
    body = "[00:05.00]<00:05.00>a <00:40.00>b <00:40.40>c <00:40.90>d <00:41.30>\n"
    repaired = repair_aligned_lrc(body).splitlines()[0]
    assert repaired.startswith("[00:39.") and "<00:40.00>b" in repaired


def test_repair_leaves_dense_head_with_strayed_tail_to_the_asr_repair():
    body = "[00:10.00]<00:10.00>silver <00:10.50>river <00:30.00>flows <00:45.00>away <00:45.50>\n"
    assert "<00:30.00>flows" in repair_aligned_lrc(body)


# ---------------------------------------------------------------------------
# #293: constant-offset shift of a curated record (skit-intro music videos)
# ---------------------------------------------------------------------------
from karaoke.worker.lyrics import (  # noqa: E402
    estimate_lrclib_offset,
    lrclib_offset_is_reliable,
    shift_lrc,
)


def _synced(n, step=4.0, start=10.0):
    return "\n".join(f"[{_fmt(start + i * step)}]line {i}" for i in range(n))


def _fmt(sec):
    return f"{int(sec) // 60:02d}:{sec % 60:05.2f}"


def _aligned(n, offset, step=4.0, start=10.0, drift=None):
    drift = drift or {}
    return "\n".join(
        f"[{_fmt(start + i * step + offset + drift.get(i, 0.0))}]line {i}" for i in range(n)
    )


def test_estimate_offset_median_ignores_drifted_lines():
    synced = _synced(46)
    aligned = _aligned(46, 45.4, drift={0: -50.0, 18: 8.0, 19: 5.3, 20: 1.7})
    offset, matched, spread = estimate_lrclib_offset(synced, aligned)
    assert abs(offset - 45.4) < 0.05 and matched == 46 and spread < 0.1
    assert lrclib_offset_is_reliable((offset, matched, spread), 46)


def test_offset_not_reliable_when_lines_disagree_or_too_few():
    synced = _synced(12)
    scattered = _aligned(12, 45.0, drift={i: (i % 3) * 3.0 for i in range(12)})
    assert not lrclib_offset_is_reliable(estimate_lrclib_offset(synced, scattered), 12)
    assert not lrclib_offset_is_reliable(estimate_lrclib_offset(_synced(6), _aligned(6, 45.0)), 6)
    # aligner kept only 18 of 34 lines (VAD veto, #253): not a skit-intro shape
    assert not lrclib_offset_is_reliable(estimate_lrclib_offset(_synced(34), _aligned(18, 45.0)), 34)
    assert not lrclib_offset_is_reliable(estimate_lrclib_offset(synced, _aligned(12, 1.0)), 12)
    assert estimate_lrclib_offset(synced, None) is None
    assert estimate_lrclib_offset(synced, "[00:10.00]totally different\n") is None


def test_shift_lrc_moves_all_tags_and_keeps_text():
    body = "[ar:x]\n[00:10.38]<00:10.38>a <00:10.90>b <00:11.20>\n[00:12.49]c\n"
    assert shift_lrc(body, 45.4) == "[ar:x]\n[00:55.78]<00:55.78>a <00:56.30>b <00:56.60>\n[00:57.89]c\n"
    assert shift_lrc("[00:01.00]x", -5.0) == "[00:00.00]x"


# ---------------------------------------------------------------------------
# ASR hallucination guard (#282 follow-up): repetition-loop segments are not lyrics
# ---------------------------------------------------------------------------
from karaoke.worker.lyrics import is_degenerate_segment_text  # noqa: E402


def test_degenerate_segment_predicate():
    assert is_degenerate_segment_text("נא " * 111)
    assert is_degenerate_segment_text("na, na, na, na, na, na, na, na!")
    assert not is_degenerate_segment_text("נא נא נא")
    assert not is_degenerate_segment_text("ממעמקים קראתי אלייך בואי אליי בשובך יחזור שוב האור")
    assert not is_degenerate_segment_text(None)


def test_whisper_segments_to_lrc_skips_degenerate_segments():
    segments = [
        {"start": 10.0, "end": 40.0, "text": "נא " * 111},
        {"start": 50.0, "end": 54.0, "text": "ממעמקים קראתי אלייך"},
    ]
    lrc = whisper_segments_to_lrc(segments)
    assert "נא נא" not in lrc and "ממעמקים" in lrc


# ---------------------------------------------------------------------------
# #289: junk records; #290: canonical-title rung, exact-title salvage
# ---------------------------------------------------------------------------
_GOOD_PLAIN = "\n".join(f"line number {i} of the song text" for i in range(8))


def _rec(artist, track, duration, plain=_GOOD_PLAIN):
    return {"artistName": artist, "trackName": track, "duration": duration,
            "plainLyrics": plain, "syncedLyrics": None, "instrumental": False}


def test_junk_get_record_falls_through_to_a_real_search_edition():
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 200, "body": _rec("Rick Astley", "Never Gonna Give You Up", 213, "probe")},
        {"expect_in": "/api/search", "code": 200, "body": [
            _rec("Rick Astley", "Never Gonna Give You Up", 213, "probe"),
            _rec("Rick Astley", "Never Gonna Give You Up", 214),
        ]},
    ])
    hit = LyricsSource(http=rec, retry_delays=(0,)).fetch(
        artist="Rick Astley", track="Never Gonna Give You Up", duration=213
    )
    assert hit.found and hit.plain == _GOOD_PLAIN and hit.source == "lrclib_search"


def test_canonical_title_artist_free_rung_lands_dashless_upload():
    rec = _Recorder([
        {"expect_in": "/api/search", "code": 200, "body": []},  # parsed, no artist
        {"expect_in": "/api/get", "code": 404, "body": {}},     # canonical artist + parsed track
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/get", "code": 404, "body": {}},     # canonical artist + canonical title
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/search", "code": 200, "body": [_rec("Zohar Argov", "הפרח בגני", 222)]},
    ])
    hit = LyricsSource(http=rec, retry_delays=(0,)).fetch(
        artist=None, track="זוהר ארגוב הפרח בגני", duration=221,
        canonical_artist="Zohar Argov", canonical_track="הפרח בגני", canonical_duration=221,
    )
    assert hit.found and hit.match_variant == "הפרח בגני"
    assert rec.calls[-1][2] == {"q": "הפרח בגני"}


def test_exact_title_with_artist_overlap_is_salvaged_for_alignment():
    """Video shorter than every release (288 s vs 307+): no edition gates in,
    but the title is exact and the artist overlaps → text salvaged (#149 shape)."""
    editions = [_rec("Idan Raichel", "ממעמקים", 307), _rec("Idan Raichel", "ממעמקים", 316)]
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/search", "code": 200, "body": editions},   # q=ממעמקים
        {"expect_in": "/api/search", "code": 200, "body": editions},   # editions expansion
        {"expect_in": "/api/search", "code": 200, "body": []},         # q=<full track> (#260)
    ])
    hit = LyricsSource(http=rec, retry_delays=(0,)).fetch(
        artist="The Idan Raichel Project", track="הפרויקט של עידן רייכל - ממעמקים", duration=288,
    )
    assert not hit.found
    assert hit.rejected == "duration_mismatch (19s)" and hit.rejected_text == _GOOD_PLAIN
    assert hit.match_variant == "ממעמקים"


def test_exact_title_without_artist_overlap_is_not_salvaged():
    rec = _Recorder([
        {"expect_in": "/api/get", "code": 404, "body": {}},
        {"expect_in": "/api/search", "code": 200, "body": []},
        {"expect_in": "/api/search", "code": 200, "body": [_rec("Someone Else", "ממעמקים", 330)]},
        {"expect_in": "/api/search", "code": 200, "body": [_rec("Someone Else", "ממעמקים", 330)]},
        {"expect_in": "/api/search", "code": 200, "body": []},
    ])
    hit = LyricsSource(http=rec, retry_delays=(0,)).fetch(
        artist="The Idan Raichel Project", track="הפרויקט של עידן רייכל - ממעמקים", duration=288,
    )
    assert not hit.found and hit.rejected is None
