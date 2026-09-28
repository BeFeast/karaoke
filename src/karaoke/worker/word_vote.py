"""Multi-source word vote (#281, operator decision 2).

When two references exist for a song — LRCLIB (skeleton and timing) and a
second plain text (Genius) — the words they disagree on are settled by the
ASR transcript: the variant the singer can be heard saying wins. When ASR
cannot tell, a dictionary word beats a digit or a digit-glued typo (``9`` vs
``תשע``, ``פה2`` vs ``פה שתה``). Everything else stays as LRCLIB has it.

The vote is deliberately conservative: it only touches short disputed spans
between exact anchors, it never restores or drops lines, and it bails out
entirely when the two texts are too different to be the same performance
(wrong Genius page, another edit). It runs on the finished export, so the
alignment and quality gates upstream see the text they were built on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from karaoke.textnorm import normalize

_LRC_TAG_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
_ANY_TAG_RE = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]|<\d{1,2}:\d{2}(?:[.:]\d{1,3})?>")
_LRC_META_RE = re.compile(r"^\[[A-Za-z]+:[^\]]*\]$")
_DIGIT_RE = re.compile(r"\d")
# Punctuation the second source glues to words ("פה?", "בייבי,") — LRCLIB
# lines carry none, so replacements are inserted bare.
_EDGE_PUNCT_RE = re.compile(r"^[\s\.,;:!?…\"«»“”()\[\]-]+|[\s\.,;:!?…\"«»“”()\[\]-]+$")
# A disputed span longer than this on either side is a rewrite, not a word.
_MAX_SPAN = 3
# Below this share of anchored (identical) tokens the texts are not the same
# performance; above this share of disputed tokens likewise (with a small
# absolute allowance so a short text can still carry a couple of typos).
_MIN_ANCHOR_RATIO = 0.5
_MAX_DISPUTED_RATIO = 0.15
_MIN_DISPUTED_ALLOWANCE = 3
# ASR evidence: exact token match, or this similarity of the joined span.
_ASR_MATCH_RATIO = 0.84
_ASR_MARGIN = 0.1
# Seconds around a timed line in which ASR words count as evidence for it.
_ASR_WINDOW_BEFORE_S = 3.0
_ASR_WINDOW_AFTER_S = 20.0

ARBITER_ASR = "asr"
ARBITER_DICTIONARY = "dictionary"


@dataclass(frozen=True, slots=True)
class Token:
    surface: str
    key: str
    line: int
    time: float | None


@dataclass(frozen=True, slots=True)
class Correction:
    """Replace ``length`` primary tokens starting at token ``start`` (index
    into the primary token stream) with ``replacement`` surface words."""

    start: int
    length: int
    old: tuple[str, ...]
    replacement: tuple[str, ...]
    arbiter: str


def _tag_seconds(match: re.Match) -> float:
    frac = match[3] or "0"
    return int(match[1]) * 60 + int(match[2]) + int(frac) / (10 ** len(frac))


def tokenize(text: str, lang: str | None) -> list[Token]:
    """Surface tokens of a plain or LRC text with their comparison key,
    line number and the line's LRC time (``None`` for untimed lines)."""
    out: list[Token] = []
    for line_no, raw in enumerate(text.splitlines()):
        if _LRC_META_RE.match(raw.strip()):
            continue
        stamps = [_tag_seconds(m) for m in _LRC_TAG_RE.finditer(raw)]
        time = min(stamps) if stamps else None
        for surface in _ANY_TAG_RE.sub(" ", raw).split():
            key = normalize(surface, lang)
            if key:
                out.append(Token(surface=surface, key=key, line=line_no, time=time))
    return out


def _lcs_pairs(left: list[str], right: list[str]) -> list[tuple[int, int]]:
    width = len(right) + 1
    directions = bytearray((len(left) + 1) * width)
    previous = [0] * width
    for i, token in enumerate(left, 1):
        row = [0] * width
        for j, other in enumerate(right, 1):
            if token == other:
                row[j] = previous[j - 1] + 1
                directions[i * width + j] = 1
            elif previous[j] >= row[j - 1]:
                row[j] = previous[j]
                directions[i * width + j] = 2
            else:
                row[j] = row[j - 1]
                directions[i * width + j] = 3
        previous = row
    i, j = len(left), len(right)
    pairs: list[tuple[int, int]] = []
    while i and j:
        direction = directions[i * width + j]
        if direction == 1:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif direction == 2:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def _has_digit(keys: tuple[str, ...]) -> bool:
    return any(_DIGIT_RE.search(k) for k in keys)


def _asr_similarity(span: tuple[str, ...], asr_keys: list[str], window: range | None) -> float:
    """Best similarity of ``span`` against any equally long window of ASR
    keys (exact token runs score 1.0). ``window`` restricts the ASR indexes
    searched; ``None`` searches the whole transcript."""
    if not span or not asr_keys:
        return 0.0
    target = " ".join(span)
    indexes = window if window is not None else range(len(asr_keys))
    best = 0.0
    for start in indexes:
        if start < 0 or start >= len(asr_keys):
            continue
        for size in range(max(1, len(span) - 1), len(span) + 2):
            chunk = asr_keys[start:start + size]
            if len(chunk) != size:
                continue
            if tuple(chunk) == span:
                return 1.0
            ratio = SequenceMatcher(None, target, " ".join(chunk), autojunk=False).ratio()
            if ratio > best:
                best = ratio
    return best


def _asr_window(time: float | None, asr_times: list[float]) -> range | None:
    if time is None or not asr_times:
        return None
    lo = time - _ASR_WINDOW_BEFORE_S
    hi = time + _ASR_WINDOW_AFTER_S
    indexes = [i for i, t in enumerate(asr_times) if lo <= t <= hi]
    if not indexes:
        return None
    return range(indexes[0], indexes[-1] + 1)


def vote_corrections(
    primary: str,
    secondary: str,
    asr_words: list[tuple[str, float | None]] | None,
    lang: str | None,
) -> list[Correction]:
    """Corrections to ``primary`` (plain or LRC) from ``secondary`` (plain).

    ``asr_words`` are ``(text, start_seconds)`` pairs in transcript order.
    Returns ``[]`` when the two texts are not the same performance.
    """
    prim = tokenize(primary, lang)
    sec = tokenize(secondary, lang)
    if not prim or not sec:
        return []
    pairs = _lcs_pairs([t.key for t in prim], [t.key for t in sec])
    if len(pairs) < _MIN_ANCHOR_RATIO * len(prim):
        return []

    asr_keys: list[str] = []
    asr_times: list[float] = []
    for text, start in asr_words or []:
        key = normalize(text, lang)
        if key:
            asr_keys.append(key)
            asr_times.append(float(start) if start is not None else -1.0)
    timed = any(t >= 0 for t in asr_times)

    corrections: list[Correction] = []
    disputed = 0
    boundaries = [(-1, -1), *pairs, (len(prim), len(sec))]
    for (pi, si), (pj, sj) in zip(boundaries, boundaries[1:], strict=False):
        p_span = prim[pi + 1:pj]
        s_span = sec[si + 1:sj]
        if not p_span or not s_span:
            continue  # pure insertion/deletion: lines are never restored or dropped
        if len(p_span) > _MAX_SPAN or len(s_span) > _MAX_SPAN:
            disputed += len(p_span)
            continue
        if len({t.line for t in p_span}) != 1:
            disputed += len(p_span)
            continue
        disputed += len(p_span)
        p_keys = tuple(t.key for t in p_span)
        s_keys = tuple(t.key for t in s_span)
        arbiter: str | None = None
        if asr_keys:
            window = _asr_window(p_span[0].time, asr_times) if timed else None
            p_score = _asr_similarity(p_keys, asr_keys, window)
            s_score = _asr_similarity(s_keys, asr_keys, window)
            if s_score >= _ASR_MATCH_RATIO and s_score >= p_score + _ASR_MARGIN:
                arbiter = ARBITER_ASR
            elif p_score >= _ASR_MATCH_RATIO and p_score >= s_score + _ASR_MARGIN:
                continue  # ASR confirms LRCLIB
        if arbiter is None and _has_digit(p_keys) and not _has_digit(s_keys):
            arbiter = ARBITER_DICTIONARY
        if arbiter is None:
            continue
        corrections.append(
            Correction(
                start=pi + 1,
                length=len(p_span),
                old=tuple(t.surface for t in p_span),
                replacement=tuple(_EDGE_PUNCT_RE.sub("", t.surface) or t.surface for t in s_span),
                arbiter=arbiter,
            )
        )
    if disputed > max(_MIN_DISPUTED_ALLOWANCE, _MAX_DISPUTED_RATIO * len(prim)):
        return []
    return corrections


_PIECE_RE = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]|<\d{1,2}:\d{2}(?:[.:]\d{1,3})?>|\s+|[^\s\[<]+|[\[<]")


def apply_corrections(text: str, corrections: list[Correction], lang: str | None) -> str:
    """Rewrite ``text`` (plain or LRC, any tags kept) with ``corrections``
    computed on the same text by :func:`vote_corrections`."""
    if not corrections:
        return text
    by_start = {c.start: c for c in corrections}
    out_lines: list[str] = []
    index = 0
    lines = text.splitlines()
    for raw in lines:
        if _LRC_META_RE.match(raw.strip()):
            out_lines.append(raw)
            continue
        pieces = _PIECE_RE.findall(raw)
        words = [k for k, p in enumerate(pieces) if p and not p.isspace() and not _ANY_TAG_RE.fullmatch(p) and normalize(p, lang)]
        skip_until = -1
        for position, piece_index in enumerate(words):
            token_index = index + position
            if piece_index < skip_until:
                pieces[piece_index] = ""
                continue
            correction = by_start.get(token_index)
            if correction is None:
                continue
            end_position = position + correction.length
            if end_position > len(words):
                continue
            pieces[piece_index] = " ".join(correction.replacement)
            last_piece = words[end_position - 1]
            # Drop the remaining words of the span and everything between them
            # (their word tags and spaces); the span's first tag survives.
            for k in range(piece_index + 1, last_piece + 1):
                pieces[k] = ""
            skip_until = last_piece + 1
        index += len(words)
        out_lines.append(re.sub(r"[ \t]{2,}", " ", "".join(pieces)).rstrip())
    return "\n".join(out_lines) + ("\n" if text.endswith("\n") else "")
