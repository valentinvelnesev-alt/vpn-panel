"""uuid ключа Remnawave для панелей старее 2.9

Revision ID: 0014
Revises: 0013
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_subscriptions") as batch:
        batch.add_column(sa.Column("remnawave_uuid", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bot_subscriptions") as batch:
        batch.drop_column("remnawave_uuid")
