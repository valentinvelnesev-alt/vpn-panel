"""Бот использует свои подписи и тексты из панели."""

from dataclasses import replace

import pytest

from app import keyboards, texts
from app.icons import ICONS
from shared import botcopy
from shared.db.models import EmojiMode
from tests.test_audit_fixes import make_config


def labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def menu(config, **kwargs):
    params = dict(trial_available=True, has_subscription=False, balance_kopeks=15000)
    params.update(kwargs)
    return keyboards.main_menu(config, **params)


def test_defaults_when_nothing_customised() -> None:
    config = make_config()
    assert labels(menu(config))[:2] == ["Попробовать бесплатно", "Купить подписку"]
    assert "Баланс: 150 ₽" in labels(menu(config))


def test_custom_labels_reach_the_menu() -> None:
    config = replace(
        make_config(),
        menu_labels={"trial": "Бесплатно 3 дня", "buy": "Хочу VPN", "balance": "Кошелёк {balance}"},
    )
    shown = labels(menu(config))
    assert "Бесплатно 3 дня" in shown and "Хочу VPN" in shown and "Кошелёк 150 ₽" in shown
    assert "Купить подписку" not in shown


def test_renew_and_buy_labels_are_separate() -> None:
    config = replace(make_config(), menu_labels={"renew": "Продлить VPN"})
    assert "Продлить VPN" in labels(menu(config, has_subscription=True, trial_available=False))
    assert "Купить подписку" in labels(menu(config))  # «Купить» не тронуто


def test_back_and_cancel_follow_settings() -> None:
    config = replace(make_config(), menu_labels={"back": "← Назад", "cancel": "✖ Закрыть"})
    assert labels(keyboards.back_to_menu(config)) == ["← Назад"]
    assert labels(keyboards.cancel_to_menu(config)) == ["✖ Закрыть"]
    assert labels(keyboards.back_to_menu(None)) == ["Назад"]  # без конфига — стандарт


def test_custom_message_replaces_default_and_is_rendered() -> None:
    config = replace(make_config(), bot_texts={"expired": "{@cross} Всё, {days} дн. назад"})
    assert config.message("expired") == "{@cross} Всё, {days} дн. назад"
    assert config.message("plans_header") == botcopy.default_message("plans_header")
    rendered = texts.render(config.message("expired"), EmojiMode.PLAIN, days=3)
    assert rendered == "❌ Всё, 3 дн. назад"


def test_empty_override_falls_back_to_default() -> None:
    config = replace(make_config(), menu_labels={"promo": ""}, bot_texts={"expired": ""})
    assert config.label("promo") == "Промокод"
    assert config.message("expired") == botcopy.default_message("expired")


def test_texts_module_defaults_come_from_the_registry() -> None:
    assert texts.EXPIRED == botcopy.default_message("expired")
    assert texts.SUBSCRIPTION_ACTIVE == botcopy.default_message("subscription_active")


@pytest.mark.parametrize("name", sorted(botcopy.PLAIN_ICONS))
def test_preview_icons_match_the_bots_icons(name: str) -> None:
    # Панель рисует превью по этой таблице — она не должна отставать от бота.
    assert botcopy.PLAIN_ICONS[name] == ICONS[name].plain
