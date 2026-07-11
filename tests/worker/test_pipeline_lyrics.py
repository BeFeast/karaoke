"""Tests for the pipeline's lyrics precedence wiring.

Exercises :func:`karaoke.worker.pipeline._resolve_lyrics` directly (pure file
I/O + provenance) so we lock down the precedence without standing up the full
async job / GPU / DB machinery — plus one end-to-end ``run_real_job`` check
(mocked yt-dlp/ffmpeg/GPU/LRCLIB) that a duration hard-reject (#148) lands in
``exports/metadata.json``.

Precedence: LRCLIB synced > LRCLIB text force-aligned when possible (plain
#55, or duration-rejected text #149) > LRCLIB plain (untimed) > Whisper ASR
(with an approximate segment-timed LRC when usable, #145); ``instrumental``
drops lyrics entirely.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from karaoke.worker.lyrics import (
    SOURCE_FORCED_ALIGNED,
    SOURCE_INSTRUMENTAL,
    SOURCE_LRCLIB_PLAIN,
    SOURCE_LRCLIB_SYNCED,
    SOURCE_WHISPER_ASR,
    SOURCE_WHISPER_ASR_SYNCED,
    LyricsResult,
    lrc_to_plain,
)
from karaoke.worker.pipeline import (
    _align_lang,
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
    assert lrc_to_plain("[00:12.00]hello\n[01:03.50]world") == "hello\nworld"
    # Handles bare [mm:ss] and blank lines.
    assert lrc_to_plain("[00:01]a\n\n[00:02]b") == "a\nb"


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


def test_synced_wins_over_nonmatching_aligned(tmp_path):
    """Native LRCLIB synced wins: a non-matching aligned LRC merges nothing, so
    the curated body is written verbatim and no word-timing flag is set (#222)."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    aligned = _aligned_file(tmp_path)  # ALIGNED text differs from SYNCED

    prov = _resolve_lyrics(
        LyricsResult(synced_lrc=SYNCED, plain="hello world\nsecond line", source="lrclib_get"),
        exports,
        whisper,
        aligned,
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_SYNCED
    assert "lyrics_word_timing" not in prov
    assert (exports / "lyrics.lrc").read_text() == SYNCED


def test_synced_merges_aligner_word_tags(tmp_path):
    """When the aligner timed the SAME curated text, its word ``<>`` tags are
    merged into the LRCLIB lines: provenance stays ``lrclib_synced``, the curated
    line tags/text are preserved, and ``lyrics_word_timing`` marks the merge."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    aligned = _aligned_file(
        tmp_path,
        body=(
            "[00:12.05]<00:12.05>hello <00:12.9>world <00:13.4>\n"
            "[00:15.55]<00:15.55>second <00:16.1>line <00:16.8>"
        ),
    )

    prov = _resolve_lyrics(
        LyricsResult(synced_lrc=SYNCED, plain="hello world\nsecond line", source="lrclib_get"),
        exports,
        whisper,
        aligned,
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_SYNCED  # provenance unchanged
    assert prov["synced"] is True
    assert prov["lyrics_word_timing"] == SOURCE_FORCED_ALIGNED
    lrc = (exports / "lyrics.lrc").read_text()
    # Curated LRCLIB line tags kept; aligner word tags spliced in.
    assert lrc == (
        "[00:12.00]<00:12.05>hello <00:12.90>world <00:13.40>\n"
        "[00:15.50]<00:15.55>second <00:16.10>line <00:16.80>"
    )
    # Line text stays byte-identical to the curated LRCLIB source.
    assert lrc_to_plain(lrc) == lrc_to_plain(SYNCED)
    # Plain-text export unchanged.
    assert (exports / "lyrics.txt").read_text() == "hello world\nsecond line"


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


# ---------------------------------------------------------------------------
# duration hard-reject provenance (#148): rejection reason flows to metadata
# ---------------------------------------------------------------------------
def test_rejected_lookup_falls_to_asr_floor_with_provenance(tmp_path):
    """A duration-rejected LRCLIB record behaves as a miss (ASR floor, synced
    when segments are usable) AND carries the rejection reason in provenance."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path, text="asr one\nasr two")
    lyrics_json = _whisper_json(tmp_path, SEGMENTS_JSON)

    prov = _resolve_lyrics(
        LyricsResult(source="none", rejected="duration_mismatch (28s)"),
        exports,
        whisper,
        None,
        lyrics_json,
    )
    assert prov["lyrics_source"] == SOURCE_WHISPER_ASR_SYNCED
    assert prov["lrc_written"] is True
    assert prov["lyrics_lrclib_rejected"] == "duration_mismatch (28s)"


def test_rejected_lookup_without_segments_keeps_reason(tmp_path):
    """Untimed ASR floor (no usable segments) still records the rejection."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)

    prov = _resolve_lyrics(
        LyricsResult(source="none", rejected="duration_mismatch (171s)"),
        exports,
        whisper,
        None,
        None,
    )
    assert prov["lyrics_source"] == SOURCE_WHISPER_ASR
    assert prov["lyrics_lrclib_rejected"] == "duration_mismatch (171s)"


def test_plain_miss_has_no_rejection_key(tmp_path):
    """A plain LRCLIB miss (nothing returned, nothing rejected) keeps the
    provenance mapping shape unchanged — no spurious key."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)

    prov = _resolve_lyrics(LyricsResult(source="none"), exports, whisper)
    assert "lyrics_lrclib_rejected" not in prov
    assert "lyrics_align_reason" not in prov


# ---------------------------------------------------------------------------
# rejected-text force-align (#149): right text + timings from the actual audio
# ---------------------------------------------------------------------------
REJECTED_WITH_TEXT = LyricsResult(
    source="none",
    rejected="duration_mismatch (28s)",
    rejected_text="plain one\nplain two",
)


def test_rejected_text_plus_aligned_lrc_writes_forced_aligned(tmp_path):
    """Duration-rejected LRCLIB text + a usable GPU-aligned LRC → the synced
    export carries the salvaged text with timings from the actual audio."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    aligned = _aligned_file(tmp_path)

    prov = _resolve_lyrics(REJECTED_WITH_TEXT, exports, whisper, aligned)

    assert prov["lyrics_source"] == SOURCE_FORCED_ALIGNED
    assert prov["synced"] is True
    assert prov["lrc_written"] is True
    assert prov["lyrics_align_reason"] == "lrclib_duration_mismatch (28s)"
    # The align reason tells the whole story; the record's text was used, so it
    # is not reported as "rejected".
    assert "lyrics_lrclib_rejected" not in prov
    # The GPU-aligned LRC body is written verbatim; the plain export keeps the
    # salvaged LRCLIB text (not the Whisper transcript).
    assert (exports / "lyrics.lrc").read_text() == ALIGNED
    assert (exports / "lyrics.txt").read_text() == "plain one\nplain two"


def test_rejected_text_without_aligned_falls_to_asr_floor(tmp_path):
    """No aligned LRC came back (old image / alignment failed) → the post-#145
    whisper_asr_synced floor, with the #148 rejection reason preserved."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path, text="asr one\nasr two")
    lyrics_json = _whisper_json(tmp_path, SEGMENTS_JSON)

    prov = _resolve_lyrics(REJECTED_WITH_TEXT, exports, whisper, None, lyrics_json)

    assert prov["lyrics_source"] == SOURCE_WHISPER_ASR_SYNCED
    assert prov["lrc_written"] is True
    assert prov["lyrics_lrclib_rejected"] == "duration_mismatch (28s)"
    assert "lyrics_align_reason" not in prov
    # The floor keeps the Whisper transcript, not the salvaged text.
    assert (exports / "lyrics.txt").read_text() == "asr one\nasr two"


def test_rejected_text_with_garbage_aligned_falls_to_asr_floor(tmp_path):
    """An aligned file with no timestamp tags is rejected by _read_aligned_lrc
    → whisper_asr_synced floor, same as a missing file."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path, text="asr one\nasr two")
    lyrics_json = _whisper_json(tmp_path, SEGMENTS_JSON)
    bad = _aligned_file(tmp_path, body="not an lrc, no timestamps here")

    prov = _resolve_lyrics(REJECTED_WITH_TEXT, exports, whisper, bad, lyrics_json)

    assert prov["lyrics_source"] == SOURCE_WHISPER_ASR_SYNCED
    assert prov["lyrics_lrclib_rejected"] == "duration_mismatch (28s)"
    assert (exports / "lyrics.lrc").read_text() != "not an lrc, no timestamps here"


def test_rejected_text_garbage_aligned_no_segments_untimed_floor(tmp_path):
    """Garbage aligned LRC AND no usable segments → the untimed ASR floor,
    still carrying the rejection reason."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path, text="ASR OUTPUT")

    prov = _resolve_lyrics(REJECTED_WITH_TEXT, exports, whisper, None, None)

    assert prov["lyrics_source"] == SOURCE_WHISPER_ASR
    assert prov["lrc_written"] is False
    assert prov["lyrics_lrclib_rejected"] == "duration_mismatch (28s)"
    assert not (exports / "lyrics.lrc").exists()


def test_rejection_without_text_skips_align_branch(tmp_path):
    """A rejected record with nothing to salvage (#148 shape) never consults
    the aligned LRC — even a usable one (it would carry someone else's text)."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path, text="asr one\nasr two")
    aligned = _aligned_file(tmp_path)

    prov = _resolve_lyrics(
        LyricsResult(source="none", rejected="duration_mismatch (171s)"),
        exports,
        whisper,
        aligned,
    )
    assert prov["lyrics_source"] == SOURCE_WHISPER_ASR
    assert prov["lyrics_lrclib_rejected"] == "duration_mismatch (171s)"


@pytest.mark.asyncio
async def test_run_real_job_writes_rejection_to_metadata(tmp_path, monkeypatch):
    """End-to-end (mocked yt-dlp/ffmpeg/GPU/LRCLIB): the only search candidate
    is the wrong edit (257 s vs the actual 229 s) → the job completes on the
    whisper_asr_synced floor and ``metadata.json`` records the rejection."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.config import Settings
    from karaoke.db.models import Base, Job, JobStatus
    from karaoke.db.session import create_engine_and_sessionmaker
    from karaoke.worker.lyrics import LyricsSource
    from karaoke.worker.runpod_client import RunpodClient
    from karaoke.worker.vast_client import GpuJobResult

    url = f"sqlite+aiosqlite:///{tmp_path / 'lyr.db'}"
    engine, factory = create_engine_and_sessionmaker(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            job = Job(
                job_token="tok-lyr",
                owner_subject="owner",
                source_url="https://example.com/song",
                status=JobStatus.queued,
                progress=0,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        monkeypatch.setattr(
            pipeline,
            "_ytdlp_metadata",
            lambda url, settings=None, **_: {
                "title": "Artist - Song",
                "artist": "Artist",
                "track": "Song",
                "duration": 229,
            },
        )

        def fake_file(src, dest: Path, *a, **k):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")
            return dest

        monkeypatch.setattr(pipeline, "_download_audio", fake_file)
        monkeypatch.setattr(pipeline, "_to_wav", fake_file)
        monkeypatch.setattr(pipeline, "_wav_to_mp3", fake_file)

        # LRCLIB: /api/get misses; /api/search has only the wrong edit.
        script = [
            (404, {"code": 404}),
            (
                200,
                [
                    {
                        "trackName": "Song",
                        "duration": 257,
                        "syncedLyrics": "[00:10.00]wrong edit line",
                        "plainLyrics": "wrong edit line",
                    }
                ],
            ),
        ]
        monkeypatch.setattr(
            pipeline,
            "_LYRICS_SOURCE",
            LyricsSource(http=lambda method, url, params: script.pop(0)),
        )

        def fake_gpu_run(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None):
            work_dir.mkdir(parents=True, exist_ok=True)
            voc = work_dir / "vocals.wav"
            inst = work_dir / "instrumental.wav"
            ltxt = work_dir / "lyrics.txt"
            ljson = work_dir / "lyrics.json"
            voc.write_bytes(b"v")
            inst.write_bytes(b"i")
            ltxt.write_text("asr line", encoding="utf-8")
            ljson.write_text(
                '{"segments": [{"start": 1.0, "end": 2.0, "text": "asr line"}]}',
                encoding="utf-8",
            )
            return GpuJobResult(
                vast_instance_id="rp-1",
                vast_cost=0.01,
                gpu_model="RTX 4090",
                vocals_path=voc,
                instrumental_path=inst,
                lyrics_txt_path=ltxt,
                lyrics_json_path=ljson,
            )

        monkeypatch.setattr(RunpodClient, "run", fake_gpu_run)

        settings = Settings(
            device_mode="runpod",
            runpod_api_key="k",
            runpod_endpoint_id="ep",
            artifact_root=str(tmp_path),
        )
        await pipeline.run_real_job(factory, job_id, settings)

        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job.status == JobStatus.completed, job.error

        meta = json.loads(
            (tmp_path / "tok-lyr" / "exports" / "metadata.json").read_text(encoding="utf-8")
        )
        assert meta["lyrics_lrclib_rejected"] == "duration_mismatch (28s)"
        assert meta["lyrics_source"] == SOURCE_WHISPER_ASR_SYNCED
        assert meta["synced"] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_real_job_force_aligns_rejected_text(tmp_path, monkeypatch):
    """End-to-end (#149): the only search candidate is the wrong edit but
    carries synced text → the GPU payload gets ``align_text`` with timestamps
    stripped, the handler's ``aligned_lrc`` is exported as ``lyrics.lrc`` with
    ``forced_aligned`` provenance + ``lyrics_align_reason``."""
    import karaoke.worker.pipeline as pipeline
    from karaoke.config import Settings
    from karaoke.db.models import Base, Job, JobStatus
    from karaoke.db.session import create_engine_and_sessionmaker
    from karaoke.worker.lyrics import LyricsSource
    from karaoke.worker.runpod_client import RunpodClient
    from karaoke.worker.vast_client import GpuJobResult

    url = f"sqlite+aiosqlite:///{tmp_path / 'lyr149.db'}"
    engine, factory = create_engine_and_sessionmaker(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            job = Job(
                job_token="tok-149",
                owner_subject="owner",
                source_url="https://example.com/song",
                status=JobStatus.queued,
                progress=0,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        monkeypatch.setattr(
            pipeline,
            "_ytdlp_metadata",
            lambda url, settings=None, **_: {
                "title": "Artist - Song",
                "artist": "Artist",
                "track": "Song",
                "duration": 229,
            },
        )

        def fake_file(src, dest: Path, *a, **k):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")
            return dest

        monkeypatch.setattr(pipeline, "_download_audio", fake_file)
        monkeypatch.setattr(pipeline, "_to_wav", fake_file)
        monkeypatch.setattr(pipeline, "_wav_to_mp3", fake_file)

        # LRCLIB: /api/get misses; /api/search has only the wrong edit, with
        # synced-only text (the timestamps must be stripped before alignment).
        script = [
            (404, {"code": 404}),
            (
                200,
                [
                    {
                        "trackName": "Song",
                        "duration": 257,
                        "syncedLyrics": "[00:10.00]right text one\n[00:14.00]right text two",
                    }
                ],
            ),
        ]
        monkeypatch.setattr(
            pipeline,
            "_LYRICS_SOURCE",
            LyricsSource(http=lambda method, url, params: script.pop(0)),
        )

        ALIGNED_BODY = "[00:02.10]right text one\n[00:05.40]right text two"
        captured: dict[str, object] = {}

        def fake_gpu_run(self, mix_wav, work_dir: Path, *, align_text=None, align_lang=None):
            captured["align_text"] = align_text
            captured["align_lang"] = align_lang
            work_dir.mkdir(parents=True, exist_ok=True)
            voc = work_dir / "vocals.wav"
            inst = work_dir / "instrumental.wav"
            ltxt = work_dir / "lyrics.txt"
            ljson = work_dir / "lyrics.json"
            alrc = work_dir / "aligned.lrc"
            voc.write_bytes(b"v")
            inst.write_bytes(b"i")
            ltxt.write_text("asr line", encoding="utf-8")
            ljson.write_text(
                '{"segments": [{"start": 1.0, "end": 2.0, "text": "asr line"}]}',
                encoding="utf-8",
            )
            alrc.write_text(ALIGNED_BODY, encoding="utf-8")
            return GpuJobResult(
                vast_instance_id="rp-1",
                vast_cost=0.01,
                gpu_model="RTX 4090",
                vocals_path=voc,
                instrumental_path=inst,
                lyrics_txt_path=ltxt,
                lyrics_json_path=ljson,
                aligned_lrc_path=alrc,
            )

        monkeypatch.setattr(RunpodClient, "run", fake_gpu_run)

        settings = Settings(
            device_mode="runpod",
            runpod_api_key="k",
            runpod_endpoint_id="ep",
            artifact_root=str(tmp_path),
        )
        await pipeline.run_real_job(factory, job_id, settings)

        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job.status == JobStatus.completed, job.error

        # The GPU payload carried the salvaged text, timestamps stripped.
        assert captured["align_text"] == "right text one\nright text two"
        assert captured["align_lang"] == "eng"

        exports = tmp_path / "tok-149" / "exports"
        meta = json.loads((exports / "metadata.json").read_text(encoding="utf-8"))
        assert meta["lyrics_source"] == SOURCE_FORCED_ALIGNED
        assert meta["synced"] is True
        assert meta["lyrics_align_reason"] == "lrclib_duration_mismatch (28s)"
        assert "lyrics_lrclib_rejected" not in meta
        assert (exports / "lyrics.lrc").read_text(encoding="utf-8") == ALIGNED_BODY
        assert (
            exports / "lyrics.txt"
        ).read_text(encoding="utf-8") == "right text one\nright text two"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# align-coverage gate (#237): curated text that barely aligns = wrong match
# ---------------------------------------------------------------------------

def _many_line_lrc(n: int) -> str:
    return "\n".join(f"[{i:02d}:00.00]line number {i}" for i in range(n))


def _aligned_covering(lines: str, upto: int) -> str:
    """Enhanced-LRC aligner output word-matching only the first ``upto`` lines."""
    out = []
    for i, raw in enumerate(lines.splitlines()):
        if i >= upto:
            break
        text = raw[raw.index("]") + 1 :]
        words = text.split()
        base = i * 60
        tags = " ".join(f"<{base // 60:02d}:{(base % 60) + j:05.2f}>{w}" for j, w in enumerate(words))
        out.append(f"[{base // 60:02d}:00.00]{tags} <{base // 60:02d}:59.00>")
    return "\n".join(out)


def test_low_align_coverage_rejects_synced_to_whisper_floor(tmp_path):
    """4/12 (33%>x) — below 30%? use 3/12=25%: curated record rejected, ASR floor
    ships, and metadata records the coverage rejection."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path, "asr words here")
    synced = _many_line_lrc(12)
    aligned = _aligned_covering(synced, 3)  # 3/12 = 25% < 30%
    prov = _resolve_lyrics(
        LyricsResult(synced_lrc=synced, plain=lrc_to_plain(synced), source="lrclib_get"),
        exports,
        whisper,
        aligned_lrc_path=_aligned_file(tmp_path, aligned),
    )
    assert prov["lyrics_source"] == SOURCE_WHISPER_ASR
    assert prov["lyrics_lrclib_rejected"].startswith("align_coverage_low (3/12)")
    assert (exports / "lyrics.txt").read_text() == "asr words here"
    assert not (exports / "lyrics.lrc").exists()


def test_good_align_coverage_keeps_synced(tmp_path):
    """9/12 = 75% coverage: curated record ships with word timing as before."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    synced = _many_line_lrc(12)
    aligned = _aligned_covering(synced, 9)
    prov = _resolve_lyrics(
        LyricsResult(synced_lrc=synced, plain=lrc_to_plain(synced), source="lrclib_get"),
        exports,
        whisper,
        aligned_lrc_path=_aligned_file(tmp_path, aligned),
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_SYNCED
    assert prov["lyrics_word_timing"] == SOURCE_FORCED_ALIGNED


def test_small_records_skip_the_coverage_gate(tmp_path):
    """eligible < 10: ratio is noise — record ships even with 0 merged lines."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    synced = _many_line_lrc(5)
    prov = _resolve_lyrics(
        LyricsResult(synced_lrc=synced, plain=lrc_to_plain(synced), source="lrclib_get"),
        exports,
        whisper,
        aligned_lrc_path=_aligned_file(tmp_path, "[00:00.00]unrelated aligner text"),
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_SYNCED


def test_no_aligned_lrc_skips_the_coverage_gate(tmp_path):
    """Alignment never ran: today's behavior — curated synced ships plain."""
    exports = tmp_path / "exports"
    exports.mkdir()
    whisper = _whisper(tmp_path)
    synced = _many_line_lrc(12)
    prov = _resolve_lyrics(
        LyricsResult(synced_lrc=synced, plain=lrc_to_plain(synced), source="lrclib_get"),
        exports,
        whisper,
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_SYNCED
