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

from karaoke.textnorm import normalize
from karaoke.worker.lyrics import LRC_WORD_TAG_RE, SPEECH_MARKER, repair_aligned_lrc

_LINE_RE = re.compile(r"^\s*\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
_WORD_RE = re.compile(r"<(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?>")
_META_RE = re.compile(r"^\[[A-Za-z]+:[^\]]*\]$")
# Pasted lines closer than this are one covered span (a verse, a chorus).
_SPAN_GAP_S = 8.0
# A pasted line with no end tag is assumed to last this long at most; an end
# tag further than _MAX_LINE_S away is an aligner stretch, not the line.
_DEFAULT_LINE_S = 5.0
_MAX_LINE_S = 12.0
# Neighbouring pasted lines further apart than this were torn apart by the
# aligner (see anchor_to_asr).
_CONTIGUOUS_GAP_S = 20.0
_PULLED_LINE_S = 3.5


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
        if end is not None and end - start <= _MAX_LINE_S:
            stop = end
        else:
            stop = min(start + _DEFAULT_LINE_S, nxt) if nxt else start + _DEFAULT_LINE_S
        stop = max(stop, start + 0.5)
        if spans and start - spans[-1][1] < _SPAN_GAP_S:
            spans[-1] = (spans[-1][0], max(spans[-1][1], stop))
        else:
            spans.append((start, stop))
    return spans


def anchor_to_asr(
    reference: list[tuple[float, float | None, str]], asr_segments: list[dict] | None, lang: str | None
) -> list[tuple[float, float | None, str]]:
    """Re-time pasted lines the aligner tore away from their neighbours.

    CTC alignment places every line in order whatever the audio holds, so a
    partial paste gets stretched over the whole song: a pasted intro lands on
    a music video's dialogue, the rest of a chorus on a later repeat. Pasted
    lines are sung one after another, so when two neighbours end up more than
    20 s apart the transcript decides: a forward pass looks for line ``i`` in
    the 20 s after line ``i-1``, a backward pass for line ``i`` in the 20 s
    before line ``i+1``; if >= 40 % of the line's words (>= 2) are heard
    there, the line moves to the first of them and loses its word tags.
    """
    words: list[tuple[str, float]] = []
    for seg in asr_segments or []:
        if not isinstance(seg, dict):
            continue
        for w in seg.get("words") or []:
            if not isinstance(w, dict):
                continue
            try:
                t = float(w.get("start"))
            except (TypeError, ValueError):
                continue
            words += [(key, t) for key in normalize(str(w.get("word") or ""), lang).split()]
    if not words or len(reference) < 2:
        return reference
    words.sort(key=lambda kw: kw[1])
    keys = [
        normalize(LRC_WORD_TAG_RE.sub(" ", raw[_LINE_RE.match(raw).end():]), lang).split()
        for _, _, raw in reference
    ]
    times = [start for start, _, _ in reference]
    moved = [False] * len(reference)

    def heard(idx: int, lo: float, hi: float) -> float | None:
        want = set(keys[idx])
        if not want:
            return None
        hits = [(t, k) for k, t in words if lo <= t <= hi and k in want]
        found = {k for _, k in hits}
        if len(found) >= 2 and len(found) >= 0.4 * len(want):
            return min(t for t, _ in hits)
        return None

    for i in range(1, len(times)):
        if times[i] - times[i - 1] > _CONTIGUOUS_GAP_S:
            t = heard(i, times[i - 1] + 0.3, times[i - 1] + _CONTIGUOUS_GAP_S)
            if t is not None and (i + 1 >= len(times) or t < times[i + 1] or moved[i + 1] is False):
                times[i], moved[i] = t, True
    for i in range(len(times) - 2, -1, -1):
        if times[i + 1] - times[i] > _CONTIGUOUS_GAP_S:
            floor = times[i - 1] + 0.3 if i > 0 else 0.0
            t = heard(i, max(floor, times[i + 1] - _CONTIGUOUS_GAP_S), times[i + 1] - 0.3)
            if t is not None:
                times[i], moved[i] = t, True
    # Still torn away and not even heard where the aligner put it (a pasted
    # intro sitting on dialogue): pull the run up to just before the next
    # line, ~3.5 s a line — plausible timing beats timing on someone talking.
    i = len(times) - 2
    while i >= 0:
        if times[i + 1] - times[i] > _CONTIGUOUS_GAP_S and not moved[i] and heard(i, times[i] - 3, times[i] + 3) is None:
            run_start = i
            while run_start > 0 and not moved[run_start - 1] and times[run_start] - times[run_start - 1] <= _CONTIGUOUS_GAP_S \
                    and heard(run_start - 1, times[run_start - 1] - 3, times[run_start - 1] + 3) is None:
                run_start -= 1
            floor = times[run_start - 1] + 0.5 if run_start > 0 else 0.0
            run = list(range(run_start, i + 1))
            for pos, k in enumerate(run):
                t = times[i + 1] - _PULLED_LINE_S * (len(run) - pos)
                if t > floor:
                    times[k], moved[k] = t, True
            i = run_start - 1
        else:
            i -= 1
    out: list[tuple[float, float | None, str]] = []
    for idx, (start, end, raw) in enumerate(reference):
        if moved[idx] and abs(times[idx] - start) > 1e-6:
            plain = " ".join(LRC_WORD_TAG_RE.sub(" ", raw[_LINE_RE.match(raw).end():]).split())
            out.append((times[idx], None, f"{_fmt(times[idx])}{plain}"))
        else:
            out.append((start, end, raw))
    out.sort(key=lambda item: item[0])
    return out


def merge_reference_with_asr(
    aligned_lrc: str, asr_lrc: str | None, asr_segments: list[dict] | None = None, lang: str | None = None
) -> str:
    """The pasted reference's aligned lines (anchored to the transcript, see
    :func:`anchor_to_asr`) plus the transcript lines outside the spans they
    cover, in time order, with strictly increasing starts."""
    reference = anchor_to_asr(_timed(repair_aligned_lrc(aligned_lrc)), asr_segments, lang)
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
