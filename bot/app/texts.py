"""Тексты бота в двух вариантах эмодзи.

Иконки описаны в app/icons.py: там и обычный символ, и id премиум-эмодзи,
и точная заглушка набора. Премиум вставляется тегом <tg-emoji>: Telegram
показывает анимированную версию тем, у кого она видна, и обычный символ
остальным.
"""

from app.icons import ICONS
from shared.db.models import EmojiMode

PremiumMap = dict[str, str]


def icon(name: str, mode: EmojiMode, premium: PremiumMap | None = None) -> str:
    entry = ICONS.get(name)
    if entry is None:
        return ""
    if mode is not EmojiMode.PREMIUM:
        return entry.plain
    # Карта из панели важнее встроенной, но заглушку под чужой id мы не
    # знаем — берём обычный символ, он всегда корректен для своего же id.
    override = (premium or {}).get(name)
    if override:
        return f'<tg-emoji emoji-id="{override}">{entry.plain}</tg-emoji>'
    if entry.emoji_id:
        return f'<tg-emoji emoji-id="{entry.emoji_id}">{entry.placeholder}</tg-emoji>'
    return entry.plain


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
    # Подставляем только известные значения, а не str.format: приветствие
    # правится админом в панели, и любая посторонняя фигурная скобка в нём
    # роняла бы /start для всех пользователей.
    for key, value in values.items():
        text = text.replace("{%s}" % key, str(value))
    return text


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

PLANS_HEADER = "{@card} <b>Выберите тариф</b>"
NO_PLANS = "{@info} Тарифы ещё не настроены. Загляните позже."

CHANNEL_REQUIRED = (
    "{@warning} <b>Нужна подписка на канал</b>\n\n"
    "Подпишитесь на наш канал — и возвращайтесь к боту."
)

DEVICES_HEADER = "{@devices} <b>Ваши устройства</b>\n\n"
DEVICES_EMPTY = "{@devices} Подключённых устройств пока нет."

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
