"""add job lyrics rebuild bookkeeping

Revision ID: 0004_job_lyrics_rebuild
Revises: 0003_job_stage_note
Create Date: 2026-09-29 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_job_lyrics_rebuild"
down_revision: str | Sequence[str] | None = "0003_job_stage_note"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("lyrics_rebuilt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("lyrics_rebuild_cost_micros", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "lyrics_rebuild_cost_micros")
    op.drop_column("jobs", "lyrics_rebuilt_at")
