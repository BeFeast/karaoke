"""add job stage note

Revision ID: 0003_job_stage_note
Revises: 0002_job_source_metadata
Create Date: 2026-06-10 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_job_stage_note"
down_revision: str | Sequence[str] | None = "0002_job_source_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("stage_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "stage_note")
