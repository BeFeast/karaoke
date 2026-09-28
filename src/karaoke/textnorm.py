"""Language-aware lyric text normalisation and edit-distance metrics.

Shared by the benchmark harness (``scripts/lyrics_bench.py``) and the worker
(multi-source word vote, #281). Stdlib only, deterministic, no I/O.

Normalisation folds the differences that are *not* transcription errors:
Hebrew pointing (niqqud / cantillation), final-letter forms, maqaf and other
dashes, quotes / geresh / gershayim, punctuation, letter case, and — for
Hebrew — Latin loanwords that ASR and lyric sites spell either way
(``love`` vs ``לאב``). The ktiv male/haser question (extra ו/י as vowel
letters) is folded separately by :func:`skeleton` so it can be reported as
its own metric instead of hiding real errors.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

# Hebrew points: cantillation U+0591–U+05AF plus niqqud U+05B0–U+05C7, minus
# the punctuation code points in that block (maqaf U+05BE, paseq U+05C0,
# sof pasuq U+05C3, nun hafukha U+05C6) which the generic punctuation rule
# handles. NFC keeps these as separate combining characters.
_HEBREW_POINTS_RE = re.compile("[֑-ׇֽֿׁׂׅׄ]")
# Dash-like characters that join words in one source and separate them in
# another ("איי-איי" vs "איי איי"); maqaf U+05BE is the Hebrew hyphen.
_DASHES_RE = re.compile("[-‐-―־]")
# Apostrophes / quotes / Hebrew geresh (U+05F3) and gershayim (U+05F4) are
# removed *without* a space: they occur inside words (ג'ורג', צה"ל, don't).
_QUOTES_RE = re.compile("['‘’׳״\"“”«»]")
# Bidi controls and BOM that copy-pasted RTL text often carries.
_BIDI_RE = re.compile("[‎‏‪-‮⁦-⁩؜﻿]")
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_FINALS = str.maketrans("ךםןףץ", "כמנפצ")
_MATRES = str.maketrans("", "", "וי")
_HEBREW_LETTER_RE = re.compile("[א-ת]")

# Latin loanwords that Hebrew lyric sites and ASR spell in either script.
# Applied to whole tokens after casefolding, Hebrew only.
LOANWORDS: dict[str, str] = {
    "love": "לאב",
    "song": "סונג",
    "baby": "בייבי",
}


def strip_hebrew_marks(text: str) -> str:
    """Drop niqqud and cantillation; maqaf becomes a space."""
    text = unicodedata.normalize("NFC", text)
    return _HEBREW_POINTS_RE.sub("", text.replace("־", " "))


def fold_final_letters(text: str) -> str:
    """Map Hebrew final-form letters to their regular forms (ך→כ, …)."""
    return text.translate(_FINALS)


def has_hebrew(text: str) -> bool:
    return _HEBREW_LETTER_RE.search(text) is not None


def normalize(
    text: str, lang: str | None = None, *, strip_parenthetical: bool = False
) -> str:
    """Return the comparison form of ``text``: casefolded, unpointed,
    punctuation-free, single-spaced.

    ``lang="he"`` additionally transliterates :data:`LOANWORDS`.
    ``strip_parenthetical`` drops ``(…)`` spans — ad-libs in lyric sites'
    transcriptions that a scorer should not count against ASR.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _BIDI_RE.sub("", text)
    if strip_parenthetical:
        text = _PARENTHETICAL_RE.sub(" ", text)
    text = strip_hebrew_marks(text)
    text = _DASHES_RE.sub(" ", text)
    text = _QUOTES_RE.sub("", text)
    text = "".join(" " if unicodedata.category(ch)[0] in "PSZC" else ch for ch in text)
    text = text.casefold()
    words = text.split()
    if lang == "he":
        words = [LOANWORDS.get(w, w) for w in words]
    return fold_final_letters(" ".join(words))


def skeleton(normalized: str) -> str:
    """Drop ו/י (matres lectionis) from already-normalised text so ktiv male
    vs ktiv haser spellings compare equal."""
    return " ".join(w for w in (t.translate(_MATRES) for t in normalized.split()) if w)


def tokens(text: str, lang: str | None = None, **kwargs) -> list[str]:
    return normalize(text, lang, **kwargs).split()


def levenshtein(a: Sequence, b: Sequence) -> int:
    """Edit distance between two sequences (two-row DP)."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y))
            )
        previous = current
    return previous[-1]


def error_rate(reference: Sequence, hypothesis: Sequence) -> float:
    """``levenshtein / len(reference)``; 0.0 when both are empty, 1.0 when
    only the reference is empty."""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return levenshtein(reference, hypothesis) / len(reference)


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate on the normalised strings (spaces included)."""
    return error_rate(reference, hypothesis)


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate on the normalised strings."""
    return error_rate(reference.split(), hypothesis.split())


Op = tuple[str, int | None, int | None]


def align(reference: Sequence, hypothesis: Sequence) -> list[Op]:
    """Levenshtein alignment with backtrace.

    Returns ``(op, ref_index, hyp_index)`` triples in reference order, with
    ``op`` one of ``"match"``, ``"sub"``, ``"del"`` (reference item absent from
    the hypothesis, ``hyp_index`` is ``None``) or ``"ins"`` (extra hypothesis
    item, ``ref_index`` is ``None``). Ties prefer match/sub, then deletion,
    then insertion, so the result is deterministic.
    """
    n, m = len(reference), len(hypothesis)
    dist = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dist[i][0] = i
    for j in range(1, m + 1):
        dist[0][j] = j
    for i in range(1, n + 1):
        row, above = dist[i], dist[i - 1]
        ref_item = reference[i - 1]
        for j in range(1, m + 1):
            row[j] = min(
                above[j - 1] + (ref_item != hypothesis[j - 1]),
                above[j] + 1,
                row[j - 1] + 1,
            )
    ops: list[Op] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            same = reference[i - 1] == hypothesis[j - 1]
            if dist[i][j] == dist[i - 1][j - 1] + (not same):
                ops.append(("match" if same else "sub", i - 1, j - 1))
                i, j = i - 1, j - 1
                continue
        if i > 0 and dist[i][j] == dist[i - 1][j] + 1:
            ops.append(("del", i - 1, None))
            i -= 1
            continue
        ops.append(("ins", None, j - 1))
        j -= 1
    ops.reverse()
    return ops
