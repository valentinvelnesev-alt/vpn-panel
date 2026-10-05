from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response, status

from app.core import gate
from app.core.config import settings

router = APIRouter(tags=["gate"])


@router.get("/gate", include_in_schema=False)
async def check_gate(request: Request) -> Response:
    """Проверка для Caddy forward_auth перед отдачей SPA (см. core/gate.py).

    Caddy передаёт исходный адрес в X-Forwarded-Uri и cookie браузера как
    есть; ответ не-2xx он целиком (вместе с Location и Set-Cookie) отдаёт
    браузеру."""
    if not gate.enabled():
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    uri = request.headers.get("x-forwarded-uri", "/")
    if gate.is_secret_path(urlsplit(uri).path):
        response = Response(
            status_code=status.HTTP_302_FOUND, headers={"Location": "/", "Cache-Control": "no-store"}
        )
        response.set_cookie(
            gate.GATE_COOKIE,
            gate.gate_token(),
            max_age=gate.GATE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=settings.https_enabled,
            path="/",
        )
        return response

    if gate.has_pass(request.headers.get("cookie")):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(status_code=status.HTTP_404_NOT_FOUND, media_type="text/plain")
