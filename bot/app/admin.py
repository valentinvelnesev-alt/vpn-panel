"""Админ-панель прямо в Telegram: команда /admin со статистикой.

Отдельно от веб-панели — быстрый обзор с телефона, без захода в браузер.
Управление тарифами, промокодами и рассылками остаётся в веб-панели: не
дублируем одну и ту же логику в двух местах, только читаем те же таблицы.

Кто видит /admin определяется списком `admin_telegram_ids` в настройках
бота (вкладка «Бот» в панели) — это не то же самое, что администраторы
самой веб-панели.
"""

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config
from shared.db.models import (
    Broadcast,
    BotUser,
    ExpiryNotification,
    Payment,
    PaymentStatus,
    PromoCode,
    PromoCodeActivation,
    ReferralReward,
    Wallet,
    WalletTransaction,
    WalletTxType,
)
from shared.db.session import session

router = Router()


def _is_admin(config: Config, telegram_id: int) -> bool:
    return telegram_id in config.admin_telegram_ids


def _fmt_rub(kopeks: int) -> str:
    return f"{kopeks / 100:,.2f}".replace(",", " ") + " ₽"


def _back_keyboard(callback_data: str = "admin_stats") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data=callback_data)]]
    )


def _stats_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💰 Финансы", callback_data="admin_stats_finance")],
            [InlineKeyboardButton(text="👥 Рефералы", callback_data="admin_stats_referrals")],
            [InlineKeyboardButton(text="📢 Рассылки", callback_data="admin_stats_broadcasts:0")],
            [InlineKeyboardButton(text="🔔 Уведомления", callback_data="admin_stats_notifications")],
            [InlineKeyboardButton(text="Закрыть", callback_data="admin_close")],
        ]
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, config: Config) -> None:
    if not _is_admin(config, message.from_user.id):
        return  # молча игнорируем — не выдаём, что команда вообще существует
    await message.answer(
        "🛠 <b>Админ-панель</b>\n\nВыберите раздел:", reply_markup=_stats_menu_keyboard()
    )


@router.callback_query(F.data == "admin_close")
async def cb_admin_close(callback: CallbackQuery) -> None:
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery, config: Config) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()

    async with session() as db:
        overview = await _overview_stats(db)

    text = (
        "📊 <b>Статистика бота — обзор</b>\n\n"
        f"👥 Пользователей всего: <b>{overview['total']}</b>\n"
        f"🆕 За 24ч / 7 дней: <b>{overview['last_24h']}</b> / <b>{overview['last_7d']}</b>\n"
        f"🎁 Брали пробный период: <b>{overview['trial_used']}</b>\n"
        f"🔗 Пришли по рефералке: <b>{overview['with_referrer']}</b>\n"
        f"✅ С активной подпиской сейчас: <b>{overview['active_now']}</b>\n\n"
        "Выберите раздел для подробностей:"
    )
    await callback.message.edit_text(text, reply_markup=_stats_menu_keyboard())
    await callback.answer()


async def _overview_stats(db: AsyncSession) -> dict:
    now = datetime.now(UTC)
    total = await db.scalar(select(func.count()).select_from(BotUser)) or 0
    last_24h = await db.scalar(
        select(func.count())
        .select_from(BotUser)
        .where(BotUser.created_at >= now - timedelta(hours=24))
    ) or 0
    last_7d = await db.scalar(
        select(func.count())
        .select_from(BotUser)
        .where(BotUser.created_at >= now - timedelta(days=7))
    ) or 0
    trial_used = await db.scalar(
        select(func.count()).select_from(BotUser).where(BotUser.trial_used.is_(True))
    ) or 0
    with_referrer = await db.scalar(
        select(func.count()).select_from(BotUser).where(BotUser.referred_by_id.is_not(None))
    ) or 0
    active_now = await db.scalar(
        select(func.count()).select_from(BotUser).where(BotUser.expire_at > now)
    ) or 0
    return {
        "total": total,
        "last_24h": last_24h,
        "last_7d": last_7d,
        "trial_used": trial_used,
        "with_referrer": with_referrer,
        "active_now": active_now,
    }


@router.callback_query(F.data == "admin_stats_finance")
async def cb_admin_finance(callback: CallbackQuery, config: Config) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()

    async with session() as db:
        s = await _finance_stats(db)

    text = (
        "💰 <b>Финансы</b>\n\n"
        f"👛 Кошельков с балансом: <b>{s['wallets_count']}</b>\n"
        f"👛 Сумма на всех балансах: <b>{_fmt_rub(s['wallets_total'])}</b>\n\n"
        f"⬆️ Пополнений всего: <b>{s['topup_count']}</b> на <b>{_fmt_rub(s['topup_total'])}</b>\n"
        f"⬆️ Пополнено за 24ч: <b>{_fmt_rub(s['topup_24h'])}</b>\n"
        f"⬆️ Пополнено за 7 дней: <b>{_fmt_rub(s['topup_7d'])}</b>\n\n"
        f"⬇️ Оплат с баланса: <b>{s['spent_count']}</b> на <b>{_fmt_rub(s['spent_total'])}</b>\n\n"
        f"💳 Платежей всего оплачено: <b>{s['payments_paid_count']}</b> на "
        f"<b>{_fmt_rub(s['payments_paid_total'])}</b>\n\n"
        f"🎟 Промокодов создано: <b>{s['promo_codes_total']}</b>\n"
        f"🎟 Промокодов активировано: <b>{s['promo_activations_total']}</b>"
    )
    await callback.message.edit_text(text, reply_markup=_back_keyboard())
    await callback.answer()


async def _finance_stats(db: AsyncSession) -> dict:
    now = datetime.now(UTC)

    wallets_count = await db.scalar(
        select(func.count()).select_from(Wallet).where(Wallet.balance_kopeks > 0)
    ) or 0
    wallets_total = await db.scalar(
        select(func.coalesce(func.sum(Wallet.balance_kopeks), 0))
    ) or 0

    def _tx_sum(tx_type: WalletTxType, *, since: datetime | None = None):
        q = select(
            func.count(), func.coalesce(func.sum(WalletTransaction.amount_kopeks), 0)
        ).where(WalletTransaction.type == tx_type)
        if since is not None:
            q = q.where(WalletTransaction.created_at >= since)
        return q

    topup_count, topup_total = (await db.execute(_tx_sum(WalletTxType.TOPUP))).one()
    _, topup_24h = (
        await db.execute(_tx_sum(WalletTxType.TOPUP, since=now - timedelta(hours=24)))
    ).one()
    _, topup_7d = (
        await db.execute(_tx_sum(WalletTxType.TOPUP, since=now - timedelta(days=7)))
    ).one()
    spent_count, spent_total_neg = (await db.execute(_tx_sum(WalletTxType.PURCHASE))).one()

    payments_paid_count, payments_paid_total = (
        await db.execute(
            select(
                func.count(), func.coalesce(func.sum(Payment.amount_kopeks), 0)
            ).where(Payment.status == PaymentStatus.PAID)
        )
    ).one()

    promo_codes_total = await db.scalar(select(func.count()).select_from(PromoCode)) or 0
    promo_activations_total = (
        await db.scalar(select(func.count()).select_from(PromoCodeActivation)) or 0
    )

    return {
        "wallets_count": wallets_count,
        "wallets_total": wallets_total,
        "topup_count": topup_count,
        "topup_total": topup_total,
        "topup_24h": topup_24h,
        "topup_7d": topup_7d,
        "spent_count": spent_count,
        "spent_total": abs(spent_total_neg),
        "payments_paid_count": payments_paid_count,
        "payments_paid_total": payments_paid_total,
        "promo_codes_total": promo_codes_total,
        "promo_activations_total": promo_activations_total,
    }


@router.callback_query(F.data == "admin_stats_referrals")
async def cb_admin_referrals(callback: CallbackQuery, config: Config) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()

    async with session() as db:
        s = await _referral_stats(db)

    text = (
        "👥 <b>Реферальная программа</b>\n\n"
        f"🔗 Приглашённых пользователей: <b>{s['referred_total']}</b>\n"
        f"🙋 Активных рефереров: <b>{s['referrers_count']}</b>\n\n"
        f"🎁 Начислений дней (за первую покупку друга): <b>{s['reward_count']}</b> "
        f"на <b>{s['reward_days']}</b> дн.\n\n"
        f"💵 Комиссий на баланс начислено: <b>{s['commission_count']}</b> "
        f"на <b>{_fmt_rub(s['commission_total'])}</b>"
    )
    await callback.message.edit_text(text, reply_markup=_back_keyboard())
    await callback.answer()


async def _referral_stats(db: AsyncSession) -> dict:
    referred_total = await db.scalar(
        select(func.count()).select_from(BotUser).where(BotUser.referred_by_id.is_not(None))
    ) or 0
    referrers_count = await db.scalar(
        select(func.count(func.distinct(BotUser.referred_by_id))).where(
            BotUser.referred_by_id.is_not(None)
        )
    ) or 0
    reward_count, reward_days = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(ReferralReward.days), 0))
        )
    ).one()
    commission_count, commission_total = (
        await db.execute(
            select(
                func.count(), func.coalesce(func.sum(WalletTransaction.amount_kopeks), 0)
            ).where(WalletTransaction.type == WalletTxType.REFERRAL_REWARD)
        )
    ).one()
    return {
        "referred_total": referred_total,
        "referrers_count": referrers_count,
        "reward_count": reward_count,
        "reward_days": reward_days,
        "commission_count": commission_count,
        "commission_total": commission_total,
    }


@router.callback_query(F.data == "admin_stats_notifications")
async def cb_admin_notifications(callback: CallbackQuery, config: Config) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()

    async with session() as db:
        total = await db.scalar(select(func.count()).select_from(ExpiryNotification)) or 0
        by_window = await db.execute(
            select(ExpiryNotification.window, func.count())
            .group_by(ExpiryNotification.window)
        )
        lines = "\n".join(f"⏰ Окно «{w}»: <b>{c}</b>" for w, c in by_window) or "Пока пусто"

    text = f"🔔 <b>Уведомления об истечении подписки</b>\n\nВсего отправлено: <b>{total}</b>\n\n{lines}"
    await callback.message.edit_text(text, reply_markup=_back_keyboard())
    await callback.answer()


_BROADCAST_STATUS_LABEL = {
    "scheduled": "⏳ Запланирована",
    "sending": "🔄 Отправляется",
    "completed": "✅ Отправлена",
    "cancelled": "❌ Отменена",
}
_PAGE_SIZE = 5


@router.callback_query(F.data.startswith("admin_stats_broadcasts:"))
async def cb_admin_broadcasts(callback: CallbackQuery, config: Config) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()

    page = int(callback.data.split(":", 1)[1])

    async with session() as db:
        total = await db.scalar(select(func.count()).select_from(Broadcast)) or 0
        rows = (
            await db.scalars(
                select(Broadcast)
                .order_by(Broadcast.created_at.desc())
                .offset(page * _PAGE_SIZE)
                .limit(_PAGE_SIZE)
            )
        ).all()

    if not rows:
        lines = ["Рассылок пока не было."]
    else:
        lines = []
        for b in rows:
            status_label = _BROADCAST_STATUS_LABEL.get(str(b.status), str(b.status))
            full_text = (b.text or "").strip().replace("\n", " ")
            preview = full_text[:60] + ("…" if len(full_text) > 60 else "")
            lines.append(
                f"{status_label} · {b.sent_count}/{b.total_recipients} доставлено\n«{preview}»"
            )

    has_next = (page + 1) * _PAGE_SIZE < total
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_stats_broadcasts:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"Стр. {page + 1}", callback_data="noop"))
    if has_next:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_stats_broadcasts:{page + 1}"))

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[nav, [InlineKeyboardButton(text="↩️ Назад", callback_data="admin_stats")]]
    )
    text = "📢 <b>Рассылки</b>\n\n" + "\n\n".join(lines)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()
