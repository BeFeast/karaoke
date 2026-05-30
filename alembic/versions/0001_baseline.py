"""baseline: jobs, artifacts, extension_tokens

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-30 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JOB_STATUS = sa.Enum(
    "queued",
    "downloading",
    "separating",
    "transcribing",
    "completed",
    "failed",
    "cancelled",
    name="job_status",
)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_token", sa.String(64), nullable=False, unique=True),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("owner_email", sa.String(320), nullable=True),
        sa.Column("owner_display_name", sa.String(255), nullable=True),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("status", JOB_STATUS, nullable=False),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("vast_instance_id", sa.String(64), nullable=True),
        sa.Column("vast_cost_micros", sa.BigInteger, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_job_token", "jobs", ["job_token"], unique=True)
    op.create_index("ix_jobs_owner_subject", "jobs", ["owner_subject"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_owner_status", "jobs", ["owner_subject", "status"])

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer,
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("relative_path", sa.Text, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("content_type", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_job_id", "artifacts", ["job_id"])

    op.create_table(
        "extension_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("owner_subject", sa.String(255), nullable=False),
        sa.Column("owner_email", sa.String(320), nullable=True),
        sa.Column("owner_display_name", sa.String(255), nullable=True),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_extension_tokens_token_hash",
        "extension_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_extension_tokens_owner_subject",
        "extension_tokens",
        ["owner_subject"],
    )
    op.create_index(
        "ix_extension_tokens_disabled",
        "extension_tokens",
        ["disabled"],
    )


def downgrade() -> None:
    op.drop_index("ix_extension_tokens_disabled", table_name="extension_tokens")
    op.drop_index("ix_extension_tokens_owner_subject", table_name="extension_tokens")
    op.drop_index("ix_extension_tokens_token_hash", table_name="extension_tokens")
    op.drop_table("extension_tokens")

    op.drop_index("ix_artifacts_job_id", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("ix_jobs_owner_status", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_owner_subject", table_name="jobs")
    op.drop_index("ix_jobs_job_token", table_name="jobs")
    op.drop_table("jobs")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        JOB_STATUS.drop(bind, checkfirst=True)
