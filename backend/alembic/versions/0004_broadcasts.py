"""Рассылки

Revision ID: 0004
Revises: 0003
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("photo_url", sa.String(512)),
        sa.Column("buttons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("segment", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="scheduled"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "total_recipients", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by_admin_id",
            sa.Integer(),
            sa.ForeignKey("admins.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_broadcasts_status_scheduled_at", "broadcasts", ["status", "scheduled_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_broadcasts_status_scheduled_at", "broadcasts")
    op.drop_table("broadcasts")
