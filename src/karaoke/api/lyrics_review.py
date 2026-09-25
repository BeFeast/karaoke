"""Owner-reviewed lyric revisions with validation and recoverable file writes."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from karaoke.db.models import Artifact, Job
from karaoke.worker.lyrics import lrc_to_plain

_FILES = ("lyrics.lrc", "lyrics.txt", "metadata.json", "lyrics.quality.json")
_LINE = re.compile(r"^\[(\d{1,2}):([0-5]\d)(?:\.(\d{1,3}))?\]")
_WORD = re.compile(r"<(\d{1,2}):([0-5]\d)(?:\.(\d{1,3}))?>")


def snapshot(exports: Path) -> dict[str, bytes | None]:
    result = {}
    for name in _FILES:
        try:
            result[name] = (exports / name).read_bytes()
        except FileNotFoundError:
            result[name] = None
    return result


def revision(files: dict[str, bytes | None]) -> str:
    digest = hashlib.sha256()
    for name in _FILES:
        data = files.get(name)
        digest.update(name.encode() + b"\0")
        digest.update(b"missing" if data is None else str(len(data)).encode() + b":" + data)
    return digest.hexdigest()


def _seconds(match: re.Match) -> float:
    return int(match[1]) * 60 + int(match[2]) + float("0." + (match[3] or "0"))


def validate_lrc(body: str, duration: float | None) -> str:
    """Reject malformed corrections instead of silently dropping their text."""
    if len(body.encode("utf-8")) > 256 * 1024:
        raise HTTPException(413, "Lyrics are too large")
    if not body.strip():
        raise HTTPException(422, "Enter timed lyrics before confirming review")
    limit = duration + 2 if duration and math.isfinite(duration) else 24 * 3600
    previous = -1.0
    kept = []
    for number, raw in enumerate(body.splitlines(), 1):
        if not raw.strip():
            continue
        tag = _LINE.match(raw)
        if not tag:
            raise HTTPException(422, f"Line {number}: expected [mm:ss.xx] before the text")
        start = _seconds(tag)
        rest = raw[tag.end() :]
        text = _WORD.sub("", rest).strip()
        if not text or any(char in text for char in "<>[]"):
            raise HTTPException(422, f"Line {number}: invalid or empty lyric text")
        word_tags = list(_WORD.finditer(rest))
        times = [_seconds(match) for match in word_tags]
        # The player requires tag-led words, with no empty segment except
        # an optional final sung-end tag. Do not silently downgrade malformed
        # enhanced LRC to the approximate line wipe after marking it reviewed.
        if word_tags and (
            rest[: word_tags[0].start()].strip()
            or any(
                not rest[left.end() : right.start()].strip()
                for left, right in zip(word_tags, word_tags[1:], strict=False)
            )
        ):
            raise HTTPException(422, f"Line {number}: each word tag must precede lyric text")
        # Equal line starts make all but the final line unreachable in the
        # player's last-start-at-or-before lookup.
        if start <= previous or start > limit or any(t > limit for t in times):
            raise HTTPException(
                422, f"Line {number}: timestamp is out of order or outside the audio"
            )
        if times and (
            # Curated line tags can trail their first aligned word. The player
            # explicitly supports up to two seconds of this normal alignment.
            times[0] < start - 2 or any(a >= b for a, b in zip(times, times[1:], strict=False))
        ):
            raise HTTPException(422, f"Line {number}: word timestamps are out of order")
        previous = start
        kept.append(raw.rstrip())
    return "\n".join(kept) + "\n"


def _replace(path: Path, body: bytes) -> None:
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_bytes(body)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


async def save_review(
    session: AsyncSession,
    job: Job,
    exports: Path,
    body: str,
    expected_revision: str,
    reviewer: str,
) -> None:
    """Caller holds the job row lock; compare-and-swap before mutating files."""
    original = snapshot(exports)
    if revision(original) != expected_revision:
        raise HTTPException(409, "Lyrics changed. Reload them before saving your review.")
    lrc = validate_lrc(body, job.duration)
    try:
        metadata = json.loads(original["metadata.json"] or b"{}")
        if not isinstance(metadata, dict):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(409, "Lyrics metadata is invalid; repair it before review") from exc
    stamp = dt.datetime.now(dt.UTC).isoformat()
    quality = {
        "schema_version": 1,
        "status": "reviewed",
        "issues": [],
        "reviewed_at": stamp,
        "review_basis": "human_listening_confirmation",
    }
    prior = metadata.get("lyrics_quality")
    if isinstance(prior, dict):
        quality["automated_before_review"] = prior.get("automated_before_review", prior)
    metadata.update(
        {
            "lyrics_source": "human_reviewed",
            "synced": True,
            "instrumental": False,
            "lyrics_quality": quality,
        }
    )
    new = {
        "lyrics.lrc": lrc.encode(),
        "lyrics.txt": lrc_to_plain(lrc).encode(),
        "metadata.json": json.dumps(metadata, ensure_ascii=False, indent=2).encode(),
        "lyrics.quality.json": json.dumps(quality, ensure_ascii=False, indent=2).encode(),
    }
    exports.mkdir(parents=True, exist_ok=True)
    backup = exports.parent / "work" / "lyrics-reviews" / uuid.uuid4().hex
    backup.mkdir(parents=True)
    for name, data in original.items():
        if data is not None:
            (backup / name).write_bytes(data)
    (backup / "review.json").write_text(
        json.dumps(
            {
                "previous_revision": expected_revision,
                "reviewer": reviewer,
                "reviewed_at": stamp,
                "original_present": [n for n, b in original.items() if b is not None],
            }
        )
    )
    artifacts = list(
        (
            await session.scalars(
                select(Artifact).where(
                    Artifact.job_id == job.id, Artifact.kind.in_(["lyrics", "lyrics_lrc"])
                )
            )
        ).all()
    )
    (backup / "artifact-rows.json").write_text(
        json.dumps([{"id": a.id, "kind": a.kind, "size_bytes": a.size_bytes} for a in artifacts])
    )
    try:
        for name, data in new.items():
            _replace(exports / name, data)
        for kind, name in (("lyrics", "lyrics.txt"), ("lyrics_lrc", "lyrics.lrc")):
            existing = [a for a in artifacts if a.kind == kind]
            if existing:
                for artifact in existing:
                    artifact.size_bytes = len(new[name])
            else:
                session.add(
                    Artifact(
                        job_id=job.id,
                        kind=kind,
                        relative_path=f"{job.job_token}/exports/{name}",
                        size_bytes=len(new[name]),
                        content_type="text/plain",
                    )
                )
        job.stage_note = None
        await session.commit()
    except BaseException:
        await session.rollback()
        for name, data in original.items():
            if data is None:
                (exports / name).unlink(missing_ok=True)
            else:
                _replace(exports / name, data)
        raise
