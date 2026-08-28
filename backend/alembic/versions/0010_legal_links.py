"""Ссылки на политику конфиденциальности и пользовательское соглашение

Revision ID: 0010
Revises: 0009
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(sa.Column("privacy_policy_url", sa.String(512), nullable=True))
        batch.add_column(sa.Column("terms_url", sa.String(512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("terms_url")
        batch.drop_column("privacy_policy_url")
