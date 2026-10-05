"""Персональные тарифы, общая скидка, скидка по промокоду, настройки меню бота

Revision ID: 0011
Revises: 0010
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(
            sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("discount_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "allow_multiple_subscriptions",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("menu_hidden", sa.JSON(), nullable=False, server_default="[]")
        )

    with op.batch_alter_table("bot_plans") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_bot_plans_owner_user_id",
            "bot_users",
            ["owner_user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_bot_plans_owner_user_id", ["owner_user_id"])

    with op.batch_alter_table("bot_users") as batch:
        batch.add_column(
            sa.Column(
                "pending_discount_percent", sa.Integer(), nullable=False, server_default="0"
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("bot_users") as batch:
        batch.drop_column("pending_discount_percent")

    with op.batch_alter_table("bot_plans") as batch:
        batch.drop_index("ix_bot_plans_owner_user_id")
        batch.drop_constraint("fk_bot_plans_owner_user_id", type_="foreignkey")
        batch.drop_column("owner_user_id")

    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("menu_hidden")
        batch.drop_column("allow_multiple_subscriptions")
        batch.drop_column("discount_until")
        batch.drop_column("discount_percent")
