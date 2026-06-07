"""Pydantic schemas for the public coordinator API."""
from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from karaoke.db.models import Job, JobStatus


class JobCreate(BaseModel):
    """Body for ``POST /jobs``."""

    url: str = Field(min_length=1)
    profile: str | None = None
    lyrics_mode: str | None = None
    keep_full_stems: bool = False
    title: str | None = None
    youtube_cookies: str | None = None


class HealthOut(BaseModel):
    ok: bool
    service: str
    redis: bool
    db: bool
    device_mode: str
    vast_configured: bool


class JobArtifactOut(BaseModel):
    """A ready output file, projected for the SPA's "what's available" UI."""

    kind: str
    name: str
    size: int | None


class JobSubmitOut(BaseModel):
    """Submit response for ``POST /jobs``.

    Keeps the newer PRD fields (``job_id``/``share``) and the existing SPA
    projection in one response so older clients continue to work.
    """

    job_id: int
    status: JobStatus
    share: str
    id: int
    job_token: str
    source_url: str
    title: str | None
    artist: str | None
    track: str | None
    progress: int
    error: str | None
    share_url: str
    owner_subject: str
    artifacts: list[JobArtifactOut]

    @classmethod
    def from_job(cls, job: Job, *, public_base_url: str) -> JobSubmitOut:
        share = public_base_url.rstrip("/") + f"/share/{job.id}/{job.job_token}"
        return cls(
            job_id=job.id,
            status=job.status,
            share=share,
            id=job.id,
            job_token=job.job_token,
            source_url=job.source_url,
            title=job.title,
            artist=job.artist,
            track=job.track,
            progress=job.progress,
            error=job.error,
            share_url=share,
            owner_subject=job.owner_subject,
            artifacts=[
                JobArtifactOut(
                    kind=a.kind,
                    name=a.relative_path.rsplit("/", 1)[-1],
                    size=a.size_bytes,
                )
                for a in job.artifacts
            ],
        )


class JobStatusOut(BaseModel):
    """Full lifecycle status payload for ``GET /status/{job_id}``."""

    job_id: int
    status: JobStatus
    stage: JobStatus
    created_at: dt.datetime
    updated_at: dt.datetime
    completed_at: dt.datetime | None
    error: str | None
    device: str
    vast_instance_id: str | None
    vast_cost: float | None
    share: str

    source_url: str
    title: str | None
    artist: str | None
    track: str | None
    progress: int
    artifacts: list[JobArtifactOut]

    @classmethod
    def from_job(cls, job: Job, *, public_base_url: str, device: str) -> JobStatusOut:
        share = public_base_url.rstrip("/") + f"/share/{job.id}/{job.job_token}"
        vast_cost = (
            None
            if job.vast_cost_micros is None
            else round(job.vast_cost_micros / 1_000_000, 6)
        )
        return cls(
            job_id=job.id,
            status=job.status,
            stage=job.status,
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
            error=job.error,
            device=device,
            vast_instance_id=job.vast_instance_id,
            vast_cost=vast_cost,
            share=share,
            source_url=job.source_url,
            title=job.title,
            artist=job.artist,
            track=job.track,
            progress=job.progress,
            artifacts=[
                JobArtifactOut(
                    kind=a.kind,
                    name=a.relative_path.rsplit("/", 1)[-1],
                    size=a.size_bytes,
                )
                for a in job.artifacts
            ],
        )
