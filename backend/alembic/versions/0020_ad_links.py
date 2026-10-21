"""Рекламные ссылки с процентом от покупок

Revision ID: 0020
Revises: 0019
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_ad_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("prefix", sa.String(64), nullable=False, unique=True),
        sa.Column("percent", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("owner_username", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bot_ad_links_owner_telegram_id", "bot_ad_links", ["owner_telegram_id"])
    op.create_table(
        "bot_ad_earnings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ad_link_id",
            sa.Integer(),
            sa.ForeignKey("bot_ad_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "buyer_user_id",
            sa.Integer(),
            sa.ForeignKey("bot_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount_kopeks", sa.Integer(), nullable=False),
        sa.Column("commission_kopeks", sa.Integer(), nullable=False),
        sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bot_ad_earnings_ad_link_id", "bot_ad_earnings", ["ad_link_id"])
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(
            sa.Column("ad_links_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("bot_users") as batch:
        batch.add_column(sa.Column("ad_link_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_bot_users_ad_link_id", "bot_ad_links", ["ad_link_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_bot_users_ad_link_id", ["ad_link_id"])


def downgrade() -> None:
    with op.batch_alter_table("bot_users") as batch:
        batch.drop_index("ix_bot_users_ad_link_id")
        batch.drop_constraint("fk_bot_users_ad_link_id", type_="foreignkey")
        batch.drop_column("ad_link_id")
    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("ad_links_enabled")
    op.drop_table("bot_ad_earnings")
    op.drop_table("bot_ad_links")
