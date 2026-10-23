"""Подписи кнопок и тексты сообщений бота, которые владелец правит в панели.

Единый реестр: и бот, и панель берут отсюда стандартный вариант и список
допустимых подстановок, поэтому они не расходятся. Свои значения лежат в
bot_config (menu_labels, bot_texts) и хранят только то, что отличается от
стандартного — пустое поле в панели возвращает стандартный текст.

Тексты сообщений — HTML Telegram. Сломанная разметка (незакрытый тег, лишний
«<») отклоняется Telegram'ом вместе со всем сообщением, то есть у всех
клиентов экран просто не откроется. Поэтому при сохранении тексты строго
проверяются (validate_message), а не доверяются.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True, slots=True)
class Var:
    name: str
    hint: str
    example: str


@dataclass(frozen=True, slots=True)
class Copy:
    key: str
    default: str
    title: str  # как называется в панели
    where: str  # где клиент это видит
    variables: tuple[Var, ...] = ()


# ── Кнопки ────────────────────────────────────────────────────────────
BUTTONS: tuple[Copy, ...] = (
    Copy("trial", "Попробовать бесплатно", "Пробный период", "Главное меню"),
    Copy("buy", "Купить подписку", "Купить", "Главное меню, когда у клиента ещё нет подписки"),
    Copy("renew", "Продлить подписку", "Продлить", "Главное меню, когда у клиента уже есть подписка"),
    Copy("buy_more", "Купить ещё одну", "Купить ещё одну", "Главное меню, если разрешено несколько подписок"),
    Copy("subscriptions", "Подписка", "Подписка", "Главное меню"),
    Copy("connect", "Подключиться", "Подключиться", "Главное меню"),
    Copy("profile", "Личный кабинет", "Личный кабинет", "Главное меню"),
    Copy(
        "balance",
        "Баланс: {balance}",
        "Баланс",
        "Главное меню",
        (Var("balance", "сумма на балансе клиента", "150 ₽"),),
    ),
    Copy("referral", "Рефералка", "Рефералка", "Главное меню"),
    Copy("promo", "Промокод", "Промокод", "Главное меню"),
    Copy("channel", "Наш канал", "Канал", "Главное меню, если указана ссылка на канал"),
    Copy("support", "Поддержка", "Поддержка", "Главное меню, если настроена поддержка"),
    Copy("ad_stats", "Статистика", "Статистика", "Главное меню владельцев рекламных ссылок"),
    Copy("back", "Назад", "Назад", "На всех экранах, где можно вернуться"),
    Copy("cancel", "Отмена", "Отмена", "Там, где действие можно отменить"),
)

# ── Сообщения ─────────────────────────────────────────────────────────
MESSAGES: tuple[Copy, ...] = (
    Copy(
        "plans_header",
        "{@card} <b>Выберите тариф</b>",
        "Список тарифов",
        "Шапка экрана, когда клиент нажал «Купить»",
    ),
    Copy(
        "subscription_active",
        "{@check} <b>Подписка активна</b>\n\n"
        "Действует до: <b>{until}</b>\n"
        "Осталось: <b>{left}</b>\n\n"
        "Ссылка для подключения:\n<code>{url}</code>",
        "Подписка активна",
        "Экран «Подписка» у клиента с действующей подпиской",
        (
            Var("until", "дата окончания", "30.11.2026"),
            Var("left", "сколько осталось", "12 дней"),
            Var("url", "ссылка для подключения", "https://sub.example.com/abc123"),
        ),
    ),
    Copy(
        "subscription_none",
        "{@clock} <b>Подписки нет</b>\n\nВыберите тариф — доступ выдаётся сразу после оплаты.",
        "Подписки нет",
        "Экран «Подписка» у клиента без подписки",
    ),
    Copy(
        "trial_granted",
        "{@gift} <b>Пробный период активирован</b>\n\n"
        "Доступ открыт на {days} — до <b>{until}</b>.\n\n"
        "Ссылка для подключения:\n<code>{url}</code>",
        "Пробный период выдан",
        "Сразу после нажатия «Попробовать бесплатно»",
        (
            Var("days", "срок пробного периода", "3 дня"),
            Var("until", "дата окончания", "26.10.2026"),
            Var("url", "ссылка для подключения", "https://sub.example.com/abc123"),
        ),
    ),
    Copy(
        "channel_required",
        "{@warning} <b>Нужна подписка на канал</b>\n\nПодпишитесь на наш канал — и возвращайтесь к боту.",
        "Нужна подписка на канал",
        "Если включена обязательная подписка и клиент ещё не подписан",
    ),
    Copy(
        "expiry_warning",
        "{@clock} <b>Подписка заканчивается</b>\n\n"
        "Осталось: <b>{left}</b>, до <b>{until}</b>.\n"
        "Продлите, чтобы не потерять доступ.",
        "Напоминание об окончании",
        "Приходит клиенту за 3 дня и за 1 день до конца подписки",
        (
            Var("left", "сколько осталось", "1 день"),
            Var("until", "дата окончания", "26.10.2026"),
        ),
    ),
    Copy(
        "expired",
        "{@cross} <b>Подписка закончилась</b>\n\nДоступ отключён. Продлите — и всё заработает снова.",
        "Подписка закончилась",
        "Приходит клиенту, когда подписка истекла",
    ),
    Copy(
        "payment_plan_paid",
        "{@check} Оплата получена, подписка «{title}» действует до {until}",
        "Оплата подписки получена",
        "Сообщение клиенту после успешной оплаты тарифа",
        (
            Var("title", "название тарифа", "Месяц"),
            Var("until", "дата окончания", "26.11.2026"),
        ),
    ),
    Copy(
        "payment_topup_paid",
        "{@check} Баланс пополнен на {amount} ₽",
        "Баланс пополнен",
        "Сообщение клиенту после пополнения баланса",
        (Var("amount", "сумма пополнения", "300.00"),),
    ),
)

BUTTON_KEYS = {c.key: c for c in BUTTONS}
MESSAGE_KEYS = {c.key: c for c in MESSAGES}

# Обычные эмодзи для `{@имя}` — только те, что встречаются в стандартных
# текстах; у бота полный набор в app/icons.py (там же премиум-версии), а
# панели они нужны для превью. Согласованность проверяет тест.
PLAIN_ICONS: dict[str, str] = {
    "shield": "🛡",
    "rocket": "🚀",
    "info": "ℹ️",
    "warning": "⚠️",
    "check": "✅",
    "cross": "❌",
    "clock": "⏳",
    "key": "🔑",
    "star": "⭐️",
    "traffic": "📶",
    "card": "💳",
    "wallet": "💰",
    "gift": "🎁",
    "user": "👤",
    "link": "🔗",
    "devices": "📱",
}

MAX_BUTTON_LENGTH = 64
MAX_MESSAGE_LENGTH = 3000

_PLACEHOLDER = re.compile(r"\{(@?[A-Za-z_]+)\}")
_ALLOWED_TAGS = frozenset(
    {
        "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "a",
        "tg-spoiler", "tg-emoji", "blockquote", "span",
    }
)


class CopyError(ValueError):
    """Сообщение пригодно для показа администратору как есть."""


def default_button(key: str) -> str:
    return BUTTON_KEYS[key].default


def default_message(key: str) -> str:
    return MESSAGE_KEYS[key].default


def _check_placeholders(text: str, allowed: set[str], *, label: str) -> None:
    for match in _PLACEHOLDER.finditer(text):
        name = match.group(1)
        if name.startswith("@"):
            if name[1:] not in PLAIN_ICONS:
                raise CopyError(
                    f"{label}: неизвестный значок {{{name}}}. Доступны: "
                    + ", ".join("{@%s}" % k for k in PLAIN_ICONS)
                )
        elif name not in allowed:
            have = ", ".join("{%s}" % v for v in sorted(allowed)) or "здесь подстановок нет"
            raise CopyError(f"{label}: неизвестная подстановка {{{name}}}. Доступны: {have}")


def validate_button(key: str, text: str) -> str | None:
    """Подпись кнопки → очищенная строка или None (вернуть стандартную)."""
    if key not in BUTTON_KEYS:
        raise CopyError(f"Неизвестная кнопка «{key}»")
    value = " ".join(text.split())
    if not value or value == BUTTON_KEYS[key].default:
        return None
    title = BUTTON_KEYS[key].title
    if len(value) > MAX_BUTTON_LENGTH:
        raise CopyError(f"«{title}»: подпись длиннее {MAX_BUTTON_LENGTH} символов")
    variables = {v.name for v in BUTTON_KEYS[key].variables}
    if "{@" in value:
        raise CopyError(f"«{title}»: значки {{@…}} на кнопках не работают — у кнопки они задаются отдельно")
    _check_placeholders(value, variables, label=f"«{title}»")
    return value


class _Balance(HTMLParser):
    """Проверяет, что теги разрешены, закрыты и вложены правильно."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self.error: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            self.error = self.error or f"тег <{tag}> не поддерживается Telegram"
            return
        if tag == "a" and not any(name == "href" and value for name, value in attrs):
            self.error = self.error or "у ссылки <a> нет href"
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            # Первая найденная ошибка точнее последующих.
            self.error = self.error or f"закрывающий тег </{tag}> не к месту"
            return
        self.stack.pop()


def check_html(text: str) -> None:
    """Разметка Telegram: разрешённые теги, все закрыты, «<» и «&» экранированы."""
    # «<» и «&» вне тегов и сущностей Telegram не принимает.
    stripped = re.sub(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?>", "", text)
    if "<" in stripped or ">" in stripped:
        raise CopyError("в тексте одиночный «<» или «>» — пишите их как &lt; и &gt;")
    if re.search(r"&(?!(?:lt|gt|amp|quot|#\d+|#x[0-9a-fA-F]+);)", text):
        raise CopyError("в тексте одиночный «&» — пишите его как &amp;")
    parser = _Balance()
    parser.feed(text)
    parser.close()
    if parser.error:
        raise CopyError(f"ошибка в разметке: {parser.error}")
    if parser.stack:
        raise CopyError(f"не закрыт тег <{parser.stack[-1]}>")


def validate_message(key: str, text: str) -> str | None:
    """Текст сообщения → строка для сохранения или None (стандартный)."""
    if key not in MESSAGE_KEYS:
        raise CopyError(f"Неизвестное сообщение «{key}»")
    value = text.strip()
    if not value or value == MESSAGE_KEYS[key].default:
        return None
    title = MESSAGE_KEYS[key].title
    if len(value) > MAX_MESSAGE_LENGTH:
        raise CopyError(f"«{title}»: текст длиннее {MAX_MESSAGE_LENGTH} символов")
    variables = {v.name for v in MESSAGE_KEYS[key].variables}
    _check_placeholders(value, variables, label=f"«{title}»")
    try:
        # Подстановки и значки не мешают разметке — проверяем «голый» текст.
        check_html(_PLACEHOLDER.sub("x", value))
    except CopyError as exc:
        raise CopyError(f"«{title}»: {exc}") from None
    return value
