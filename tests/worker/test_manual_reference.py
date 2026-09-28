"""Pasted reference wins in covered spans; transcript elsewhere (#284)."""
from karaoke.worker.manual_reference import covered_spans, merge_reference_with_asr

ALIGNED = (
    "[00:47.00]<00:47.00>בא <00:47.40>לי <00:47.80>איזה <00:48.40>לאב <00:49.00>סונג <00:50.00>\n"
    "[00:56.66]תדע זה פשע\n"
    "[00:57.94]מישהו צריך להתערב\n"
)
ASR = (
    "[00:03.60][speech] זה סבבה לך המקום נכון\n"
    "[00:47.10]בלי איזה לב סונג\n"
    "[00:57.00]מישו צריך להתערב\n"
    "[01:30.00]שורה מאוחרת מהתמלול\n"
)


def test_spans_merge_across_short_gaps_only():
    assert covered_spans(ALIGNED) == [(47.0, 62.94)]  # 6.7 s gap < 8 s: one span
    assert covered_spans(ALIGNED + "[01:20.00]פזמון מאוחר\n") == [(47.0, 62.94), (80.0, 85.0)]


def test_paste_replaces_covered_asr_lines_and_keeps_the_rest():
    merged = merge_reference_with_asr(ALIGNED, ASR).splitlines()
    assert merged[0] == "[00:03.60][speech] זה סבבה לך המקום נכון"
    assert merged[1].startswith("[00:47.00]<00:47.00>בא")
    assert merged[2:4] == ["[00:56.66]תדע זה פשע", "[00:57.94]מישהו צריך להתערב"]
    assert merged[4] == "[01:30.00]שורה מאוחרת מהתמלול"
    assert not any("בלי איזה" in ln or "מישו" in ln for ln in merged)


def test_equal_starts_are_made_strictly_increasing():
    merged = merge_reference_with_asr("[00:10.00]אחת\n[00:10.00]שתיים\n", None).splitlines()
    assert merged == ["[00:10.00]אחת", "[00:10.01]שתיים"]


def _asr_segments(words_at):
    return [{"start": t, "end": t + 3, "text": " ".join(ws),
             "words": [{"word": w, "start": t + i * 0.4, "end": t + i * 0.4 + 0.3} for i, w in enumerate(ws)]}
            for t, ws in words_at]


def test_a_stretched_line_end_does_not_swallow_the_transcript():
    aligned = "[01:00.00]<01:00.00>אחת <01:00.50>שתיים <02:30.00>\n"
    asr = "[01:30.00]שורה מהתמלול באמצע\n"
    merged = merge_reference_with_asr(aligned, asr)
    assert "[01:30.00]שורה מהתמלול באמצע" in merged


def test_lines_torn_to_a_later_repeat_come_back_next_to_their_neighbours():
    aligned = ("[01:27.94]לא בא לי איזה לאב סונג\n"
               "[02:39.52]בסוף כל השירים אותו דבר\n"
               "[02:43.76]מה יש כבר לחפש פה\n")
    asr = _asr_segments([(87.9, ["לא", "בא", "לי", "איזה", "לאב", "סונג"]), (91.2, ["בסוף", "כל", "השירים", "אותו", "דבר"]),
                         (95.7, ["מה", "יש", "כבר", "לחפש", "פה"]), (159.6, ["בסוף", "כל", "השירים", "אותו", "דבר"])])
    merged = merge_reference_with_asr(aligned, None, asr, "he").splitlines()
    assert [ln[:10] for ln in merged] == ["[01:27.94]", "[01:31.20]", "[01:35.70]"]


def test_an_unheard_intro_on_dialogue_is_pulled_before_the_verse():
    aligned = "[00:04.54]בא לי איזה לאב סונג\n[00:07.54]בסוף כל השירים\n[00:55.86]תדע זה פשע\n"
    asr = _asr_segments([(3.6, ["זה", "סבבה", "לך", "המקום"]), (55.9, ["תדע", "זה", "פשע"])])
    merged = merge_reference_with_asr(aligned, None, asr, "he").splitlines()
    assert [ln[:10] for ln in merged] == ["[00:48.86]", "[00:52.36]", "[00:55.86]"]
