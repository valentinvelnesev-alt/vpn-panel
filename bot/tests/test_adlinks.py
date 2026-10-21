"""Рекламные ссылки: привязка, доля владельцу, статистика."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Config
from app.services import adlinks, wallet
from app.services import subscriptions as subs
from shared.db.base import Base
from shared.db.models import AdEarning, AdLink, EmojiMode


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def make_config(enabled: bool = True) -> Config:
    return Config(
        token="1:x",
        enabled=True,
        emoji_mode=EmojiMode.PLAIN,
        premium_emoji={},
        brand="Test",
        welcome_text=None,
        support_url=None,
        channel_url=None,
        channel_id=None,
        require_channel_sub=False,
        trial_enabled=True,
        trial_days=3,
        trial_squad_uuids=[],
        trial_hwid_limit=2,
        ad_links_enabled=enabled,
    )


async def make_link(db, **kwargs) -> AdLink:
    link = AdLink(
        name="YouTube",
        prefix="yt",
        percent=50,
        created_at=datetime.now(UTC),
        **kwargs,
    )
    db.add(link)
    await db.flush()
    return link


async def test_disabled_feature_ignores_everything(db) -> None:
    await make_link(db, owner_telegram_id=10)
    user = await subs.get_or_create_user(db, 1)
    assert not await adlinks.attach(db, make_config(False), user, "yt")
    assert await adlinks.owner_links(db, make_config(False), 10, None) == []


async def test_attach_only_new_users_first_touch(db) -> None:
    link = await make_link(db, owner_telegram_id=10)
    other = AdLink(name="TG", prefix="tg", percent=30, created_at=datetime.now(UTC))
    db.add(other)
    user = await subs.get_or_create_user(db, 1)
    await db.flush()

    assert await adlinks.attach(db, make_config(), user, "YT")
    assert user.ad_link_id == link.id
    # Второе касание ничего не меняет.
    assert not await adlinks.attach(db, make_config(), user, "tg")
    assert user.ad_link_id == link.id

    old = await subs.get_or_create_user(db, 2)
    old.trial_used = True
    assert not await adlinks.attach(db, make_config(), old, "yt")


async def test_commission_credited_to_owner_balance(db) -> None:
    link = await make_link(db, owner_telegram_id=10)
    owner = await subs.get_or_create_user(db, 10)
    buyer = await subs.get_or_create_user(db, 1)
    buyer.ad_link_id = link.id
    await db.flush()

    awarded = await adlinks.credit_commission(db, make_config(), buyer, 20000)
    assert [(u.telegram_id, s) for u, s in awarded] == [(10, 10000)]
    assert (await wallet.get_or_create(db, owner)).balance_kopeks == 10000


async def test_commission_waits_for_username_owner(db) -> None:
    link = await make_link(db, owner_username="Adman")
    buyer = await subs.get_or_create_user(db, 1)
    buyer.ad_link_id = link.id
    await db.flush()

    assert await adlinks.credit_commission(db, make_config(), buyer, 20000) == []

    # Владелец появился в боте — видит ссылку, и накопленное зачислено.
    owner = await subs.get_or_create_user(db, 77, username="adman")
    await db.flush()
    links = await adlinks.owner_links(db, make_config(), 77, "adman")
    assert [lk.id for lk in links] == [link.id]
    assert link.owner_telegram_id == 77
    assert (await wallet.get_or_create(db, owner)).balance_kopeks == 10000

    # Повторный заход не зачисляет второй раз.
    await adlinks.owner_links(db, make_config(), 77, "adman")
    assert (await wallet.get_or_create(db, owner)).balance_kopeks == 10000


async def test_stats(db) -> None:
    link = await make_link(db, owner_telegram_id=10)
    await subs.get_or_create_user(db, 10)
    buyer = await subs.get_or_create_user(db, 1)
    buyer.ad_link_id = link.id
    visitor = await subs.get_or_create_user(db, 2)
    visitor.ad_link_id = link.id
    await db.flush()
    await adlinks.credit_commission(db, make_config(), buyer, 10000)
    await adlinks.credit_commission(db, make_config(), buyer, 30000)

    [row] = await adlinks.stats(db, [link])
    assert (row.users, row.buyers, row.purchases) == (2, 1, 2)
    assert (row.turnover_kopeks, row.earned_kopeks) == (40000, 20000)


async def test_inactive_link_pays_nothing(db) -> None:
    link = await make_link(db, owner_telegram_id=10)
    link.is_active = False
    buyer = await subs.get_or_create_user(db, 1)
    buyer.ad_link_id = link.id
    await db.flush()
    assert await adlinks.credit_commission(db, make_config(), buyer, 10000) == []
    assert AdEarning  # модель импортируется


async def test_manual_mode_does_not_touch_balance(db) -> None:
    link = await make_link(db, owner_telegram_id=10, payout_mode="manual")
    owner = await subs.get_or_create_user(db, 10)
    buyer = await subs.get_or_create_user(db, 1)
    buyer.ad_link_id = link.id
    await db.flush()

    assert await adlinks.credit_commission(db, make_config(), buyer, 20000) == []
    assert (await wallet.get_or_create(db, owner)).balance_kopeks == 0
    [row] = await adlinks.stats(db, [link])
    assert row.owed_kopeks == 10000
    link.paid_out_kopeks = 10000
    assert row.owed_kopeks == 0
