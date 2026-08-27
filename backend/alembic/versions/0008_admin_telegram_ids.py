"""Telegram ID администраторов бота (для команды /admin)

Revision ID: 0008
Revises: 0007
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(
            sa.Column(
                "admin_telegram_ids",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("admin_telegram_ids")
