"""Тексты бота в двух вариантах эмодзи.

Каждая иконка описана парой: обычный юникод-символ и id премиум-эмодзи.
Рендерер подставляет нужный вариант по режиму из настроек, поэтому старый
хардкод вида `_EMOJI_ID = "5271604874419647061"` в коде хендлеров исчезает.

Премиум-эмодзи вставляются как HTML-тег <tg-emoji>: Telegram показывает
анимированную версию тем, у кого её видно, и обычный символ остальным.
"""

from shared.db.models import EmojiMode

# Обычные символы — работают у всех и всегда.
ICONS: dict[str, str] = {
    "shield": "🛡",
    "rocket": "🚀",
    "key": "🔑",
    "clock": "⏳",
    "check": "✅",
    "cross": "❌",
    "star": "⭐️",
    "card": "💳",
    "phone": "📱",
    "gift": "🎁",
    "warning": "⚠️",
    "info": "ℹ️",
}

# id премиум-эмодзи не «универсальны»: они принадлежат конкретным наборам
# стикеров, и выдуманный id Telegram отвергнет. Поэтому владелец панели
# задаёт соответствие «иконка → id» сам (в разделе «Бот»), а пока карта
# пуста — премиум-режим просто рисует обычные символы.
PremiumMap = dict[str, str]


def icon(name: str, mode: EmojiMode, premium: PremiumMap | None = None) -> str:
    plain = ICONS.get(name, "")
    if mode is EmojiMode.PREMIUM and premium:
        emoji_id = premium.get(name)
        if emoji_id:
            return f'<tg-emoji emoji-id="{emoji_id}">{plain}</tg-emoji>'
    return plain


def render(
    template: str,
    mode: EmojiMode,
    premium: PremiumMap | None = None,
    **values: object,
) -> str:
    """Подставляет иконки `{@name}` и значения `{name}`.

        render("{@shield} Привет, {name}", mode, name="Иван")
    """
    text = template
    for key in ICONS:
        placeholder = "{@%s}" % key
        if placeholder in text:
            text = text.replace(placeholder, icon(key, mode, premium))
    return text.format(**values) if values else text


# ── Шаблоны ───────────────────────────────────────────────────────────
WELCOME_DEFAULT = (
    "{@shield} <b>{brand}</b>\n\n"
    "Быстрый и безопасный VPN. Выберите тариф или попробуйте бесплатно."
)

SUBSCRIPTION_ACTIVE = (
    "{@check} <b>Подписка активна</b>\n\n"
    "Действует до: <b>{until}</b>\n"
    "Осталось: <b>{left}</b>\n\n"
    "Ссылка для подключения:\n<code>{url}</code>"
)

SUBSCRIPTION_NONE = (
    "{@clock} <b>Подписки нет</b>\n\n"
    "Выберите тариф — доступ выдаётся сразу после оплаты."
)

TRIAL_GRANTED = (
    "{@gift} <b>Пробный период активирован</b>\n\n"
    "Доступ открыт на {days} — до <b>{until}</b>.\n\n"
    "Ссылка для подключения:\n<code>{url}</code>"
)

TRIAL_USED = "{@warning} Пробный период уже использован."
TRIAL_DISABLED = "{@warning} Пробный период сейчас недоступен."

PLANS_HEADER = "{@card} <b>Тарифы</b>\n\nВыберите срок подписки:"
NO_PLANS = "{@info} Тарифы ещё не настроены. Загляните позже."

CHANNEL_REQUIRED = (
    "{@warning} <b>Нужна подписка на канал</b>\n\n"
    "Подпишитесь на наш канал — и возвращайтесь к боту."
)

DEVICES_HEADER = "{@phone} <b>Ваши устройства</b>\n\n"
DEVICES_EMPTY = "{@phone} Подключённых устройств пока нет."

EXPIRY_WARNING = (
    "{@clock} <b>Подписка заканчивается</b>\n\n"
    "Осталось: <b>{left}</b>, до <b>{until}</b>.\n"
    "Продлите, чтобы не потерять доступ."
)

EXPIRED = (
    "{@cross} <b>Подписка закончилась</b>\n\n"
    "Доступ отключён. Продлите — и всё заработает снова."
)

HELP = (
    "{@info} <b>Помощь</b>\n\n"
    "/start — главное меню\n"
    "/subscription — моя подписка\n"
    "/help — эта справка"
)

ERROR_GENERIC = (
    "{@warning} Что-то пошло не так. Попробуйте ещё раз или напишите в поддержку."
)
