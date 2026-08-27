"""Денежная 2-уровневая реферальная комиссия + уведомления о продажах

Revision ID: 0006
Revises: 0005
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(
            sa.Column(
                "referral_commission_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "referral_level1_percent",
                sa.Integer(),
                nullable=False,
                server_default="25",
            )
        )
        batch.add_column(
            sa.Column(
                "referral_level2_percent",
                sa.Integer(),
                nullable=False,
                server_default="5",
            )
        )
        batch.add_column(sa.Column("purchase_notify_chat_id", sa.BigInteger()))


def downgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("purchase_notify_chat_id")
        batch.drop_column("referral_level2_percent")
        batch.drop_column("referral_level1_percent")
        batch.drop_column("referral_commission_enabled")
