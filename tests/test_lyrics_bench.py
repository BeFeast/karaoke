"""Benchmark harness cases on synthetic snippets — no song lyrics in fixtures."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "lyrics_bench", Path(__file__).parents[1] / "scripts" / "lyrics_bench.py"
)
bench = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bench  # dataclasses resolve postponed annotations via sys.modules
SPEC.loader.exec_module(bench)

SONG = {"id": "synthetic", "group": "hebrew", "language": "he", "title": "שיר"}


def test_parse_lrc_strips_tags_and_sorts():
    text = bench.parse_lrc("[ti:x]\n[00:10.50]<00:10.50>שורה <00:11.00>ראשונה\n[00:05.00]אפס\n\n")
    assert [(ln.text, ln.start) for ln in text.lines] == [("אפס", 5.0), ("שורה ראשונה", 10.5)]
    assert text.timed


def test_parse_whisper_json_uses_segment_text():
    body = json.dumps({"segments": [{"start": 1.0, "text": " a b "}, {"start": 2.0, "text": ""}]})
    text = bench.parse_whisper_json(body)
    assert [(ln.text, ln.start) for ln in text.lines] == [("a b", 1.0)]
    assert text.source == "whisper_asr"


def test_load_text_file_dispatches_on_content(tmp_path):
    plain = tmp_path / "x.txt"
    plain.write_text("one\ntwo\n")
    assert not bench.load_text_file(plain).timed
    lrc = tmp_path / "y.txt"
    lrc.write_text("[00:01.00]one\n")
    assert bench.load_text_file(lrc).timed


def test_load_share_prefers_lrc_and_keeps_source():
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"synced": True, "lrc": "[00:01.00]a\n[00:02.00]b", "plain": "a\nb", "source": "lrclib_synced"}

    text = bench.load_share("http://h/share/tok", http=lambda url, **kw: Resp())
    assert text.source == "lrclib_synced" and text.timed and len(text.lines) == 2


def test_missing_regions_groups_consecutive_lines_with_times():
    ref = bench.parse_lrc("[00:10.00]אחת שתיים\n[00:20.00]שלוש ארבע\n[00:30.00]חמש שש\n[00:40.00]שבע שמונה")
    hyp = bench.parse_plain("אחת שתיים\nשבע שמונה")
    count, regions = bench.missing_regions(ref, hyp, "he")
    assert count == 2
    assert regions == ["L2-L3 [00:20-00:40]"]


def test_missing_regions_without_times_uses_line_numbers():
    ref = bench.parse_plain("a b\nc d")
    _, regions = bench.missing_regions(ref, bench.parse_plain("a b"), None)
    assert regions == ["L2"]


def test_score_song_metrics_and_skeleton():
    ref = bench.parse_plain("כל יום בתשע\nמילים")
    hyp = bench.parse_plain("כל יומ בתשע\nמלים")  # final-letter + ktiv haser differences only
    score = bench.score_song(SONG, ref, "genius", None, hyp)
    assert score.cer < 0.1
    assert score.cer_skel == 0.0
    assert score.missing_lines == 0


def test_run_score_reports_missing_reference(tmp_path):
    scores = bench.run_score([SONG], tmp_path, {"synthetic": bench.parse_plain("x")}, "auto")
    assert scores[0].cer is None and "no reference" in scores[0].note


def test_load_reference_prefers_synced_lrclib_then_genius(tmp_path):
    (tmp_path / "synthetic.genius.txt").write_text("א ב ג")
    (tmp_path / "synthetic.lrclib.txt").write_text("א ב")
    text, name, timed = bench.load_reference(tmp_path, SONG, "auto")
    assert name == "genius" and timed is None  # plain-only LRCLIB ranks below Genius
    (tmp_path / "synthetic.lrclib.lrc").write_text("[00:01.00]א ב")
    text, name, timed = bench.load_reference(tmp_path, SONG, "auto")
    assert name == "lrclib" and text.timed and timed is not None
    text, name, _ = bench.load_reference(tmp_path, SONG, "genius")
    assert name == "genius" and not text.timed


def test_render_table_has_medians_per_group():
    scores = [
        bench.Score(id="a", group="hebrew", source="s", reference="genius", cer=0.1, wer=0.2, cer_skel=0.05),
        bench.Score(id="b", group="hebrew", source="s", reference="genius", cer=0.3, wer=0.4, cer_skel=0.25),
        bench.Score(id="c", group="latin_control", source="s", reference="lrclib", cer=0.0, wer=0.0, cer_skel=0.0),
    ]
    table = bench.render_table(scores)
    assert "| hebrew | 2 | 0.2 | 0.3 | 0.15 |" in table
    assert "| latin_control | 1 | 0.0 | 0.0 | 0.0 |" in table


def test_load_songs_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "songs.json"
    path.write_text(json.dumps({"songs": [{"id": "x"}, {"id": "x"}]}))
    with pytest.raises(SystemExit):
        bench.load_songs(path)


def test_committed_song_set_is_metadata_only():
    songs = bench.load_songs(bench.DEFAULT_SONGS)
    assert len([s for s in songs if s["group"] == "hebrew"]) >= 12
    assert {s["group"] for s in songs} == {"hebrew", "latin_control"}
    for song in songs:
        assert set(song) & {"lyrics", "text", "plain", "lrc"} == set()
        assert song["youtube_id"] and song["title"]
