"""Lyrics-only rebuild of a finished job (#284).

"Rebuild lyrics" re-runs just the lyrics stage with the current pipeline —
canonical metadata, LRCLIB, Genius, web references, the current GPU image's
transcription and alignment — on the job's existing vocal stem
(``work/vocals.wav``): no download, no separation. A pasted reference
(owner-only) takes the same path with the pasted text as the alignment text
and :func:`karaoke.worker.manual_reference.merge_reference_with_asr` as the
policy (decision 4).

The job stays ``completed`` throughout (a restart must not fail a finished
job); progress is ``stage_note`` and the result swaps into ``exports/`` in
one step, with the previous lyrics files backed up under
``work/lyrics-rebuilds/<stamp>/``. The GPU pass counts against the rolling
daily cap via ``Job.lyrics_rebuilt_at`` / ``lyrics_rebuild_cost_micros``.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import shutil
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from karaoke.db.models import Artifact, Job, JobStatus
from karaoke.worker.asr_reference import strip_speech_lines
from karaoke.worker.lyrics import LyricsResult, lrc_to_plain
from karaoke.worker.manual_reference import merge_reference_with_asr

log = logging.getLogger("karaoke.worker.lyrics_rebuild")

_LOCK_NAME = ".lyrics-rebuild.lock"
_LOCK_STALE_S = 45 * 60
_CAPACITY_TRIES = 3
_CAPACITY_WAIT_S = 60.0
REASON_REBUILD = "rebuild"
REASON_REFERENCE = "reference_paste"
SOURCE_MANUAL_REFERENCE = "manual_reference"

# metadata.json keys owned by the lyrics stage (replaced by a rebuild).
_LYRICS_KEYS = ("synced", "instrumental", "whisper_language", "whisper_language_probability", "whisper_model")


class RebuildBusy(RuntimeError):
    pass


class RebuildUnavailable(RuntimeError):
    pass


def _paths(settings, job: Job) -> tuple[Path, Path, Path]:
    root = Path(settings.artifact_root) / job.job_token
    return root, root / "work", root / "exports"


def acquire_lock(work: Path) -> Path:
    """Exclusive per-job rebuild lock; a lock older than 45 min is stale."""
    lock = work / _LOCK_NAME
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > _LOCK_STALE_S:
            lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as exc:
        raise RebuildBusy("a lyrics rebuild is already running for this job") from exc
    with os.fdopen(fd, "w") as fh:
        fh.write(dt.datetime.now(dt.UTC).isoformat())
    return lock


def rebuild_running(settings, job: Job) -> bool:
    lock = _paths(settings, job)[1] / _LOCK_NAME
    return lock.exists() and time.time() - lock.stat().st_mtime <= _LOCK_STALE_S


def check_rebuildable(settings, job: Job) -> None:
    if job.status != JobStatus.completed:
        raise RebuildUnavailable("Wait for audio processing to finish before rebuilding lyrics")
    _, work, exports = _paths(settings, job)
    if not (work / "vocals.wav").is_file():
        raise RebuildUnavailable("This job's vocal stem is no longer stored; resubmit the song instead")
    if not (exports / "metadata.json").is_file():
        raise RebuildUnavailable("This job has no metadata to rebuild from")


async def _set_note(session_factory: async_sessionmaker, job_id: int, note: str | None) -> None:
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is not None:
            job.stage_note = note
            await session.commit()


def _gpu_lyrics_pass(settings, prior_micros: int, vocals: Path, out_dir: Path, **kwargs):
    """One separation-free GPU pass, retrying RunPod capacity stalls."""
    from karaoke.worker.runpod_client import (
        RunpodCapacityError,
        RunpodClient,
        RunpodColdStartError,
    )

    last: Exception | None = None
    for attempt in range(_CAPACITY_TRIES):
        try:
            client = RunpodClient(settings, prior_24h_cost_micros=prior_micros)
            return client.run(vocals, out_dir, mode="lyrics", **kwargs)
        except (RunpodCapacityError, RunpodColdStartError) as exc:
            last = exc
            log.warning("lyrics rebuild GPU capacity (attempt %d): %s", attempt + 1, exc)
            time.sleep(_CAPACITY_WAIT_S)
    raise last  # type: ignore[misc]


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


async def rebuild_lyrics(
    session_factory: async_sessionmaker,
    job_id: int,
    settings,
    *,
    reference_text: str | None = None,
    requested_by: str | None = None,
) -> dict[str, object]:
    """Rebuild one job's lyrics; returns a summary (never raises for a
    pipeline failure — it is recorded in ``stage_note`` and the summary)."""
    from karaoke.worker import pipeline as p

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None:
            raise RebuildUnavailable("job not found")
        check_rebuildable(settings, job)
        root, work, exports = _paths(settings, job)
        title = job.title
    reason = REASON_REFERENCE if reference_text else REASON_REBUILD
    summary: dict[str, object] = {"job_id": job_id, "reason": reason}
    try:
        lock = acquire_lock(work)
    except RebuildBusy as exc:
        summary["error"] = str(exc)
        return summary
    try:
        await _set_note(session_factory, job_id, "Rebuilding lyrics…")
        metadata = json.loads((exports / "metadata.json").read_text(encoding="utf-8"))
        summary["source_before"] = metadata.get("lyrics_source")
        source_meta = {k: metadata.get(k) for k in ("artist", "track", "album", "duration")}
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = work / f"rebuild-{stamp}"
        out = run_dir / "exports"
        out.mkdir(parents=True, exist_ok=True)
        vocals = work / "vocals.wav"

        lang_hint = p._script_lang_hint(source_meta.get("track")) or p._script_lang_hint(
            title, source_meta.get("artist"), source_meta.get("track")
        )
        canonical = None
        lyrics = LyricsResult(source="none")
        second_text = None
        web_reference = None
        if reference_text:
            lang_hint = p._script_lang_hint(reference_text) or lang_hint
            align_text = reference_text
        else:
            canonical = await asyncio.to_thread(
                p._CANONICAL_RESOLVER.resolve, source_meta.get("artist"), source_meta.get("track"),
                source_meta.get("duration"),
            )
            lyrics = await asyncio.to_thread(
                p._LYRICS_SOURCE.fetch,
                artist=source_meta.get("artist"), track=source_meta.get("track"),
                album=source_meta.get("album"), duration=source_meta.get("duration"),
                canonical_artist=canonical.artist if canonical else None,
                canonical_track=canonical.title if canonical else None,
                canonical_duration=canonical.duration if canonical else None,
            )
            record_text = lyrics.plain or (lrc_to_plain(lyrics.synced_lrc) if lyrics.synced_lrc else None) or lyrics.rejected_text
            if lang_hint and record_text and p._script_lang_hint(record_text) != lang_hint:
                lyrics = LyricsResult(source="none", rejected="script_mismatch")
                record_text = None
            align_text = None if lyrics.instrumental else record_text
            if align_text and settings.word_vote_enabled:
                second = await asyncio.to_thread(
                    p._GENIUS_SOURCE.fetch,
                    (canonical.artist if canonical else None) or source_meta.get("artist"),
                    source_meta.get("track"), lang=lang_hint,
                )
                second_text = second.text if second else None
            if not align_text and not lyrics.instrumental and settings.web_reference_enabled:
                web_reference = await asyncio.to_thread(
                    p._web_references(settings).find,
                    artist=source_meta.get("artist"), track=source_meta.get("track"),
                    video_id=canonical.video_id if canonical else None,
                    latin_artist=canonical.artist if canonical else None, lang=lang_hint,
                )

        prior = await p._prior_24h_cost_micros(session_factory)
        gpu = await asyncio.to_thread(
            _gpu_lyrics_pass, settings, prior, vocals, run_dir,
            align_text=align_text, align_lang=p._align_lang(source_meta, hint=lang_hint) if align_text else None,
            whisper_lang=lang_hint,
        )
        cost = gpu.vast_cost
        aligned_path, asr_path = gpu.aligned_lrc_path, gpu.lyrics_json_path
        post_asr = None

        if reference_text:
            asr_doc = p._read_json_object(asr_path) or {}
            from karaoke.worker.lyrics import whisper_segments_to_lrc

            asr_lrc = whisper_segments_to_lrc(asr_doc.get("segments") or []) or ""
            raw = run_dir / "aligned.raw.lrc"
            aligned_body = raw.read_text(encoding="utf-8") if raw.is_file() else (
                aligned_path.read_text(encoding="utf-8") if aligned_path else ""
            )
            if not aligned_body.strip():
                raise RuntimeError("the pasted lyrics could not be aligned to the vocals")
            merged = merge_reference_with_asr(aligned_body, asr_lrc, asr_doc.get("segments") or [], lang_hint)
            (out / "lyrics.lrc").write_text(merged, encoding="utf-8")
            (out / "lyrics.txt").write_text(lrc_to_plain(merged), encoding="utf-8")
            from karaoke.worker.lyrics_quality import assess_lyrics

            quality = assess_lyrics(reference_text, strip_speech_lines(merged), asr_doc)
            (out / "lyrics.quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
            prov: dict[str, object] = {
                "lyrics_source": SOURCE_MANUAL_REFERENCE, "synced": True, "instrumental": False,
                "lrc_written": True, "lyrics_quality": quality, "lyrics_policy": "reference_wins_in_covered_spans",
            }
        else:
            if (
                not (lyrics.synced_lrc or lyrics.plain or lyrics.rejected_text or lyrics.instrumental)
                and settings.post_asr_reference_enabled
            ):
                asr_doc = p._read_json_object(asr_path) or {}
                queries, hit, hit_text, agreement = await p._post_asr_lookup(
                    asr_doc, source_meta, canonical, lang_hint, settings
                )
                if hit is not None and agreement is not None and agreement >= p._POST_ASR_MIN_AGREEMENT:
                    realign = await asyncio.to_thread(
                        _gpu_lyrics_pass, settings, prior + round(cost * 1_000_000), vocals, run_dir / "realign",
                        align_text=hit_text, align_lang=p._align_lang(source_meta, hint=lang_hint), whisper_lang=lang_hint,
                    )
                    cost += realign.vast_cost
                    lyrics = hit
                    aligned_path, asr_path = realign.aligned_lrc_path, realign.lyrics_json_path
                    post_asr = f"lrclib ({agreement:.2f})"
                elif web_reference is None and settings.web_reference_enabled:
                    for line in queries:
                        web_reference = await asyncio.to_thread(p._web_references(settings).find_by_text, line, lang=lang_hint)
                        if web_reference is not None:
                            post_asr = f"web:{web_reference.provider}"
                            break
            prov = p._resolve_lyrics(
                lyrics, out, gpu.lyrics_txt_path, aligned_path, asr_path,
                second_text=second_text, lang=lang_hint, web_reference=web_reference,
            )
            if post_asr:
                prov["lyrics_post_asr_reference"] = post_asr

        summary.update(source_after=prov["lyrics_source"], cost=round(cost, 6))
        await _swap_in(session_factory, job_id, exports, work, out, metadata, prov, asr_path, lyrics,
                       canonical, cost, reason, stamp, requested_by)
        log.info("job %s: lyrics rebuilt (%s): %s -> %s, $%.4f", job_id, reason,
                 summary["source_before"], summary["source_after"], cost)
        return summary
    except Exception as exc:  # noqa: BLE001 — recorded on the job, never crashes the loop
        log.exception("job %s: lyrics rebuild failed", job_id)
        await _set_note(session_factory, job_id, f"Lyrics rebuild failed: {type(exc).__name__}: {exc}"[:500])
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary
    finally:
        lock.unlink(missing_ok=True)


async def _swap_in(session_factory, job_id, exports, work, out, metadata, prov, asr_path, lyrics,
                   canonical, cost, reason, stamp, requested_by) -> None:
    backup = work / "lyrics-rebuilds" / stamp
    backup.mkdir(parents=True, exist_ok=True)
    for name in ("lyrics.lrc", "lyrics.txt", "metadata.json", "lyrics.quality.json"):
        if (exports / name).is_file():
            shutil.copy2(exports / name, backup / name)

    new_meta = {k: v for k, v in metadata.items() if not k.startswith("lyrics_") and k not in _LYRICS_KEYS}
    for key, value in prov.items():
        if key.startswith("lyrics_") or key in ("synced", "instrumental"):
            new_meta[key] = value
    asr = json.loads(Path(asr_path).read_text(encoding="utf-8")) if asr_path and Path(asr_path).is_file() else {}
    for src, dst in (("language", "whisper_language"), ("language_probability", "whisper_language_probability"),
                     ("model", "whisper_model")):
        if asr.get(src) is not None:
            new_meta[dst] = asr[src]
    if lyrics.match_variant:
        new_meta["lyrics_match_variant"] = lyrics.match_variant
    if canonical is not None:
        new_meta.update(canonical_artist=canonical.artist, canonical_track=canonical.title,
                        canonical_duration=canonical.duration)
    now = dt.datetime.now(dt.UTC)
    new_meta["lyrics_rebuild"] = {"reason": reason, "at": now.isoformat(), "cost": round(cost, 6),
                                  "backup": f"work/lyrics-rebuilds/{stamp}", "by": requested_by}

    async with session_factory() as session:
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None:
            raise RuntimeError("job disappeared during the rebuild")
        lrc_written = bool(prov.get("lrc_written")) and (out / "lyrics.lrc").is_file()
        if lrc_written:
            _atomic_write(exports / "lyrics.lrc", (out / "lyrics.lrc").read_bytes())
        else:
            (exports / "lyrics.lrc").unlink(missing_ok=True)
        if (out / "lyrics.txt").is_file():
            _atomic_write(exports / "lyrics.txt", (out / "lyrics.txt").read_bytes())
        if (out / "lyrics.quality.json").is_file():
            _atomic_write(exports / "lyrics.quality.json", (out / "lyrics.quality.json").read_bytes())
        _atomic_write(exports / "metadata.json", json.dumps(new_meta, ensure_ascii=False, indent=2).encode("utf-8"))

        artifacts = list((await session.scalars(
            select(Artifact).where(Artifact.job_id == job_id, Artifact.kind.in_(["lyrics", "lyrics_lrc"]))
        )).all())
        for kind, name in (("lyrics", "lyrics.txt"), ("lyrics_lrc", "lyrics.lrc")):
            path = exports / name
            rows = [a for a in artifacts if a.kind == kind]
            if path.is_file():
                if rows:
                    for row in rows:
                        row.size_bytes = path.stat().st_size
                else:
                    session.add(Artifact(job_id=job_id, kind=kind, relative_path=f"{job.job_token}/exports/{name}",
                                         size_bytes=path.stat().st_size, content_type="text/plain"))
            else:
                for row in rows:
                    await session.delete(row)
        job.lyrics_rebuilt_at = now
        job.lyrics_rebuild_cost_micros = (job.lyrics_rebuild_cost_micros or 0) + round(cost * 1_000_000)
        job.stage_note = None
        await session.commit()


def schedule_rebuild(session_factory, job_id: int, settings, **kwargs) -> asyncio.Task:
    return asyncio.create_task(rebuild_lyrics(session_factory, job_id, settings, **kwargs))
