"""Curated catalog identities, lookup, and protected asset storage."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from karaoke.db.models import CatalogAsset, CatalogRecording, CatalogSource, Job, JobStatus

PILOT_OWNER = "internal:catalog-pilot"
PIPELINE_VERSION = "1"
YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def youtube_id(url: str) -> str | None:
    """Match a single YouTube recording without calling an extractor."""
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return None
        host = parsed.hostname
    except ValueError:
        return None
    parts = parsed.path.strip("/").split("/")
    if host == "youtu.be" and len(parts) == 1:
        candidate = parts[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parts == ["watch"]:
            values = parse_qs(parsed.query).get("v", [])
            candidate = values[0] if len(values) == 1 else ""
        elif len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}:
            candidate = parts[1]
        else:
            return None
    else:
        return None
    return candidate if YOUTUBE_ID.fullmatch(candidate) else None


def youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def pilot_job_root(artifact_root: str | Path, storage_token: str) -> Path:
    # Previous releases also serve /share, but only read <root>/<job token>.
    return Path(artifact_root) / "catalog" / storage_token


def ready_assets():
    """Newest ready version remains usable while a replacement is processing."""
    return (
        select(CatalogAsset)
        .join(CatalogAsset.source)
        .join(CatalogAsset.job)
        .where(
            CatalogSource.available.is_(True),
            CatalogSource.metadata_allowed.is_(True),
            CatalogSource.playback_allowed.is_(True),
            CatalogAsset.state == "ready",
            Job.status == JobStatus.completed,
        )
        .options(
            joinedload(CatalogAsset.source)
            .joinedload(CatalogSource.recording)
            .joinedload(CatalogRecording.track),
            joinedload(CatalogAsset.job),
        )
        .order_by(CatalogAsset.id.desc())
    )


async def ready_for_url(session: AsyncSession, url: str) -> CatalogAsset | None:
    video_id = youtube_id(url)
    if video_id is None:
        return None
    return await session.scalar(
        ready_assets()
        .where(CatalogSource.provider == "youtube", CatalogSource.external_id == video_id)
        .limit(1)
    )
