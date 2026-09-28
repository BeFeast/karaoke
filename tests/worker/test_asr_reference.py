"""ASR-base correction and [speech] marking (#283) — synthetic snippets."""
from __future__ import annotations

from karaoke.worker.asr_reference import (
    correct_asr_lrc,
    insert_speech_lines,
    mark_edge_lines_as_speech,
    speech_lines_outside_lyrics,
    strip_speech_lines,
)
from karaoke.worker.lyrics import lrc_to_plain

REF = "כל יום בתשע אתה רואה לי את הלב\nמפחיד להתאהב אוהב שזה כואב\nלא בא לי איזה לאב סונג בייבי\nבסוף כל השירים אותו דבר"


def test_asr_words_corrected_per_line_including_repeats_and_tags_kept():
    asr = (
        "[00:10.00]<00:10.00>כל <00:10.40>יום <00:10.80>בתשה <00:11.20>אתה <00:11.60>רואה <00:12.00>לי <00:12.30>את <00:12.60>הלב <00:13.00>\n"
        "[00:20.00]לא בא לי איזה לב סונג בייבי\n"
        "[00:40.00]לא בא לי איזה לב סונג בייבי\n"
    )
    fixed, stats = correct_asr_lrc(asr, REF, "he")
    lines = fixed.splitlines()
    assert "<00:10.80>בתשע" in lines[0]
    assert lines[1] == "[00:20.00]לא בא לי איזה לאב סונג בייבי"
    assert lines[2] == "[00:40.00]לא בא לי איזה לאב סונג בייבי"
    assert stats.matched_lines == 3 and stats.corrections == 3


def test_reference_lines_the_asr_never_heard_are_not_added():
    asr = "[00:10.00]מפחיד להתאהב אוהב שזה כואב\n"
    fixed, stats = correct_asr_lrc(asr, REF, "he")
    assert fixed == asr and stats.lines == 1


def test_unmatched_edges_become_speech_but_middle_lines_do_not():
    asr = (
        "[00:03.00]זה סבבה לך המקום נכון\n"
        "[00:10.00]כל יום בתשע אתה רואה לי את הלב\n"
        "[00:15.00]משהו אחר לגמרי שנאמר באמצע\n"
        "[00:20.00]מפחיד להתאהב אוהב שזה כואב\n"
        "[00:60.00]תודה רבה\n"
    )
    _, stats = correct_asr_lrc(asr, REF, "he")
    marked, n = mark_edge_lines_as_speech(asr, stats.unmatched)
    lines = marked.splitlines()
    assert lines[0] == "[00:03.00][speech] זה סבבה לך המקום נכון"
    assert "[speech]" not in lines[2]
    assert lines[4] == "[00:60.00]תודה רבה"  # hallucination phrase, never speech
    assert n == 1
    assert "זה סבבה" not in lrc_to_plain(marked)
    assert strip_speech_lines(marked).count("\n") == 4


def test_speech_outside_curated_lyrics_is_inserted_in_time_order():
    lyrics = "[00:56.66]תדע זה פשע\n[00:57.94]מישהו צריך להתערב\n"
    segments = [
        {"start": 3.6, "end": 5.3, "text": "זה סבבה לך המקום, נכון?"},
        {"start": 22.0, "end": 23.0, "text": "תודה רבה."},
        {"start": 56.7, "end": 57.9, "text": "תדע זה פשע"},
        {"start": 33.9, "end": 34.8, "text": "מי זאת בכלל?"},
        {"start": 40.0, "end": 40.5, "text": "כן כן"},
    ]
    speech = speech_lines_outside_lyrics(lyrics, segments, "he")
    assert [t for _, t in speech] == ["זה סבבה לך המקום, נכון?", "מי זאת בכלל?"]
    merged = insert_speech_lines(lyrics, speech)
    assert merged.splitlines()[0] == "[00:03.60][speech] זה סבבה לך המקום, נכון?"
    assert merged.splitlines()[2] == "[00:56.66]תדע זה פשע"
    assert lrc_to_plain(merged) == "תדע זה פשע\nמישהו צריך להתערב"
