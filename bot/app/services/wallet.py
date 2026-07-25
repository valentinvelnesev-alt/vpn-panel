"""Внутренний баланс пользователя — в копейках.

Каждое движение записывается в WalletTransaction: панель и пользователь
видят не только текущую цифру, но и откуда она взялась.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db.models import BotUser, Wallet, WalletTransaction, WalletTxType


class InsufficientFunds(Exception):
    pass


async def get_or_create(db: AsyncSession, user: BotUser) -> Wallet:
    wallet = await db.scalar(select(Wallet).where(Wallet.user_id == user.id))
    if wallet is None:
        wallet = Wallet(user_id=user.id, balance_kopeks=0)
        db.add(wallet)
        await db.flush()
    return wallet


async def credit(
    db: AsyncSession,
    user: BotUser,
    amount_kopeks: int,
    type_: WalletTxType,
    description: str | None = None,
) -> Wallet:
    if amount_kopeks <= 0:
        raise ValueError("сумма пополнения должна быть положительной")
    wallet = await get_or_create(db, user)
    wallet.balance_kopeks += amount_kopeks
    db.add(
        WalletTransaction(
            wallet_id=wallet.id,
            amount_kopeks=amount_kopeks,
            type=type_,
            description=description,
        )
    )
    return wallet


async def debit(
    db: AsyncSession,
    user: BotUser,
    amount_kopeks: int,
    type_: WalletTxType,
    description: str | None = None,
) -> Wallet:
    if amount_kopeks <= 0:
        raise ValueError("сумма списания должна быть положительной")
    wallet = await get_or_create(db, user)
    if wallet.balance_kopeks < amount_kopeks:
        raise InsufficientFunds(
            f"на балансе {wallet.balance_kopeks / 100:.2f} ₽, "
            f"нужно {amount_kopeks / 100:.2f} ₽"
        )
    wallet.balance_kopeks -= amount_kopeks
    db.add(
        WalletTransaction(
            wallet_id=wallet.id,
            amount_kopeks=-amount_kopeks,
            type=type_,
            description=description,
        )
    )
    return wallet
