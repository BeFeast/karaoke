"""Tests for the pipeline's lyrics precedence wiring.

Exercises :func:`karaoke.worker.pipeline._resolve_lyrics` directly (pure file
I/O + provenance) so we lock down the precedence without standing up the full
async job / GPU / DB machinery.

Precedence: LRCLIB synced > LRCLIB plain > Whisper ASR; ``instrumental`` drops
lyrics entirely.
"""
from __future__ import annotations

from pathlib import Path

from karaoke.worker.lyrics import (
    SOURCE_INSTRUMENTAL,
    SOURCE_LRCLIB_PLAIN,
    SOURCE_LRCLIB_SYNCED,
    SOURCE_WHISPER_ASR,
    LyricsResult,
)
from karaoke.worker.pipeline import _lrc_to_plain, _resolve_lyrics

SYNCED = "[00:12.00]hello world\n[00:15.50]second line"


def _whisper(tmp_path: Path, text: str = "whisper transcript") -> Path:
    p = tmp_path / "work" / "lyrics.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_synced_writes_lrc_and_plain(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)

    prov = _resolve_lyrics(
        LyricsResult(synced_lrc=SYNCED, plain="hello world\nsecond line", source="lrclib_get"),
        exports,
        whisper,
    )

    assert prov["lyrics_source"] == SOURCE_LRCLIB_SYNCED
    assert prov["synced"] is True
    assert prov["lrc_written"] is True
    assert (exports / "lyrics.lrc").read_text() == SYNCED
    # Plain export prefers the supplied plain text.
    assert (exports / "lyrics.txt").read_text() == "hello world\nsecond line"


def test_synced_without_plain_derives_plain_from_lrc(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)

    prov = _resolve_lyrics(
        LyricsResult(synced_lrc=SYNCED, plain=None, source="lrclib_get"),
        exports,
        whisper,
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_SYNCED
    assert (exports / "lyrics.txt").read_text() == "hello world\nsecond line"


def test_plain_only_writes_txt_no_lrc(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)

    prov = _resolve_lyrics(
        LyricsResult(plain="plain lyrics body", source="lrclib_get"),
        exports,
        whisper,
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_PLAIN
    assert prov["synced"] is False
    assert prov["lrc_written"] is False
    assert (exports / "lyrics.txt").read_text() == "plain lyrics body"
    assert not (exports / "lyrics.lrc").exists()


def test_instrumental_drops_lyrics(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    # Stage a stale whisper transcript that must be removed.
    (exports / "lyrics.txt").write_text("stale", encoding="utf-8")
    whisper = _whisper(tmp_path)

    prov = _resolve_lyrics(
        LyricsResult(instrumental=True, source="instrumental"),
        exports,
        whisper,
    )
    assert prov["lyrics_source"] == SOURCE_INSTRUMENTAL
    assert prov["instrumental"] is True
    assert prov["lrc_written"] is False
    assert not (exports / "lyrics.txt").exists()
    assert not (exports / "lyrics.lrc").exists()


def test_no_match_keeps_whisper(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path, text="WHISPER ASR OUTPUT")

    prov = _resolve_lyrics(LyricsResult(source="none"), exports, whisper)

    assert prov["lyrics_source"] == SOURCE_WHISPER_ASR
    assert prov["synced"] is False
    assert prov["lrc_written"] is False
    assert (exports / "lyrics.txt").read_text() == "WHISPER ASR OUTPUT"
    assert not (exports / "lyrics.lrc").exists()


def test_lrc_to_plain_strips_timestamps():
    assert _lrc_to_plain("[00:12.00]hello\n[01:03.50]world") == "hello\nworld"
    # Handles bare [mm:ss] and blank lines.
    assert _lrc_to_plain("[00:01]a\n\n[00:02]b") == "a\nb"
