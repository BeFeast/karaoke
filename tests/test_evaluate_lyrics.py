"""Synthetic evaluator cases; no copyrighted recordings or lyric fixtures."""

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "evaluate_lyrics", Path(__file__).parents[1] / "scripts" / "evaluate_lyrics.py"
)
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


def manifest(tmp_path, text, lrc, times=None):
    (tmp_path / "candidate.lrc").write_text(lrc)
    reference = {"text": text}
    if times is not None:
        reference["words"] = [
            {"text": word, "start": start} for word, start in zip(text.split(), times, strict=True)
        ]
    return {
        "schema_version": 1,
        "entries": [
            {
                "id": "synthetic",
                "language": "en",
                "variant": "test-recording",
                "reference": reference,
                "candidate_lrc": "candidate.lrc",
            }
        ],
    }


def test_missing_negation_is_a_word_error(tmp_path):
    result = evaluator.evaluate(
        manifest(tmp_path, "we will not go", "[00:01.00]we will go"), tmp_path
    )
    metrics = result["aggregate"]
    assert metrics["deletions"] == 1
    assert metrics["wer"] == 0.25
    assert metrics["reference_words"] == 4
    assert metrics["timing_unscored_reference_words"] == 4
    assert metrics["word_start_mae_s"] is None


def test_extra_words_and_substitutions_count(tmp_path):
    result = evaluator.evaluate(
        manifest(tmp_path, "sing now", "[00:01.00]sing loudly tomorrow"), tmp_path
    )
    assert result["aggregate"]["insertions"] == 1
    assert result["aggregate"]["substitutions"] == 1
    assert result["aggregate"]["wer"] == 1


def test_complete_repeated_chorus_keeps_every_occurrence(tmp_path):
    text = "sing with me sing with me"
    lrc = "[00:01.00]<00:01.00>sing <00:02.00>with <00:03.00>me <00:04.00>\n[00:05.00]<00:05.00>sing <00:06.00>with <00:07.00>me <00:08.00>"
    result = evaluator.evaluate(manifest(tmp_path, text, lrc, [1, 2, 3, 5, 6, 7]), tmp_path)
    assert result["aggregate"]["reference_words"] == 6
    assert result["aggregate"]["wer"] == 0
    assert result["aggregate"]["word_timing_coverage"] == 1
    assert result["aggregate"]["word_start_mae_s"] == 0


def test_missing_repeated_chorus_does_not_invent_timing_matches(tmp_path):
    data = manifest(
        tmp_path,
        "sing with me sing with me",
        "[00:05.00]<00:05.00>sing <00:06.00>with <00:07.00>me",
        [1, 2, 3, 5, 6, 7],
    )
    result = evaluator.evaluate(data, tmp_path)
    assert result["aggregate"]["deletions"] == 3
    assert result["entries"][0]["alignment_ambiguous"] is True
    assert result["aggregate"]["timing_scored_words"] == 0
    assert result["aggregate"]["timing_unscored_reference_words"] == 6


def test_wrong_word_timing_is_measured_and_fails_threshold(tmp_path):
    data = manifest(
        tmp_path,
        "one two three",
        "[00:01.00]<00:01.00>one <00:02.50>two <00:04.00>three",
        [1, 2, 3],
    )
    result = evaluator.evaluate(data, tmp_path, {"max_word_start_p95_s": 0.2})
    assert result["aggregate"]["wer"] == 0
    assert result["aggregate"]["word_start_mae_s"] == 0.5
    assert result["aggregate"]["word_start_p50_s"] == 0.5
    assert result["aggregate"]["word_start_p95_s"] == 1
    assert result["violations"][0]["metric"] == "word_start_p95_s"


def test_unicode_and_punctuation_normalization(tmp_path):
    result = evaluator.evaluate(
        manifest(tmp_path, "CAFÉ don't stop", "[00:01.00]cafe\u0301, don’t STOP!"), tmp_path
    )
    assert result["aggregate"]["wer"] == 0


def test_line_timing_never_counts_as_word_timing(tmp_path):
    data = manifest(tmp_path, "one two", "[00:01.00]one two", [1, 2])
    result = evaluator.evaluate(
        data, tmp_path, {"max_word_start_mae_s": 0.1, "min_word_timing_coverage": 1}
    )
    assert result["aggregate"]["timed_reference_words"] == 2
    assert result["aggregate"]["timing_scored_words"] == 0
    assert len(result["violations"]) == 2


def test_partial_reference_labels_expose_full_denominator(tmp_path):
    data = manifest(tmp_path, "one two", "[00:01.00]<00:01.00>one <00:02.00>two", [1, None])
    result = evaluator.evaluate(data, tmp_path)
    assert result["aggregate"]["word_timing_coverage"] == 0.5
    assert result["aggregate"]["timed_reference_words"] == 1
    assert result["aggregate"]["timing_unscored_reference_words"] == 1


def test_mismatched_reference_labels_rejected(tmp_path):
    data = manifest(tmp_path, "one two", "[00:01.00]one two", [1, 2])
    data["entries"][0]["reference"]["words"].pop()
    with pytest.raises(ValueError, match="every normalized text token"):
        evaluator.evaluate(data, tmp_path)


def test_cli_exit_codes(tmp_path, capsys):
    data = manifest(tmp_path, "one two", "[00:01.00]one")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    limits = tmp_path / "limits.json"
    limits.write_text(json.dumps({"max_wer": 0}))
    assert evaluator.main([str(path)]) == 0
    assert evaluator.main([str(path), "--thresholds", str(limits)]) == 1
    assert '"deletions": 1' in capsys.readouterr().out
    limits.write_text('{"max_wer": -1}')
    with pytest.raises(SystemExit) as error:
        evaluator.main([str(path), "--thresholds", str(limits)])
    assert error.value.code == 2


def test_duplicate_ids_rejected(tmp_path):
    data = manifest(tmp_path, "one", "[00:01.00]one")
    data["entries"].append(data["entries"][0])
    with pytest.raises(ValueError, match="unique"):
        evaluator.evaluate(data, tmp_path)
