"""Synthetic lyrics only: exercise omissions, repetitions, edits and timing."""
from karaoke.worker.lyrics_quality import (
    _asr_words,
    assess_lyrics,
    reconcile_alignment,
)


def line(text="silver river", start=10.0, step=0.5):
    from karaoke.worker.lyrics import _fmt_lrc_timestamp
    words = text.split()
    return _fmt_lrc_timestamp(start) + " ".join(
        _fmt_lrc_timestamp(start + i * step, "<", ">") + word
        for i, word in enumerate(words)
    ) + " " + _fmt_lrc_timestamp(start + len(words) * step, "<", ">")


def asr(*entries):
    return {"segments": [{"text": text, "words": [
        {"word": word, "start": start + i * 0.5,
         "end": start + (i + 1) * 0.5, "probability": 0.99}
        for i, word in enumerate(text.split())
    ]} for text, start in entries]}


def codes(quality):
    return {issue["code"] for issue in quality["issues"]}


def test_complete_text_and_timing_is_only_automatically_checked():
    quality = assess_lyrics("silver river", line(), asr(("silver river", 10)))
    assert quality["status"] == "checked"
    assert quality["counts"]["expected_lines"] == quality["counts"]["matched_lines"] == 1
    assert quality["counts"]["expected_words"] == quality["counts"]["matched_words"] == 2


def test_full_reference_denominator_and_repeated_missing_line():
    curated = "silver river\nbright meadow\nsilver river"
    lrc = line("bright meadow", 10) + "\n" + line("silver river", 15)
    quality = assess_lyrics(curated, lrc, asr(("bright meadow", 10), ("silver river", 15)))
    assert quality["counts"]["expected_lines"] == 3
    assert quality["counts"]["matched_lines"] == 2
    assert quality["counts"]["missing_lines"] == 1
    assert quality["counts"]["missing_words"] == 2
    assert quality["status"] == "needs_review"


def test_partial_line_words_and_unexpected_repetition():
    quality = assess_lyrics("silver river flows", line("silver silver river"),
                            asr(("silver river flows", 10)))
    assert quality["counts"]["missing_words"] == 1
    assert quality["counts"]["extra_words"] == 1
    assert {"reference_words_missing", "unexpected_output_words",
            "asr_words_unrepresented"} <= codes(quality)


def test_score_outlier_restored_only_with_nearby_independent_evidence():
    texts = [f"silver river number {i}" for i in range(8)]
    raw = "\n".join(line(text, 10 + i * 4) for i, text in enumerate(texts))
    transcript = asr(*[(text, 10 + i * 4) for i, text in enumerate(texts)])
    result, quality = reconcile_alignment("\n".join(texts), raw, [-3] + [-0.1] * 7, transcript)
    assert len(result.splitlines()) == 8
    assert quality["counts"]["restored_lines"] == 1
    assert quality["counts"]["matched_lines"] == 8
    result, quality = reconcile_alignment("\n".join(texts), raw, [-3] + [-0.1] * 7, None)
    assert len(result.splitlines()) == 7
    assert quality["counts"]["restored_lines"] == 0
    assert quality["counts"]["missing_lines"] == 1


def test_shortened_edit_does_not_reinsert_squeezed_verse():
    raw = line("silver river flows beyond distant mountains", step=0.03)
    result, quality = reconcile_alignment("silver river flows beyond distant mountains",
                                         raw, None, asr(("unrelated words", 10)))
    assert result is None
    assert quality["counts"]["missing_lines"] == 1
    assert quality["status"] == "needs_review"


def test_repeated_verse_far_away_cannot_restore_earlier_occurrence():
    texts = ["silver river"] + [f"other words {i}" for i in range(7)]
    raw = "\n".join(line(text, 10 + i * 4) for i, text in enumerate(texts))
    result, quality = reconcile_alignment("\n".join(texts), raw, [-3] + [-0.1] * 7,
                                         asr(("silver river", 100)))
    assert quality["counts"]["restored_lines"] == 0
    assert len(result.splitlines()) == 7


def test_internal_tail_absorption_repaired_from_reliable_exact_asr():
    raw = "[00:10.00]<00:10.00>silver <00:10.50>river <00:30.00>flows <00:45.00>away <00:45.50>"
    result, quality = reconcile_alignment("silver river flows away", raw, None,
                                         asr(("silver river flows away", 10)))
    assert "<00:11.00>flows" in result
    assert "<00:11.50>away" in result
    assert quality["counts"]["repaired_timing_lines"] == 1
    assert "word_timing_gap" not in codes(quality)


def test_unreliable_or_wrong_asr_cannot_repair_tail():
    raw = "[00:10.00]<00:10.00>silver <00:10.50>river <00:30.00>flows <00:45.00>away <00:45.50>"
    evidence = asr(("silver river flows away", 10))
    evidence["segments"][0]["words"][2]["probability"] = 0.1
    result, quality = reconcile_alignment("silver river flows away", raw, None, evidence)
    assert "<00:30.00>flows" in result
    assert quality["counts"]["repaired_timing_lines"] == 0
    assert "word_timing_gap" in codes(quality)


def test_no_reference_and_segment_only_asr_are_not_verified():
    quality = assess_lyrics(None, "[00:10.00]silver river",
                            {"segments": [{"start": 10, "end": 11, "text": "silver river"}]})
    assert {"reference_unavailable", "asr_evidence_unavailable",
            "word_timing_missing"} <= codes(quality)
    assert quality["status"] == "needs_review"


def test_multi_timestamp_reference_preserves_occurrences():
    quality = assess_lyrics("[00:10.00][00:20.00]silver river", line(),
                            asr(("silver river", 10)))
    assert quality["counts"]["expected_lines"] == 2
    assert quality["counts"]["missing_lines"] == 1


def test_curated_match_does_not_hide_wrong_audio_text():
    quality = assess_lyrics("silver river", line(), asr(("golden meadow", 10)))
    assert quality["counts"]["missing_lines"] == 0
    assert quality["status"] == "needs_review"
    assert {"asr_line_unconfirmed", "asr_words_unrepresented"} <= codes(quality)


def test_nearby_asr_word_timing_disagreement_flagged_separately():
    quality = assess_lyrics("silver river", line(start=10), asr(("silver river", 11.5)))
    assert quality["counts"]["missing_words"] == 0
    assert "asr_word_timing_disagreement" in codes(quality)
    assert quality["timing_confidence"] == "uncertain"


def test_oversized_input_reports_review_without_truncation():
    quality = assess_lyrics("silver " * 2001, line(), None)
    assert "comparison_limit_exceeded" in codes(quality)
    assert quality["status"] == "needs_review"


def test_accepted_retry_replaces_overlapping_asr_instead_of_counting_twice():
    original = asr(("silver wrong", 10))
    original["retry_segments"] = asr(("silver river", 10.05))["segments"]
    assert [word.text for word in _asr_words(original)] == ["silver", "river"]
    quality = assess_lyrics("silver river", line(), original)
    assert quality["counts"]["asr_unmatched_words"] == 0


def test_malformed_evidence_does_not_certify_alignment():
    evidence = {"segments": [None, {"words": [
        {}, {"word": "silver", "start": float("nan"), "end": 10},
        {"word": "river", "start": 10, "end": 11, "probability": -1}
    ]}]}
    quality = assess_lyrics("silver river", line(), evidence)
    assert "asr_evidence_unavailable" in codes(quality)



def test_segment_text_with_untimed_voiced_words_requires_review():
    evidence = asr(("silver river", 10))
    evidence["segments"][0]["text"] = "silver river beyond mountains"
    quality = assess_lyrics("silver river", line(), evidence)
    assert "asr_segment_words_incomplete" in codes(quality)


def test_reconcile_oversized_raw_alignment_returns_review_without_exception():
    raw = line("silver " * 2001)
    result, quality = reconcile_alignment("silver " * 2001, raw, None, None)
    assert result == raw
    assert "comparison_limit_exceeded" in codes(quality)


def test_low_asr_probability_keeps_text_and_timing_confidence_uncertain():
    evidence = asr(("silver river", 10))
    evidence["segments"][0]["words"][0]["probability"] = 0.1
    quality = assess_lyrics("silver river", line(), evidence)
    assert "asr_confidence_low" in codes(quality)
    assert quality["text_confidence"] == quality["timing_confidence"] == "uncertain"


def test_synthetic_corpus_metrics_are_not_song_specific():
    cases = [
        ("violet morning", "violet morning", 0, 0),
        ("violet morning rises", "violet morning", 1, 0),
        ("echo echo returns", "echo returns", 1, 0),
        ("lights fade", "lights lights fade", 0, 1),
        ("clouds pass\nwinds return", "winds return", 2, 0),
    ]
    totals = {"missing": 0, "extra": 0}
    for reference, performed, missing, extra in cases:
        quality = assess_lyrics(reference, line(performed), asr((performed, 10)))
        assert quality["counts"]["missing_words"] == missing
        assert quality["counts"]["extra_words"] == extra
        totals["missing"] += missing
        totals["extra"] += extra
    assert totals == {"missing": 4, "extra": 1}


def test_non_lrc_garbage_is_not_an_accepted_alignment():
    result, quality = reconcile_alignment("silver river", "not an lrc, no timestamps here",
                                         None, asr(("silver river", 10)))
    assert result is None
    assert "alignment_malformed" in codes(quality)
    assert quality["status"] == "needs_review"


def test_repeated_output_cannot_borrow_same_asr_evidence_twice():
    raw = line("silver river", 10) + "\n" + line("silver river", 11)
    quality = assess_lyrics("silver river\nsilver river", raw, asr(("silver river", 10)))
    assert quality["counts"]["missing_words"] == 0
    assert quality["counts"]["output_unconfirmed_words"] == 2
    assert "output_words_unconfirmed" in codes(quality)
    assert quality["status"] == "needs_review"



def test_crop_observations_outside_accepted_candidate_are_not_hidden():
    evidence = asr(("silver river", 10))
    evidence["retry_segments"] = asr(("extra silver river", 9.5))["segments"]
    quality = assess_lyrics("silver river", line(), evidence)
    assert quality["status"] == "needs_review"
    assert quality["counts"]["asr_unmatched_words"] == 1
    assert "asr_words_unrepresented" in codes(quality)


def test_fuzzy_asr_cannot_restore_unperformed_negation():
    texts = ["we will never surrender now"] + [f"silver river number {i}" for i in range(7)]
    raw = "\n".join(line(text, 10 + i * 4) for i, text in enumerate(texts))
    evidence = asr(("we will surrender now", 10))
    result, quality = reconcile_alignment("\n".join(texts), raw, [-3] + [-0.1] * 7, evidence)
    assert "never" not in result
    assert quality["counts"]["restored_lines"] == 0
    assert quality["counts"]["missing_lines"] == 1
    assert quality["status"] == "needs_review"


def test_malformed_asr_container_types_require_review_without_exception():
    cases = [
        42, "invalid", [], {"segments": 42}, {"retry_segments": 42},
        {"segments": [{"words": 42}]}, {"segments": [42]},
        {"segments": [{"words": [42]}]},
    ]
    for evidence in cases:
        result, quality = reconcile_alignment("silver river", line(), None, evidence)
        assert result is not None
        assert quality["status"] == "needs_review"
        assert "asr_evidence_invalid" in codes(quality)



def test_identical_full_crop_observations_with_jitter_count_only_once():
    evidence = asr(("silver wrong", 10))
    evidence["retry_segments"] = [
        *asr(("silver river", 10))["segments"],
        *asr(("silver river", 10.15))["segments"],
    ]
    assert [word.text for word in _asr_words(evidence)] == ["silver", "river"]
    quality = assess_lyrics("silver river", line(), evidence)
    assert quality["counts"]["asr_unmatched_words"] == 0
    assert quality["status"] == "checked"


def test_overlapping_disagreeing_crops_remain_visible():
    evidence = {"retry_segments": [
        *asr(("silver river", 10))["segments"],
        *asr(("golden river", 10.15))["segments"],
    ]}
    words = [word.text for word in _asr_words(evidence)]
    assert "silver" in words and "golden" in words
    quality = assess_lyrics("silver river", line(), evidence)
    assert quality["status"] == "needs_review"
    assert "asr_words_unrepresented" in codes(quality)


def test_close_real_repeated_words_are_not_collapsed():
    evidence = {"retry_segments": [
        *asr(("echo", 10))["segments"],
        *asr(("echo", 10.5))["segments"],
    ]}
    assert [word.text for word in _asr_words(evidence)] == ["echo", "echo"]
    quality = assess_lyrics("echo echo", line("echo echo"), evidence)
    assert quality["counts"]["matched_words"] == 2
    assert quality["counts"]["asr_unmatched_words"] == 0
    assert quality["status"] == "checked"


def test_duplicate_crops_keep_low_confidence_instead_of_cherry_picking():
    confident = asr(("silver river", 10))["segments"][0]
    uncertain = asr(("silver river", 10.15))["segments"][0]
    uncertain["words"][0]["probability"] = 0.1
    quality = assess_lyrics("silver river", line(),
                            {"retry_segments": [confident, uncertain]})
    assert quality["status"] == "needs_review"
    assert "asr_confidence_low" in codes(quality)
    assert quality["counts"]["asr_unmatched_words"] == 0


def test_repeated_words_within_each_crop_keep_occurrence_count_after_dedupe():
    evidence = {"retry_segments": [
        *asr(("echo echo returns", 10))["segments"],
        *asr(("echo echo returns", 10.15))["segments"],
    ]}
    assert [word.text for word in _asr_words(evidence)] == ["echo", "echo", "returns"]
    quality = assess_lyrics("echo echo returns", line("echo echo returns"), evidence)
    assert quality["status"] == "checked"

