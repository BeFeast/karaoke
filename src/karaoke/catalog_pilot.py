"""Run the bounded catalog pilot on the residential coordinator."""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import re
import secrets
import shutil
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from karaoke.api.lyrics_review import revision, snapshot
from karaoke.catalog import (
    PILOT_DOWNLOAD_GATE_NOTE,
    PILOT_OWNER,
    PIPELINE_VERSION,
    YOUTUBE_ID,
    lock_download_admission,
    pilot_job_root,
    youtube_url,
)
from karaoke.config import Settings, get_settings
from karaoke.db.models import (
    CatalogAsset,
    CatalogPilotState,
    CatalogRecording,
    CatalogSource,
    CatalogTrack,
    Job,
    JobStatus,
)
from karaoke.db.session import create_engine_and_sessionmaker
from karaoke.worker.pipeline import run_real_job
from karaoke.worker.runpod_client import RUNPOD_REST, TERMINAL_STATES
from karaoke.worker.scheduler import _has_runpod, _use_runpod

MAX_SONGS = 30
TOTAL_MICROS = 1_000_000
DAILY_MICROS = 350_000
COOLDOWN = dt.timedelta(minutes=5, seconds=1)
EXECUTION_SECONDS = 480
# Flex startup and billing rounding are included in each reservation.
BILLING_SECONDS = EXECUTION_SECONDS + 120
RUNNER_LOCK = 0x534F4E4552454C49
DEFAULT_MANIFEST = Path("catalog/pilot-2026-11-24.json")


class Appearance(BaseModel):
    playlist: str
    rank: int = Field(gt=0)


class SeedSong(BaseModel):
    video_id: str = Field(pattern=f"^{YOUTUBE_ID.pattern}$")
    title: str = Field(min_length=1)
    artist: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    version: Literal["studio", "live", "cover"]
    language: str = Field(pattern=r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$")
    genres: list[str]
    genres_uncertain: bool
    collections: list[str]
    decade: int = Field(ge=1900, le=2100, multiple_of=10)
    appearances: list[Appearance] = Field(min_length=1)
    available: bool = True
    metadata_allowed: bool = True
    playback_basis: str | None = None


class SeedManifest(BaseModel):
    date: dt.date
    playlists: dict[str, str]
    songs: list[SeedSong] = Field(min_length=1)


def read_seeds(path: Path) -> list[SeedSong]:
    manifest = SeedManifest.model_validate_json(path.read_text(encoding="utf-8"))
    return unique_seeds(manifest)


def unique_seeds(manifest: SeedManifest) -> list[SeedSong]:
    songs: dict[str, SeedSong] = {}
    for song in manifest.songs:
        if not {a.playlist for a in song.appearances} <= manifest.playlists.keys():
            raise ValueError(f"{song.video_id}: unknown provenance playlist")
        previous = songs.get(song.video_id)
        appearances = [*previous.appearances, *song.appearances] if previous else song.appearances
        songs[song.video_id] = song.model_copy(update={
            "appearances": list({entry.playlist: entry for entry in appearances}.values()),
        })
    if len(songs) > MAX_SONGS:
        raise ValueError(f"pilot permits at most {MAX_SONGS} unique YouTube recordings")
    return list(songs.values())


async def import_seeds(session: AsyncSession, manifest: SeedManifest) -> list[CatalogSource]:
    seen = []
    for seed in unique_seeds(manifest):
        source = await session.scalar(
            select(CatalogSource)
            .where(CatalogSource.provider == "youtube", CatalogSource.external_id == seed.video_id)
            .options(joinedload(CatalogSource.recording).joinedload(CatalogRecording.track))
        )
        if source is None:
            source = CatalogSource(
                provider="youtube", external_id=seed.video_id,
                recording=CatalogRecording(track=CatalogTrack()),
            )
            session.add(source)
        source.recording.track.title = seed.title
        source.recording.track.artist = seed.artist
        source.recording.track.aliases = seed.aliases
        source.recording.version = seed.version
        source.recording.language = seed.language
        source.recording.genres = sorted(set(seed.genres))
        source.recording.genres_uncertain = seed.genres_uncertain
        source.recording.collections = sorted(set(seed.collections))
        source.recording.decade = seed.decade
        source.available = seed.available
        source.metadata_allowed = seed.metadata_allowed
        source.playback_allowed = bool((seed.playback_basis or "").strip())
        source.provenance = [
            {
                **entry.model_dump(), "date": manifest.date.isoformat(),
                "title": manifest.playlists[entry.playlist],
                "playback_basis": seed.playback_basis,
            }
            for entry in seed.appearances
        ]
        seen.append(source)
    await session.flush()
    return seen


def _utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def admission(
    *, now: dt.datetime, attempts: list[CatalogAsset], foreground: bool,
    paused: bool, reserve: int,
) -> str:
    if paused:
        return "paused"
    if any(a.state == "processing" for a in attempts):
        return "waiting_for_active"
    if foreground:
        return "waiting_for_foreground"
    if any(now - _utc(a.finished_at or a.download_started_at) < COOLDOWN for a in attempts):
        return "waiting_for_cooldown"
    if len({a.source_id for a in attempts}) >= MAX_SONGS:
        return "song_limit"
    total = day = 0
    for attempt in attempts:
        charge = attempt.charged_micros
        if charge is None:
            charge = attempt.reserved_micros
        total += charge
        # A job crossing UTC midnight counts against both touched days.
        if _utc(attempt.download_started_at).date() <= now.date() <= _utc(
            attempt.finished_at or now
        ).date():
            day += charge
    if reserve <= 0 or total + reserve > TOTAL_MICROS or day + reserve > DAILY_MICROS:
        return "waiting_for_budget"
    return "eligible"


def _checked_exports(settings: Settings, job: Job) -> str | None:
    exports = pilot_job_root(settings.artifact_root, job.job_token) / "exports"
    for name in ("vocals.mp3", "karaoke.mp3"):
        file = exports / name
        if not file.is_file() or not file.stat().st_size:
            return None
    files = snapshot(exports)
    lrc = files["lyrics.lrc"]
    if not lrc:
        return None
    lines = re.findall(rb"(?m)^\[\d+:\d+(?:[.:]\d+)?\][^\r\n]+", lrc)
    return revision(files) if len(lines) >= 2 else None


async def settle(session: AsyncSession, asset: CatalogAsset, state: CatalogPilotState, settings: Settings) -> None:
    job = asset.job
    asset.finished_at = dt.datetime.now(dt.UTC)
    if job.status == JobStatus.completed:
        asset.charged_micros = (
            job.vast_cost_micros + math.ceil(asset.reserved_micros * 120 / BILLING_SECONDS)
            if job.vast_cost_micros is not None else asset.reserved_micros
        )
        asset.lyric_revision = _checked_exports(settings, job)
        asset.state = "review" if asset.lyric_revision else "failed"
        if asset.charged_micros > asset.reserved_micros:
            state.paused = True
    else:
        # A failed submit can be ambiguous. Count the full reservation for
        # any GPU-phase failure, including unknown or lost provider responses.
        entered_gpu = job.progress >= 75
        asset.charged_micros = asset.reserved_micros if entered_gpu else 0
        asset.state = "failed"
        if not entered_gpu and job.stage_note == PILOT_DOWNLOAD_GATE_NOTE:
            state.paused = True
    await session.commit()


async def _recover(session, state, settings):
    active = await session.scalars(
        select(CatalogAsset).where(CatalogAsset.state == "processing").options(joinedload(CatalogAsset.job))
    )
    for asset in active:
        job = asset.job
        if job.status == JobStatus.completed or (
            job.progress < 75 and job.status in {JobStatus.failed, JobStatus.cancelled}
        ):
            await settle(session, asset, state, settings)
            continue
        # A received ID must be checked after a restart. With no ID, allow
        # the provider TTL, the download timeout and an ambiguity margin to
        # expire before clearing the attempt. In either case it is never rerun.
        terminal = False
        if job.vast_instance_id:
            remote = job.vast_instance_id.removeprefix("runpod-")
            url = f"{RUNPOD_REST}/{settings.runpod_endpoint_id}/status/{remote}"
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(url, headers={"Authorization": f"Bearer {settings.runpod_api_key}"})
                if resp.is_success:
                    terminal = resp.json().get("status") in TERMINAL_STATES | {"COMPLETED"}
                elif resp.status_code == 404:
                    terminal = dt.datetime.now(dt.UTC) - _utc(job.updated_at) > dt.timedelta(minutes=45)
        else:
            terminal = dt.datetime.now(dt.UTC) - _utc(job.updated_at) > dt.timedelta(minutes=45)
        if not terminal:
            return False
        job.status = JobStatus.failed
        if not job.error:
            job.error = "Catalog pilot interrupted before exports were finalized"
        await settle(session, asset, state, settings)
    return True


async def _record_runpod_id(factory, job_id: int, remote: str) -> None:
    async with factory() as session:
        job = await session.get(Job, job_id)
        job.vast_instance_id = f"runpod-{remote}"
        await session.commit()


async def execute(args: argparse.Namespace, manifest: SeedManifest, settings: Settings) -> None:
    engine, factory = create_engine_and_sessionmaker(settings.database_url)
    try:
        async with engine.connect() as lock:
            if lock.dialect.name != "postgresql":
                raise ValueError("catalog pilot writes require coordinator Postgres")
            locked = await lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": RUNNER_LOCK})
            if not locked:
                raise ValueError("another pilot runner holds the coordinator lock")
            await lock.commit()
            try:
                await _execute_locked(args, manifest, settings, factory)
            finally:
                await lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": RUNNER_LOCK})
    finally:
        await engine.dispose()


async def _execute_locked(args, manifest, settings, factory) -> None:
    loop = asyncio.get_running_loop()
    async with factory() as session:
        state = await session.get(CatalogPilotState, 1)
        if state is None:
            state = CatalogPilotState(id=1, paused=False)
            session.add(state)
        sources = await import_seeds(session, manifest)
        await session.commit()
        if args.import_only:
            print(f"imported {len(sources)} catalog records")
            return
        if args.publish:
            asset = await session.scalar(
                select(CatalogAsset)
                .join(CatalogAsset.source)
                .where(
                    CatalogSource.external_id == args.publish,
                    CatalogSource.available.is_(True),
                    CatalogSource.metadata_allowed.is_(True),
                    CatalogSource.playback_allowed.is_(True),
                    CatalogAsset.state.in_(("review", "ready")),
                )
                .options(joinedload(CatalogAsset.job))
                .order_by(CatalogAsset.id.desc())
            )
            if asset is None or not asset.lyric_revision or _checked_exports(settings, asset.job) != asset.lyric_revision:
                raise ValueError("publication requires reviewed stems and an unchanged synced lyric revision")
            asset.state = "ready"
            await session.commit()
            await asyncio.to_thread(
                shutil.rmtree, pilot_job_root(settings.artifact_root, asset.job.job_token) / "work",
                ignore_errors=True,
            )
            print(
                f"published {args.publish}, GPU estimate ${(asset.job.vast_cost_micros or 0) / 1_000_000:.6f}, "
                f"budget charge ${asset.charged_micros / 1_000_000:.6f}"
            )
            return
        if not _use_runpod(settings) or not _has_runpod(settings):
            raise ValueError("catalog processing requires the configured coordinator RunPod endpoint")
        rate = args.usd_per_hour_ceiling
        if not math.isfinite(rate) or rate <= 0 or rate < settings.runpod_hourly_rate_estimate:
            raise ValueError("--usd-per-hour-ceiling must cover the endpoint's billed hourly rate")
        reserve = math.ceil(rate * BILLING_SECONDS / 3600 * 1_000_000)
        if reserve > DAILY_MICROS:
            raise ValueError("one reservation would exceed the daily pilot budget")
        if not await _recover(session, state, settings):
            print("waiting_for_active")
            return
        if args.resume:
            state.paused = False
            await session.commit()

        if settings.runpod_max_job_cost > 0:
            max_seconds = math.floor(settings.runpod_max_job_cost * 3600 / rate) - 120
        else:
            max_seconds = EXECUTION_SECONDS
        execution_seconds = min(EXECUTION_SECONDS, max_seconds)
        if execution_seconds < 30:
            raise ValueError("the configured GPU job cap is too small for the pilot")
        pilot_settings = settings.model_copy(update={
            "runpod_max_job_cost": min(settings.runpod_max_job_cost, reserve / 1_000_000)
            if settings.runpod_max_job_cost > 0 else reserve / 1_000_000,
            "runpod_wall_ceiling_s": min(settings.runpod_wall_ceiling_s, execution_seconds),
            "runpod_hourly_rate_estimate": rate,
        })
        dispatched = 0
        for source in sources:
            if dispatched >= args.limit:
                break
            if not (source.available and source.metadata_allowed and source.playback_allowed):
                continue
            while True:
                attempts = list(await session.scalars(select(CatalogAsset)))
                if any(a.source_id == source.id for a in attempts):
                    break
                await lock_download_admission(session)
                foreground = await session.scalar(
                    select(Job.id).where(
                        Job.owner_subject != PILOT_OWNER,
                        Job.status.in_((JobStatus.queued, JobStatus.downloading)),
                    ).limit(1)
                )
                now = dt.datetime.now(dt.UTC)
                reason = admission(
                    now=now, attempts=attempts, foreground=foreground is not None,
                    paused=state.paused, reserve=reserve,
                )
                if reason != "eligible":
                    await session.commit()
                    print(reason, flush=True)
                    if args.wait and reason in {"waiting_for_foreground", "waiting_for_cooldown"}:
                        # Wait with the advisory lock held and poll foreground before every start.
                        await asyncio.sleep(15)
                        continue
                    return
                track = source.recording.track
                job = Job(
                    job_token=secrets.token_urlsafe(24), owner_subject=PILOT_OWNER,
                    source_url=youtube_url(source.external_id),
                    artist=track.artist, title=track.title, track=track.title,
                    # Previous releases only re-dispatch queued jobs at boot.
                    # A process crash or rollback must never dispatch a pilot
                    # job as an ordinary private worker with public exports.
                    status=JobStatus.downloading,
                )
                session.add(job)
                await session.flush()
                asset = CatalogAsset(
                    job=job, source=source, pipeline_version=PIPELINE_VERSION,
                    state="processing", download_started_at=now, reserved_micros=reserve,
                )
                session.add(asset)
                await session.commit()
                dispatched += 1

                def store_remote(remote: str, job_id: int = job.id) -> None:
                    future = asyncio.run_coroutine_threadsafe(_record_runpod_id(factory, job_id, remote), loop)
                    future.result(timeout=15)

                await run_real_job(
                    factory, job.id, pilot_settings,
                    pilot_execution_seconds=execution_seconds, pilot_runpod_id=store_remote,
                )
                await session.refresh(job)
                if job.status == JobStatus.completed or job.progress < 75:
                    await settle(session, asset, state, settings)
                elif not await _recover(session, state, settings):
                    print("waiting_for_active", flush=True)
                    return
                print(
                    f"{source.external_id} {asset.state} ${asset.charged_micros / 1_000_000:.6f}",
                    flush=True,
                )
                if state.paused:
                    print("paused", flush=True)
                    return
                break
        print(f"{dispatched} pilot attempts", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--import-only", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--publish", metavar="VIDEO_ID", help="Publish after reviewing the intro and chorus")
    parser.add_argument("--limit", type=int, choices=range(1, MAX_SONGS + 1), default=MAX_SONGS)
    parser.add_argument("--usd-per-hour-ceiling", type=float, default=0)
    parser.add_argument("--wait", action="store_true", help="Wait for cooldown and foreground downloads")
    parser.add_argument("--resume", action="store_true", help="Resume after resolving a YouTube gate")
    args = parser.parse_args()
    manifest = SeedManifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    seeds = unique_seeds(manifest)
    if not (args.run or args.publish or args.import_only):
        print(json.dumps({
            "unique": len(seeds), "eligible": sum(s.available and s.metadata_allowed and bool((s.playback_basis or "").strip()) for s in seeds),
            "total_usd": 1.0, "utc_day_usd": 0.35, "cooldown_seconds": 300,
            "sources": [s.video_id for s in seeds],
        }, ensure_ascii=False))
    else:
        try:
            asyncio.run(execute(args, manifest, get_settings()))
        except ValueError as exc:
            parser.error(str(exc))


if __name__ == "__main__":
    main()
