"""Клиент cisPay (H2H API мерчанта): https://api.cispay.app.

Создаём счёт без жёсткого способа оплаты — на хостинговой странице cisPay
покупатель сам выбирает подключённый у магазина способ (СБП, карта,
криптовалюта). В этом режиме API требует customer_id: передаём Telegram ID.

Как и у остальных провайдеров, телу вебхука не верим: сначала сверяем
подпись (HMAC-SHA256 тела на API-ключе магазина), затем перезапрашиваем
статус платежа своими учётными данными и верим только этому ответу.
"""

import hashlib
import hmac

import httpx

BASE_URL = "https://api.cispay.app"


class CisPayError(Exception):
    pass


class CisPayClient:
    def __init__(self, shop_id: str, api_key: str, *, timeout: float = 15.0) -> None:
        self._shop_id = shop_id
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "X-Shop-ID": self._shop_id,
            "X-Api-Key": self._api_key,
            "Accept": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with httpx.AsyncClient(base_url=BASE_URL, timeout=self._timeout) as client:
                response = await client.request(method, path, headers=self._headers(), **kwargs)
        except httpx.HTTPError as exc:
            raise CisPayError(f"cisPay недоступен: {exc}") from exc
        if response.status_code >= 400:
            raise CisPayError(f"cisPay вернул {response.status_code}: {response.text[:300]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise CisPayError("cisPay вернул не JSON") from exc
        if not isinstance(data, dict):
            raise CisPayError(f"cisPay: неожиданный ответ {str(data)[:200]}")
        return data

    async def create_payment(
        self,
        *,
        amount_kopeks: int,
        order_id: str,
        customer_id: str,
        description: str,
        return_url: str | None = None,
    ) -> dict:
        """Ответ cisPay: {"id", "payment_url", "status", ...}."""
        body: dict = {
            "amount": amount_kopeks,
            "currency": "RUB",
            "order_id": order_id,
            "customer_id": customer_id,
            "description": description[:255],
        }
        if return_url:
            body["redirect_success_url"] = return_url
            body["redirect_fail_url"] = return_url
        return await self._request("POST", "/payments", json=body)

    async def get_payment(self, payment_id: str) -> dict:
        return await self._request("GET", "/payments/status", params={"id": payment_id})

    async def capabilities(self) -> dict:
        """Подключённые способы оплаты — заодно проверка ключей из панели."""
        return await self._request("GET", "/store/capabilities")

    @staticmethod
    def is_paid(payment: dict) -> bool:
        return str(payment.get("status", "")).upper() == "PAID"


def verify_signature(api_key: str, raw_body: bytes, signature: str) -> bool:
    """X-Signature вебхука: HMAC-SHA256 от тела запроса как есть, ключ — API-ключ."""
    if not api_key or not signature:
        return False
    expected = hmac.new(api_key.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())
