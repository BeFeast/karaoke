"""Offline, text-aligned lyrics evaluation against human-labelled references."""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from pathlib import Path

TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
STAMP = r"(\d{1,2}):([0-5]\d)(?:\.(\d{1,3}))?"
LINE = re.compile(r"\[" + STAMP + r"\]")
WORD = re.compile(r"<" + STAMP + r">")
LIMITS = {
    "max_wer": "wer",
    "max_missing_words": "deletions",
    "max_extra_words": "insertions",
    "max_word_start_mae_s": "word_start_mae_s",
    "max_word_start_p95_s": "word_start_p95_s",
    "min_word_timing_coverage": "word_timing_coverage",
}


def tokens(text: str) -> list[str]:
    return [
        w.replace("'", "").replace("’", "")
        for w in TOKEN.findall(unicodedata.normalize("NFKC", text).casefold())
    ]


def seconds(match: re.Match) -> float:
    return int(match[1]) * 60 + int(match[2]) + float("0." + (match[3] or "0"))


def candidate_words(lrc: str) -> list[tuple[str, float | None]]:
    result = []
    for number, raw in enumerate(lrc.splitlines(), 1):
        raw = raw.strip()
        if not raw or re.fullmatch(r"\[[A-Za-z]+:.*\]", raw):
            continue
        stamps, end = [], 0
        while (match := LINE.match(raw, end)) is not None:
            stamps.append(seconds(match))
            end = match.end()
        if not stamps:
            raise ValueError(f"Candidate line {number}: missing LRC timestamp")
        body = raw[end:]
        word_tags = list(WORD.finditer(body))
        if any(c in WORD.sub("", body) for c in "<>[]"):
            raise ValueError(f"Candidate line {number}: malformed tags")
        words = []
        if word_tags:
            if body[: word_tags[0].start()].strip():
                raise ValueError(f"Candidate line {number}: untagged enhanced text")
            for i, tag in enumerate(word_tags):
                stop = word_tags[i + 1].start() if i + 1 < len(word_tags) else len(body)
                segment = tokens(body[tag.end() : stop])
                if not segment and i + 1 < len(word_tags):
                    raise ValueError(f"Candidate line {number}: empty word segment")
                # A multi-token span has no independently measured word starts.
                words.extend(
                    (w, seconds(tag) if len(segment) == 1 and len(stamps) == 1 else None)
                    for w in segment
                )
        else:
            words = [(w, None) for w in tokens(body)]
        for stamp in stamps:
            result.append((stamp, words))
    return [word for _, words in sorted(result, key=lambda pair: pair[0]) for word in words]


def align(reference: list[str], candidate: list[str]):
    """Levenshtein alignment; deterministic ties, ambiguity reported explicitly."""
    n, m = len(reference), len(candidate)
    distance = [[0] * (m + 1) for _ in range(n + 1)]
    ways = [[1] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        distance[i][0] = i
    for j in range(m + 1):
        distance[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            costs = [
                distance[i - 1][j - 1] + (reference[i - 1] != candidate[j - 1]),
                distance[i - 1][j] + 1,
                distance[i][j - 1] + 1,
            ]
            distance[i][j] = best = min(costs)
            ways[i][j] = min(
                2,
                sum(
                    count
                    for cost, count in zip(
                        costs, [ways[i - 1][j - 1], ways[i - 1][j], ways[i][j - 1]], strict=True
                    )
                    if cost == best
                ),
            )
    i, j, pairs, substitutions, deletions, insertions = n, m, [], 0, 0, 0
    while i or j:
        if (
            i
            and j
            and distance[i][j] == distance[i - 1][j - 1] + (reference[i - 1] != candidate[j - 1])
        ):
            if reference[i - 1] == candidate[j - 1]:
                pairs.append((i - 1, j - 1))
            else:
                substitutions += 1
            i -= 1
            j -= 1
        elif i and distance[i][j] == distance[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1
    return pairs, substitutions, deletions, insertions, ways[n][m] > 1


def metrics(
    n: int,
    m: int,
    correct: int,
    sub: int,
    delete: int,
    insert: int,
    errors: list[float],
    timed: int,
):
    ordered = sorted(errors)
    return {
        "reference_words": n,
        "candidate_words": m,
        "correct_words": correct,
        "substitutions": sub,
        "deletions": delete,
        "insertions": insert,
        "wer": (sub + delete + insert) / n,
        "timed_reference_words": timed,
        "timing_scored_words": len(errors),
        "timing_unscored_reference_words": n - len(errors),
        "word_timing_coverage": len(errors) / n,
        "word_start_mae_s": sum(errors) / len(errors) if errors else None,
        "word_start_p50_s": ordered[math.ceil(len(ordered) * 0.5) - 1] if ordered else None,
        "word_start_p95_s": ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None,
    }


def evaluate_entry(entry: dict, base: Path):
    reference = entry["reference"]
    words = tokens(reference["text"])
    if not words:
        raise ValueError("Reference must contain at least one word")
    labels = reference.get("words")
    times = [None] * len(words)
    if labels is not None:
        if [tokens(w["text"]) for w in labels] != [[w] for w in words]:
            raise ValueError(
                "Reference word labels must match every normalized text token in order"
            )
        times = [w.get("start") for w in labels]
        if any(
            t is not None
            and (
                isinstance(t, bool)
                or not isinstance(t, (int, float))
                or not math.isfinite(t)
                or t < 0
            )
            for t in times
        ):
            raise ValueError("Reference times must be finite nonnegative seconds or null")
    candidate = candidate_words((base / entry["candidate_lrc"]).read_text(encoding="utf-8"))
    pairs, sub, delete, insert, ambiguous = align(words, [w for w, _ in candidate])
    # Repeated-word ties cannot identify the correct vocal occurrence. Do not
    # report deceptively precise timings from an arbitrary optimal text path.
    errors = (
        []
        if ambiguous
        else [
            abs(times[i] - candidate[j][1])
            for i, j in pairs
            if times[i] is not None and candidate[j][1] is not None
        ]
    )
    report = {
        "id": entry["id"],
        "language": entry.get("language"),
        "variant": entry.get("variant"),
        "alignment_ambiguous": ambiguous,
        **metrics(
            len(words),
            len(candidate),
            len(pairs),
            sub,
            delete,
            insert,
            errors,
            sum(t is not None for t in times),
        ),
    }
    return report, errors


def evaluate(manifest: dict, base: Path, thresholds: dict | None = None):
    if manifest.get("schema_version") != 1 or not manifest.get("entries"):
        raise ValueError("Expected schema_version 1 and a nonempty entries list")
    limits = thresholds or {}
    if set(limits) - LIMITS.keys():
        raise ValueError("Unknown threshold key")
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
        for v in limits.values()
    ):
        raise ValueError("Thresholds must be finite nonnegative numbers")
    reports, errors, seen = [], [], set()
    for entry in manifest["entries"]:
        if not isinstance(entry.get("id"), str) or not entry["id"] or entry["id"] in seen:
            raise ValueError("Entry IDs must be nonempty unique strings")
        seen.add(entry["id"])
        report, values = evaluate_entry(entry, base)
        reports.append(report)
        errors.extend(values)

    def total(key):
        return sum(r[key] for r in reports)

    aggregate = metrics(
        total("reference_words"),
        total("candidate_words"),
        total("correct_words"),
        total("substitutions"),
        total("deletions"),
        total("insertions"),
        errors,
        total("timed_reference_words"),
    )
    violations = []
    for report in reports:  # Every recording must meet its thresholds; means cannot hide failures.
        for limit, key in LIMITS.items():
            if limit not in limits:
                continue
            value = report[key]
            if value is None or (
                value < limits[limit] if limit.startswith("min_") else value > limits[limit]
            ):
                violations.append(
                    {"id": report["id"], "metric": key, "value": value, "threshold": limits[limit]}
                )
    return {
        "schema_version": 1,
        "entries": reports,
        "aggregate": aggregate,
        "violations": violations,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--thresholds", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        limits = (
            json.loads(args.thresholds.read_text(encoding="utf-8")) if args.thresholds else None
        )
        result = evaluate(manifest, args.manifest.parent, limits)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Evaluation error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if result["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
