"""SOCKS5-прокси для связи бота с Telegram

Revision ID: 0019
Revises: 0018
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(sa.Column("proxy_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("proxy_encrypted")
