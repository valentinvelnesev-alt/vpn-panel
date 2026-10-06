"""Выбор VPN-панели, с которой работают бот и админка.

Тип задаётся при установке (install.sh пишет PANEL_TYPE в .env) и не
меняется на лету: данные ключей в БД бота привязаны к конкретной панели.

    remnawave — Remnawave (по умолчанию, как было всегда)
    3xui      — 3x-ui v3+, клиенты и инбаунды через /panel/api

Оба клиента отдают одинаковые модели и одинаково называют методы, поэтому
остальной код просто вызывает make_client и не знает, что за ним.
"""

import os

from shared.remnawave import RemnawaveClient
from shared.xui import XuiClient

REMNAWAVE = "remnawave"
XUI = "3xui"

PanelClient = RemnawaveClient | XuiClient


def panel_type() -> str:
    value = os.environ.get("PANEL_TYPE", REMNAWAVE).strip().lower().replace("-", "")
    return XUI if value in ("3xui", "xui", "3x") else REMNAWAVE


def panel_name() -> str:
    return "3x-ui" if panel_type() == XUI else "Remnawave"


def make_client(url: str, token: str, *, verify_tls: bool = True) -> PanelClient:
    if panel_type() == XUI:
        return XuiClient(url, token, verify_tls=verify_tls)
    return RemnawaveClient(url, token, verify_tls=verify_tls)
