"""Оплата Telegram Stars.

Telegram не публикует официальный курс Stars к рублю — стоимость звёзд
зависит от магазина и региона пользователя. Ниже используется приближённый
курс, которого придерживаются большинство ботов на момент написания;
при заметных расхождениях его стоит поправить.
"""

RUB_PER_STAR = 1.8  # по умолчанию; свой курс задаётся в панели (Платежи → Stars)


def rub_to_stars(amount_rub: float, rub_per_star: float = RUB_PER_STAR) -> int:
    return max(1, round(amount_rub / (rub_per_star or RUB_PER_STAR)))
