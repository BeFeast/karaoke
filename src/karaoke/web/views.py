"""HTML web UI for karaoke.

Routes
------
- ``GET /``                      — library (card grid of jobs visible to caller).
- ``GET /share/{job_token}``     — public/owner-aware share page (HTML).
- ``GET /submit``                — submit form (Clerk-protected in production).
- ``GET /artifacts/{token}/{f}`` — owner-aware static artifact server.
- ``GET /feed.xml``              — RSS 2.0 of completed jobs (owner-scoped).

The JSON ``/share/{token}`` shape that previously lived in
``karaoke.api.routes`` has moved to ``GET /api/share/{token}``.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from karaoke.api.auth import (
    AuthState,
    Owner,
    is_trusted_lan_request,
    require_owner,
    resolve_owner,
)
from karaoke.config import Settings, get_settings
from karaoke.db.models import Job, JobStatus
from karaoke.db.session import get_session

router = APIRouter(tags=["web"])

_WEB_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_DIR / "templates"))

_LIST_LIMIT = 200
_FEED_LIMIT = 40

_FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="6" fill="#0f172a"/>
<path d="M10 22V10M14 24V8M18 22V10M22 22V14" stroke="#f8fafc" stroke-width="2.4" stroke-linecap="round"/>
</svg>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_owner_view(owner: Owner, job: Job) -> bool:
    if owner.state in {AuthState.trusted_lan, AuthState.machine_bearer}:
        return True
    return owner.subject == job.owner_subject


def _format_relative(when: dt.datetime | None) -> str:
    if when is None:
        return "—"
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    delta = dt.datetime.now(dt.UTC) - when
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        m = seconds // 60
        return f"{m}m ago"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h}h ago"
    days = seconds // 86400
    if days < 30:
        return f"{days}d ago"
    return when.strftime("%Y-%m-%d")


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


_TEMPLATES.env.filters["relative"] = _format_relative
_TEMPLATES.env.filters["duration"] = _format_duration


def _artifact_url(job_token: str, relative_path: str) -> str:
    """``vocals.mp3`` for artifact stored at ``<token>/vocals.mp3``."""
    # Strip the ``<token>/`` prefix to keep URLs short and predictable.
    rel = relative_path
    prefix = f"{job_token}/"
    if rel.startswith(prefix):
        rel = rel[len(prefix):]
    return f"/artifacts/{job_token}/{rel}"


def _rss_date(when: dt.datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return email.utils.formatdate(when.astimezone(dt.UTC).timestamp(), usegmt=True)


# ---------------------------------------------------------------------------
# Library — GET /
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def library(
    request: Request,
    owner: Owner | None = Depends(resolve_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Card grid of karaoke jobs the caller is allowed to see."""
    if owner is None:
        # Anonymous — show an empty library shell rather than 401, so the
        # public landing renders something useful.
        jobs: list[Job] = []
    else:
        stmt = (
            select(Job)
            .order_by(Job.id.desc())
            .limit(_LIST_LIMIT)
        )
        if owner.state not in {AuthState.trusted_lan, AuthState.machine_bearer}:
            stmt = stmt.where(Job.owner_subject == owner.subject)
        jobs = list((await session.scalars(stmt)).all())

    cards = [
        {
            "id": j.id,
            "job_token": j.job_token,
            "title": j.title or j.source_url,
            "source_url": j.source_url,
            "status": j.status.value,
            "progress": j.progress,
            "completed_at": j.completed_at,
            "created_at": j.created_at,
            "share_url": f"/share/{j.job_token}",
        }
        for j in jobs
    ]

    return _TEMPLATES.TemplateResponse(
        request,
        "library.html",
        {
            "jobs": cards,
            "owner": owner,
            "public_base_url": settings.public_base_url.rstrip("/"),
        },
    )


# ---------------------------------------------------------------------------
# Share page — GET /share/{job_token}
# ---------------------------------------------------------------------------


@router.get("/share/{job_token}", response_class=HTMLResponse, include_in_schema=False)
async def share_page(
    job_token: str,
    request: Request,
    owner: Owner | None = Depends(resolve_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Owner-aware + unlisted-token-aware share page (HTML)."""
    job = await session.scalar(
        select(Job)
        .where(Job.job_token == job_token)
        .options(selectinload(Job.artifacts))
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")

    holds_unlisted_token = True  # path proves possession of the secret
    is_owner = owner is not None and _can_owner_view(owner, job)
    is_lan = is_trusted_lan_request(request, settings)
    if not (holds_unlisted_token or is_owner or is_lan):  # pragma: no cover
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")

    artifacts_by_kind: dict[str, dict] = {}
    for a in job.artifacts:
        artifacts_by_kind[a.kind] = {
            "kind": a.kind,
            "relative_path": a.relative_path,
            "content_type": a.content_type,
            "url": _artifact_url(job.job_token, a.relative_path),
        }

    # Lyrics are rendered inline; fetch them lazily via JS to avoid blocking.
    lyrics = artifacts_by_kind.get("lyrics")

    context = {
        "job": {
            "id": job.id,
            "job_token": job.job_token,
            "title": job.title or "Untitled",
            "source_url": job.source_url,
            "status": job.status.value,
            "progress": job.progress,
            "owner_display_name": job.owner_display_name,
            "vast_cost_micros": job.vast_cost_micros,
            "completed_at": job.completed_at,
            "created_at": job.created_at,
        },
        "vocals": artifacts_by_kind.get("vocals"),
        "karaoke": artifacts_by_kind.get("karaoke"),
        "lyrics": lyrics,
        "public_base_url": settings.public_base_url.rstrip("/"),
    }
    return _TEMPLATES.TemplateResponse(request, "share.html", context)


# ---------------------------------------------------------------------------
# Submit form — GET /submit
# ---------------------------------------------------------------------------


@router.get("/submit", response_class=HTMLResponse, include_in_schema=False)
async def submit_form(
    request: Request,
    owner: Owner = Depends(require_owner),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Submit form — requires authentication."""
    return _TEMPLATES.TemplateResponse(
        request,
        "submit.html",
        {
            "owner": owner,
            "public_base_url": settings.public_base_url.rstrip("/"),
        },
    )


# ---------------------------------------------------------------------------
# Static artifact server — GET /artifacts/{job_token}/{relative:path}
# ---------------------------------------------------------------------------


@router.get("/artifacts/{job_token}/{relative:path}", include_in_schema=False)
async def serve_artifact(
    job_token: str,
    relative: str,
    request: Request,
    owner: Owner | None = Depends(resolve_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Serve an artifact file from ``settings.artifacts_dir``.

    Owner check: the unlisted ``job_token`` already gates access (the URL
    contains the secret), so anyone with the URL can fetch the file. We
    still verify (a) the job exists, (b) the relative path resolves to a
    file inside the artifacts root (no traversal), and (c) an artifact
    row references it (so we don't expose arbitrary files in the dir).
    """
    job = await session.scalar(
        select(Job)
        .where(Job.job_token == job_token)
        .options(selectinload(Job.artifacts))
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # Authorise — same rules as the share page.
    holds_unlisted_token = True
    is_owner = owner is not None and _can_owner_view(owner, job)
    is_lan = is_trusted_lan_request(request, settings)
    if not (holds_unlisted_token or is_owner or is_lan):  # pragma: no cover
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    expected = f"{job_token}/{relative}"
    artifact = next((a for a in job.artifacts if a.relative_path == expected), None)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    artifacts_root = Path(settings.artifacts_dir).resolve()
    candidate = (artifacts_root / artifact.relative_path).resolve()
    try:
        candidate.relative_to(artifacts_root)
    except ValueError as exc:  # pragma: no cover - traversal attempt
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
    if not candidate.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    media_type = artifact.content_type
    if not media_type:
        guess, _ = mimetypes.guess_type(candidate.name)
        media_type = guess or "application/octet-stream"
    return FileResponse(candidate, media_type=media_type, filename=candidate.name)


# ---------------------------------------------------------------------------
# Favicon + RSS
# ---------------------------------------------------------------------------


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(
        _FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/feed.xml", include_in_schema=False)
async def feed(
    request: Request,
    owner: Owner | None = Depends(resolve_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.completed)
        .order_by(Job.id.desc())
        .limit(_FEED_LIMIT)
    )
    if owner is not None and owner.state not in {AuthState.trusted_lan, AuthState.machine_bearer}:
        stmt = stmt.where(Job.owner_subject == owner.subject)
    elif owner is None:
        stmt = stmt.where(Job.owner_subject == "__none__")  # anonymous -> empty feed
    rows = list((await session.scalars(stmt)).all())

    base = html.escape(settings.public_base_url.rstrip("/"))
    items: list[str] = []
    for j in rows:
        link = f"{base}/share/{j.job_token}"
        item_pub = _rss_date(j.completed_at or j.created_at)
        item_title = html.escape(j.title or j.source_url or "Untitled")
        items.append(
            f"    <item>\n"
            f"      <title>{item_title}</title>\n"
            f"      <link>{link}</link>\n"
            f"      <guid isPermaLink=\"true\">{link}</guid>\n"
            f"      <pubDate>{item_pub}</pubDate>\n"
            f"    </item>"
        )
    now = _rss_date(dt.datetime.now(dt.UTC))
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        "    <title>karaoke</title>\n"
        f"    <link>{base}/</link>\n"
        "    <description>Latest karaoke jobs</description>\n"
        f"    <lastBuildDate>{now}</lastBuildDate>\n"
        + "\n".join(items)
        + "\n  </channel>\n</rss>\n"
    )
    return Response(content=body, media_type="application/rss+xml; charset=utf-8")
