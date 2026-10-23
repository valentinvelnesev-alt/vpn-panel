"""Свои подписи кнопок и тексты сообщений бота

Revision ID: 0025
Revises: 0024
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(sa.Column("menu_labels", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("bot_texts", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("bot_texts")
        batch.drop_column("menu_labels")
