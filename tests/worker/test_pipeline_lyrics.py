"""Tests for the pipeline's lyrics precedence wiring.

Exercises :func:`karaoke.worker.pipeline._resolve_lyrics` directly (pure file
I/O + provenance) so we lock down the precedence without standing up the full
async job / GPU / DB machinery.

Precedence: LRCLIB synced > LRCLIB plain (force-aligned when possible) >
Whisper ASR (with an approximate segment-timed LRC when usable, #145);
``instrumental`` drops lyrics entirely.
"""
from __future__ import annotations

from pathlib import Path

from karaoke.worker.lyrics import (
    SOURCE_FORCED_ALIGNED,
    SOURCE_INSTRUMENTAL,
    SOURCE_LRCLIB_PLAIN,
    SOURCE_LRCLIB_SYNCED,
    SOURCE_WHISPER_ASR,
    SOURCE_WHISPER_ASR_SYNCED,
    LyricsResult,
)
from karaoke.worker.pipeline import (
    _align_lang,
    _lrc_to_plain,
    _read_aligned_lrc,
    _read_whisper_segments,
    _resolve_lyrics,
)

SYNCED = "[00:12.00]hello world\n[00:15.50]second line"
ALIGNED = "[00:01.00]plain one\n[00:02.50]plain two"


def _aligned_file(tmp_path: Path, body: str = ALIGNED, name: str = "aligned.lrc") -> Path:
    p = tmp_path / "work" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


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


# ---------------------------------------------------------------------------
# force-align (#55): plain + GPU aligned LRC → synced (provenance forced_aligned)
# ---------------------------------------------------------------------------
def test_plain_plus_aligned_lrc_writes_synced(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    aligned = _aligned_file(tmp_path)

    prov = _resolve_lyrics(
        LyricsResult(plain="plain one\nplain two", source="lrclib_get"),
        exports,
        whisper,
        aligned,
    )

    assert prov["lyrics_source"] == SOURCE_FORCED_ALIGNED
    assert prov["synced"] is True
    assert prov["lrc_written"] is True
    # The GPU-aligned LRC body is written verbatim.
    assert (exports / "lyrics.lrc").read_text() == ALIGNED
    # Plain export keeps the LRCLIB plain text (not the Whisper transcript).
    assert (exports / "lyrics.txt").read_text() == "plain one\nplain two"


def test_synced_wins_over_aligned(tmp_path):
    """Native LRCLIB synced always wins; the aligned LRC is ignored."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    aligned = _aligned_file(tmp_path)

    prov = _resolve_lyrics(
        LyricsResult(synced_lrc=SYNCED, plain="hello world\nsecond line", source="lrclib_get"),
        exports,
        whisper,
        aligned,
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_SYNCED
    assert (exports / "lyrics.lrc").read_text() == SYNCED


def test_plain_only_no_aligned_falls_back_to_plain(tmp_path):
    """Old image / alignment failed → no aligned LRC → plain-only (untimed)."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)

    prov = _resolve_lyrics(
        LyricsResult(plain="plain body", source="lrclib_get"),
        exports,
        whisper,
        None,  # no aligned LRC came back from the GPU
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_PLAIN
    assert prov["synced"] is False
    assert prov["lrc_written"] is False
    assert (exports / "lyrics.txt").read_text() == "plain body"
    assert not (exports / "lyrics.lrc").exists()


def test_plain_with_empty_aligned_falls_back_to_plain(tmp_path):
    """An aligned file that is empty or has no timestamp tag is rejected."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    bad = _aligned_file(tmp_path, body="not an lrc, no timestamps here")

    prov = _resolve_lyrics(
        LyricsResult(plain="plain body", source="lrclib_get"),
        exports,
        whisper,
        bad,
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_PLAIN
    assert prov["lrc_written"] is False
    assert not (exports / "lyrics.lrc").exists()


def test_read_aligned_lrc_tolerant():
    # None / missing path → None.
    assert _read_aligned_lrc(None) is None
    assert _read_aligned_lrc(Path("/nonexistent/aligned.lrc")) is None


def test_read_aligned_lrc_validates_body(tmp_path):
    good = _aligned_file(tmp_path, body="[00:01.00]hi")
    assert _read_aligned_lrc(good) == "[00:01.00]hi"
    empty = _aligned_file(tmp_path, body="   \n  ", name="empty.lrc")
    assert _read_aligned_lrc(empty) is None
    no_ts = _aligned_file(tmp_path, body="hello\nworld", name="no_ts.lrc")
    assert _read_aligned_lrc(no_ts) is None


def test_align_lang_maps_iso639():
    assert _align_lang({"language": "en"}) == "eng"
    assert _align_lang({"language": "ru"}) == "rus"
    assert _align_lang({"lang": "es"}) == "spa"
    # Already ISO-639-3 passes through.
    assert _align_lang({"language": "jpn"}) == "jpn"
    # Unknown / absent → English fallback.
    assert _align_lang({}) == "eng"
    assert _align_lang({"language": "??"}) == "eng"


# ---------------------------------------------------------------------------
# ASR floor sync (#145): LRCLIB miss + Whisper segments → approximate LRC
# ---------------------------------------------------------------------------
def _whisper_json(tmp_path: Path, body: str, name: str = "lyrics.json") -> Path:
    p = tmp_path / "work" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


SEGMENTS_JSON = (
    '{"language": "en", "duration": 30.0, "segments": ['
    '{"start": 1.5, "end": 4.0, "text": " asr one "},'
    '{"start": 10.25, "end": 12.0, "text": "asr two"}]}'
)


def test_no_match_with_segments_writes_approximate_lrc(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path, text="asr one\nasr two")
    lyrics_json = _whisper_json(tmp_path, SEGMENTS_JSON)

    prov = _resolve_lyrics(
        LyricsResult(source="none"), exports, whisper, None, lyrics_json
    )

    assert prov["lyrics_source"] == SOURCE_WHISPER_ASR_SYNCED
    assert prov["synced"] is True
    assert prov["lrc_written"] is True
    assert (exports / "lyrics.lrc").read_text() == "[00:01.50]asr one\n[00:10.25]asr two"
    # The plain export stays the Whisper transcript.
    assert (exports / "lyrics.txt").read_text() == "asr one\nasr two"


def test_no_match_without_segments_stays_untimed(tmp_path):
    """No lyrics.json at all (old GPU result shape) → untimed ASR floor."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path, text="ASR OUTPUT")

    prov = _resolve_lyrics(LyricsResult(source="none"), exports, whisper, None, None)

    assert prov["lyrics_source"] == SOURCE_WHISPER_ASR
    assert prov["synced"] is False
    assert prov["lrc_written"] is False
    assert not (exports / "lyrics.lrc").exists()


def test_no_match_with_unusable_segments_stays_untimed(tmp_path):
    """Bad / empty / segment-less lyrics.json degrades to the untimed floor."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)

    for name, body in [
        ("garbage.json", "not json at all"),
        ("nosegs.json", '{"language": "en"}'),
        ("notalist.json", '{"segments": "nope"}'),
        ("emptysegs.json", '{"segments": []}'),
        ("blanktext.json", '{"segments": [{"start": 1.0, "text": "  "}]}'),
    ]:
        bad = _whisper_json(tmp_path, body, name=name)
        prov = _resolve_lyrics(
            LyricsResult(source="none"), exports, whisper, None, bad
        )
        assert prov["lyrics_source"] == SOURCE_WHISPER_ASR, name
        assert prov["lrc_written"] is False, name
        assert not (exports / "lyrics.lrc").exists(), name


def test_lrclib_sources_ignore_whisper_segments(tmp_path):
    """Any LRCLIB hit outranks the ASR-synced floor; segments are not consulted."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    lyrics_json = _whisper_json(tmp_path, SEGMENTS_JSON)

    prov = _resolve_lyrics(
        LyricsResult(synced_lrc=SYNCED, plain="hello world\nsecond line", source="lrclib_get"),
        exports,
        whisper,
        None,
        lyrics_json,
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_SYNCED
    assert (exports / "lyrics.lrc").read_text() == SYNCED

    prov = _resolve_lyrics(
        LyricsResult(plain="plain body", source="lrclib_get"),
        exports,
        whisper,
        None,
        lyrics_json,
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_PLAIN
    assert prov["lrc_written"] is False


def test_instrumental_ignores_whisper_segments(tmp_path):
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    lyrics_json = _whisper_json(tmp_path, SEGMENTS_JSON)

    prov = _resolve_lyrics(
        LyricsResult(instrumental=True, source="instrumental"),
        exports,
        whisper,
        None,
        lyrics_json,
    )
    assert prov["lyrics_source"] == SOURCE_INSTRUMENTAL
    assert not (exports / "lyrics.lrc").exists()


def test_read_whisper_segments_tolerant(tmp_path):
    assert _read_whisper_segments(None) is None
    assert _read_whisper_segments(Path("/nonexistent/lyrics.json")) is None
    good = _whisper_json(tmp_path, SEGMENTS_JSON)
    segs = _read_whisper_segments(good)
    assert isinstance(segs, list) and len(segs) == 2
