"""SQLAlchemy ORM models for karaoke."""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for karaoke ORM models."""


class JobStatus(enum.StrEnum):
    """Lifecycle for a karaoke separation job."""

    queued = "queued"
    downloading = "downloading"
    separating = "separating"
    transcribing = "transcribing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Job(Base):
    """A karaoke job: one URL → vocals + instrumental + lyrics."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Public, unguessable token used by /share/{job_token} for unlisted access.
    job_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # Owner attribution — Clerk subject is the canonical identity.
    owner_subject: Mapped[str] = mapped_column(String(255), index=True)
    owner_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    owner_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Source music metadata (lyrics-lookup foundation). Populated during the
    # download stage from the yt-dlp info dict; ``artist``/``track`` fall back
    # to parsing the video title. ``duration`` is the source length in seconds.
    artist: Mapped[str | None] = mapped_column(Text, nullable=True)
    track: Mapped[str | None] = mapped_column(Text, nullable=True)
    album: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"),
        default=JobStatus.queued,
        index=True,
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0..100
    stage_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # vast.ai bookkeeping (mocked in this skeleton).
    vast_instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vast_cost_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_jobs_owner_status", "owner_subject", "status"),
    )


class Artifact(Base):
    """Output file produced by a job (vocals.mp3, karaoke.mp3, lyrics.txt, …)."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32))  # "vocals" | "karaoke" | "lyrics" | ...
    relative_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    job: Mapped[Job] = relationship(back_populates="artifacts")


class ExtensionToken(Base):
    """A ``ktx_…`` token issued to a Chrome-extension install."""

    __tablename__ = "extension_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # SHA-256 of the raw token; we never persist the raw value.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # Owner attribution — same shape as Job.
    owner_subject: Mapped[str] = mapped_column(String(255), index=True)
    owner_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    owner_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
