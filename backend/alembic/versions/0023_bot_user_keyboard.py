"""Какая клавиатура «Меню / Помощь» уже стоит у клиента

Revision ID: 0023
Revises: 0022
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_users") as batch:
        batch.add_column(sa.Column("keyboard_state", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bot_users") as batch:
        batch.drop_column("keyboard_state")
