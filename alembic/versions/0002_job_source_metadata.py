"""job source metadata: artist, track, album, duration

Adds nullable source-music metadata columns to ``jobs`` so lyrics can later
be looked up by artist/track. All columns are nullable with no server default
(back-filled lazily by the download stage), so the migration is safe on a
populated table.

Revision ID: 0002_job_source_metadata
Revises: 0001_baseline
Create Date: 2026-06-01 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_job_source_metadata"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("artist", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("track", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("album", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("duration", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "duration")
    op.drop_column("jobs", "album")
    op.drop_column("jobs", "track")
    op.drop_column("jobs", "artist")
