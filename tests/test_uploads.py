"""Unit tests for the upload filename/sentinel helpers (#172)."""
from __future__ import annotations

import pytest

from karaoke.uploads import (
    MAX_UPLOAD_FILENAME_CHARS,
    UPLOAD_PREFIX,
    is_upload_source,
    sanitize_upload_filename,
    upload_display_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("song.mp3", "song.mp3"),
        ("../../evil.mp3", "evil.mp3"),
        ("..\\..\\evil.mp3", "evil.mp3"),
        ("/abs/path/track.flac", "track.flac"),
        ("C:\\Users\\me\\track.m4a", "track.m4a"),
        ("with\x00control\x1fchars.ogg", "withcontrolchars.ogg"),
        ("  too   many\tspaces .wav", "too many spaces.wav"),
        (".mp3", "audio.mp3"),
        ("", "audio"),
        (None, "audio"),
        ("noext", "noext"),
    ],
)
def test_sanitize_upload_filename(raw, expected):
    assert sanitize_upload_filename(raw) == expected


def test_sanitize_caps_length_and_keeps_extension():
    name = sanitize_upload_filename("x" * 500 + ".mp3")
    assert len(name) <= MAX_UPLOAD_FILENAME_CHARS
    assert name.endswith(".mp3")


def test_upload_sentinel_helpers():
    assert is_upload_source(UPLOAD_PREFIX + "a.mp3") is True
    assert is_upload_source("https://example.com/x") is False
    assert is_upload_source(None) is False
    assert upload_display_name("upload://song.mp3") == "song.mp3"
    assert upload_display_name("https://example.com/x") == "https://example.com/x"
