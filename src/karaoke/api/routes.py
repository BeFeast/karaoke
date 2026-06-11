"""HTTP and WebSocket routes for the karaoke API."""
from __future__ import annotations

import asyncio
import contextlib
import secrets

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
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
from karaoke.api.worker_stub import schedule_mock_job
from karaoke.config import Settings, get_settings
from karaoke.db.models import Job, JobStatus
from karaoke.db.session import get_session, get_session_factory

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


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------


router = APIRouter()


@router.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe — no auth, no DB hit."""
    return {"status": "ok"}


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

    # Mocked worker — no real vast.ai provisioning.
    schedule_mock_job(get_session_factory(), job.id)

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


@router.get("/api/share/{job_token}", response_model=SharePayload, tags=["share"])
async def share_payload(
    job_token: str,
    request: Request,
    owner: Owner | None = Depends(resolve_owner),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SharePayload:
    """Owner-aware + unlisted-token-aware share endpoint (JSON).

    The HTML share page lives at ``GET /share/{job_token}`` (see
    ``karaoke.web.views``); this endpoint returns the same data as
    structured JSON for API clients.

    - The owner of the job can always see it.
    - Anyone holding the unlisted ``job_token`` can see it.
    - Trusted-LAN callers (machine viewers) can see it.
    Otherwise, 404.
    """
    job = await session.scalar(
        select(Job)
        .where(Job.job_token == job_token)
        .options(selectinload(Job.artifacts))
    )
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    holds_unlisted_token = True  # path itself proves possession of the token
    is_owner = owner is not None and _can_owner_view(owner, job)
    is_lan = is_trusted_lan_request(request, settings)
    if not (holds_unlisted_token or is_owner or is_lan):  # pragma: no cover
        raise HTTPException(status_code=404, detail="job not found")

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
