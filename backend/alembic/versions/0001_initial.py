"""Администраторы, сессии, журнал действий, настройки

Revision ID: 0001
Revises:
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("login", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("totp_secret", sa.String(64)),
        sa.Column(
            "totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
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
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "admin_id",
            sa.Integer(),
            sa.ForeignKey("admins.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("user_agent", sa.String(255)),
        sa.Column("ip", sa.String(45)),
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
    )
    op.create_index("ix_refresh_tokens_admin_id", "refresh_tokens", ["admin_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "admin_id", sa.Integer(), sa.ForeignKey("admins.id", ondelete="SET NULL")
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(128)),
        sa.Column("details", sa.JSON()),
        sa.Column("ip", sa.String(45)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_created_at_desc", "audit_log", ["created_at"])

    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text()),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
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
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_index("ix_audit_log_created_at_desc", "audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_refresh_tokens_admin_id", "refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("admins")
