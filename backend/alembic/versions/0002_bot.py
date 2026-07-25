"""Telegram-бот: конфигурация, тарифы, пользователи, покупки, напоминания

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "bot_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_encrypted", sa.Text()),
        sa.Column("bot_username", sa.String(64)),
        sa.Column("bot_name", sa.String(128)),
        sa.Column("bot_id", sa.BigInteger()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state", sa.String(16), nullable=False, server_default="stopped"),
        sa.Column("state_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("emoji_mode", sa.String(16), nullable=False, server_default="plain"),
        sa.Column("premium_checked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "premium_available", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("premium_emoji", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("welcome_text", sa.Text()),
        sa.Column("support_url", sa.String(255)),
        sa.Column("channel_url", sa.String(255)),
        sa.Column("channel_id", sa.String(64)),
        sa.Column(
            "require_channel_sub",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "trial_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("trial_days", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "trial_squad_uuids", sa.JSON(), nullable=False, server_default="[]"
        ),
        sa.Column("trial_hwid_limit", sa.Integer(), nullable=False, server_default="3"),
        *_timestamps(),
    )
    # Конфигурация единственная — создаём строку сразу, чтобы панель и бот
    # читали её без «а вдруг ещё нет».
    op.execute("INSERT INTO bot_config (id) VALUES (1)")

    op.create_table(
        "bot_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("price_kopeks", sa.Integer(), nullable=False),
        sa.Column("squad_uuids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("hwid_limit", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "traffic_limit_bytes", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
    )

    op.create_table(
        "bot_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("username", sa.String(64)),
        sa.Column("first_name", sa.String(128)),
        sa.Column("language_code", sa.String(8)),
        sa.Column("remnawave_uuid", sa.String(64)),
        sa.Column("subscription_url", sa.Text()),
        sa.Column("expire_at", sa.DateTime(timezone=True)),
        sa.Column("trial_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "has_stopped_bot", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_bot_users_telegram_id", "bot_users", ["telegram_id"])

    op.create_table(
        "bot_purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("bot_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "plan_id", sa.Integer(), sa.ForeignKey("bot_plans.id", ondelete="SET NULL")
        ),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("amount_kopeks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("expire_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_bot_purchases_user_id", "bot_purchases", ["user_id"])

    op.create_table(
        "bot_expiry_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("bot_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("window", sa.String(16), nullable=False),
        sa.Column("expire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "window", "expire_at", name="uq_expiry_notification"
        ),
    )


def downgrade() -> None:
    op.drop_table("bot_expiry_notifications")
    op.drop_index("ix_bot_purchases_user_id", "bot_purchases")
    op.drop_table("bot_purchases")
    op.drop_index("ix_bot_users_telegram_id", "bot_users")
    op.drop_table("bot_users")
    op.drop_table("bot_plans")
    op.drop_table("bot_config")
