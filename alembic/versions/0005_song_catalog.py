"""Create the curated song catalog and optional owner links."""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_song_catalog"
down_revision = "0004_job_lyrics_rebuild"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_tracks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("artist", sa.Text, nullable=False),
        sa.Column("aliases", sa.JSON, nullable=False),
    )
    op.create_table(
        "catalog_recordings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("track_id", sa.Integer, sa.ForeignKey("catalog_tracks.id"), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("language", sa.String(12), nullable=False),
        sa.Column("genres", sa.JSON, nullable=False),
        sa.Column("genres_uncertain", sa.Boolean, nullable=False),
        sa.Column("collections", sa.JSON, nullable=False),
        sa.Column("decade", sa.Integer),
    )
    op.create_table(
        "catalog_sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("recording_id", sa.Integer, sa.ForeignKey("catalog_recordings.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("provenance", sa.JSON, nullable=False),
        sa.Column("available", sa.Boolean, nullable=False),
        sa.Column("metadata_allowed", sa.Boolean, nullable=False),
        sa.Column("playback_allowed", sa.Boolean, nullable=False),
        sa.UniqueConstraint("provider", "external_id"),
    )
    op.create_table(
        "catalog_assets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("catalog_sources.id"), nullable=False),
        sa.Column("pipeline_version", sa.String(32), nullable=False),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("lyric_revision", sa.String(64)),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("download_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("reserved_micros", sa.BigInteger, nullable=False),
        sa.Column("charged_micros", sa.BigInteger),
        sa.UniqueConstraint("source_id", "pipeline_version"),
    )
    op.create_table(
        "catalog_pilot_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("paused", sa.Boolean, nullable=False),
    )
    op.add_column("jobs", sa.Column("catalog_asset_id", sa.Integer))
    op.create_foreign_key(
        "fk_jobs_catalog_asset_id", "jobs", "catalog_assets", ["catalog_asset_id"], ["id"]
    )
    op.create_index("ix_jobs_catalog_asset_id", "jobs", ["catalog_asset_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_catalog_asset_id", table_name="jobs")
    op.drop_constraint("fk_jobs_catalog_asset_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "catalog_asset_id")
    op.drop_table("catalog_pilot_state")
    op.drop_table("catalog_assets")
    op.drop_table("catalog_sources")
    op.drop_table("catalog_recordings")
    op.drop_table("catalog_tracks")
