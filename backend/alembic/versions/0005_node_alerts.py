"""Алерты о падении нод

Revision ID: 0005
Revises: 0004
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(
            sa.Column(
                "node_alerts_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("node_alerts_chat_id", sa.BigInteger()))


def downgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("node_alerts_chat_id")
        batch.drop_column("node_alerts_enabled")
