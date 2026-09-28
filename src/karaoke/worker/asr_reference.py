"""Transcript-first use of web references, and ``[speech]`` marking (#283).

Operator decisions for #279:

* 3 — with a web reference, the ASR transcript is the base and the reference
  corrects its words; reference lines the transcript never heard are NOT
  restored (a lyrics page may differ from this performance).
* 5 — non-sung speech (a music video's dialogue skit) stays visible, marked
  ``[speech]``, dimmed, and is never highlighted as singing.

Correction is line-local: every transcript line is paired with the most
similar reference line (or two adjacent reference lines — a Whisper segment
often spans a line break). Repeated choruses therefore each find the same
reference line, which a global alignment of the two texts would not do.
Inside a paired line, short substitutions (and short insertions between two
confirmed words) take the reference's words; nothing is deleted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from karaoke.textnorm import normalize
from karaoke.worker.lyrics import LRC_TIMESTAMP_RE, LRC_WORD_TAG_RE, SPEECH_MARKER
from karaoke.worker.word_vote import _EDGE_PUNCT_RE, Correction, apply_corrections, tokenize

# A transcript line and a reference line are "the same line" at this
# similarity of their normalised text.
_LINE_MATCH_MIN = 0.55
# Below this WORD-level similarity to every reference line (or pair), an edge
# line counts as unmatched (candidate for [speech]).
_LINE_UNMATCHED_MAX = 0.34
# Dialogue, not an ad-lib ("yeah yeah", "come on") — a spoken line has words.
_SPEECH_MIN_WORDS = 3
_MAX_SUB = 4
_MAX_INS = 2
_LRC_LINE_RE = re.compile(r"^\s*\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
_META_RE = re.compile(r"^\[[A-Za-z]+:[^\]]*\]$")
# Whisper's classic hallucinations on silence / credits — never [speech].
_HALLUCINATION_RE = re.compile(
    r"^(?:תודה(?: רבה)?|thank you(?: (?:so much|for watching))?|thanks for watching|"
    r"subtitles by.*|כתוביות.*|תרגום.*|you)$",
    re.IGNORECASE,
)


@dataclass
class CorrectionStats:
    lines: int = 0
    matched_lines: int = 0
    corrections: int = 0
    unmatched: list[int] = field(default_factory=list)  # indexes into the timed lines


def _seconds(match: re.Match[str]) -> float:
    frac = match[3] or "0"
    return int(match[1]) * 60 + int(match[2]) + int(frac) / (10 ** len(frac))


def _plain(line: str) -> str:
    return " ".join(LRC_WORD_TAG_RE.sub(" ", LRC_TIMESTAMP_RE.sub(" ", line)).split())


def _ref_units(reference: str, lang: str | None) -> list[tuple[str, list]]:
    lines = [ln.strip() for ln in reference.splitlines() if ln.strip()]
    units: list[tuple[str, list]] = []
    for i, line in enumerate(lines):
        units.append((normalize(line, lang), tokenize(line, lang)))
        if i + 1 < len(lines):
            pair = f"{line} {lines[i + 1]}"
            units.append((normalize(pair, lang), tokenize(pair, lang)))
    return [u for u in units if u[0]]


def correct_asr_lrc(asr_lrc: str, reference: str, lang: str | None) -> tuple[str, CorrectionStats]:
    """Correct the words of ``asr_lrc`` from ``reference`` (see module doc).

    Returns the corrected LRC (tags preserved) and statistics; ``unmatched``
    lists the timed-line indexes with no reference line similar enough.
    """
    stats = CorrectionStats()
    units = _ref_units(reference, lang)
    all_tokens = tokenize(asr_lrc, lang)
    by_line: dict[int, list[int]] = {}
    for index, token in enumerate(all_tokens):
        by_line.setdefault(token.line, []).append(index)
    corrections: list[Correction] = []
    timed_index = -1
    for line_no, raw in enumerate(asr_lrc.splitlines()):
        if not _LRC_LINE_RE.match(raw) or _META_RE.match(raw.strip()):
            continue
        timed_index += 1
        token_ids = by_line.get(line_no, [])
        if not token_ids:
            continue
        stats.lines += 1
        line_key = " ".join(all_tokens[i].key for i in token_ids)
        best_ratio, best = 0.0, None
        for key, ref_tokens in units:
            ratio = SequenceMatcher(None, line_key, key, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio, best = ratio, ref_tokens
        if best is None or best_ratio < _LINE_MATCH_MIN:
            # "Not from the song" is judged on WORDS: two unrelated Hebrew
            # sentences already share ~a third of their letters.
            line_words = line_key.split()
            word_ratio = max(
                (SequenceMatcher(None, line_words, key.split(), autojunk=False).ratio() for key, _ in units),
                default=0.0,
            )
            if word_ratio < _LINE_UNMATCHED_MAX:
                stats.unmatched.append(timed_index)
            continue
        stats.matched_lines += 1
        asr_keys = [all_tokens[i].key for i in token_ids]
        ref_keys = [t.key for t in best]
        for op, i1, i2, j1, j2 in SequenceMatcher(None, asr_keys, ref_keys, autojunk=False).get_opcodes():
            surfaces = tuple(_EDGE_PUNCT_RE.sub("", t.surface) or t.surface for t in best[j1:j2])
            if op == "replace" and i2 - i1 <= _MAX_SUB and j2 - j1 <= _MAX_SUB:
                corrections.append(Correction(
                    start=token_ids[i1], length=i2 - i1,
                    old=tuple(all_tokens[i].surface for i in token_ids[i1:i2]),
                    replacement=surfaces, arbiter="reference",
                ))
            elif op == "insert" and 0 < i1 < len(token_ids) and j2 - j1 <= _MAX_INS:
                prev = all_tokens[token_ids[i1 - 1]]
                corrections.append(Correction(
                    start=token_ids[i1 - 1], length=1, old=(prev.surface,),
                    replacement=(prev.surface, *surfaces), arbiter="reference",
                ))
    # Two corrections may not touch the same token (an insert after a
    # replaced token); keep the first.
    taken: set[int] = set()
    kept: list[Correction] = []
    for corr in sorted(corrections, key=lambda c: c.start):
        span = set(range(corr.start, corr.start + corr.length))
        if span & taken:
            continue
        taken |= span
        kept.append(corr)
    stats.corrections = len(kept)
    return apply_corrections(asr_lrc, kept, lang), stats


def mark_edge_lines_as_speech(lrc: str, unmatched: list[int]) -> tuple[str, int]:
    """Prefix ``[speech]`` to the leading and trailing runs of timed lines in
    ``unmatched`` — dialogue before the first / after the last sung line.
    Unmatched lines between sung lines stay as they are (garbled singing is
    far likelier there than dialogue)."""
    timed = [i for i, raw in enumerate(lrc.splitlines()) if _LRC_LINE_RE.match(raw) and not _META_RE.match(raw.strip())]
    bad = set(unmatched)
    edge: set[int] = set()
    for k in range(len(timed)):
        if k not in bad:
            break
        edge.add(k)
    for k in range(len(timed) - 1, -1, -1):
        if k not in bad:
            break
        edge.add(k)
    if len(edge) == len(timed):  # nothing matched at all: no evidence either way
        return lrc, 0
    out = lrc.splitlines()
    marked = 0
    for k in sorted(edge):
        line_no = timed[k]
        match = _LRC_LINE_RE.match(out[line_no])
        text = _plain(out[line_no])
        if not text or _HALLUCINATION_RE.match(text.strip(" .!?,")) or len(text.split()) < _SPEECH_MIN_WORDS:
            continue
        out[line_no] = f"{out[line_no][: match.end()]}{SPEECH_MARKER} {text}"
        marked += 1
    return "\n".join(out) + ("\n" if lrc.endswith("\n") else ""), marked


def speech_lines_outside_lyrics(
    lyrics_lrc: str, asr_segments: list[dict], lang: str | None
) -> list[tuple[float, str]]:
    """ASR segments entirely before the first sung line (minus 1 s) or after
    the last one (plus 2 s) that do not resemble any lyric line — a music
    video's spoken intro/outro around a curated (LRCLIB) record."""
    starts = [_seconds(m) for raw in lyrics_lrc.splitlines() if (m := _LRC_LINE_RE.match(raw))]
    lyric_keys = [normalize(_plain(raw), lang) for raw in lyrics_lrc.splitlines() if _LRC_LINE_RE.match(raw)]
    if not starts:
        return []
    first, last = min(starts), max(starts)
    out: list[tuple[float, str]] = []
    for seg in asr_segments or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        try:
            start, end = float(seg.get("start")), float(seg.get("end"))
        except (TypeError, ValueError):
            continue
        if not (end <= first - 1.0 or start >= last + 2.0):
            continue
        words = text.split()
        if len(words) < _SPEECH_MIN_WORDS or _HALLUCINATION_RE.match(text.strip(" .!?,")):
            continue
        key = normalize(text, lang)
        if any(SequenceMatcher(None, key, lk, autojunk=False).ratio() >= 0.5 for lk in lyric_keys if lk):
            continue
        out.append((start, text))
    return out


def insert_speech_lines(lrc: str, speech: list[tuple[float, str]]) -> str:
    """Merge ``[speech]`` lines into ``lrc`` in time order."""
    if not speech:
        return lrc
    lines = [raw for raw in lrc.splitlines() if raw.strip()]
    timed: list[tuple[float, int, str]] = []
    header: list[str] = []
    for i, raw in enumerate(lines):
        m = _LRC_LINE_RE.match(raw)
        if m and not _META_RE.match(raw.strip()):
            timed.append((_seconds(m), i, raw))
        else:
            header.append(raw)
    for j, (start, text) in enumerate(speech):
        tag = f"[{int(start // 60):02d}:{start % 60:05.2f}]"
        timed.append((start, -1000 + j, f"{tag}{SPEECH_MARKER} {text}"))
    timed.sort(key=lambda t: (t[0], t[1]))
    body = "\n".join([*header, *(t[2] for t in timed)])
    return body + ("\n" if lrc.endswith("\n") else "")


def strip_speech_lines(lrc: str) -> str:
    """``lrc`` without ``[speech]`` lines (for audits and plain exports)."""
    kept = [raw for raw in lrc.splitlines() if not _plain(raw).startswith(SPEECH_MARKER)]
    return "\n".join(kept) + ("\n" if lrc.endswith("\n") and kept else "")
