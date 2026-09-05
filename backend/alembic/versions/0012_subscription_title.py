"""Снимок названия тарифа и явный признак пробного ключа

Revision ID: 0012
Revises: 0011
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bot_subscriptions") as batch:
        batch.add_column(sa.Column("title", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("is_trial", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("bot_purchases") as batch:
        batch.add_column(sa.Column("plan_title", sa.String(64), nullable=True))

    # Названия берём у ещё живых тарифов; для удалённых имя восстановить
    # неоткуда — там останется общее «Подписка».
    op.execute(
        """
        UPDATE bot_subscriptions SET title = (
            SELECT title FROM bot_plans WHERE bot_plans.id = bot_subscriptions.plan_id
        ) WHERE plan_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE bot_purchases SET plan_title = (
            SELECT title FROM bot_plans WHERE bot_plans.id = bot_purchases.plan_id
        ) WHERE plan_id IS NOT NULL
        """
    )
    # Пробный — тот ключ, по которому была выдача с источником trial и не
    # было ни одной платной покупки.
    op.execute(
        """
        UPDATE bot_subscriptions SET is_trial = true
        WHERE EXISTS (
            SELECT 1 FROM bot_purchases p
            WHERE p.subscription_id = bot_subscriptions.id AND p.source = 'trial'
        ) AND NOT EXISTS (
            SELECT 1 FROM bot_purchases p2
            WHERE p2.subscription_id = bot_subscriptions.id AND p2.amount_kopeks > 0
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("bot_purchases") as batch:
        batch.drop_column("plan_title")
    with op.batch_alter_table("bot_subscriptions") as batch:
        batch.drop_column("is_trial")
        batch.drop_column("title")
