"""Картинка над экранами бота

Revision ID: 0015
Revises: 0014
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(sa.Column("menu_photo", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("menu_photo")
