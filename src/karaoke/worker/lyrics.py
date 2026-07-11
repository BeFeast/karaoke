"""LRCLIB synced-lyrics sourcing (Track 1).

The coordinator (devbox, residential IP) tries to source *real* lyrics for a
job from `LRCLIB <https://lrclib.net>`_ — a free, key-less community lyrics
database that returns time-synced ``.lrc`` lyrics when available. This runs on
the coordinator, **never** inside the GPU handler: LRCLIB is a plain HTTP API
and the lookup keys (artist / track / album / duration) are already known from
the yt-dlp metadata captured during download (see :mod:`karaoke.titles`).

Precedence the pipeline applies with this module's result:

    LRCLIB synced (duration OK)  >  forced-aligned (LRCLIB text: plain, or
    duration-rejected)  >  Whisper ASR synced  >  untimed floor

Lookup strategy (mirrors LRCLIB's own client guidance):

  1. ``GET /api/get`` with ``artist_name`` / ``track_name`` / ``album_name`` /
     ``duration`` — an exact match; LRCLIB matches duration within ±2s.
  2. On a 404 (or any miss), fall back to ``GET /api/search`` (fuzzy) and pick
     the best candidate by title similarity + duration proximity. A best
     candidate whose duration is more than ``_DURATION_REJECT_S`` away from
     the actual audio is rejected (#148): it is the wrong edit/cut, and its
     synced timings would drift against our track — strictly worse than the
     Whisper ASR floor, which is timed against the real audio. The candidate's
     *text* is still salvaged (#149) as ``rejected_text`` (timestamps
     stripped) so the pipeline can force-align it against the actual vocal
     stem: right text + right timings.

Results are cached in-process by ``(artist, track, duration)`` so re-running a
job (or a retry) does not re-hit LRCLIB.

A descriptive ``User-Agent`` is sent as LRCLIB requests (their docs ask clients
to identify themselves with a link back to the project).

The HTTP call is injectable (``http=...``) so unit tests never touch the
network — the same test seam shape the RunPod/vast clients use.
"""
from __future__ import annotations

import math
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import httpx

from karaoke.titles import track_cleanup_variants

LRCLIB_BASE = "https://lrclib.net"
USER_AGENT = "karaoke/1.0 (+https://github.com/BeFeast/karaoke)"
# LRCLIB matches the supplied duration within this many seconds for /api/get;
# we reuse it to score /api/search candidates.
_DURATION_TOLERANCE_S = 2
# Hard ceiling for /api/search candidates (#148): a best candidate further
# than this from the actual audio duration is the wrong edit/cut, so the whole
# record is rejected (text included) and the pipeline falls through to the
# Whisper ASR floor. Does not apply to /api/get — LRCLIB enforces ±2s there.
_DURATION_REJECT_S = 5
# Upper bound on the extra artist-free ``/api/search?q=`` calls the #230 cleanup
# ladder may issue after the parsed ``(artist, track)`` lookup missed.
_MAX_LADDER_QUERIES = 3

# Matches an LRC line timestamp tag, e.g. "[01:23.45]" / "[01:23]", including
# repeated tags on one line. Used to derive plain text from a synced LRC body.
LRC_TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")
# Matches an Enhanced-LRC inline *word* tag, e.g. "<01:23.45>" — same numeric
# shape as the line tag but angle-bracketed. Stripped alongside line tags when
# deriving plain text (a word-tagged line must reduce to just its words).
LRC_WORD_TAG_RE = re.compile(r"<\d{1,2}:\d{2}(?:[.:]\d{1,3})?>")
# Capturing variants of the line/word tag patterns, for reading a tag's time
# (mm/ss/frac groups). Kept separate from the strip patterns above so those
# stay bare non-capturing for ``re.sub`` speed.
_LRC_TAG_CAP_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
_LRC_WORD_TAG_CAP_RE = re.compile(r"<(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?>")

# Enhanced-LRC word-tag emission thresholds (#219). A segment whose words span
# more than this is split into sub-lines at inter-word gaps (see
# :func:`whisper_segments_to_lrc`); only gaps at least this wide are split on.
_MAX_LINE_SPAN_S = 12.0
_MIN_SPLIT_GAP_S = 1.0

# Max drift (seconds) between an aligner line's start and the curated LRCLIB
# line tag before we distrust the alignment and leave that line plain (#222).
# A wider gap means the aligner squeezed the words somewhere they don't belong;
# curated LRCLIB line timing must win over a bad word alignment.
_WORD_MERGE_DRIFT_S = 2.0

# Lyrics-source provenance values recorded in metadata.json.
SOURCE_LRCLIB_SYNCED = "lrclib_synced"
# LRCLIB text that we force-aligned against the vocal stem into a synced LRC
# inside the GPU job window: plain-only records (#55) and duration-rejected
# records whose text was salvaged (#149). Ranks just below native LRCLIB synced.
SOURCE_FORCED_ALIGNED = "forced_aligned"
SOURCE_LRCLIB_PLAIN = "lrclib_plain"
# Whisper ASR transcript with an approximate LRC synthesized from the Whisper
# segment timestamps (#145). Ranks below ``forced_aligned``: it only applies
# when LRCLIB missed entirely, so any LRCLIB-derived source always wins.
SOURCE_WHISPER_ASR_SYNCED = "whisper_asr_synced"
SOURCE_WHISPER_ASR = "whisper_asr"
SOURCE_INSTRUMENTAL = "instrumental"


def whisper_segments_to_lrc(segments: list[dict[str, Any]] | None) -> str:
    """Build a line-level LRC from Whisper segment timestamps (#145, #219).

    ``segments`` is the ``"segments"`` list of the GPU job's ``lyrics.json``
    (faster-whisper output). Each entry is
    ``{"start": float, "end": float, "text": str, "words": [...], ...}`` where
    each word is ``{"start": float, "end": float, "word": str, ...}``.

    When a segment carries usable per-word timestamps we emit an **Enhanced
    LRC** line — the line tag plus one inline ``<mm:ss.xx>`` start tag before
    every word and one trailing ``<mm:ss.xx>`` sung-end tag::

        [mm:ss.xx]<mm:ss.xx>word <mm:ss.xx>word … <mm:ss.xx>

    so the SPA can wipe word-by-word instead of interpolating linearly (bug 1).
    A degenerate mega-segment (faster-whisper occasionally fuses tens of
    seconds into one segment) that spans more than ``_MAX_LINE_SPAN_S`` is
    split into several lines at its largest inter-word gaps
    (``>= _MIN_SPLIT_GAP_S``), greedily largest-first, until every sub-line is
    short enough or no wide-enough gap remains (bug 2). Each sub-line's ``[..]``
    tag is its first word's start.

    A segment **without** usable words falls back to today's behavior: one
    plain line (no ``<>`` tags) tagged ``[mm:ss.xx]`` with the segment *start* —
    mixed word-tagged and plain lines in one body are valid by contract.

    Pure + tolerant by design: emitted lines are sorted by start time
    (out-of-order input yields stable output); entries that are not dicts or
    have empty/whitespace text are skipped; a word-less segment with no numeric
    ``start`` is skipped; malformed ``words`` (non-dict entry, missing keys,
    non-numeric time, empty word text) demote the whole segment to the
    word-less path rather than raising; negative times clamp to zero. Returns
    ``""`` when nothing usable remains — the caller then keeps the untimed ASR
    floor.
    """
    lines: list[tuple[float, str]] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        words = _parse_words(seg.get("words"))
        if words is not None:
            for group in _split_word_groups(words):
                lines.append(_enhanced_line(group))
            continue
        try:
            start = float(seg["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(start):
            continue
        start = max(start, 0.0)
        lines.append((start, f"{_fmt_lrc_timestamp(start)}{text}"))
    lines.sort(key=lambda item: item[0])
    return "\n".join(body for _, body in lines)


def _parse_words(raw: Any) -> list[tuple[float, float, str]] | None:
    """Validate a segment's ``words`` into ``(start, end, text)`` tuples.

    Returns ``None`` — signalling the caller to fall back to the word-less
    path — when ``raw`` is not a non-empty list or *any* entry is malformed
    (not a dict, missing/non-numeric ``start``/``end``, or empty ``word`` text
    once its faster-whisper leading space is stripped). All-or-nothing on
    purpose: a partially-parsed segment would emit a line with holes.
    """
    if not isinstance(raw, list) or not raw:
        return None
    parsed: list[tuple[float, float, str]] = []
    for word in raw:
        if not isinstance(word, dict):
            return None
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            return None
        # float() accepts "nan"/"inf" strings and float NaN/Infinity, which
        # would blow up timestamp formatting downstream — treat as malformed.
        if not (math.isfinite(start) and math.isfinite(end)):
            return None
        text = str(word.get("word") or "").strip()
        if not text:
            return None
        parsed.append((start, end, text))
    return parsed


def _split_word_groups(
    words: list[tuple[float, float, str]],
) -> list[list[tuple[float, float, str]]]:
    """Split ``words`` into sub-line groups on the widest inter-word gaps.

    Only over-long groups (span ``> _MAX_LINE_SPAN_S``) are eligible; each pass
    splits the single widest gap ``>= _MIN_SPLIT_GAP_S`` found across them,
    greedily largest-first, until no eligible gap remains. Word order is
    preserved, so the resulting groups stay in temporal order.
    """
    groups = [words]
    while True:
        best: tuple[float, int, int] | None = None  # (gap, group_index, word_index)
        for gi, group in enumerate(groups):
            if group[-1][1] - group[0][0] <= _MAX_LINE_SPAN_S:
                continue
            for wi in range(len(group) - 1):
                gap = group[wi + 1][0] - group[wi][1]
                if gap < _MIN_SPLIT_GAP_S:
                    continue
                if best is None or gap > best[0]:
                    best = (gap, gi, wi)
        if best is None:
            break
        _, gi, wi = best
        group = groups[gi]
        groups[gi : gi + 1] = [group[: wi + 1], group[wi + 1 :]]
    return groups


def _enhanced_line(group: list[tuple[float, float, str]]) -> tuple[float, str]:
    """Render one Enhanced-LRC line from a word group → ``(line_start, body)``.

    The line tag and the first word tag share the group's first-word start; a
    trailing ``<..>`` end tag carries the last word's end time.
    """
    line_start = max(group[0][0], 0.0)
    rendered = " ".join(
        f"{_fmt_lrc_word_tag(start)}{text}" for start, _end, text in group
    )
    end_tag = _fmt_lrc_word_tag(group[-1][1])
    return line_start, f"{_fmt_lrc_timestamp(line_start)}{rendered} {end_tag}"


def _fmt_lrc_timestamp(seconds: float, open_b: str = "[", close_b: str = "]") -> str:
    """Format ``seconds`` as an LRC timestamp tag ``mm:ss.xx`` (negatives clamp).

    Defaults to a line tag ``[mm:ss.xx]``; pass ``"<", ">"`` for an Enhanced-LRC
    inline word tag ``<mm:ss.xx>``. Reused for both so line and word tags stay
    byte-identical in time formatting — only the surrounding brackets differ.
    """
    seconds = max(seconds, 0.0)
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{open_b}{minutes:02d}:{rem:05.2f}{close_b}"


def _fmt_lrc_word_tag(seconds: float) -> str:
    """Format ``seconds`` as an Enhanced-LRC inline word tag ``<mm:ss.xx>``."""
    return _fmt_lrc_timestamp(seconds, "<", ">")


def lrc_to_plain(lrc: str) -> str:
    """Strip LRC timestamp tags from a body, keeping non-empty lines.

    Removes both ``[mm:ss.xx]`` line tags and Enhanced-LRC ``<mm:ss.xx>`` inline
    word tags (#219), so a word-tagged line reduces to just its words.
    """
    out: list[str] = []
    for line in lrc.splitlines():
        stripped = LRC_WORD_TAG_RE.sub("", LRC_TIMESTAMP_RE.sub("", line)).strip()
        if stripped:
            out.append(stripped)
    return "\n".join(out)


def _tag_seconds(match: re.Match[str]) -> float:
    """Convert a captured ``[..]``/``<..>`` timestamp tag to seconds.

    Normalizes the fractional part regardless of 2- or 3-digit precision
    (".45" -> 0.45s, ".456" -> 0.456s). Expects the capturing patterns
    (:data:`_LRC_TAG_CAP_RE` / :data:`_LRC_WORD_TAG_CAP_RE`).
    """
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    frac_raw = match.group(3) or "0"
    frac = int(frac_raw) / (10 ** len(frac_raw))
    return minutes * 60 + seconds + frac


def repair_aligned_lrc(body: str) -> str:
    """Repair CTC boundary-absorption in a force-aligned Enhanced LRC (#241).

    ctc-forced-aligner assigns every emission frame to some token, so leading
    instrumental silence gets absorbed into a line's FIRST word (its start is
    seconds before the voice) and trailing silence into the sung-end tag.
    Live evidence (Creep, job `rQM-`): "I" spanned 1:34.42→1:36.82 while the
    voice starts ≈1:36.4 — which both broke the drift check against curated
    LRCLIB tags and made the wipe crawl through the first word during the
    instrumental.

    Per line: when the first word's span to the second word (or the sung-end
    gap after the last word) exceeds ``max(3 s, 4× median word gap)``, pull it
    in to ``median gap`` from its neighbor. The line ``[..]`` tag follows the
    repaired first word. Lines with < 2 word tags pass through untouched.
    Pure + tolerant: unparseable lines are kept verbatim.
    """
    out: list[str] = []
    for raw in body.splitlines():
        tag = _LRC_TAG_CAP_RE.match(raw)
        if tag is None:
            out.append(raw)
            continue
        rest = raw[tag.end() :]
        word_tags = list(_LRC_WORD_TAG_CAP_RE.finditer(rest))
        if len(word_tags) < 3 or rest[word_tags[-1].end() :].strip():
            out.append(raw)
            continue
        starts = [_tag_seconds(m) for m in word_tags[:-1]]
        end = _tag_seconds(word_tags[-1])
        gaps = [b - a for a, b in zip(starts, starts[1:], strict=False) if b > a]
        if not gaps:
            out.append(raw)
            continue
        gaps_sorted = sorted(gaps)
        median_gap = max(gaps_sorted[len(gaps_sorted) // 2], 0.2)
        threshold = max(2.0, 3.0 * median_gap)
        new_start = starts[0]
        if len(starts) >= 2 and starts[1] - starts[0] > threshold:
            new_start = starts[1] - median_gap
        new_end = end
        if end - starts[-1] > threshold:
            new_end = starts[-1] + median_gap
        if new_start == starts[0] and new_end == end:
            out.append(raw)
            continue
        pieces = [_fmt_lrc_timestamp(new_start)]
        cursor = 0
        for i, m in enumerate(word_tags):
            pieces.append(rest[cursor : m.start()])
            if i == 0:
                pieces.append(_fmt_lrc_word_tag(new_start))
            elif i == len(word_tags) - 1:
                pieces.append(_fmt_lrc_word_tag(new_end))
            else:
                pieces.append(m.group())
            cursor = m.end()
        pieces.append(rest[cursor:])
        out.append("".join(pieces))
    repaired = "\n".join(out)
    if body.endswith("\n"):
        repaired += "\n"
    return repaired


@dataclass(frozen=True, slots=True)
class _AlignerLine:
    """One parsed aligner Enhanced-LRC line, for the LRCLIB word-tag merge.

    * ``start`` — the ``[..]`` line-tag time (== the first word start).
    * ``norm`` — whitespace-normalized plain text, for order-preserving line
      matching against the LRCLIB lines.
    * ``word_starts`` — inline ``<..>`` word-start times, in order.
    * ``end`` — the trailing ``<..>`` sung-end time, or ``None`` when the line
      is not a well-formed Enhanced line (no usable word timing to merge).
    """

    start: float
    norm: str
    word_starts: tuple[float, ...]
    end: float | None


def _parse_aligner_lines(aligned_lrc: str) -> list[_AlignerLine]:
    """Parse a GPU force-aligner Enhanced LRC into :class:`_AlignerLine` entries.

    Only lines with a leading ``[..]`` line tag are kept, in file order (the
    aligner emits input-line order, low-confidence lines dropped — #149). A line
    is treated as carrying word timing only when it is a well-formed Enhanced
    line: ``[start]<w0>w0 … <wN>wN <end>`` with a trailing *bare* end tag. Any
    other shape parses with ``word_starts=()``/``end=None`` (no word timing).
    """
    parsed: list[_AlignerLine] = []
    for raw in aligned_lrc.splitlines():
        tag = _LRC_TAG_CAP_RE.match(raw)
        if tag is None:
            continue
        body = raw[tag.end() :]
        norm = " ".join(_LRC_WORD_TAG_CAP_RE.sub("", body).split())
        if not norm:
            continue
        word_tags = list(_LRC_WORD_TAG_CAP_RE.finditer(body))
        word_starts: tuple[float, ...] = ()
        end: float | None = None
        # A well-formed Enhanced line ends with a bare ``<..>`` sung-end tag
        # (nothing after it); the tags before it are the word starts. Times
        # must be non-decreasing with the end at/after the last start —
        # nonmonotonic tags would surface as negative word durations downstream.
        if len(word_tags) >= 2 and not body[word_tags[-1].end() :].strip():
            starts = tuple(_tag_seconds(t) for t in word_tags[:-1])
            last_tag = _tag_seconds(word_tags[-1])
            monotonic = all(a <= b for a, b in zip(starts, starts[1:], strict=False)) and (
                not starts or last_tag >= starts[-1]
            )
            if monotonic:
                word_starts = starts
                end = last_tag
        parsed.append(
            _AlignerLine(start=_tag_seconds(tag), norm=norm, word_starts=word_starts, end=end)
        )
    return parsed


def _splice_word_tags(
    prefix: str, text: str, word_starts: tuple[float, ...], end: float | None
) -> str:
    """Insert ``<..>`` word tags into ``text`` at word boundaries, byte-preserving.

    ``prefix`` is the untouched LRCLIB ``[..]`` line tag; ``text`` is the rest of
    the raw line verbatim. One ``<start>`` tag is inserted immediately before
    each whitespace-delimited word (counts already checked equal by the caller),
    the original inter-word whitespace is preserved exactly, and a trailing
    ``<end>`` sung-end tag is appended — so stripping all tags reproduces the
    LRCLIB line text byte-for-byte.
    """
    parts = [prefix]
    last = 0
    for i, m in enumerate(re.finditer(r"\S+", text)):
        parts.append(text[last : m.start()])
        parts.append(_fmt_lrc_word_tag(word_starts[i]))
        parts.append(m.group())
        last = m.end()
    # Keep the original trailing whitespace verbatim (byte-preserving contract);
    # the sung-end tag goes after it, with a separating space only when the
    # line doesn't already end in whitespace.
    suffix = text[last:]
    parts.append(suffix)
    if end is not None:
        sep = "" if suffix.endswith((" ", "\t")) else " "
        parts.append(f"{sep}{_fmt_lrc_word_tag(end)}")
    return "".join(parts)


def merge_lrclib_word_tags(
    synced_lrc: str,
    aligned_lrc: str | None,
    *,
    drift_tolerance_s: float = _WORD_MERGE_DRIFT_S,
) -> tuple[str, bool, int, int]:
    """Overlay aligner word timings onto a curated LRCLIB synced LRC (#222).

    LRCLIB's synced LRC carries authoritative, human-curated line text + line
    timestamps but no word timing. ``aligned_lrc`` is the GPU force-aligner's
    Enhanced LRC for the *same* text (the coordinator sends
    ``lrc_to_plain(synced_lrc)`` as the align text): line tags + inline
    ``<mm:ss.xx>`` word tags, in input-line order, with low-confidence lines
    dropped (#149).

    We walk the LRCLIB lines and, for each, splice the matching aligner line's
    word ``<>`` tags between the LRCLIB words — keeping the LRCLIB line tag and
    text byte-for-byte. A line is left plain (LRCLIB verbatim) when:

    * the aligner dropped it (no matching aligner line, in order), or
    * the aligner emitted no word tags for it (token drift), or
    * its word count differs from the aligner's (word-count drift), or
    * the aligner start drifts more than ``drift_tolerance_s`` from the curated
      LRCLIB line tag — a bad alignment must not fight curated timing.

    Matching is by line order with a text guard: the aligner output is a strict
    *subsequence* of the LRCLIB lines, so an unmatched LRCLIB line keeps the
    aligner cursor for the next line (tolerating dropped lines). Multi-tag lines
    (``[t1][t2]text``) and bare line tags (instrumental breaks) pass through
    untouched.

    Pure + total: any parse quirk leaves the affected line plain. Returns
    ``(merged_lrc, merged_any, eligible, matched)`` — ``eligible`` counts the
    single-tag non-empty LRCLIB lines that were merge candidates and
    ``matched`` how many found their aligner counterpart (text equal, start
    within drift tolerance) — regardless of whether word tags were actually
    spliced (a plain aligner line or token-count drift still proves the TEXT
    fits the audio). Their ratio is a *match-quality* signal (#237): curated
    text that belongs to a different performance matches almost nowhere.
    When nothing merged, ``merged_lrc`` is ``synced_lrc`` verbatim (byte-exact
    fallback) and ``merged_any`` is False.
    """
    if not aligned_lrc or not aligned_lrc.strip():
        return synced_lrc, False, 0, 0
    aligner = _parse_aligner_lines(aligned_lrc)
    raw_lines = synced_lrc.splitlines()

    # Pre-parse every LRCLIB line once: (tag_match, text, norm, tag_times).
    # tag_times holds ALL leading tag times (multi-tag lines have several).
    parsed: list[tuple[re.Match[str] | None, str, str, list[float]]] = []
    for raw in raw_lines:
        tag = _LRC_TAG_CAP_RE.match(raw)
        text = raw[tag.end() :] if tag is not None else ""
        times: list[float] = []
        rest = text
        if tag is not None:
            times.append(_tag_seconds(tag))
            while (m := _LRC_TAG_CAP_RE.match(rest)) is not None:
                times.append(_tag_seconds(m))
                rest = rest[m.end() :]
        norm = " ".join(_LRC_WORD_TAG_CAP_RE.sub("", rest).split())
        parsed.append((tag, text, norm, times))

    out: list[str] = []
    cursor = 0
    merged_any = False
    eligible = 0
    matched_count = 0
    for i, raw in enumerate(raw_lines):
        tag, text, norm, times = parsed[i]
        # Lines without a tag or without text pass through and do not consume
        # the aligner cursor — they had no aligner counterpart.
        if tag is None or not norm:
            out.append(raw)
            continue
        if len(times) == 1:
            eligible += 1
        if cursor >= len(aligner) or aligner[cursor].norm != norm:
            out.append(raw)
            continue
        al = aligner[cursor]
        # Anchor drift on the SECOND word too: even after repair the first
        # word can carry residual leading-silence absorption, while word 2
        # is voice-locked (#241 measurements: 33/36 vs 28/37 within 2 s).
        anchors = [al.start]
        if len(al.word_starts) >= 2:
            anchors.append(al.word_starts[1])
        drift_ok = any(
            abs(a - t) <= drift_tolerance_s for a in anchors for t in times
        )
        # A text match outside tolerance can mean the aligner entry belongs to
        # a LATER occurrence of a repeated line (this one was dropped, #149).
        # Defer consuming it only when a later curated line with the same text
        # sits within tolerance of the entry; otherwise consume it here as an
        # ordinary drift-reject (line stays plain either way).
        if not drift_ok:
            defer = any(
                p_norm == norm
                and any(
                    abs(a - t) <= drift_tolerance_s for a in anchors for t in p_times
                )
                for _, _, p_norm, p_times in parsed[i + 1 :]
            )
            if not defer:
                cursor += 1
            out.append(raw)
            continue
        cursor += 1
        if len(times) == 1:
            matched_count += 1
        # Multi-tag lines ([t1][t2]text) consume their aligner entry (their
        # text was sent to the aligner) but stay plain — repeated-tag timing
        # is ambiguous.
        words = re.findall(r"\S+", text)
        if len(times) == 1 and al.word_starts and len(al.word_starts) == len(words):
            out.append(_splice_word_tags(raw[: tag.end()], text, al.word_starts, al.end))
            merged_any = True
            continue
        out.append(raw)
    if not merged_any:
        return synced_lrc, False, eligible, matched_count
    merged = "\n".join(out)
    # Preserve the source's final newline (byte-preserving outside merged lines).
    if synced_lrc.endswith("\n"):
        merged += "\n"
    return merged, True, eligible, matched_count


@dataclass(frozen=True, slots=True)
class LyricsResult:
    """Outcome of an LRCLIB lookup.

    * ``synced_lrc`` — time-synced ``.lrc`` body, or ``None``.
    * ``plain`` — plain-text lyrics, or ``None``.
    * ``instrumental`` — LRCLIB flagged the track as instrumental (no lyrics).
    * ``source`` — where the data came from for provenance / logging:
      ``"lrclib_get"``, ``"lrclib_search"``, ``"instrumental"`` or ``"none"``.
    * ``rejected`` — why a record LRCLIB *did* return was dropped anyway
      (e.g. ``"duration_mismatch (28s)"``, #148), or ``None``. Only ever set
      alongside ``source="none"``; surfaced in ``metadata.json`` provenance.
    * ``rejected_text`` — plain text salvaged from a duration-rejected record
      (#149): the words are usually still right (same song, different edit) —
      only the timings belong to the wrong cut. Timestamps are already
      stripped from synced input; plain input passes through as-is. Only ever
      set alongside ``rejected``; never makes :attr:`found` true, so #148's
      reject semantics are unchanged. The pipeline force-aligns it against the
      actual vocal stem.
    * ``match_variant`` — the cleaned track variant that produced this hit via
      the #230 fallback ladder (e.g. ``"Конь"`` for a title whose parsed track
      was ``«Конь». Голубой Ургант. …``), or ``None`` when the parsed
      ``(artist, track)`` matched directly. Surfaced in ``metadata.json`` as
      ``lyrics_match_variant`` for debuggability only.
    """

    synced_lrc: str | None = None
    plain: str | None = None
    instrumental: bool = False
    source: str = "none"
    rejected: str | None = None
    rejected_text: str | None = None
    match_variant: str | None = None

    @property
    def found(self) -> bool:
        """True when LRCLIB returned usable info (lyrics or an instrumental flag)."""
        return bool(self.synced_lrc or self.plain or self.instrumental)


# Type of the injectable HTTP callable: (method, url, params) -> (status, json).
HttpFn = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]


def _default_http(
    method: str, url: str, params: dict[str, Any] | None
) -> tuple[int, Any]:
    """Real HTTP via ``httpx`` (sync — the pipeline calls us in a worker thread).

    Returns ``(status_code, parsed_json_or_None)``. Network / decode failures
    surface as ``(0, None)`` so the caller treats them as a miss and keeps the
    Whisper transcript rather than failing the whole job over a lyrics lookup.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        resp = httpx.request(
            method, url, params=params, headers=headers, timeout=10.0
        )
    except httpx.HTTPError:
        return 0, None
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    return resp.status_code, payload


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _from_record(record: dict[str, Any], source: str) -> LyricsResult:
    """Build a :class:`LyricsResult` from an LRCLIB record dict."""
    if record.get("instrumental"):
        return LyricsResult(instrumental=True, source="instrumental")
    synced = _clean(record.get("syncedLyrics"))
    plain = _clean(record.get("plainLyrics"))
    if not synced and not plain:
        return LyricsResult(source="none")
    return LyricsResult(synced_lrc=synced, plain=plain, source=source)


def _duration_delta(record: dict[str, Any], duration: int | None) -> float | None:
    """Absolute duration delta (seconds) between a candidate and the actual
    audio, or ``None`` when either side is unknown/unparseable."""
    rec_dur = record.get("duration")
    if duration is None or rec_dur is None:
        return None
    try:
        return abs(float(rec_dur) - float(duration))
    except (TypeError, ValueError):
        return None


def _score_candidate(
    record: dict[str, Any], track: str, duration: int | None
) -> tuple[int, float]:
    """Score a ``/api/search`` candidate. Higher is better.

    Ranks by (1) duration proximity bucket then (2) title-token overlap.
    Returns a tuple usable as a sort key (both ascending-friendly when negated).
    """
    # Duration proximity: within tolerance is best, then by absolute delta.
    delta = _duration_delta(record, duration)
    within = 1 if delta is not None and delta <= _DURATION_TOLERANCE_S else 0
    if delta is None:
        delta = float("inf")

    # Title-token overlap (cheap fuzzy match, no extra deps).
    want = {t for t in track.lower().split() if t}
    got = {t for t in str(record.get("trackName") or "").lower().split() if t}
    overlap = len(want & got)

    # Prefer candidates that actually carry synced lyrics.
    has_synced = 1 if _clean(record.get("syncedLyrics")) else 0

    # Sort key: maximize within-tolerance, then synced availability, then token
    # overlap, then minimize duration delta (negated so larger = better).
    return (within + has_synced + overlap, -delta)


class LyricsSource:
    """LRCLIB lookup client with an in-process cache.

    Test seam: pass ``http`` to inject a callable with the same shape as
    :func:`_default_http` so unit tests drive the lookup without the network.
    """

    def __init__(self, *, http: HttpFn | None = None, base_url: str = LRCLIB_BASE) -> None:
        self._http = http or _default_http
        self._base = base_url.rstrip("/")
        self._cache: dict[tuple[str, str, int | None], LyricsResult] = {}
        self._lock = threading.Lock()

    def _cache_key(
        self, artist: str | None, track: str | None, duration: int | None
    ) -> tuple[str, str, int | None]:
        return ((artist or "").strip().lower(), (track or "").strip().lower(), duration)

    def fetch(
        self,
        *,
        artist: str | None,
        track: str | None,
        album: str | None = None,
        duration: int | None = None,
    ) -> LyricsResult:
        """Look up lyrics for ``(artist, track[, album, duration])``.

        Tries the exact ``/api/get`` endpoint first, then the fuzzy
        ``/api/search`` fallback. Returns an empty :class:`LyricsResult`
        (``source="none"``) when LRCLIB has nothing — the caller then keeps the
        Whisper transcript. Results are cached by ``(artist, track, duration)``.
        """
        track = _clean(track)
        if not track:
            # Without at least a track name there is nothing to look up.
            return LyricsResult(source="none")
        artist = _clean(artist)
        album = _clean(album)

        key = self._cache_key(artist, track, duration)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        result = self._lookup(artist, track, album, duration)

        with self._lock:
            self._cache[key] = result
        return result

    # -- internals ----------------------------------------------------------
    def _lookup(
        self,
        artist: str | None,
        track: str,
        album: str | None,
        duration: int | None,
    ) -> LyricsResult:
        get_result = self._try_get(artist, track, album, duration)
        if get_result is not None and get_result.found:
            return get_result
        search_result = self._try_search(artist, track, duration)
        if search_result is not None and search_result.found:
            return search_result

        # Fallback ladder (#230): the parsed (artist, track) missed. Retry with
        # progressively cleaned track variants via an artist-free
        # /api/search?q=<variant> — covers covers / TV performances where the
        # channel-supplied artist is wrong (e.g. «Конь» credited to Little Big
        # but curated under Любэ). Each candidate must clear the same duration
        # hard-reject (#148): an exact-duration synced hit (±5s) is trustworthy
        # even with a mismatched artist, and a wrong-duration one is dropped.
        # Requires a known audio duration (the gate is the only safety net here)
        # and is bounded to ≤ _MAX_LADDER_QUERIES extra HTTP calls.
        if duration is not None:
            for variant in track_cleanup_variants(track)[:_MAX_LADDER_QUERIES]:
                laddered = self._try_search_q(variant, duration)
                # An artist-free instrumental "match" is untrustworthy — the
                # only evidence is duration, and marking a lyrical track
                # instrumental drops its transcript entirely. Require lyrics.
                if laddered is not None and laddered.found and not laddered.instrumental:
                    return replace(laddered, match_variant=variant)

        # No ladder hit: preserve the primary search result's #148 rejection /
        # #149 salvaged text for the pipeline's force-align + provenance.
        if search_result is not None and search_result.rejected:
            return search_result
        return LyricsResult(source="none")

    def _try_get(
        self,
        artist: str | None,
        track: str,
        album: str | None,
        duration: int | None,
    ) -> LyricsResult | None:
        """Exact match via ``GET /api/get``. ``artist_name``/``track_name`` are
        required by LRCLIB; we only call it when an artist is known."""
        if not artist:
            return None
        params: dict[str, Any] = {"artist_name": artist, "track_name": track}
        if album:
            params["album_name"] = album
        if duration is not None:
            params["duration"] = duration
        status, body = self._http("GET", f"{self._base}/api/get", params)
        if status == 200 and isinstance(body, dict):
            return _from_record(body, source="lrclib_get")
        return None

    def _try_search(
        self, artist: str | None, track: str, duration: int | None
    ) -> LyricsResult | None:
        """Fuzzy match via ``GET /api/search``; pick the best candidate."""
        params: dict[str, Any] = {"track_name": track}
        if artist:
            params["artist_name"] = artist
        status, body = self._http("GET", f"{self._base}/api/search", params)
        if status != 200 or not isinstance(body, list) or not body:
            return None
        candidates = [c for c in body if isinstance(c, dict)]
        if not candidates:
            return None
        best = max(candidates, key=lambda c: _score_candidate(c, track, duration))
        # Hard reject (#148): a best candidate too far from the actual audio
        # duration is the wrong edit/cut — its synced timings would drift
        # against our track. Drop the record's lyrics/instrumental flag, but
        # salvage its *text* (#149): for the common "official video edit vs
        # canonical recording" case the words are still right, so the pipeline
        # force-aligns them against the actual vocal stem instead of falling
        # straight to the Whisper ASR floor. Unknown durations (either side)
        # are never rejected.
        delta = _duration_delta(best, duration)
        if delta is not None and delta > _DURATION_REJECT_S:
            plain = _clean(best.get("plainLyrics"))
            synced = _clean(best.get("syncedLyrics"))
            salvaged = plain or (lrc_to_plain(synced) if synced else "")
            return LyricsResult(
                source="none",
                rejected=f"duration_mismatch ({round(delta, 1):g}s)",
                rejected_text=salvaged or None,
            )
        return _from_record(best, source="lrclib_search")

    def _try_search_q(self, query: str, duration: int) -> LyricsResult | None:
        """Artist-free fuzzy search via ``GET /api/search?q=<query>`` (#230).

        Scores candidates with :func:`_score_candidate` against ``query`` and
        accepts the best one only when its duration is known and within
        ``_DURATION_REJECT_S`` of the actual audio — the mismatched-artist risk
        means the duration gate is the sole safety net, so unlike the
        artist-scoped path this never salvages a wrong-duration record's text.
        Returns ``None`` on a transport miss, an empty result, or a
        gate-failing best candidate (so the ladder keeps trying / falls back)."""
        status, body = self._http("GET", f"{self._base}/api/search", {"q": query})
        if status != 200 or not isinstance(body, list) or not body:
            return None
        picked = self._pick_gated(body, query, duration)
        if picked is not None:
            return picked
        # Editions expansion (#233): /api/search?q= returns LRCLIB's top-N by
        # relevance, which can hide the in-tolerance *edition* of a song whose
        # visible records all fail the duration gate. When a gate-failed
        # candidate's track name equals the query (case-folded), the SONG is
        # right and only the edition is wrong — one artist+track follow-up
        # surfaces its other editions, re-gated the same way.
        folded = query.casefold()
        # Deduplicate title-matched (artist, track) pairs preserving order —
        # multiple artists can share the exact title, and the first artist may
        # have no in-tolerance edition while a later one does. Each pair costs
        # one follow-up call, so keep the total bounded.
        pairs: list[tuple[str, str]] = []
        for c in body:
            if not isinstance(c, dict):
                continue
            track_name = str(c.get("trackName") or "").strip()
            artist_name = str(c.get("artistName") or "").strip()
            if not track_name or not artist_name:
                continue
            if track_name.casefold() != folded:
                continue
            key = (artist_name, track_name)
            if key not in pairs:
                pairs.append(key)
        for artist_name, track_name in pairs[:_MAX_LADDER_QUERIES]:
            status2, body2 = self._http(
                "GET",
                f"{self._base}/api/search",
                {"artist_name": artist_name, "track_name": track_name},
            )
            if status2 != 200 or not isinstance(body2, list) or not body2:
                continue
            picked = self._pick_gated(body2, query, duration)
            if picked is not None:
                return picked
        return None

    def _pick_gated(
        self, body: list[Any], query: str, duration: int
    ) -> LyricsResult | None:
        """Duration-gate ``body`` BEFORE ranking, then pick the best candidate.

        Gate-first matters: a highly ranked wrong-duration record must not
        shadow a valid lower-ranked one. Returns ``None`` when no candidate
        survives the gate.
        """
        candidates = [
            c
            for c in body
            if isinstance(c, dict)
            and (delta := _duration_delta(c, duration)) is not None
            and delta <= _DURATION_REJECT_S
        ]
        if not candidates:
            return None
        best = max(candidates, key=lambda c: _score_candidate(c, query, duration))
        return _from_record(best, source="lrclib_search")
