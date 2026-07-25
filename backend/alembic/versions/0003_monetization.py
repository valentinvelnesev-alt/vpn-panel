"""Кошелёк, платежи, промокоды, рефералы

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
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
    # batch_alter_table: на SQLite (тесты, dev) добавление колонки с внешним
    # ключом требует пересоздания таблицы; на Postgres (прод) это обычный ALTER.
    with op.batch_alter_table("bot_config") as batch:
        batch.add_column(
            sa.Column(
                "referral_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "referral_reward_days", sa.Integer(), nullable=False, server_default="3"
            )
        )
        batch.add_column(
            sa.Column(
                "referral_bonus_days", sa.Integer(), nullable=False, server_default="1"
            )
        )

    with op.batch_alter_table("bot_users") as batch:
        batch.add_column(sa.Column("referral_code", sa.String(16)))
        batch.add_column(sa.Column("referred_by_id", sa.Integer()))
        batch.add_column(
            sa.Column(
                "referral_reward_paid",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "auto_renew_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("auto_renew_plan_id", sa.Integer()))
        batch.create_unique_constraint(
            "uq_bot_users_referral_code", ["referral_code"]
        )
        batch.create_foreign_key(
            "fk_bot_users_referred_by_id",
            "bot_users",
            ["referred_by_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_bot_users_auto_renew_plan_id",
            "bot_plans",
            ["auto_renew_plan_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "bot_wallets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("bot_users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("balance_kopeks", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
    )

    op.create_table(
        "bot_wallet_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("bot_wallets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount_kopeks", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("description", sa.String(255)),
        *_timestamps(),
    )
    op.create_index(
        "ix_bot_wallet_transactions_wallet_id", "bot_wallet_transactions", ["wallet_id"]
    )

    op.create_table(
        "bot_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("bot_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("amount_kopeks", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column(
            "plan_id", sa.Integer(), sa.ForeignKey("bot_plans.id", ondelete="SET NULL")
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("raw_payload", sa.JSON()),
        *_timestamps(),
        sa.UniqueConstraint("provider", "external_id", name="uq_payment_external"),
    )
    op.create_index("ix_bot_payments_user_id", "bot_payments", ["user_id"])

    op.create_table(
        "bot_promo_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("bonus_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_uses", sa.Integer()),
        sa.Column("uses_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
    )

    op.create_table(
        "bot_promo_code_activations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "promo_code_id",
            sa.Integer(),
            sa.ForeignKey("bot_promo_codes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("bot_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("promo_code_id", "user_id", name="uq_promo_activation"),
    )

    op.create_table(
        "bot_referral_rewards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "referrer_user_id",
            sa.Integer(),
            sa.ForeignKey("bot_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "referred_user_id",
            sa.Integer(),
            sa.ForeignKey("bot_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("referred_user_id", name="uq_referral_reward_once"),
    )
    op.create_index(
        "ix_bot_referral_rewards_referrer", "bot_referral_rewards", ["referrer_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_bot_referral_rewards_referrer", "bot_referral_rewards")
    op.drop_table("bot_referral_rewards")
    op.drop_table("bot_promo_code_activations")
    op.drop_table("bot_promo_codes")
    op.drop_index("ix_bot_payments_user_id", "bot_payments")
    op.drop_table("bot_payments")
    op.drop_index(
        "ix_bot_wallet_transactions_wallet_id", "bot_wallet_transactions"
    )
    op.drop_table("bot_wallet_transactions")
    op.drop_table("bot_wallets")

    with op.batch_alter_table("bot_users") as batch:
        batch.drop_column("auto_renew_plan_id")
        batch.drop_column("auto_renew_enabled")
        batch.drop_column("referral_reward_paid")
        batch.drop_column("referred_by_id")
        batch.drop_column("referral_code")

    with op.batch_alter_table("bot_config") as batch:
        batch.drop_column("referral_bonus_days")
        batch.drop_column("referral_reward_days")
        batch.drop_column("referral_enabled")
