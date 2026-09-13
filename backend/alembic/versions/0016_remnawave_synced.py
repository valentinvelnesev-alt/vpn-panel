"""Флаг импорта существующих аккаунтов Remnawave по telegram_id

Revision ID: 0016
Revises: 0015
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_users") as batch:
        batch.add_column(
            sa.Column(
                "remnawave_synced", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("bot_users") as batch:
        batch.drop_column("remnawave_synced")
