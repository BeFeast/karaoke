"""HTTP and WebSocket routes for the karaoke API."""
from __future__ import annotations

import asyncio
import contextlib
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

from karaoke.api.auth import (
    AuthState,
    Owner,
    is_trusted_lan_request,
    require_owner,
    resolve_owner,
)
from karaoke.config import Settings, get_settings
from karaoke.db.models import Job, JobStatus
from karaoke.db.session import get_session, get_session_factory
from karaoke.worker.scheduler import schedule_job

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class JobCreate(BaseModel):
    """Body for ``POST /jobs`` — submit a URL for separation."""

    url: str = Field(min_length=1)
    title: str | None = None


class JobOut(BaseModel):
    """Owner-visible job projection."""

    id: int
    job_token: str
    source_url: str
    title: str | None
    status: JobStatus
    progress: int
    error: str | None
    share_url: str
    owner_subject: str

    @classmethod
    def from_orm_job(cls, job: Job, *, public_base_url: str) -> JobOut:
        share = public_base_url.rstrip("/") + f"/share/{job.job_token}"
        return cls(
            id=job.id,
            job_token=job.job_token,
            source_url=job.source_url,
            title=job.title,
            status=job.status,
            progress=job.progress,
            error=job.error,
            share_url=share,
            owner_subject=job.owner_subject,
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
    status: JobStatus
    progress: int
    owner_display_name: str | None
    artifacts: list[ArtifactOut]


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
    """Liveness probe — no auth, no DB hit."""
    return {"status": "ok"}


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
    await session.refresh(job)

    # Dispatch to the real vast.ai worker, or the in-process mock when no
    # vast key is configured (CI / dev default). See worker.scheduler.
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
    job = await session.get(Job, job_id)
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
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
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
    job = await session.get(Job, job_id)
    if job is None or not _can_owner_view(owner, job):
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409, detail=f"job is already {job.status.value}"
        )
    job.status = JobStatus.cancelled
    await session.commit()
    await session.refresh(job)
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
    return Response(status_code=status.HTTP_204_NO_CONTENT)


_SHARE_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title} — karaoke</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
   max-width: 720px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
 h1 {{ font-size: 1.4rem; margin: 0 0 0.25rem; }}
 .meta {{ color: #888; font-size: 0.85rem; margin-bottom: 1.5rem; }}
 section {{ margin: 1.25rem 0; }}
 section h2 {{ font-size: 1rem; margin: 0 0 0.4rem; }}
 audio {{ width: 100%; }}
 pre {{ white-space: pre-wrap; word-wrap: break-word; background: #f6f6f6;
   padding: 0.75rem; border-radius: 4px; max-height: 50vh; overflow: auto;
   font-size: 0.85rem; }}
 @media (prefers-color-scheme: dark) {{ pre {{ background: #1f1f1f; }} }}
 .empty {{ color: #888; font-style: italic; }}
</style>
</head>
<body>
<h1>{title_escaped}</h1>
<div class="meta">
  status: <strong>{status}</strong>
  · progress: {progress}%
  {owner_block}
</div>
{karaoke_block}
{vocals_block}
{lyrics_block}
</body>
</html>
"""


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
    owner_block = f"· owner: {_html_escape(owner)}" if owner else ""
    base = f"/share/{job.job_token}"
    artifacts_by_kind = {a.kind: a for a in job.artifacts}

    def audio_block(kind: str, label: str) -> str:
        if kind not in artifacts_by_kind:
            return f'<section><h2>{label}</h2><div class="empty">not yet available</div></section>'
        return (
            f'<section><h2>{label}</h2>'
            f'<audio controls preload="none" src="{base}/{kind}.mp3"></audio>'
            f'</section>'
        )

    if lyrics_text:
        lyrics_block = (
            f'<section><h2>lyrics</h2>'
            f'<pre>{_html_escape(lyrics_text)}</pre></section>'
        )
    elif "lyrics" in artifacts_by_kind:
        lyrics_block = (
            f'<section><h2>lyrics</h2>'
            f'<p><a href="{base}/lyrics.txt">open lyrics.txt</a></p></section>'
        )
    else:
        lyrics_block = '<section><h2>lyrics</h2><div class="empty">not yet available</div></section>'

    return _SHARE_HTML_TEMPLATE.format(
        title=_html_escape(title)[:80],
        title_escaped=_html_escape(title),
        status=job.status.value,
        progress=job.progress,
        owner_block=owner_block,
        karaoke_block=audio_block("karaoke", "karaoke (instrumental)"),
        vocals_block=audio_block("vocals", "vocals only"),
        lyrics_block=lyrics_block,
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
            status=job.status,
            progress=job.progress,
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


_ALLOWED_ARTIFACTS: dict[str, tuple[str, str]] = {
    # name -> (relative path under exports/, content-type)
    "karaoke.mp3": ("karaoke.mp3", "audio/mpeg"),
    "vocals.mp3": ("vocals.mp3", "audio/mpeg"),
    "lyrics.txt": ("lyrics.txt", "text/plain; charset=utf-8"),
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
    terminal = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}

    try:
        while True:
            async with factory() as session:
                job = await session.get(Job, job_id)
            if job is None:
                await websocket.send_json({"error": "job not found"})
                break
            if job.status != last_status or job.progress != last_progress:
                await websocket.send_json(
                    {
                        "job_id": job.id,
                        "status": job.status.value,
                        "progress": job.progress,
                        "error": job.error,
                    }
                )
                last_status = job.status
                last_progress = job.progress
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
