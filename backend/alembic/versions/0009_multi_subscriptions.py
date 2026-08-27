"""Несколько подписок (ключей) на пользователя бота

Revision ID: 0009
Revises: 0008
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("bot_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("remnawave_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("subscription_url", sa.Text(), nullable=True),
        sa.Column("expire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("bot_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_bot_subscriptions_user_id", "bot_subscriptions", ["user_id"]
    )

    with op.batch_alter_table("bot_purchases") as batch:
        batch.add_column(sa.Column("subscription_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_bot_purchases_subscription_id",
            "bot_subscriptions",
            ["subscription_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("bot_payments") as batch:
        batch.add_column(sa.Column("subscription_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_bot_payments_subscription_id",
            "bot_subscriptions",
            ["subscription_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("bot_users") as batch:
        batch.add_column(sa.Column("auto_renew_subscription_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_bot_users_auto_renew_subscription_id",
            "bot_subscriptions",
            ["auto_renew_subscription_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Бэкфилл: у кого уже есть единственная (старая) подписка на BotUser —
    # заводим для неё запись в bot_subscriptions, чтобы «Мои подписки»
    # сразу показывал её в общем списке наравне с новыми покупками.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, remnawave_uuid, subscription_url, expire_at, "
            "auto_renew_enabled FROM bot_users WHERE remnawave_uuid IS NOT NULL"
        )
    ).fetchall()
    for user_id, remnawave_uuid, subscription_url, expire_at, auto_renew_enabled in rows:
        try:
            remnawave_id = int(remnawave_uuid)
        except (TypeError, ValueError):
            continue
        result = conn.execute(
            sa.text(
                "INSERT INTO bot_subscriptions "
                "(user_id, remnawave_id, username, subscription_url, expire_at, "
                "created_at, updated_at) "
                "VALUES (:user_id, :remnawave_id, :username, :subscription_url, "
                ":expire_at, now(), now()) RETURNING id"
            ),
            {
                "user_id": user_id,
                "remnawave_id": remnawave_id,
                "username": f"legacy_{user_id}",
                "subscription_url": subscription_url,
                "expire_at": expire_at,
            },
        )
        new_id = result.scalar_one()
        if auto_renew_enabled:
            conn.execute(
                sa.text(
                    "UPDATE bot_users SET auto_renew_subscription_id = :sub_id WHERE id = :user_id"
                ),
                {"sub_id": new_id, "user_id": user_id},
            )


def downgrade() -> None:
    with op.batch_alter_table("bot_users") as batch:
        batch.drop_constraint("fk_bot_users_auto_renew_subscription_id", type_="foreignkey")
        batch.drop_column("auto_renew_subscription_id")

    with op.batch_alter_table("bot_payments") as batch:
        batch.drop_constraint("fk_bot_payments_subscription_id", type_="foreignkey")
        batch.drop_column("subscription_id")

    with op.batch_alter_table("bot_purchases") as batch:
        batch.drop_constraint("fk_bot_purchases_subscription_id", type_="foreignkey")
        batch.drop_column("subscription_id")

    op.drop_index("ix_bot_subscriptions_user_id", table_name="bot_subscriptions")
    op.drop_table("bot_subscriptions")
