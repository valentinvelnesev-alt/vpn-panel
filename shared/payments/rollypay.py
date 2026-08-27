"""Клиент RollyPay (СБП) — второй, резервный способ оплаты через СБП.

Документация: https://docs.rollypay.io/. Как и у Platega, статус вебхука
не считается доверенным сам по себе — после колбэка мы перезапрашиваем
платёж через `get_payment` своими учётными данными и верим только этому
ответу (см. shared/payments/platega.py).
"""

import httpx

BASE_URL = "https://rollypay.io"


class RollyPayError(Exception):
    pass


class RollyPayClient:
    def __init__(self, api_key: str, *, timeout: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        import uuid

        return {
            "X-API-Key": self._api_key,
            "X-Nonce": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    async def create_payment(
        self, *, amount_rub: float, description: str, order_id: str
    ) -> dict:
        """Возвращает тело ответа RollyPay: {"payment_id": ..., "pay_url": ...}."""
        payload = {
            "amount": f"{int(round(amount_rub))}.00",
            "payment_currency": "RUB",
            "payment_method": "sbp",
            "order_id": order_id,
            "description": description,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{BASE_URL}/api/v1/payments", json=payload, headers=self._headers()
            )
        if response.status_code >= 400:
            raise RollyPayError(
                f"RollyPay вернула {response.status_code}: {response.text[:200]}"
            )
        return response.json()

    async def get_payment(self, payment_id: str) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{BASE_URL}/api/v1/payments/{payment_id}", headers=self._headers()
            )
        if response.status_code >= 400:
            raise RollyPayError(
                f"RollyPay вернула {response.status_code}: {response.text[:200]}"
            )
        return response.json()

    @staticmethod
    def is_paid(payment: dict) -> bool:
        return str(payment.get("status", "")).lower() == "paid"
