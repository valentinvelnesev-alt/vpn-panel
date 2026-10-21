"""Режим выплаты рекламной ссылки и учёт выплаченного

Revision ID: 0021
Revises: 0020
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_ad_links") as batch:
        batch.add_column(
            sa.Column("payout_mode", sa.String(16), nullable=False, server_default="balance")
        )
        batch.add_column(
            sa.Column("paid_out_kopeks", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("bot_ad_links") as batch:
        batch.drop_column("paid_out_kopeks")
        batch.drop_column("payout_mode")
