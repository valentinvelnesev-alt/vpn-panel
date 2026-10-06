"""Раскладка и цвета кнопок меню бота, кнопка «Меню» слева от поля ввода

Revision ID: 0018
Revises: 0017
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(
            sa.Column("menu_columns", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("menu_styles", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column("menu_mode", sa.String(16), nullable=False, server_default="keyboard")
        )


def downgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("menu_mode")
        batch.drop_column("menu_styles")
        batch.drop_column("menu_columns")
