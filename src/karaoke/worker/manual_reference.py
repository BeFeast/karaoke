"""Owner-pasted reference lyrics merged with the transcript (#284).

Operator decision 4 for #279: a pasted reference wins in the spans it covers
— every pasted line is kept (restored even where the aligner was unsure) and
timed by forced alignment against the job's own vocal stem; outside those
spans the transcript stays.

Covered spans are the pasted lines' aligned extents, merged across short
gaps; a transcript line whose start falls inside a covered span is replaced
by the pasted text, one outside every span is kept.
"""
from __future__ import annotations

import re

from karaoke.worker.lyrics import SPEECH_MARKER, repair_aligned_lrc

_LINE_RE = re.compile(r"^\s*\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
_WORD_RE = re.compile(r"<(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?>")
_META_RE = re.compile(r"^\[[A-Za-z]+:[^\]]*\]$")
# Pasted lines closer than this are one covered span (a verse, a chorus).
_SPAN_GAP_S = 8.0
# A pasted line with no end tag is assumed to last this long at most.
_DEFAULT_LINE_S = 5.0


def _sec(match: re.Match[str]) -> float:
    frac = match[3] or "0"
    return int(match[1]) * 60 + int(match[2]) + int(frac) / (10 ** len(frac))


def _fmt(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"[{int(seconds // 60):02d}:{seconds % 60:05.2f}]"


def _timed(lrc: str) -> list[tuple[float, float | None, str]]:
    """``(start, end_or_None, raw_line)`` for every timed, non-empty line."""
    out: list[tuple[float, float | None, str]] = []
    for raw in lrc.splitlines():
        if _META_RE.match(raw.strip()):
            continue
        m = _LINE_RE.match(raw)
        if not m or not raw[m.end():].strip():
            continue
        words = list(_WORD_RE.finditer(raw))
        end = _sec(words[-1]) if words and not raw[words[-1].end():].strip() else None
        out.append((_sec(m), end, raw.rstrip()))
    return out


def covered_spans(aligned_lrc: str) -> list[tuple[float, float]]:
    lines = _timed(aligned_lrc)
    spans: list[tuple[float, float]] = []
    for i, (start, end, _) in enumerate(lines):
        nxt = lines[i + 1][0] if i + 1 < len(lines) else None
        stop = end if end is not None else min(start + _DEFAULT_LINE_S, nxt) if nxt else start + _DEFAULT_LINE_S
        stop = max(stop, start + 0.5)
        if spans and start - spans[-1][1] < _SPAN_GAP_S:
            spans[-1] = (spans[-1][0], max(spans[-1][1], stop))
        else:
            spans.append((start, stop))
    return spans


def merge_reference_with_asr(aligned_lrc: str, asr_lrc: str | None) -> str:
    """The pasted reference's aligned lines plus the transcript lines outside
    the spans they cover, in time order, with strictly increasing starts."""
    reference = _timed(repair_aligned_lrc(aligned_lrc))
    spans = covered_spans("\n".join(raw for _, _, raw in reference))
    merged: list[tuple[float, int, str]] = [(start, 0, raw) for start, _, raw in reference]
    for start, _, raw in _timed(asr_lrc or ""):
        if raw[_LINE_RE.match(raw).end():].strip().startswith(SPEECH_MARKER):
            inside = False
        else:
            inside = any(lo - 1.0 <= start <= hi + 1.0 for lo, hi in spans)
        if not inside:
            merged.append((start, 1, raw))
    merged.sort(key=lambda item: (item[0], item[1]))
    out: list[str] = []
    previous = -1.0
    for start, _, raw in merged:
        if start <= previous:
            start = previous + 0.01
            raw = _fmt(start) + raw[_LINE_RE.match(raw).end():]
        previous = start
        out.append(raw)
    return "\n".join(out) + "\n"
