"""owner-scoped auth records and sharing

Revision ID: 0003_owner_scoped_auth
Revises: 0002_job_source_metadata
Create Date: 2026-06-05 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_owner_scoped_auth"
down_revision: str | Sequence[str] | None = "0002_job_source_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_owners_subject", "owners", ["subject"], unique=True)
    op.create_index("ix_owners_email", "owners", ["email"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("clerk_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_users_owner_id", "users", ["owner_id"])
    op.create_index("ix_users_clerk_subject", "users", ["clerk_subject"], unique=True)
    op.create_index("ix_users_email", "users", ["email"])

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("share_grants", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("vast_cost", sa.String(length=32), nullable=True))
        batch.create_index("ix_jobs_owner_id", ["owner_id"])
        batch.create_foreign_key(
            "fk_jobs_owner_id_owners",
            "owners",
            ["owner_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("extension_tokens") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.create_index("ix_extension_tokens_owner_id", ["owner_id"])
        batch.create_foreign_key(
            "fk_extension_tokens_owner_id_owners",
            "owners",
            ["owner_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("extension_tokens") as batch:
        batch.drop_constraint("fk_extension_tokens_owner_id_owners", type_="foreignkey")
        batch.drop_index("ix_extension_tokens_owner_id")
        batch.drop_column("owner_id")

    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_owner_id_owners", type_="foreignkey")
        batch.drop_index("ix_jobs_owner_id")
        batch.drop_column("vast_cost")
        batch.drop_column("share_grants")
        batch.drop_column("owner_id")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_clerk_subject", table_name="users")
    op.drop_index("ix_users_owner_id", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_owners_email", table_name="owners")
    op.drop_index("ix_owners_subject", table_name="owners")
    op.drop_table("owners")
