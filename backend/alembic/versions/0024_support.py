"""Поддержка через бота: настройки и обращения

Revision ID: 0024
Revises: 0023
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(
            sa.Column("support_mode", sa.String(16), nullable=False, server_default="link")
        )
        batch.add_column(sa.Column("support_token_encrypted", sa.Text(), nullable=True))
        batch.add_column(sa.Column("support_bot_username", sa.String(64), nullable=True))
        batch.add_column(sa.Column("support_bot_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("support_chat_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("support_admin_ids", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("support_welcome", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("support_state", sa.String(16), nullable=False, server_default="stopped")
        )
        batch.add_column(sa.Column("support_state_message", sa.Text(), nullable=True))
        batch.add_column(sa.Column("support_seen_chat_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("support_seen_chat_title", sa.String(255), nullable=True))

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_support_tickets_user", "support_tickets", ["user_telegram_id"])
    op.create_index("ix_support_tickets_thread", "support_tickets", ["chat_id", "thread_id"])


def downgrade() -> None:
    op.drop_index("ix_support_tickets_thread", table_name="support_tickets")
    op.drop_index("ix_support_tickets_user", table_name="support_tickets")
    op.drop_table("support_tickets")
    with op.batch_alter_table("bot_config") as batch:
        for column in (
            "support_seen_chat_title",
            "support_seen_chat_id",
            "support_state_message",
            "support_state",
            "support_welcome",
            "support_admin_ids",
            "support_chat_id",
            "support_bot_id",
            "support_bot_username",
            "support_token_encrypted",
            "support_mode",
        ):
            batch.drop_column(column)
