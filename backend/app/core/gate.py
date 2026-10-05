"""Секретный путь панели (PANEL_SECRET_PATH).

Пока путь не задан — всё работает как раньше. Когда задан:
- Caddy перед отдачей SPA спрашивает /api/v1/gate (forward_auth). Зашли
  по секретному адресу — ставим cookie-пропуск и редиректим на «/»; есть
  cookie — пропускаем; иначе пустой 404, как будто здесь ничего нет.
- Middleware ниже точно так же прячет API: без пропуска даже /auth/login
  отвечает 404, перебирать пароль просто некуда.
Вебхуки платёжек и healthcheck пропуска не требуют — их дёргают снаружи.

Пропуск — HMAC от секретного пути, а не сам путь: из cookie путь не узнать.
Это не замена паролю и 2FA, а слой, который убирает панель с глаз сканеров.
"""

import hashlib
import hmac
from http.cookies import SimpleCookie

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings

GATE_COOKIE = "panel_gate"
GATE_MAX_AGE = 30 * 86400

# Доступно без пропуска: внешние колбэки, проверка здоровья, сам гейт.
_OPEN_PREFIXES = ("/api/v1/webhooks/", "/api/v1/health", "/api/v1/gate")


def enabled() -> bool:
    return bool(settings.secret_path)


def gate_token() -> str:
    return hmac.new(
        settings.secret_key.encode(),
        f"gate:{settings.secret_path}".encode(),
        hashlib.sha256,
    ).hexdigest()[:40]


def has_pass(cookie_header: str | None) -> bool:
    if not cookie_header:
        return False
    cookies = SimpleCookie()
    try:
        cookies.load(cookie_header)
    except Exception:  # noqa: BLE001
        return False
    morsel = cookies.get(GATE_COOKIE)
    return morsel is not None and hmac.compare_digest(morsel.value, gate_token())


def is_secret_path(path: str) -> bool:
    return enabled() and path.rstrip("/") == f"/{settings.secret_path}"


class GateMiddleware:
    """Прячет API за секретным путём (HTTP и WebSocket)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not enabled():
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        if path.startswith(_OPEN_PREFIXES) or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        cookie = headers.get(b"cookie", b"").decode("latin-1")
        if has_pass(cookie):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", b"0")],
            }
        )
        await send({"type": "http.response.body", "body": b""})
