"""Пакеты докупаемого трафика

Revision ID: 0013
Revises: 0012
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_traffic_packages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("traffic_gb", sa.Integer(), nullable=False),
        sa.Column("price_kopeks", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("bot_payments") as batch:
        batch.add_column(sa.Column("traffic_package_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_bot_payments_traffic_package_id",
            "bot_traffic_packages",
            ["traffic_package_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("bot_payments") as batch:
        batch.drop_constraint("fk_bot_payments_traffic_package_id", type_="foreignkey")
        batch.drop_column("traffic_package_id")
    op.drop_table("bot_traffic_packages")
