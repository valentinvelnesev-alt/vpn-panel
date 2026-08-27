"""Админ-панель прямо в Telegram: команда /admin со статистикой, рассылкой
и промокодами.

Быстрые действия с телефона, без захода в браузер — но пишут в те же самые
таблицы (`bot_promo_codes`, `broadcasts`), что и веб-панель, и рассылку
по-прежнему отправляет воркер бота (см. app/workers/broadcast.py) по
событию из шины. Тарифы и полноценное редактирование промокодов/рассылок
(фото, кнопки) остаются в веб-панели — она удобнее для этого на клавиатуре.

Кто видит /admin определяется списком `admin_telegram_ids` в настройках
бота (вкладка «Бот» в панели) — это не то же самое, что администраторы
самой веб-панели.
"""

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Config
from app.states import AdminStates
from shared import bus
from shared.db.models import (
    Broadcast,
    BroadcastSegment,
    BroadcastStatus,
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


def _admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
            [
                InlineKeyboardButton(text="🎟 Создать промокод", callback_data="admin_promo_create"),
                InlineKeyboardButton(text="📋 Промокоды", callback_data="admin_promo_list"),
            ],
            [InlineKeyboardButton(text="Закрыть", callback_data="admin_close")],
        ]
    )


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
            [InlineKeyboardButton(text="↩️ Назад", callback_data="admin_close_to_panel")],
        ]
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, config: Config) -> None:
    if not _is_admin(config, message.from_user.id):
        return  # молча игнорируем — не выдаём, что команда вообще существует
    await message.answer(
        "🛠 <b>Админ-панель</b>\n\nВыберите действие:", reply_markup=_admin_menu_keyboard()
    )


@router.callback_query(F.data == "admin_close")
async def cb_admin_close(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data == "admin_close_to_panel")
async def cb_admin_close_to_panel(callback: CallbackQuery, config: Config) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()
    await callback.message.edit_text(
        "🛠 <b>Админ-панель</b>\n\nВыберите действие:", reply_markup=_admin_menu_keyboard()
    )
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


# ── Рассылка (быстрый вариант: текст без фото/кнопок — для них веб-панель) ─
_SEGMENT_LABEL = {
    BroadcastSegment.ALL: "Всем пользователям",
    BroadcastSegment.ACTIVE: "С активной подпиской",
    BroadcastSegment.EXPIRED: "С истёкшей подпиской",
    BroadcastSegment.NO_PURCHASE: "Без единой покупки",
}


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="admin_close_to_panel")]]
    )


@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, config: Config, state: FSMContext) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()
    await state.set_state(AdminStates.broadcast_text)
    await callback.message.edit_text(
        "📢 Введите текст рассылки (можно с HTML-разметкой):",
        reply_markup=_cancel_keyboard(),
    )
    await callback.answer()


@router.message(StateFilter(AdminStates.broadcast_text))
async def on_broadcast_text(message: Message, state: FSMContext, config: Config) -> None:
    if not _is_admin(config, message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст не может быть пустым. Введите текст рассылки:")
        return

    await state.update_data(text=text)
    await state.set_state(AdminStates.broadcast_segment)

    builder = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"admin_bcseg:{seg.value}")]
            for seg, label in _SEGMENT_LABEL.items()
        ]
        + [[InlineKeyboardButton(text="Отмена", callback_data="admin_close_to_panel")]]
    )
    await message.answer("Кому отправить?", reply_markup=builder)


@router.callback_query(F.data.startswith("admin_bcseg:"))
async def cb_broadcast_segment(callback: CallbackQuery, config: Config, state: FSMContext) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()

    segment = BroadcastSegment(callback.data.split(":", 1)[1])
    data = await state.get_data()
    text = data.get("text", "")
    await state.update_data(segment=segment.value)
    await state.set_state(AdminStates.broadcast_confirm)

    preview = text if len(text) <= 500 else text[:500] + "…"
    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="admin_bcconfirm")],
            [InlineKeyboardButton(text="Отмена", callback_data="admin_close_to_panel")],
        ]
    )
    await callback.message.edit_text(
        f"Проверьте рассылку:\n\nСегмент: <b>{_SEGMENT_LABEL[segment]}</b>\n\n{preview}",
        reply_markup=confirm_kb,
    )
    await callback.answer()


@router.callback_query(F.data == "admin_bcconfirm")
async def cb_broadcast_confirm(callback: CallbackQuery, config: Config, state: FSMContext) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()

    data = await state.get_data()
    text = data.get("text", "")
    segment = BroadcastSegment(data.get("segment", BroadcastSegment.ALL.value))
    await state.clear()

    now = datetime.now(UTC)
    async with session() as db:
        broadcast = Broadcast(
            text=text,
            buttons=[],
            segment=segment,
            status=BroadcastStatus.SCHEDULED,
            scheduled_at=now,
            created_at=now,
        )
        db.add(broadcast)

    # Публикуем ПОСЛЕ выхода из `session()` — она уже закоммитила транзакцию
    # (см. shared/db/session.py), иначе воркер может прочитать ещё не
    # сохранённую запись (та же гонка, что чинили в веб-панели).
    await bus.publish(bus.EVENT_BROADCAST_READY)

    await callback.message.edit_text(
        "✅ Рассылка запущена — воркер отправит её в ближайшие секунды.",
        reply_markup=_admin_menu_keyboard(),
    )
    await callback.answer()


# ── Промокоды ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "admin_promo_create")
async def cb_admin_promo_create(callback: CallbackQuery, config: Config, state: FSMContext) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()
    await state.set_state(AdminStates.promo_code)
    await callback.message.edit_text(
        "🎟 Введите код промокода (латиница и цифры, например SUMMER25):",
        reply_markup=_cancel_keyboard(),
    )
    await callback.answer()


@router.message(StateFilter(AdminStates.promo_code))
async def on_promo_code_input(message: Message, state: FSMContext, config: Config) -> None:
    if not _is_admin(config, message.from_user.id):
        return
    code = (message.text or "").strip().upper()
    if not code or not all(c.isalnum() or c in "_-" for c in code):
        await message.answer("Только буквы, цифры, «_» и «-». Введите код ещё раз:")
        return

    async with session() as db:
        exists = await db.scalar(select(PromoCode).where(PromoCode.code == code))
    if exists:
        await message.answer("Такой промокод уже существует. Введите другой код:")
        return

    await state.update_data(code=code)
    await state.set_state(AdminStates.promo_bonus_days)
    await message.answer("Сколько бонусных дней даёт промокод? (0 — только скидка)")


@router.message(StateFilter(AdminStates.promo_bonus_days))
async def on_promo_bonus_days(message: Message, state: FSMContext, config: Config) -> None:
    if not _is_admin(config, message.from_user.id):
        return
    try:
        days = int((message.text or "").strip())
        assert 0 <= days <= 3650
    except (ValueError, AssertionError):
        await message.answer("Введите целое число дней от 0 до 3650:")
        return

    await state.update_data(bonus_days=days)
    await state.set_state(AdminStates.promo_discount_percent)
    await message.answer("Скидка на покупку тарифа, в процентах? (0 — без скидки)")


@router.message(StateFilter(AdminStates.promo_discount_percent))
async def on_promo_discount_percent(message: Message, state: FSMContext, config: Config) -> None:
    if not _is_admin(config, message.from_user.id):
        return
    try:
        percent = int((message.text or "").strip())
        assert 0 <= percent <= 100
    except (ValueError, AssertionError):
        await message.answer("Введите целое число процентов от 0 до 100:")
        return

    data = await state.get_data()
    code = data["code"]
    bonus_days = data["bonus_days"]
    await state.clear()

    if bonus_days == 0 and percent == 0:
        await message.answer(
            "У промокода должны быть дни или скидка — иначе он ничего не даёт. "
            "Начните заново через /admin.",
        )
        return

    async with session() as db:
        db.add(PromoCode(code=code, bonus_days=bonus_days, discount_percent=percent))

    await message.answer(
        f"✅ Промокод <b>{code}</b> создан: {bonus_days} дн., скидка {percent}%.",
        reply_markup=_admin_menu_keyboard(),
    )


@router.callback_query(F.data == "admin_promo_list")
async def cb_admin_promo_list(callback: CallbackQuery, config: Config) -> None:
    if not _is_admin(config, callback.from_user.id):
        return await callback.answer()

    async with session() as db:
        rows = (
            await db.scalars(select(PromoCode).order_by(PromoCode.created_at.desc()).limit(20))
        ).all()

    if not rows:
        text = "📋 <b>Промокоды</b>\n\nПока не создано ни одного."
    else:
        lines = []
        for p in rows:
            status_icon = "✅" if p.is_active else "⛔️"
            limit = f"{p.uses_count}/{p.max_uses}" if p.max_uses else f"{p.uses_count}/∞"
            lines.append(f"{status_icon} <code>{p.code}</code> — {p.bonus_days} дн., {p.discount_percent}% · {limit}")
        text = "📋 <b>Промокоды</b>\n\n" + "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=_back_keyboard("admin_close_to_panel"))
    await callback.answer()
