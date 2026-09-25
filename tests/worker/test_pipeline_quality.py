"""Integration: persisted quality audits the selected export and all evidence."""
import json

from karaoke.worker.lyrics import LyricsResult
from karaoke.worker.pipeline import _resolve_lyrics


def _inputs(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    export = tmp_path / "exports"
    export.mkdir()
    (work / "lyrics.txt").write_text("silver river\nbright meadow")
    asr = {"segments": [{"text": text, "start": start, "end": start + 0.9,
                         "words": [{"word": w, "start": start + i * 0.5,
                                    "end": start + i * 0.5 + 0.4, "probability": 0.99}
                                   for i, w in enumerate(text.split())]}
                        for text, start in [("silver river", 1), ("bright meadow", 5)]]}
    (work / "lyrics.json").write_text(json.dumps(asr))
    aligned = "[00:01.00]<00:01.00>silver <00:01.50>river <00:01.90>\n[00:05.00]<00:05.00>bright <00:05.50>meadow <00:05.90>"
    (work / "aligned.lrc").write_text(aligned)
    return work, export


def test_full_reference_omission_persists_needs_review(tmp_path):
    work, export = _inputs(tmp_path)
    result = _resolve_lyrics(LyricsResult(plain="silver river\ngolden daylight\nbright meadow", source="lrclib_get"),
                             export, work / "lyrics.txt", work / "aligned.lrc", work / "lyrics.json")
    quality = result["lyrics_quality"]
    assert quality["status"] == "needs_review"
    assert quality["counts"]["expected_lines"] == 3
    assert quality["counts"]["matched_lines"] == 2
    assert quality["counts"]["missing_lines"] == 1
    assert json.loads((export / "lyrics.quality.json").read_text()) == quality
    assert "golden daylight" not in (export / "lyrics.lrc").read_text()


def test_complete_evidence_is_automatically_checked_not_human_reviewed(tmp_path):
    work, export = _inputs(tmp_path)
    result = _resolve_lyrics(LyricsResult(plain="silver river\nbright meadow", source="lrclib_get"),
                             export, work / "lyrics.txt", work / "aligned.lrc", work / "lyrics.json")
    assert result["lyrics_quality"]["status"] == "checked"
    assert "reviewed_at" not in result["lyrics_quality"]


def test_additional_crop_observations_survive_selection(tmp_path):
    work, export = _inputs(tmp_path)
    diagnostics = {"retries": [{"outcome": "rejected", "reason": "text_not_corroborated",
        "evidence_segments": [{"text": "extra words", "start": 3, "end": 3.9, "words": [
            {"word": "extra", "start": 3, "end": 3.4, "probability": .99},
            {"word": "words", "start": 3.5, "end": 3.9, "probability": .99}]}]}]}
    (work / "aligned.diagnostics.json").write_text(json.dumps(diagnostics))
    result = _resolve_lyrics(LyricsResult(plain="silver river\nbright meadow", source="lrclib_get"),
                             export, work / "lyrics.txt", work / "aligned.lrc", work / "lyrics.json")
    assert result["lyrics_quality"]["status"] == "needs_review"
    assert result["lyrics_quality"]["counts"]["asr_unmatched_words"] == 2
    assert result["lyrics_quality"]["retries"][0]["outcome"] == "rejected"


def test_untimed_result_removes_old_timed_export(tmp_path):
    work, export = _inputs(tmp_path)
    (export / "lyrics.lrc").write_text("[00:01]stale result")
    result = _resolve_lyrics(LyricsResult(plain="silver river", source="lrclib_get"),
                             export, work / "lyrics.txt")
    assert result["lyrics_quality"]["status"] == "needs_review"
    assert not (export / "lyrics.lrc").exists()
