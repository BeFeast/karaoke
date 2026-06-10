"""HTTP and WebSocket routes for the karaoke API."""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import re
import secrets
import shutil
from pathlib import Path as _PathLib

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from karaoke import __version__
from karaoke.api.auth import (
    AuthState,
    Owner,
    is_trusted_lan_request,
    require_owner,
    resolve_owner,
)
from karaoke.api.cookies_store import (
    CookieValidationError,
    previous_path,
    validate_netscape_cookies,
    write_cookies_atomically,
)
from karaoke.config import Settings, get_settings
from karaoke.db.models import Job, JobStatus
from karaoke.db.session import get_session, get_session_factory
from karaoke.worker import job_cookies
from karaoke.worker.scheduler import schedule_job

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class JobCreate(BaseModel):
    """Body for ``POST /jobs`` — submit a URL for separation."""

    url: str = Field(min_length=1)
    title: str | None = None
    # OPTIONAL per-job YouTube cookies (issue #77): a Netscape ``cookies.txt``
    # blob the submitting client (Chrome extension / native app) captured from
    # the user's logged-in YouTube session, used ONLY for this job's yt-dlp
    # download and then discarded. Never persisted, never logged. No length
    # constraint here on purpose — a Pydantic constraint error echoes the
    # offending value in the 422 body, which would leak the cookie; size and
    # format are validated by ``_validate_job_cookies`` with value-free errors.
    youtube_cookies: str | None = None


def _validate_job_cookies(raw: str | None) -> str | None:
    """Validate an optional per-job Netscape cookie blob (issue #77).

    Returns the blob unchanged when usable, or ``None`` when absent (omitted /
    null / whitespace-only — the caller then falls back to public download or
    the central jar bridge). Raises ``HTTPException`` with a value-free message
    on a too-large (413) or malformed (422) blob; the cookie value is NEVER
    echoed back. ``MAX_COOKIE_BYTES`` / ``validate_netscape_cookies`` are shared
    with the central ``/cookies/youtube`` path (defined lower in this module).
    """
    if raw is None or not raw.strip():
        return None
    if len(raw.encode("utf-8")) > MAX_COOKIE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="cookie payload too large",
        )
    try:
        validate_netscape_cookies(raw)
    except CookieValidationError as exc:
        # ``exc`` is value-free by construction (see cookies_store).
        raise HTTPException(
            status_code=422, detail=f"invalid Netscape cookie file: {exc}"
        ) from exc
    return raw


class JobArtifactOut(BaseModel):
    """A ready output file, projected for the SPA's "what's available" UI."""

    kind: str
    name: str
    size: int | None


class JobOut(BaseModel):
    """Owner-visible job projection."""

    id: int
    job_token: str
    source_url: str
    title: str | None
    artist: str | None
    track: str | None
    status: JobStatus
    progress: int
    stage_note: str | None
    error: str | None
    share_url: str
    owner_subject: str
    artifacts: list[JobArtifactOut]

    @classmethod
    def from_orm_job(cls, job: Job, *, public_base_url: str) -> JobOut:
        share = public_base_url.rstrip("/") + f"/share/{job.job_token}"
        return cls(
            id=job.id,
            job_token=job.job_token,
            source_url=job.source_url,
            title=job.title,
            artist=job.artist,
            track=job.track,
            status=job.status,
            progress=job.progress,
            stage_note=job.stage_note,
            error=job.error,
            share_url=share,
            owner_subject=job.owner_subject,
            artifacts=[
                JobArtifactOut(
                    kind=a.kind,
                    name=_PathLib(a.relative_path).name,
                    size=a.size_bytes,
                )
                for a in job.artifacts
            ],
        )


class ArtifactOut(BaseModel):
    kind: str
    relative_path: str
    content_type: str | None


class SharePayload(BaseModel):
    """Public share-page payload — owner-aware + unlisted-token-aware.

    The token itself is the unlisted-access secret; we therefore expose
    only the owner *display* attributes, never the Clerk subject.
    """

    job_token: str
    title: str | None
    artist: str | None
    track: str | None
    status: JobStatus
    progress: int
    stage_note: str | None
    owner_display_name: str | None
    artifacts: list[ArtifactOut]


class LyricsLine(BaseModel):
    """A single time-synced lyrics line: ``t`` seconds → ``text``."""

    t: float
    text: str


class LyricsPayload(BaseModel):
    """Structured lyrics for the player + lyrics panel (Track 2).

    * ``synced`` — true when a time-synced ``.lrc`` is available; ``lines`` is
      then populated and the player can highlight the active line.
    * ``lrc`` — raw ``.lrc`` body when synced, else ``None``.
    * ``lines`` — parsed ``[{t, text}]`` (sorted, blank lines dropped) when
      synced, else ``None``.
    * ``plain`` — plain-text lyrics when available, else ``None``.
    * ``source`` — provenance (``lrclib_synced`` / ``lrclib_plain`` /
      ``whisper_asr`` / ``instrumental`` / inferred fallback).
    """

    synced: bool
    lrc: str | None
    lines: list[LyricsLine] | None
    plain: str | None
    source: str


class ConfigOut(BaseModel):
    """Public runtime config the Submitter SPA needs to boot.

    The Clerk *publishable* key is safe to expose to browsers (that is its
    purpose); the secret key is never returned here.
    """

    clerk_publishable_key: str
    clerk_enabled: bool
    public_base_url: str


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


router = APIRouter()


@router.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe — no auth, no DB hit. Reports the running version."""
    return {"status": "ok", "version": __version__}


@router.get("/config", response_model=ConfigOut, tags=["meta"])
async def runtime_config(
    settings: Settings = Depends(get_settings),
) -> ConfigOut:
    """Public runtime config for the SPA — no auth.

    ``clerk_enabled`` is true only when ``clerk_spa_enabled`` is set AND a
    publishable key exists; otherwise the SPA renders in trusted-LAN "LAN
    mode" with no sign-in UI (the default until Clerk origins are set up).
    """
    key = settings.clerk_publishable_key.strip()
    enabled = settings.clerk_spa_enabled and bool(key)
    return ConfigOut(
        clerk_publishable_key=key,
        clerk_enabled=enabled,
        public_base_url=settings.public_base_url,
    )


@router.post(
    "/jobs",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
    tags=["jobs"],
)
async def create_job(
    payload: JobCreate,
    owner: Owner = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JobOut:
    """Create a karaoke job and kick off the (mocked) worker."""
    # Per-job ephemeral YouTube cookies (issue #77): validate up front so a
    # malformed jar fails fast WITHOUT creating a junk job. The value is never
    # persisted (no DB column) and never logged — it is handed to the
    # in-process worker through the ephemeral ``job_cookies`` registry, used
    # for this one download, then discarded. Empty / whitespace → absent, so
    # the existing fallback applies (public video, or the central jar bridge).
    cookies_blob = _validate_job_cookies(payload.youtube_cookies)
    job = Job(
        job_token=secrets.token_urlsafe(24),
        owner_subject=owner.subject,
        owner_email=owner.email,
        owner_display_name=owner.display_name,
        source_url=payload.url,
        title=payload.title,
        status=JobStatus.queued,
        progress=0,
    )
    session.add(job)
    await session.commit()
    # Eager-load ``artifacts`` (empty for a brand-new job) so building JobOut
    # never triggers an async lazy-load.
    await session.refresh(job, attribute_names=["artifacts"])

    # Dispatch to the real vast.ai worker, or the in-process mock when no
    # vast key is configured (CI / dev default). See worker.scheduler.
    if cookies_blob is not None:
        job_cookies.stash(job.id, cookies_blob)
    schedule_job(get_session_factory(), job.id, settings)

    return JobOut.from_orm_job(job, public_base_url=settings.public_base_url)


@router.get("/jobs/{job_id}/status", response_model=JobOut, tags=["jobs"])
async def job_status(
    job_id: int,
    owner: Owner = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JobOut:
    """Owner-scoped job status."""
    job = await session.scalar(
        select(Job).where(Job.id == job_id).options(selectinload(Job.artifacts))
    )
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not _can_owner_view(owner, job):
        # Hide existence on cross-owner reads.
        raise HTTPException(status_code=404, detail="job not found")
    return JobOut.from_orm_job(job, public_base_url=settings.public_base_url)


class MeOut(BaseModel):
    """Who the caller is, per the resolved auth layer."""

    subject: str
    email: str | None
    display_name: str | None
    state: str
    is_admin: bool


@router.get("/jobs", response_model=list[JobOut], tags=["jobs"])
async def list_jobs(
    owner: Owner = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    limit: int = 50,
) -> list[JobOut]:
    """List jobs for the caller, newest first.

    Owner-scoped: a Clerk user sees only their own jobs. Trusted-LAN and
    machine-bearer callers are treated as admin and see every job (matches
    ``_can_owner_view``).
    """
    limit = max(1, min(limit, 200))
    stmt = (
        select(Job)
        .order_by(Job.created_at.desc())
        .limit(limit)
        .options(selectinload(Job.artifacts))
    )
    if owner.state not in {AuthState.trusted_lan, AuthState.machine_bearer}:
        stmt = stmt.where(Job.owner_subject == owner.subject)
    jobs = (await session.scalars(stmt)).all()
    return [
        JobOut.from_orm_job(j, public_base_url=settings.public_base_url) for j in jobs
    ]


@router.get("/me", response_model=MeOut, tags=["meta"])
async def whoami(owner: Owner = Depends(require_owner)) -> MeOut:
    """Return the resolved caller identity — the SPA uses this to show
    sign-in state and gate admin-only affordances."""
    return MeOut(
        subject=owner.subject,
        email=owner.email,
        display_name=owner.display_name,
        state=owner.state.value,
        is_admin=owner.state in {AuthState.trusted_lan, AuthState.machine_bearer},
    )


_TERMINAL_STATUSES = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}


def _remove_artifact_files(settings: Settings, job_token: str) -> None:
    """Best-effort removal of a job's NFS artifact directory.

    Strictly scoped to ``<artifact_root>/<job_token>`` so a malformed token can
    never escalate into deleting the artifact root itself."""
    if not job_token:
        return
    root = _PathLib(settings.artifact_root)
    target = (root / job_token).resolve()
    try:
        root_resolved = root.resolve()
    except OSError:  # pragma: no cover - root not present in dev/test
        return
    if target == root_resolved or root_resolved not in target.parents:
        return
    shutil.rmtree(target, ignore_errors=True)


class ClearResult(BaseModel):
    """Result of a bulk cleanup action."""

    deleted: int


@router.post("/jobs/clear-failed", response_model=ClearResult, tags=["jobs"])
async def clear_failed_jobs(
    owner: Owner = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ClearResult:
    """Delete all of the caller's failed jobs (admin/LAN clears every failed job).

    Registered before the ``/jobs/{job_id}`` routes so the literal path is never
    shadowed by the path-parameter ones."""
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.failed)
        .options(selectinload(Job.artifacts))
    )
    if owner.state not in {AuthState.trusted_lan, AuthState.machine_bearer}:
        stmt = stmt.where(Job.owner_subject == owner.subject)
    jobs = (await session.scalars(stmt)).all()
    tokens = [j.job_token for j in jobs]
    for job in jobs:
        await session.delete(job)
    await session.commit()
    for token in tokens:
        _remove_artifact_files(settings, token)
    return ClearResult(deleted=len(tokens))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut, tags=["jobs"])
async def cancel_job(
    job_id: int,
    owner: Owner = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JobOut:
    """Cancel an in-flight job. The worker re-checks the status at each stage
    boundary and stops, so this gracefully unwinds a running pipeline."""
    job = await session.scalar(
        select(Job).where(Job.id == job_id).options(selectinload(Job.artifacts))
    )
    if job is None or not _can_owner_view(owner, job):
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409, detail=f"job is already {job.status.value}"
        )
    job.status = JobStatus.cancelled
    await session.commit()
    await session.refresh(job, attribute_names=["artifacts"])
    # Drop any per-job cookies still stashed for this job (#77): a cancel
    # before the worker popped them would otherwise leave the blob lingering
    # in the in-memory registry.
    job_cookies.discard(job_id)
    return JobOut.from_orm_job(job, public_base_url=settings.public_base_url)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["jobs"])
async def delete_job(
    job_id: int,
    owner: Owner = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Delete a job (any status) plus its artifact rows and NFS files.

    Owner-scoped: a cross-owner delete returns 404 to hide existence, matching
    the read endpoints. Artifacts are eager-loaded so the ORM delete-orphan
    cascade fires under SQLite (whose FK cascade is off by default)."""
    job = await session.scalar(
        select(Job).where(Job.id == job_id).options(selectinload(Job.artifacts))
    )
    if job is None or not _can_owner_view(owner, job):
        raise HTTPException(status_code=404, detail="job not found")
    token = job.job_token
    await session.delete(job)
    await session.commit()
    _remove_artifact_files(settings, token)
    # Drop any per-job cookies still stashed for this job (#77).
    job_cookies.discard(job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Standalone result page — mirrors the Submitter SPA's Scribe "field" design
# tokens (olive/sage, system Geist/Inter stack, light + dark) so a shared link
# reads as a sibling of the app. No external assets; CSS braces are doubled for
# str.format.
_SHARE_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{title} — karaoke</title>
<style>
 :root {{
   color-scheme: light dark;
   --font-sans: "Geist","Inter",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
   --font-mono: "Geist Mono","JetBrains Mono",ui-monospace,"SF Mono",Menlo,monospace;
   --bg:#eceef2; --bg-soft:#d1d5de; --bg-card:#f5f6f9; --fg:#1c2018; --fg-soft:#3a4234;
   --muted:#837569; --border:#b7b6c2; --border-soft:#c8ccd3; --accent:#657153; --accent-soft:#d8dfcd;
   --ok:#657153; --err:#8a4a3a; --info:#5d7088; --radius:5px; --radius-lg:10px;
 }}
 @media (prefers-color-scheme: dark) {{
   :root {{
     --bg:#1a1e1a; --bg-soft:#232924; --bg-card:#1e231d; --fg:#d1d5de; --fg-soft:#b7b6c2;
     --muted:#837569; --border:#2d362c; --border-soft:#232924; --accent:#8aaa79; --accent-soft:#2c3a2c;
     --ok:#8aaa79; --err:#c4796a; --info:#8ea4c0;
   }}
 }}
 * {{ box-sizing: border-box; }}
 body {{ margin:0; background:var(--bg); color:var(--fg); font-family:var(--font-sans);
   line-height:1.5; -webkit-font-smoothing:antialiased; font-feature-settings:"ss01","cv11"; }}
 .bar {{ display:flex; align-items:center; gap:10px; padding:0 20px; height:56px;
   border-bottom:1px solid var(--border); font-weight:600; font-size:17px; letter-spacing:-0.01em; }}
 .mark {{ width:24px; height:24px; display:grid; place-items:center; background:var(--accent);
   color:var(--bg-card); border-radius:3px; }}
 .mark svg {{ display:block; }}
 .wrap {{ max-width:760px; margin:0 auto; padding:32px 20px 64px; }}
 h1 {{ font-size:30px; font-weight:600; letter-spacing:-0.02em; margin:0 0 10px; word-break:break-word; }}
 .meta {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:28px;
   font-family:var(--font-mono); font-size:12px; color:var(--muted); }}
 .chip {{ display:inline-flex; align-items:center; gap:5px; padding:2px 9px; font-size:11.5px;
   font-family:var(--font-mono); border-radius:999px; border:1px solid var(--border-soft);
   background:var(--bg-soft); color:var(--fg-soft); }}
 .chip .dot {{ width:6px; height:6px; border-radius:50%; background:currentColor; }}
 .chip.ok {{ color:var(--ok); background:color-mix(in oklab,var(--ok) 12%,var(--bg)); border-color:color-mix(in oklab,var(--ok) 24%,transparent); }}
 .chip.err {{ color:var(--err); background:color-mix(in oklab,var(--err) 12%,var(--bg)); border-color:color-mix(in oklab,var(--err) 24%,transparent); }}
 .chip.info {{ color:var(--info); background:color-mix(in oklab,var(--info) 12%,var(--bg)); border-color:color-mix(in oklab,var(--info) 24%,transparent); }}
 .chip.run {{ color:var(--accent); background:var(--accent-soft); border-color:color-mix(in oklab,var(--accent) 32%,transparent); }}
 .player {{ border:1px solid var(--border); border-radius:var(--radius-lg); background:var(--bg-card);
   padding:16px; margin:0 0 16px; }}
 .player-label, .sec-label {{ font-family:var(--font-mono); font-size:10.5px; font-weight:650;
   text-transform:uppercase; letter-spacing:0.08em; color:var(--muted); margin-bottom:10px; }}
 audio {{ width:100%; accent-color:var(--accent); }}
 audio::-webkit-media-controls-panel {{ background:var(--bg-soft); }}
 .lyrics {{ margin-top:28px; }}
 pre {{ white-space:pre-wrap; word-wrap:break-word; background:var(--bg-card); color:var(--fg);
   border:1px solid var(--border-soft); padding:16px; border-radius:var(--radius-lg);
   max-height:52vh; overflow:auto; font-family:var(--font-mono); font-size:13px; line-height:1.6; margin:0; }}
 .downloads {{ margin-top:28px; padding-top:16px; border-top:1px solid var(--border-soft);
   font-family:var(--font-mono); font-size:12px; color:var(--muted); display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
 .downloads a {{ color:var(--accent); text-decoration:none; }}
 .downloads a:hover {{ text-decoration:underline; }}
 .empty {{ color:var(--muted); font-style:italic; font-size:13px; }}
</style>
</head>
<body>
<header class="bar">
  <span class="mark"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3" fill="currentColor"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
  karaoke
</header>
<main class="wrap">
<h1>{title_escaped}</h1>
<div class="meta">
  <span class="chip {status_chip_class}"><span class="dot"></span>{status}</span>
  <span>{progress}%</span>
  {owner_block}
</div>
{karaoke_block}
{vocals_block}
{lyrics_block}
{downloads_block}
</main>
</body>
</html>
"""


_STATUS_CHIP_CLASS: dict[str, str] = {
    "completed": "ok",
    "failed": "err",
    "cancelled": "",
    "queued": "info",
    "downloading": "run",
    "separating": "run",
    "transcribing": "run",
}


def _html_escape(s: str | None) -> str:
    if not s:
        return ""
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


def _render_share_html(job: Job, lyrics_text: str | None) -> str:
    title = job.title or job.source_url or "karaoke job"
    owner = (job.owner_display_name or "").strip()
    owner_block = f"<span>· {_html_escape(owner)}</span>" if owner else ""
    base = f"/share/{job.job_token}"
    artifacts_by_kind = {a.kind: a for a in job.artifacts}

    def audio_block(kind: str, label: str) -> str:
        if kind not in artifacts_by_kind:
            return (
                f'<div class="player"><div class="player-label">{label}</div>'
                f'<div class="empty">not yet available</div></div>'
            )
        return (
            f'<div class="player"><div class="player-label">{label}</div>'
            f'<audio controls preload="none" src="{base}/{kind}.mp3"></audio></div>'
        )

    if lyrics_text:
        lyrics_block = (
            f'<section class="lyrics"><div class="sec-label">lyrics</div>'
            f'<pre>{_html_escape(lyrics_text)}</pre></section>'
        )
    elif "lyrics" in artifacts_by_kind:
        lyrics_block = (
            f'<section class="lyrics"><div class="sec-label">lyrics</div>'
            f'<p><a href="{base}/lyrics.txt">open lyrics.txt</a></p></section>'
        )
    else:
        lyrics_block = (
            '<section class="lyrics"><div class="sec-label">lyrics</div>'
            '<div class="empty">not yet available</div></section>'
        )

    downloads: list[str] = []
    if "karaoke" in artifacts_by_kind:
        downloads.append(f'<a href="{base}/karaoke.mp3">instrumental</a>')
    if "vocals" in artifacts_by_kind:
        downloads.append(f'<a href="{base}/vocals.mp3">vocals</a>')
    if "lyrics" in artifacts_by_kind:
        downloads.append(f'<a href="{base}/lyrics.txt">lyrics</a>')
    downloads_block = (
        '<div class="downloads">download:&nbsp;' + " · ".join(downloads) + "</div>"
        if downloads
        else ""
    )

    return _SHARE_HTML_TEMPLATE.format(
        title=_html_escape(title)[:80],
        title_escaped=_html_escape(title),
        status=job.status.value,
        status_chip_class=_STATUS_CHIP_CLASS.get(job.status.value, ""),
        progress=job.progress,
        owner_block=owner_block,
        karaoke_block=audio_block("karaoke", "karaoke (instrumental)"),
        vocals_block=audio_block("vocals", "vocals only"),
        lyrics_block=lyrics_block,
        downloads_block=downloads_block,
    )


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    # Default to HTML in browsers; JSON only when explicitly asked.
    return "application/json" in accept and "text/html" not in accept


@router.get("/share/{job_token}", tags=["share"])
async def share_page(
    job_token: str,
    request: Request,
    owner: Owner | None = Depends(resolve_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Owner-aware + unlisted-token-aware share endpoint.

    Returns HTML for browsers (with embedded ``<audio>`` players), or JSON
    when ``Accept: application/json`` is sent — keeps API consumers happy.
    """
    job = await session.scalar(
        select(Job)
        .where(Job.job_token == job_token)
        .options(selectinload(Job.artifacts))
    )
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    holds_unlisted_token = True
    is_owner = owner is not None and _can_owner_view(owner, job)
    is_lan = is_trusted_lan_request(request, settings)
    if not (holds_unlisted_token or is_owner or is_lan):  # pragma: no cover
        raise HTTPException(status_code=404, detail="job not found")

    if _wants_json(request):
        return SharePayload(
            job_token=job.job_token,
            title=job.title,
            artist=job.artist,
            track=job.track,
            status=job.status,
            progress=job.progress,
            stage_note=job.stage_note,
            owner_display_name=job.owner_display_name,
            artifacts=[
                ArtifactOut(
                    kind=a.kind,
                    relative_path=a.relative_path,
                    content_type=a.content_type,
                )
                for a in job.artifacts
            ],
        )

    # Inline lyrics if they fit comfortably on the page.
    lyrics_text: str | None = None
    artifact_root = _PathLib(settings.artifact_root)
    lyrics_path = artifact_root / job.job_token / "exports" / "lyrics.txt"
    if lyrics_path.is_file() and lyrics_path.stat().st_size < 64 * 1024:
        try:
            lyrics_text = lyrics_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            lyrics_text = None

    return HTMLResponse(_render_share_html(job, lyrics_text))


# One LRC timestamp tag, e.g. "[01:23.45]" / "[1:23]" / "[01:23:456]". Multiple
# tags may prefix a single line; we expand each into its own timed line. Mirrors
# the worker's ``_LRC_TIMESTAMP_RE`` but captures the components for conversion.
_LRC_TAG_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")


def _parse_lrc_lines(lrc: str) -> list[LyricsLine]:
    """Parse an LRC body into sorted ``LyricsLine`` entries.

    Lines without a timestamp tag (e.g. ``[ar:]`` metadata or free text) and
    timestamped lines whose text is blank are dropped. A single line carrying
    several timestamp tags expands into one entry per tag.
    """
    lines: list[LyricsLine] = []
    for raw in lrc.splitlines():
        tags = list(_LRC_TAG_RE.finditer(raw))
        if not tags:
            continue
        text = _LRC_TAG_RE.sub("", raw).strip()
        if not text:
            continue
        for tag in tags:
            minutes = int(tag.group(1))
            seconds = int(tag.group(2))
            frac_raw = tag.group(3) or "0"
            # Normalize the fractional part to seconds regardless of 2- or
            # 3-digit precision (".45" -> 0.45s, ".456" -> 0.456s).
            frac = int(frac_raw) / (10 ** len(frac_raw))
            t = minutes * 60 + seconds + frac
            lines.append(LyricsLine(t=round(t, 3), text=text))
    lines.sort(key=lambda line: line.t)
    return lines


def _lrc_strip_timestamps(lrc: str) -> str:
    """Derive plain text from an LRC body (drop tags + blank lines)."""
    out: list[str] = []
    for raw in lrc.splitlines():
        stripped = _LRC_TAG_RE.sub("", raw).strip()
        if stripped:
            out.append(stripped)
    return "\n".join(out)


def _read_lyrics_source(exports_dir: _PathLib) -> str | None:
    """Read ``metadata.json``'s ``lyrics_source`` if present, else ``None``."""
    meta_path = exports_dir / "metadata.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    source = meta.get("lyrics_source")
    return source if isinstance(source, str) and source else None


def _metadata_is_instrumental(exports_dir: _PathLib) -> bool:
    """Whether ``metadata.json`` flags the job as instrumental."""
    meta_path = exports_dir / "metadata.json"
    if not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(meta, dict) and meta.get("instrumental"))


def _read_text_artifact(path: _PathLib) -> str | None:
    """Read a small UTF-8 text artifact, or ``None`` on any IO error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - filesystem race
        return None


@router.get("/share/{job_token}/lyrics", response_model=LyricsPayload, tags=["share"])
async def share_lyrics(
    job_token: str,
    request: Request,
    owner: Owner | None = Depends(resolve_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> LyricsPayload:
    """Structured lyrics for a job, read from the NFS artifact root.

    Same auth model as the other ``/share/{job_token}`` routes (possession of
    the unlisted token is the access proof; owner and trusted-LAN also see it).

    Resolution from ``<artifact_root>/<job_token>/exports/``:

    * ``lyrics.lrc`` present  → ``synced=true``, ``lrc`` = its text, ``lines`` =
      parsed ``[mm:ss.xx]`` → ``{t, text}`` (sorted, blank lines dropped),
      ``plain`` = ``lyrics.txt`` if present else the LRC with timestamps stripped.
    * only ``lyrics.txt``     → ``synced=false``, ``plain`` only.
    * instrumental (per ``metadata.json``) → empty lyrics, ``source`` =
      ``"instrumental"``.

    ``source`` comes from ``metadata.json``'s ``lyrics_source`` when present,
    otherwise it is inferred from which artifacts exist.
    """
    job = await session.scalar(select(Job).where(Job.job_token == job_token))
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    holds_unlisted_token = True
    is_owner = owner is not None and _can_owner_view(owner, job)
    is_lan = is_trusted_lan_request(request, settings)
    if not (holds_unlisted_token or is_owner or is_lan):  # pragma: no cover
        raise HTTPException(status_code=404, detail="job not found")

    exports_dir = _PathLib(settings.artifact_root) / job_token / "exports"
    meta_source = _read_lyrics_source(exports_dir)

    # Instrumental: no lyrics regardless of any stray files.
    if _metadata_is_instrumental(exports_dir):
        return LyricsPayload(
            synced=False,
            lrc=None,
            lines=None,
            plain=None,
            source=meta_source or "instrumental",
        )

    lrc_path = exports_dir / "lyrics.lrc"
    txt_path = exports_dir / "lyrics.txt"

    lrc_text = _read_text_artifact(lrc_path) if lrc_path.is_file() else None
    if lrc_text is not None:
        plain = _read_text_artifact(txt_path) if txt_path.is_file() else None
        if plain is None:
            plain = _lrc_strip_timestamps(lrc_text)
        return LyricsPayload(
            synced=True,
            lrc=lrc_text,
            lines=_parse_lrc_lines(lrc_text),
            plain=plain,
            source=meta_source or "lrclib_synced",
        )

    plain = _read_text_artifact(txt_path) if txt_path.is_file() else None
    if plain is not None:
        return LyricsPayload(
            synced=False,
            lrc=None,
            lines=None,
            plain=plain,
            source=meta_source or "whisper_asr",
        )

    # No lyrics artifacts at all (job still running, or none produced).
    return LyricsPayload(
        synced=False,
        lrc=None,
        lines=None,
        plain=None,
        source=meta_source or "none",
    )


_ALLOWED_ARTIFACTS: dict[str, tuple[str, str]] = {
    # name -> (relative path under exports/, content-type)
    "karaoke.mp3": ("karaoke.mp3", "audio/mpeg"),
    "vocals.mp3": ("vocals.mp3", "audio/mpeg"),
    "lyrics.txt": ("lyrics.txt", "text/plain; charset=utf-8"),
    "lyrics.lrc": ("lyrics.lrc", "text/plain; charset=utf-8"),
    "metadata.json": ("metadata.json", "application/json"),
}


@router.get("/share/{job_token}/{artifact_name}", tags=["share"])
async def share_artifact(
    job_token: str,
    artifact_name: str,
    request: Request,
    owner: Owner | None = Depends(resolve_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Stream an artifact file from the NFS artifact root.

    Same auth model as ``/share/{job_token}``: possession of the unlisted
    ``job_token`` is itself the access proof; trusted-LAN and the owner
    also see it. Only an explicit allowlist of artifact names is served
    so this endpoint cannot be turned into an arbitrary-file reader.
    """
    if artifact_name not in _ALLOWED_ARTIFACTS:
        raise HTTPException(status_code=404, detail="artifact not found")

    job = await session.scalar(select(Job).where(Job.job_token == job_token))
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    holds_unlisted_token = True
    is_owner = owner is not None and _can_owner_view(owner, job)
    is_lan = is_trusted_lan_request(request, settings)
    if not (holds_unlisted_token or is_owner or is_lan):  # pragma: no cover
        raise HTTPException(status_code=404, detail="job not found")

    rel, ctype = _ALLOWED_ARTIFACTS[artifact_name]
    artifact_root = _PathLib(settings.artifact_root)
    file_path = artifact_root / job_token / "exports" / rel
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="artifact not yet ready")

    return FileResponse(
        path=str(file_path),
        media_type=ctype,
        filename=rel,
    )


# ---------------------------------------------------------------------------
# WebSocket — live progress
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def websocket_progress(websocket: WebSocket) -> None:
    """Live-progress channel.

    The client subscribes with ``{"action": "subscribe", "job_id": N}``
    and receives status snapshots until the job is terminal or the
    socket disconnects. Polling /jobs/{id}/status remains the
    fallback channel.
    """
    await websocket.accept()
    try:
        msg = await websocket.receive_json()
    except WebSocketDisconnect:
        return

    job_id = msg.get("job_id") if isinstance(msg, dict) else None
    if not isinstance(job_id, int):
        await websocket.send_json({"error": "expected {action: subscribe, job_id: int}"})
        await websocket.close(code=1003)
        return

    factory = get_session_factory()
    last_status: JobStatus | None = None
    last_progress: int | None = None
    last_stage_note: str | None = None
    terminal = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}

    try:
        while True:
            async with factory() as session:
                job = await session.get(Job, job_id)
            if job is None:
                await websocket.send_json({"error": "job not found"})
                break
            if (
                job.status != last_status
                or job.progress != last_progress
                or job.stage_note != last_stage_note
            ):
                await websocket.send_json(
                    {
                        "job_id": job.id,
                        "status": job.status.value,
                        "progress": job.progress,
                        "stage_note": job.stage_note,
                        "error": job.error,
                    }
                )
                last_status = job.status
                last_progress = job.progress
                last_stage_note = job.stage_note
            if job.status in terminal:
                break
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        return
    finally:
        with contextlib.suppress(Exception):  # pragma: no cover - already closed
            await websocket.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _can_owner_view(owner: Owner, job: Job) -> bool:
    """Owner is allowed to see jobs they own, plus trusted-LAN/machine bypass."""
    if owner.state in {AuthState.trusted_lan, AuthState.machine_bearer}:
        return True
    return owner.subject == job.owner_subject


# ---------------------------------------------------------------------------
# YouTube cookie rotation (issue #73)
# ---------------------------------------------------------------------------

# Only callers that prove they hold a logged-in YouTube session may rotate the
# jar: the Chrome extension (``ktx_`` token) or a trusted machine bearer. A
# trusted-LAN-anonymous or Clerk-user request is rejected — those layers do not
# imply possession of YouTube cookies.
COOKIE_WRITER_STATES = {AuthState.extension_token, AuthState.machine_bearer}

# Hard ceiling on an accepted payload. A real logged-in YouTube jar is a few KiB;
# 1 MiB is generous and bounds abuse.
MAX_COOKIE_BYTES = 1024 * 1024

# Serialise concurrent writers (two extension instances posting at once) so the
# last-known-good snapshot + atomic replace never interleave.
_COOKIE_WRITE_LOCK = asyncio.Lock()


async def require_cookie_writer(owner: Owner = Depends(require_owner)) -> Owner:
    """Restrict cookie rotation to the extension token / machine bearer."""
    if owner.state not in COOKIE_WRITER_STATES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cookie upload requires an extension token or machine bearer",
        )
    return owner


class CookieUploadResult(BaseModel):
    """Non-secret result of a cookie rotation."""

    accepted: bool
    cookies: int
    youtube_cookies: int
    bytes: int
    last_good_kept: bool


class CookieStoreStatus(BaseModel):
    """Non-secret metadata about the stored jar (no cookie values)."""

    configured: bool
    present: bool
    bytes: int | None = None
    modified_at: str | None = None
    last_good_present: bool = False


@router.post("/cookies/youtube", response_model=CookieUploadResult, tags=["cookies"])
async def upload_youtube_cookies(
    request: Request,
    owner: Owner = Depends(require_cookie_writer),
    settings: Settings = Depends(get_settings),
) -> CookieUploadResult:
    """Accept a Netscape ``cookies.txt`` and persist it for the pipeline.

    The raw body (``text/plain``) is validated as a Netscape jar, then written
    atomically to ``Settings.ytdlp_cookies_file`` with a last-known-good
    snapshot. Cookie values are never logged or echoed."""
    target = (settings.ytdlp_cookies_file or "").strip()
    if not target:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="cookie store is not configured",
        )

    raw = await request.body()
    if len(raw) > MAX_COOKIE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="cookie payload too large",
        )
    if not raw.strip():
        raise HTTPException(status_code=422, detail="empty cookie payload")
    try:
        blob = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=422, detail="cookie payload must be UTF-8 text"
        ) from exc

    try:
        stats = validate_netscape_cookies(blob)
    except CookieValidationError as exc:
        # ``exc`` is value-free by construction (see cookies_store).
        raise HTTPException(
            status_code=422, detail=f"invalid Netscape cookie file: {exc}"
        ) from exc

    async with _COOKIE_WRITE_LOCK:
        kept = write_cookies_atomically(_PathLib(target), blob)

    # Log counts only — never values.
    _log.info(
        "youtube cookies rotated by %s: %d cookies (%d youtube), %d bytes",
        owner.state.value,
        stats.total,
        stats.youtube,
        len(raw),
    )
    return CookieUploadResult(
        accepted=True,
        cookies=stats.total,
        youtube_cookies=stats.youtube,
        bytes=len(raw),
        last_good_kept=kept,
    )


@router.get("/cookies/youtube", response_model=CookieStoreStatus, tags=["cookies"])
async def youtube_cookie_status(
    owner: Owner = Depends(require_cookie_writer),
    settings: Settings = Depends(get_settings),
) -> CookieStoreStatus:
    """Report jar presence/freshness metadata — never the cookie values."""
    target = (settings.ytdlp_cookies_file or "").strip()
    if not target:
        return CookieStoreStatus(configured=False, present=False)
    path = _PathLib(target)
    if not path.is_file():
        return CookieStoreStatus(
            configured=True,
            present=False,
            last_good_present=previous_path(path).is_file(),
        )
    stat = path.stat()
    modified = dt.datetime.fromtimestamp(stat.st_mtime, dt.UTC).isoformat()
    return CookieStoreStatus(
        configured=True,
        present=True,
        bytes=stat.st_size,
        modified_at=modified,
        last_good_present=previous_path(path).is_file(),
    )
