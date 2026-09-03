"""Скидка из промокода, автопродление на уровне ключа, возобновление рассылок

Revision ID: 0011
Revises: 0010
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_users") as batch:
        batch.add_column(
            sa.Column(
                "discount_percent", sa.Integer(), nullable=False, server_default="0"
            )
        )
    with op.batch_alter_table("bot_subscriptions") as batch:
        batch.add_column(
            sa.Column(
                "auto_renew", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
    with op.batch_alter_table("broadcasts") as batch:
        batch.add_column(sa.Column("cursor_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("broadcasts") as batch:
        batch.drop_column("heartbeat_at")
        batch.drop_column("cursor_user_id")
    with op.batch_alter_table("bot_subscriptions") as batch:
        batch.drop_column("auto_renew")
    with op.batch_alter_table("bot_users") as batch:
        batch.drop_column("discount_percent")
