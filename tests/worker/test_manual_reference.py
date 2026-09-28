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
