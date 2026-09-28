"""Normaliser + metric cases on synthetic snippets (no song lyrics)."""
from __future__ import annotations

import pytest

from karaoke.textnorm import (
    align,
    cer,
    error_rate,
    fold_final_letters,
    has_hebrew,
    levenshtein,
    normalize,
    skeleton,
    strip_hebrew_marks,
    tokens,
    wer,
)


def test_strip_hebrew_marks_drops_niqqud_and_cantillation():
    assert strip_hebrew_marks("שָׁל֫וֹם") == "שלום"


def test_maqaf_becomes_space():
    assert normalize("בית־ספר") == "בית ספר"


def test_final_letters_fold():
    assert fold_final_letters("ךםןףץ") == "כמנפצ"
    assert normalize("שלום") == normalize("שלומ")


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        ("Love Song, בייבי!", "he", "לאב סונג בייבי"),
        ("love song", None, "love song"),  # loanwords are Hebrew-only
        ("Don't Stop — It's Alright", None, "dont stop its alright"),
        ("ג'ורג' וצה\"ל", None, "גורג וצהל"),
        ("איי-איי-איי", None, "איי איי איי"),
        ("  שתי   מילים\n\nשורה ", None, "שתי מילימ שורה"),
        ("‏שלום‎", None, "שלומ"),
    ],
)
def test_normalize(text, lang, expected):
    assert normalize(text, lang) == expected


def test_strip_parenthetical_is_opt_in():
    assert normalize("שיר (אה) בייבי") == "שיר אה בייבי"
    assert normalize("שיר (אה) בייבי", strip_parenthetical=True) == "שיר בייבי"


def test_skeleton_drops_vav_and_yod():
    assert skeleton(normalize("כל יום ויום")) == "כל מ מ"
    assert skeleton("וי") == ""


def test_has_hebrew():
    assert has_hebrew("abc שלום")
    assert not has_hebrew("abc")


def test_tokens_passes_kwargs():
    assert tokens("a (b) c", strip_parenthetical=True) == ["a", "c"]


@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [("", "", 0), ("abc", "", 3), ("", "ab", 2), ("kitten", "sitting", 3), ("abc", "abc", 0)],
)
def test_levenshtein(a, b, distance):
    assert levenshtein(a, b) == distance


def test_error_rate_edge_cases():
    assert error_rate([], []) == 0.0
    assert error_rate([], ["x"]) == 1.0
    assert error_rate(["a", "b"], []) == 1.0


def test_cer_and_wer_on_normalised_text():
    assert wer("a b c d", "a x c d") == pytest.approx(0.25)
    assert cer("abcd", "abzd") == pytest.approx(0.25)


def test_align_ops_are_deterministic_and_complete():
    ops = align(list("kitten"), list("sitting"))
    assert [op for op, _, _ in ops] == ["sub", "match", "match", "match", "sub", "match", "ins"]
    assert align([], ["a"]) == [("ins", None, 0)]
    assert align(["a"], []) == [("del", 0, None)]
