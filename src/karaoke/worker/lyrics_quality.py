"""Conservative lyric reconciliation: checked means automated checks, never perfect."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from karaoke.worker.lyrics import (
    _MAX_CHARS_PER_SECOND,
    LRC_TIMESTAMP_RE,
    LRC_WORD_TAG_RE,
    _fmt_lrc_timestamp,
    _parse_aligner_lines,
    drop_unreliable_aligned_lines,
    repair_aligned_lrc,
)

_MAX_TOKENS = 2000
_MAX_LINES = 500
_WORD = re.compile(r"[^\W_]+(?:[’'][^\W_]+)*", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.casefold().replace("’", "'"))


def _reference(text: str | None) -> list[str]:
    result = []
    for raw in (text or "").splitlines():
        if re.match(r"^\[(?:ar|ti|al|by|offset|length):", raw, re.I):
            continue
        clean = LRC_WORD_TAG_RE.sub("", LRC_TIMESTAMP_RE.sub("", raw)).strip()
        if _tokens(clean):
            result.extend([clean] * max(1, len(LRC_TIMESTAMP_RE.findall(raw))))
    return result


def _pairs(left: list, right: list) -> list[tuple[int, int]]:
    """Exact monotone one-to-one LCS with bounded quadratic work."""
    if len(left) > _MAX_TOKENS or len(right) > _MAX_TOKENS:
        raise ValueError("lyric comparison exceeds bounded token limit")
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
    result = []
    while i and j:
        direction = directions[i * width + j]
        if direction == 1:
            result.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif direction == 2:
            i -= 1
        else:
            j -= 1
    return result[::-1]


@dataclass(frozen=True)
class _Word:
    text: str
    start: float
    end: float
    probability: float | None


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _invalid_evidence(asr: Any) -> bool:
    if asr is None:
        return False
    if not isinstance(asr, dict):
        return True
    for key in ("segments", "retry_segments"):
        value = asr.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            return True
        for segment in value:
            if not isinstance(segment, dict):
                return True
            words = segment.get("words")
            if words is not None and (
                not isinstance(words, list) or any(not isinstance(word, dict) for word in words)
            ):
                return True
    return False


def _same_observation(left: list[_Word], right: list[_Word]) -> bool:
    """Collapse repeated decodes, never successive sung word occurrences."""
    if not left or len(left) != len(right):
        return False
    for a, b in zip(left, right, strict=True):
        overlap = min(a.end, b.end) - max(a.start, b.start)
        if (
            _tokens(a.text) != _tokens(b.text)
            or abs(a.start - b.start) > 0.25
            or abs(a.end - b.end) > 0.25
            or overlap < 0.5 * min(a.end - a.start, b.end - b.start)
        ):
            return False
    return True


def _dedupe_retry_segments(segments: list) -> list:
    """Deduplicate whole observations; retain competing text for review.

    Use the first timing observation, with the lowest confidence seen for each
    word. Selecting whichever version best matches the reference would hide
    uncertainty. Non-overlapping repetitions are separate performances.
    """
    retained: list = []
    parsed: list[list[_Word]] = []
    for segment in segments:
        words = _asr_words({"segments": [segment]})
        duplicate = next((i for i, prior in enumerate(parsed)
                          if _same_observation(prior, words)), None)
        if duplicate is None:
            retained.append(segment)
            parsed.append(words)
            continue
        prior = parsed[duplicate]
        merged_words = []
        for a, b in zip(prior, words, strict=True):
            probability = (
                min(a.probability, b.probability)
                if a.probability is not None and b.probability is not None else None
            )
            merged_words.append(dict(word=a.text, start=a.start, end=a.end,
                                     probability=probability))
        retained[duplicate] = dict(retained[duplicate], words=merged_words)
        parsed[duplicate] = _asr_words({"segments": [retained[duplicate]]})
    return retained


def _asr_words(asr: dict | None) -> list[_Word]:
    words = []
    data = asr if isinstance(asr, dict) else {}
    base = _list(data.get("segments"))
    retries = _dedupe_retry_segments(_list(data.get("retry_segments")))
    windows = []
    for segment in retries:
        if not isinstance(segment, dict):
            continue
        valid = _asr_words({"segments": [segment]})
        if valid:
            windows.append((valid[0].start, valid[-1].end))
    segments = [(segment, False) for segment in base] + [(segment, True) for segment in retries]
    for segment, retry in segments:
        if not isinstance(segment, dict):
            continue
        for raw in _list(segment.get("words")):
            if not isinstance(raw, dict):
                continue
            try:
                start, end = float(raw["start"]), float(raw["end"])
                probability = raw.get("probability")
                if probability is not None:
                    probability = float(probability)
                if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
                    continue
                if probability is not None and not (math.isfinite(probability) and 0 <= probability <= 1):
                    continue
                if not retry and any(a <= (start + end) / 2 <= b for a, b in windows):
                    continue
                text = str(raw.get("word") or "").strip()
                if _tokens(text):
                    words.append(_Word(text, start, end, probability))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(words, key=lambda word: word.start)


def _nearby(line, words: list[_Word]) -> list[_Word]:
    # An absorbed-silence end tag must not expand the search to another verse.
    stop = line.start + min(15.0, max(5.0, len(_tokens(line.norm)) * 1.5))
    return [word for word in words if line.start - 2 <= word.start <= stop]


def _support(line, words: list[_Word]) -> list[_Word]:
    target = _tokens(line.norm)
    if len(target) > 80:
        return []
    nearby = _nearby(line, words)
    if len(nearby) > 150:
        return []
    best: tuple[tuple[float, float], list[_Word]] = ((0, float("-inf")), [])
    for start in range(len(nearby)):
        if abs(nearby[start].start - line.start) > 2:
            continue
        for size in range(max(1, len(target) - 2), len(target) + 3):
            candidate = nearby[start:start + size]
            if len(candidate) != size:
                continue
            tokens = _tokens(" ".join(word.text for word in candidate))
            exact = len(_pairs(target, tokens))
            similarity = SequenceMatcher(None, " ".join(target), " ".join(tokens), autojunk=False).ratio()
            score = (similarity, -abs(candidate[0].start - line.start))
            if exact >= min(2, len(target)) and similarity >= 0.84 and score > best[0]:
                best = (score, candidate)
    return best[1]


def _timing_problems(line) -> list[str]:
    if not line.word_starts or line.end is None:
        return ["word_timing_missing"]
    if len(line.word_starts) != len(line.norm.split()):
        return ["word_timing_mismatch"]
    times = [*line.word_starts, line.end]
    codes = []
    if any(b - a <= 0 for a, b in zip(times, times[1:], strict=False)):
        codes.append("word_timing_collapsed")
    if any(b - a > 4 for a, b in zip(times, times[1:], strict=False)):
        codes.append("word_timing_gap")
    if abs(line.start - times[0]) > 0.5:
        codes.append("line_word_timing_drift")
    if len(line.word_starts) > 1:
        span = line.word_starts[-1] - line.word_starts[0]
        chars = len(" ".join(line.norm.split()[:-1]))
        if chars / max(span, 0.05) > _MAX_CHARS_PER_SECOND:
            codes.append("word_timing_pace")
    return codes


def _repair_timing(raw: str, line, words: list[_Word]) -> tuple[str, bool]:
    if not _timing_problems(line):
        return raw, False
    support = _support(line, words)
    if not support or _tokens(line.norm) != _tokens(" ".join(w.text for w in support)):
        return raw, False
    originals = line.norm.split()
    if len(originals) != len(support) or len(line.word_starts) != len(support):
        return raw, False
    starts = list(line.word_starts)
    for i, word in enumerate(support):
        if word.probability is not None and word.probability >= 0.7:
            starts[i] = word.start
        elif abs(starts[i] - word.start) > 1:
            return raw, False
    end = line.end
    if end is None or abs(end - support[-1].end) > 1:
        if support[-1].probability is None or support[-1].probability < 0.7:
            return raw, False
        end = support[-1].end
    if any(b <= a for a, b in zip(starts, [*starts[1:], end], strict=True)):
        return raw, False
    rendered = _fmt_lrc_timestamp(starts[0]) + " ".join(
        f"{_fmt_lrc_timestamp(start, '<', '>')}{text}"
        for start, text in zip(starts, originals, strict=True)
    ) + " " + _fmt_lrc_timestamp(end, "<", ">")
    return rendered, rendered != raw


def assess_lyrics(
    curated_text: str | None,
    selected_lrc: str | None,
    asr: dict | None,
    *,
    restored_lines: int = 0,
    repaired_timing_lines: int = 0,
    preservation_issues: list | None = None,
) -> dict[str, Any]:
    """Compare the full reference, selected output and independent ASR.

    Missing reference lines are candidates for review, never proof a verse was
    performed. Keep text completeness separate from timing confidence.
    """
    expected = _reference(curated_text)
    lines = _parse_aligner_lines(selected_lrc or "")
    issues: list[dict[str, Any]] = []
    counts = dict(expected_lines=len(expected), matched_lines=0, output_lines=len(lines),
                  missing_lines=len(expected), restored_lines=restored_lines,
                  repaired_timing_lines=repaired_timing_lines, expected_words=0,
                  matched_words=0, missing_words=0, extra_words=0, asr_unmatched_words=0,
                  output_unconfirmed_words=0)

    def issue(code: str, **details):
        issues.append(dict(code=code, **details))

    if _invalid_evidence(asr):
        issue("asr_evidence_invalid", detail="Malformed ASR evidence requires review.")
    evidence = asr if isinstance(asr, dict) else {}
    reference_words = [word for line in expected for word in _tokens(line)]
    output_words = [word for line in lines for word in _tokens(line.norm)]
    words = _asr_words(asr)
    asr_tokens = [token for word in words for token in _tokens(word.text)]
    if max(len(expected), len(lines)) > _MAX_LINES or max(len(reference_words), len(output_words), len(asr_tokens)) > _MAX_TOKENS:
        issue("comparison_limit_exceeded", detail="Input requires review; comparison was not truncated.")
        return dict(schema_version=1, status="needs_review", text_confidence="uncertain",
                    timing_confidence="uncertain", counts=counts, issues=issues)
    if expected:
        matched = _pairs([_tokens(line) for line in expected], [_tokens(line.norm) for line in lines])
        indices = {i for i, _ in matched}
        counts.update(matched_lines=len(matched), missing_lines=len(expected) - len(matched))
        for i, text in enumerate(expected):
            if i not in indices:
                issue("reference_line_missing_or_changed", line_index=i, text=text,
                      detail="May be an unperformed verse or transcription omission; review audio.")
        matched_words = _pairs(reference_words, output_words)
        left, right = {i for i, _ in matched_words}, {j for _, j in matched_words}
        missing = [word for i, word in enumerate(reference_words) if i not in left]
        extra = [word for i, word in enumerate(output_words) if i not in right]
        counts.update(expected_words=len(reference_words), matched_words=len(matched_words),
                      missing_words=len(missing), extra_words=len(extra))
        if missing:
            issue("reference_words_missing", text=" ".join(missing))
        if extra:
            issue("unexpected_output_words", text=" ".join(extra))
    else:
        issue("reference_unavailable", detail="No independent curated reference for completeness.")
    if not lines:
        issue("timed_lyrics_unavailable")
    if not words:
        issue("asr_evidence_unavailable", detail="Independent word timing evidence is unavailable.")
    else:
        matched_asr = _pairs(asr_tokens, output_words)
        indices = {i for i, _ in matched_asr}
        absent = [token for i, token in enumerate(asr_tokens) if i not in indices]
        counts["asr_unmatched_words"] = len(absent)
        output_indices = {j for _, j in matched_asr}
        unconfirmed = [token for i, token in enumerate(output_words) if i not in output_indices]
        counts["output_unconfirmed_words"] = len(unconfirmed)
        if unconfirmed:
            issue("output_words_unconfirmed", text=" ".join(unconfirmed),
                  detail="Selected words lack one-to-one ASR evidence; ASR may be wrong.")
        if absent:
            issue("asr_words_unrepresented", text=" ".join(absent),
                  detail="ASR may be wrong; review voiced content absent from selected lyrics.")
    for segment in [*_list(evidence.get("segments")), *_list(evidence.get("retry_segments"))]:
        if not isinstance(segment, dict):
            continue
        text_tokens = _tokens(str(segment.get("text") or ""))
        segment_words = _asr_words({"segments": [segment]})
        timed_tokens = _tokens(" ".join(word.text for word in segment_words))
        if text_tokens and text_tokens != timed_tokens:
            issue("asr_segment_words_incomplete", text=str(segment.get("text") or ""),
                  detail="Segment text and word timing evidence disagree; requires review.")
    previous_end = None
    for i, line in enumerate(lines):
        for code in _timing_problems(line):
            issue(code, line_index=i, text=line.norm, start=line.start, end=line.end)
        if previous_end is not None and line.start < previous_end - 0.1:
            issue("line_timing_overlap", line_index=i, start=line.start, end=previous_end)
        previous_end = line.end if line.end is not None else line.start
        if words:
            support = _support(line, words)
            if not support:
                issue("asr_line_unconfirmed", line_index=i, text=line.norm, start=line.start)
            elif _tokens(line.norm) != _tokens(" ".join(word.text for word in support)):
                issue("asr_text_disagreement", line_index=i, text=line.norm, start=line.start)
            else:
                if any(word.probability is None or word.probability < 0.3 for word in support):
                    issue("asr_confidence_low", line_index=i, text=line.norm, start=line.start)
                if len(support) == len(line.word_starts) and (
                    any(abs(word.start - start) > 1 for word, start in zip(support, line.word_starts, strict=True))
                    or line.end is not None and abs(support[-1].end - line.end) > 1
                ):
                    issue("asr_word_timing_disagreement", line_index=i, text=line.norm, start=line.start)
    selected_preservation = [
        item for item in _list(preservation_issues) if isinstance(item, dict)
        and isinstance(item.get("start"), (int, float))
        and any(_tokens(str(item.get("text") or "")) == _tokens(line.norm)
                and abs(item["start"] - line.start) <= 2 for line in lines)
    ]
    issues.extend(selected_preservation)
    counts["provisional_lines"] = sum(
        item.get("code") == "alignment_text_uncertain" for item in selected_preservation
    )
    timing_codes = {"word_timing_missing", "word_timing_mismatch", "word_timing_collapsed",
                    "word_timing_gap", "line_word_timing_drift", "line_timing_overlap",
                    "timed_lyrics_unavailable", "asr_evidence_unavailable", "asr_line_unconfirmed",
                    "asr_word_timing_disagreement", "asr_confidence_low", "word_timing_pace",
                    "alignment_word_timing_unverified"}
    return dict(schema_version=1, status="needs_review" if issues else "checked",
                text_confidence="uncertain" if any(i["code"] not in timing_codes or i["code"].startswith("asr_") for i in issues) else "checked",
                timing_confidence="uncertain" if any(i["code"] in timing_codes for i in issues) else "checked",
                counts=counts, issues=issues, preservation_issues=selected_preservation)



def _accepted_retry(line, retries: list, used: set[int]) -> bool:
    """Authenticate a recovered line against independent crop or full ASR evidence.

    Null alignment scores alone are not proof of recovery. Require an accepted
    diagnostic, plausible ASR word probabilities, and the same words/times in
    the full trusted ASR observation. Each accepted repair is consumed once.
    """
    for i, retry in enumerate(retries):
        if i in used or not isinstance(retry, dict):
            continue
        reason = retry.get("reason")
        if retry.get("outcome") != "accepted" or reason not in {
            "independent_crop_asr_match", "independent_full_asr_match"
        }:
            continue
        full_asr = reason == "independent_full_asr_match"
        if full_asr and retry.get("evidence_source") != "full_asr":
            continue
        raw_words = _list(retry.get("words"))
        candidate = _asr_words({"segments": [{"words": raw_words}]})
        if not candidate or len(candidate) != len(raw_words):
            continue
        if _tokens(line.norm) != _tokens(" ".join(word.text for word in candidate)):
            continue
        if len(candidate) != len(line.word_starts) or line.end is None:
            continue
        if any(word.probability is None or word.probability < 0.3 for word in candidate):
            continue
        if sum(word.probability for word in candidate) / len(candidate) < 0.65:
            continue
        if any(word.end - word.start < 0.04 or word.end - word.start > 8 for word in candidate):
            continue
        if any(b.start < a.end - 0.02 or b.start - a.end > 4
               for a, b in zip(candidate, candidate[1:], strict=False)):
            continue
        if any(abs(start - word.start) > 0.02
               for start, word in zip(line.word_starts, candidate, strict=True)):
            continue
        if abs(line.end - candidate[-1].end) > 0.02:
            continue
        safe_segments = []
        for segment in _list(retry.get("evidence_segments")):
            if not isinstance(segment, dict):
                continue
            if full_asr and segment.get("source") != "independent_full_asr":
                continue
            logprob, silence = segment.get("avg_logprob"), segment.get("no_speech_prob")
            if (isinstance(logprob, (int, float)) and math.isfinite(logprob) and logprob >= -1
                    and isinstance(silence, (int, float)) and math.isfinite(silence)
                    and 0 <= silence < 0.6):
                safe_segments.append(segment)
        support = _support(line, _asr_words({"retry_segments": safe_segments}))
        if len(support) != len(candidate):
            continue
        if any(_tokens(a.text) != _tokens(b.text)
               or abs(a.start - b.start) > 0.02 or abs(a.end - b.end) > 0.02
               or b.probability is None or b.probability < 0.3
               or abs(a.probability - b.probability) > 1e-9
               for a, b in zip(candidate, support, strict=True)):
            continue
        used.add(i)
        return True
    return False


def _provisional_candidate(original, line, score, diagnostics, used_rows, words, duration, *, require_support=True):
    """Retain a witnessed GPU-kept phrase as uncertain, never resurrect a veto.

    Match an ordered diagnostic occurrence to the exact input LRC, text, score,
    and raw word span. Existing pace and ASR locality gates still apply. A zero
    raw word width removes word highlighting, not the whole audible phrase.
    """
    if not isinstance(score, (int, float)) or not math.isfinite(score):
        return None
    if _timing_problems(line) or drop_unreliable_aligned_lines(original, None)[1]:
        return None
    support = _support(line, words)
    # This preserves an existing GPU-kept candidate with explicit uncertainty;
    # it does not promote it to an ASR-approved recovery. Low ASR confidence
    # must remain visible rather than silently deleting plausible aligned text.
    if require_support and (not support or line.end is None or
            min(line.end, support[-1].end) <= max(line.start, support[0].start)):
        return None
    last_index = max(used_rows, default=-1)
    for index, row in enumerate(_list((diagnostics or {}).get("lines"))):
        if index <= last_index or not isinstance(row, dict):
            continue
        if row.get("raw_lrc") != original or row.get("kept") is not True:
            continue
        if row.get("rejection_reasons") != []:
            continue
        flags = row.get("timing_issues")
        if not isinstance(flags, list) or any(
            flag not in {"relative_score_outlier", "invalid_word_timestamps"} for flag in flags
        ):
            continue
        if _tokens(str(row.get("text") or "")) != _tokens(line.norm):
            continue
        try:
            start, end, raw_score = float(row["start"]), float(row["end"]), float(row["score"])
        except (KeyError, ValueError, TypeError):
            continue
        if not all(math.isfinite(value) for value in (start, end, raw_score)):
            continue
        if (start < 0 or end <= start or abs(start - line.start) > .02
                or abs(end - line.end) > .02 or abs(score - raw_score) > 1e-9
                or duration is not None and end > duration):
            continue
        raw_words = _list(row.get("words"))
        if len(raw_words) != len(line.word_starts):
            continue
        if _tokens(" ".join(str(w.get("text") or "") for w in raw_words
                            if isinstance(w, dict))) != _tokens(line.norm):
            continue
        zero_width = False
        previous_end = start
        valid = True
        for offset, word in enumerate(raw_words):
            try:
                a, b = float(word["start"]), float(word["end"])
            except (KeyError, ValueError, TypeError):
                valid = False
                break
            if (not math.isfinite(a) or not math.isfinite(b) or a < start or b > end
                    or b < a or a < previous_end - .02
                    or abs(a - line.word_starts[offset]) > .02):
                valid = False
                break
            zero_width |= b == a
            previous_end = b
        if not valid:
            continue
        # An unspecified invalid-timestamp flag must not be hand-waved away.
        if "invalid_word_timestamps" in flags and not zero_width:
            continue
        used_rows.add(index)
        issues = [dict(code="alignment_text_uncertain", text=line.norm, start=start, end=end,
                       detail="Existing voiced alignment retained as a candidate; alignment or independent ASR evidence is uncertain. Listen to confirm the words.")]
        body = original
        if zero_width:
            body = _fmt_lrc_timestamp(start) + line.norm
            issues.append(dict(code="alignment_word_timing_unverified", text=line.norm,
                               start=start, end=end,
                               detail="A raw aligned word has zero duration; word highlighting was removed. Line timing remains approximate."))
        return body, issues
    return None

def reconcile_alignment(
    curated_text: str | None,
    raw_lrc: str | None,
    scores: list | None,
    asr: dict | None,
    *,
    accepted_retries: list | None = None,
    alignment_diagnostics: dict | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Filter alignment, recover ASR-supported lines and repair supported timing."""
    if not raw_lrc:
        return None, assess_lyrics(curated_text, None, asr)
    if not _parse_aligner_lines(raw_lrc):
        quality = assess_lyrics(curated_text, None, asr)
        quality["issues"].append({"code": "alignment_malformed",
                                  "detail": "No usable timed lyric lines in alignment."})
        return None, quality
    if len(raw_lrc.splitlines()) > _MAX_LINES or len(_tokens(LRC_WORD_TAG_RE.sub("", LRC_TIMESTAMP_RE.sub("", raw_lrc)))) > _MAX_TOKENS:
        return raw_lrc, assess_lyrics(curated_text, raw_lrc, asr)
    repaired = repair_aligned_lrc(raw_lrc)
    filtered, _ = drop_unreliable_aligned_lines(repaired, scores)
    retained = filtered.splitlines()
    words = _asr_words(asr)
    output, restored, timing = [], 0, 0
    used_retries: set[int] = set()
    used_rows: set[int] = set()
    preservation: list = []
    evidence = asr if isinstance(asr, dict) else {}
    duration = evidence.get("duration")
    duration = float(duration) if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0 else None
    for input_index, (original, raw) in enumerate(zip(raw_lrc.splitlines(), repaired.splitlines(), strict=True)):
        original_lines = _parse_aligner_lines(original)
        verified_retry = bool(original_lines) and _accepted_retry(
            original_lines[0], _list(accepted_retries), used_retries
        )
        parsed = _parse_aligner_lines(raw)
        keep = raw in retained
        if keep:
            retained.remove(raw)
        if not parsed:
            if keep:
                output.append(raw)
            continue
        if verified_retry:
            # Keep independent ASR text even when the legacy pace heuristic
            # objects. Preserve measured timestamps; assess_lyrics still
            # exposes timing uncertainty and never labels it checked.
            restored += int(not keep)
            output.append(original)
            continue
        line = parsed[0]
        current_score = scores[input_index] if isinstance(scores, list) and input_index < len(scores) else None
        has_zero_width = isinstance(alignment_diagnostics, dict) and any(
            isinstance(row, dict) and row.get("raw_lrc") == original
            and "invalid_word_timestamps" in _list(row.get("timing_issues"))
            for row in _list(alignment_diagnostics.get("lines"))
        )
        if not keep or has_zero_width:
            candidate = _provisional_candidate(
                original, original_lines[0], current_score,
                alignment_diagnostics if isinstance(alignment_diagnostics, dict) else {},
                used_rows, words, duration, require_support=not keep,
            ) if original_lines else None
            if candidate is not None:
                body, candidate_issues = candidate
                output.append(body)
                preservation.extend(candidate_issues)
                continue
        if not keep:
            if isinstance(alignment_diagnostics, dict) and any(
                isinstance(row, dict) and row.get("raw_lrc") == original
                for row in _list(alignment_diagnostics.get("lines"))
            ):
                # Known hard/invalid GPU evidence cannot be bypassed by the
                # legacy score-only recovery path. Accepted repairs were
                # independently authenticated above.
                continue
            support = _support(line, words)
            # Restoration requires exact normalized ASR words: a fuzzy match
            # could otherwise insert a negation or other unperformed text.
            # Retain the pace guard for absent cut verses.
            if (
                not support
                or _tokens(line.norm) != _tokens(" ".join(word.text for word in support))
                or _timing_problems(line)
                or drop_unreliable_aligned_lines(raw, None)[1]
            ):
                continue
            if sum(w.probability or 0 for w in support) / len(support) < 0.6:
                continue
            restored += 1
        raw, changed = _repair_timing(raw, line, words)
        timing += int(changed)
        output.append(raw)
    result = "\n".join(output)
    if raw_lrc.endswith("\n"):
        result += "\n"
    return result or None, assess_lyrics(curated_text, result, asr,
                                        restored_lines=restored, repaired_timing_lines=timing,
                                        preservation_issues=preservation)

