"""Clerk-only curated catalog and facets."""
from __future__ import annotations

import re
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from karaoke.api.auth import AuthState, Owner, resolve_owner
from karaoke.catalog import ready_assets, youtube_url
from karaoke.db.models import CatalogRecording, CatalogSource
from karaoke.db.session import get_session
from karaoke.textnorm import normalize

router = APIRouter(prefix="/catalog", tags=["catalog"])


def require_clerk(owner: Owner | None = Depends(resolve_owner)) -> Owner:
    if owner is None or owner.state != AuthState.clerk_user:
        raise HTTPException(401, "Clerk sign-in required")
    return owner


class CatalogSong(BaseModel):
    id: int
    title: str
    artist: str
    version: str
    language: str
    genres: list[str]
    genres_uncertain: bool
    collections: list[str]
    decade: int | None
    popularity: dict[str, int]
    source_url: str
    thumbnail_url: str | None
    stage_token: str | None


class CatalogPage(BaseModel):
    songs: list[CatalogSong]
    facets: dict[str, dict[str, int]]


def _facet_values(song: CatalogSong) -> dict[str, list[str]]:
    return {
        "language": [song.language],
        "genre": song.genres,
        "collection": song.collections,
        "decade": [str(song.decade)] if song.decade is not None else [],
    }


@router.get("", response_model=CatalogPage)
async def browse_catalog(
    response: Response,
    q: str = Query(default="", max_length=200),
    language: str | None = Query(default=None, max_length=12),
    genre: str | None = Query(default=None, max_length=80),
    collection: str | None = Query(default=None, max_length=100),
    decade: int | None = Query(default=None, ge=1900, le=2100),
    _owner: Owner = Depends(require_clerk),
    session: AsyncSession = Depends(get_session),
) -> CatalogPage:
    response.headers["Cache-Control"] = "private, no-store"
    sources = await session.scalars(
        select(CatalogSource)
        .where(
            CatalogSource.provider == "youtube",
            CatalogSource.available.is_(True), CatalogSource.metadata_allowed.is_(True),
        )
        .options(joinedload(CatalogSource.recording).joinedload(CatalogRecording.track))
    )
    ready: dict[int, int] = {}
    for asset in await session.scalars(ready_assets()):
        ready.setdefault(asset.source_id, asset.id)

    query = normalize(q).split()
    filters = {
        "language": language,
        "genre": genre,
        "collection": collection,
        "decade": str(decade) if decade is not None else None,
    }
    counts: dict[str, Counter[str]] = {name: Counter() for name in filters}
    songs: list[CatalogSong] = []
    # The operator-managed pilot contains at most 30 sources. Normalizing this
    # bounded set in Python gives SQLite and Postgres identical Unicode matching.
    for source in sources:
        recording = source.recording
        track = recording.track
        haystack = normalize(" ".join([track.title, track.artist, *track.aliases]))
        if not all(term in haystack for term in query):
            continue
        asset = ready.get(source.id)
        video_id = source.external_id
        song = CatalogSong(
            id=source.id,
            title=track.title,
            artist=track.artist,
            version=recording.version,
            language=recording.language,
            genres=recording.genres,
            genres_uncertain=recording.genres_uncertain,
            collections=recording.collections,
            decade=recording.decade,
            popularity={entry["playlist"]: entry["rank"] for entry in source.provenance},
            source_url=youtube_url(video_id),
            thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
            stage_token=f"cat.{asset}" if asset is not None else None,
        )
        values = _facet_values(song)
        mismatches = {
            key for key, value in filters.items()
            if value is not None and value not in values[key]
        }
        if not mismatches:
            songs.append(song)
        for name, counter in counts.items():
            if not (mismatches - {name}):
                counter.update(values[name])

    songs.sort(key=lambda song: (
        song.stage_token is None,
        min(song.popularity.values(), default=999),
        song.id,
    ))
    return CatalogPage(songs=songs, facets={key: dict(value) for key, value in counts.items()})


def catalog_asset_id(token: str) -> int | None:
    if match := re.fullmatch(r"cat\.([1-9][0-9]{0,8})", token):
        return int(match[1])
    return None
