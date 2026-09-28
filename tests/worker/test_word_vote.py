"""Multi-source word vote (#281) on synthetic snippets."""
from __future__ import annotations

from karaoke.worker.word_vote import Correction, apply_corrections, tokenize, vote_corrections

PRIMARY = "[00:10.00]כל יום ב9 אתה רואה לי את הלב\n[00:14.00]מה יש כבר לחפש פה2 אספרסו\n[00:18.00]לא בא לי אייזה לאב סונג בייבי"
SECONDARY = "כל יום בתשע אתה רואה לי את הלב\nמה יש כבר לחפש פה? שתה אספרסו\nלא בא לי איזה לאב סונג בייבי"
ASR = [
    ("כל", 10.1), ("יום", 10.4), ("בתשע", 10.8), ("אתה", 11.2), ("רואה", 11.5), ("לי", 11.8),
    ("את", 12.0), ("הלב", 12.3), ("מה", 14.1), ("יש", 14.3), ("כבר", 14.5), ("לחפש", 14.8),
    ("פה", 15.1), ("שתה", 15.4), ("אספרסו", 15.8), ("לא", 18.1), ("בא", 18.3), ("לי", 18.5),
    ("איזה", 18.8), ("לאב", 19.0), ("סונג", 19.3), ("בייבי", 19.6),
]


def test_tokenize_keeps_line_and_time_and_strips_tags():
    toks = tokenize("[00:10.50]<00:10.50>שלום <00:11.00>עולם\nplain", "he")
    assert [(t.surface, t.line, t.time) for t in toks] == [("שלום", 0, 10.5), ("עולם", 0, 10.5), ("plain", 1, None)]


def test_asr_settles_disputes_including_one_to_two_spans():
    corrections = vote_corrections(PRIMARY, SECONDARY, ASR, "he")
    assert [(c.old, c.replacement, c.arbiter) for c in corrections] == [
        (("ב9",), ("בתשע",), "asr"),
        (("פה2",), ("פה", "שתה"), "asr"),
        (("אייזה",), ("איזה",), "asr"),
    ]
    fixed = apply_corrections(PRIMARY, corrections, "he")
    assert "9" not in fixed and "פה2" not in fixed
    assert fixed.splitlines()[1] == "[00:14.00]מה יש כבר לחפש פה שתה אספרסו"
    assert fixed.count("[00:") == 3


def test_asr_confirming_primary_blocks_the_change():
    asr = [(w, t) for w, t in ASR]
    asr[2] = ("ב9", 10.8)  # the singer audibly says what LRCLIB has
    corrections = vote_corrections(PRIMARY, SECONDARY, asr, "he")
    assert ("ב9",) not in [c.old for c in corrections]


def test_dictionary_rule_without_asr_only_fixes_digits():
    corrections = vote_corrections(PRIMARY, SECONDARY, None, "he")
    assert [(c.old, c.arbiter) for c in corrections] == [(("ב9",), "dictionary"), (("פה2",), "dictionary")]


def test_ambiguous_asr_falls_back_to_dictionary_rule():
    asr = [("משהו", 10.0), ("אחר", 10.5)]
    corrections = vote_corrections("שלום ב9 עולם", "שלום בתשע עולם", asr, "he")
    assert [(c.old, c.replacement, c.arbiter) for c in corrections] == [(("ב9",), ("בתשע",), "dictionary")]


def test_wrong_song_yields_no_corrections():
    assert vote_corrections(PRIMARY, "טקסט אחר לגמרי בלי שום קשר לשיר הזה בכלל", ASR, "he") == []


def test_long_rewrites_and_line_restores_are_ignored():
    primary = "אחת שתיים שלוש ארבע\nחמש שש שבע"
    secondary = "אחת שתיים שלוש ארבע\nשורה חדשה לגמרי כאן\nחמש שש שבע"
    assert vote_corrections(primary, secondary, None, None) == []
    primary = "a b c d e f g h i j k l"
    secondary = "a b c d x y z w i j k l"  # 4-token rewrite exceeds the span cap
    assert vote_corrections(primary, secondary, [("x", 0.0), ("y", 0.1), ("z", 0.2), ("w", 0.3)], None) == []


def test_apply_preserves_enhanced_lrc_tags_and_plain_text():
    lrc = "[00:10.00]<00:10.00>כל <00:10.40>יום <00:10.80>ב9 <00:11.20>אתה"
    corrections = [Correction(start=2, length=1, old=("ב9",), replacement=("בתשע",), arbiter="asr")]
    assert apply_corrections(lrc, corrections, "he") == "[00:10.00]<00:10.00>כל <00:10.40>יום <00:10.80>בתשע <00:11.20>אתה"
    assert apply_corrections("כל יום ב9 אתה\n", corrections, "he") == "כל יום בתשע אתה\n"


def test_apply_two_to_one_span_keeps_first_tag_only():
    lrc = "[00:01.00]<00:01.00>a <00:01.50>b <00:02.00>c"
    corrections = [Correction(start=0, length=2, old=("a", "b"), replacement=("ab",), arbiter="asr")]
    assert apply_corrections(lrc, corrections, None) == "[00:01.00]<00:01.00>ab <00:02.00>c"


def test_latin_text_with_identical_sources_is_untouched():
    text = "never gonna give you up\nnever gonna let you down"
    assert vote_corrections(text, text, [("never", 0.0)], "en") == []
