"""Unit tests for the title normalizer / artist-track parser."""
from __future__ import annotations

import pytest

from karaoke.titles import (
    ParsedTitle,
    derive_metadata,
    normalize_title,
    parse_artist_track,
)

# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # empty / none
        (None, ""),
        ("", ""),
        ("   ", ""),
        # the canonical YouTube noise tags
        ("Artist - Song (Official Video)", "Artist - Song"),
        ("Artist - Song (Official Music Video)", "Artist - Song"),
        ("Artist - Song (Official Audio)", "Artist - Song"),
        ("Artist - Song (Official Lyric Video)", "Artist - Song"),
        ("Artist - Song [Official Video]", "Artist - Song"),
        ("Artist - Song (Lyrics)", "Artist - Song"),
        ("Artist - Song (Lyric Video)", "Artist - Song"),
        ("Artist - Song (Audio)", "Artist - Song"),
        ("Artist - Song (Visualizer)", "Artist - Song"),
        ("Artist - Song [4K]", "Artist - Song"),
        ("Artist - Song [8K]", "Artist - Song"),
        ("Artist - Song [HD]", "Artist - Song"),
        ("Artist - Song (HQ)", "Artist - Song"),
        ("Artist - Song [1080p]", "Artist - Song"),
        ("Artist - Song (Explicit)", "Artist - Song"),
        ("Artist - Song (Clean)", "Artist - Song"),
        ("Artist - Song (Remastered)", "Artist - Song"),
        # year-suffixed remaster
        ("Artist - Song (Remastered 2011)", "Artist - Song"),
        ("Artist - Song (2009 Remaster)", "Artist - Song"),
        # multiple noise groups stacked
        ("Artist - Song (Official Video) [4K] (Lyrics)", "Artist - Song"),
        ("Artist - Song (Official Music Video) (Remastered)", "Artist - Song"),
        # bare trailing pipe noise
        ("Artist - Song | Official Video", "Artist - Song"),
        ("Artist - Song | Official Audio", "Artist - Song"),
        # casing is irrelevant
        ("Artist - Song (OFFICIAL VIDEO)", "Artist - Song"),
        ("Artist - Song (official audio)", "Artist - Song"),
        # whitespace collapse + trailing junk
        ("Artist  -  Song   (Official Video)  ", "Artist - Song"),
        # legitimate parentheses are preserved when not noise
        ("Artist - Song (Acoustic)", "Artist - Song (Acoustic)"),
        ("Artist - Song (Live at Wembley)", "Artist - Song (Live at Wembley)"),
        # noise-only input collapses to empty
        ("(Official Video)", ""),
        ("[4K]", ""),
        # a "Live"-prefixed word must not be eaten as the "live" tag (we don't
        # list "live" as noise, but guard the word-boundary intent anyway)
        ("Artist - Lively", "Artist - Lively"),
        # feat. credit is preserved through normalization
        ("Artist - Song (feat. Other) (Official Video)", "Artist - Song (feat. Other)"),
    ],
)
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


def test_normalize_idempotent():
    once = normalize_title("Artist - Song (Official Video) [4K] (Lyrics)")
    assert normalize_title(once) == once


# ---------------------------------------------------------------------------
# parse_artist_track
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "artist", "track"),
    [
        (None, None, None),
        ("", None, None),
        # plain hyphen split
        ("Daft Punk - Get Lucky", "Daft Punk", "Get Lucky"),
        # split happens after noise removal
        ("Daft Punk - Get Lucky (Official Video)", "Daft Punk", "Get Lucky"),
        # en dash / em dash
        ("Daft Punk – Get Lucky", "Daft Punk", "Get Lucky"),
        ("Daft Punk — Get Lucky", "Daft Punk", "Get Lucky"),
        # colon separator
        ("Daft Punk : Get Lucky", "Daft Punk", "Get Lucky"),
        # only first separator splits; rest stays in track
        ("A - B - C", "A", "B - C"),
        # hyphenated words are NOT split (no surrounding spaces)
        ("Spider-Man Theme", None, "Spider-Man Theme"),
        # no separator → whole thing is the track
        ("Just A Title", None, "Just A Title"),
        # feat. folded onto the track side, parenthesized
        ("Calvin Harris - Feel So Close feat. Example", "Calvin Harris", "Feel So Close (feat. Example)"),
        ("Calvin Harris - Feel So Close ft. Example", "Calvin Harris", "Feel So Close (feat. Example)"),
        # already-parenthesized feat. is kept
        ("Eminem - Stan (feat. Dido)", "Eminem", "Stan (feat. Dido)"),
        # noise around the whole thing then split
        ("Queen - Bohemian Rhapsody (Remastered 2011)", "Queen", "Bohemian Rhapsody"),
    ],
)
def test_parse_artist_track(title, artist, track):
    result = parse_artist_track(title)
    assert result == ParsedTitle(artist=artist, track=track)


def test_parse_artist_track_returns_dataclass():
    result = parse_artist_track("A - B")
    assert isinstance(result, ParsedTitle)
    assert result.artist == "A"
    assert result.track == "B"


# ---------------------------------------------------------------------------
# derive_metadata
# ---------------------------------------------------------------------------


def test_derive_metadata_prefers_structured_fields():
    info = {
        "title": "Some Channel - Whatever (Official Video)",
        "artist": "Daft Punk",
        "track": "Get Lucky",
        "album": "Random Access Memories",
        "duration": 248,
    }
    meta = derive_metadata(info)
    assert meta == {
        "artist": "Daft Punk",
        "track": "Get Lucky",
        "album": "Random Access Memories",
        "duration": 248,
    }


def test_derive_metadata_falls_back_to_title():
    info = {"title": "Daft Punk - Get Lucky (Official Video)", "duration": 100}
    meta = derive_metadata(info)
    assert meta["artist"] == "Daft Punk"
    assert meta["track"] == "Get Lucky"
    assert meta["album"] is None
    assert meta["duration"] == 100


def test_derive_metadata_partial_structured_fills_from_title():
    # artist present, track missing → track comes from the title parse.
    info = {"title": "Daft Punk - Get Lucky", "artist": "Daft Punk"}
    meta = derive_metadata(info)
    assert meta["artist"] == "Daft Punk"
    assert meta["track"] == "Get Lucky"


def test_derive_metadata_creator_alias():
    info = {"title": "Some Song", "creator": "Some Artist"}
    meta = derive_metadata(info)
    assert meta["artist"] == "Some Artist"
    assert meta["track"] == "Some Song"


def test_derive_metadata_empty_and_none():
    assert derive_metadata(None) == {
        "artist": None,
        "track": None,
        "album": None,
        "duration": None,
    }
    assert derive_metadata({}) == {
        "artist": None,
        "track": None,
        "album": None,
        "duration": None,
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("248", 248),
        (248.7, 248),
        (248, 248),
        (None, None),
        ("not-a-number", None),
        (-5, None),
    ],
)
def test_derive_metadata_duration_coercion(raw, expected):
    meta = derive_metadata({"title": "X - Y", "duration": raw})
    assert meta["duration"] == expected


def test_derive_metadata_strips_whitespace_and_blank():
    info = {"artist": "  ", "track": "  Real Track  ", "title": "X - Y"}
    meta = derive_metadata(info)
    # blank artist falls back to the title parse ("X"); blank-stripped track wins.
    assert meta["artist"] == "X"
    assert meta["track"] == "Real Track"
