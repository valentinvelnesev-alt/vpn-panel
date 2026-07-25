"""Клиент Platega (приём СБП/карт).

Публичный API `https://app.platega.io` — не привязан к конкретному проекту,
в отличие от старого бота, где сюда же были вписаны чужие мерчант-данные.

Статус платежа из вебхука не считается доверенным сам по себе: после
получения колбэка мы всегда перезапрашиваем транзакцию через `get_transaction`
своими же учётными данными и верим только этому ответу. Это надёжнее, чем
проверять подпись колбэка по недокументированной схеме, и не хуже: подделать
такой ответ может только тот, у кого есть наш секрет мерчанта.
"""

import httpx

BASE_URL = "https://app.platega.io"


class PlategaError(Exception):
    pass


class PlategaClient:
    def __init__(self, merchant_id: str, secret: str, *, timeout: float = 15.0) -> None:
        self._merchant_id = merchant_id
        self._secret = secret
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "X-MerchantId": self._merchant_id,
            "X-Secret": self._secret,
            "Content-Type": "application/json",
        }

    async def create_transaction(
        self, *, amount_rub: float, description: str, order_id: str, return_url: str
    ) -> dict:
        """Возвращает {"id": ..., "redirectUrl": ...} — ссылку показываем пользователю."""
        payload = {
            "paymentMethod": 2,  # СБП
            "amount": round(amount_rub, 2),
            "description": description,
            "orderId": order_id,
            "returnUrl": return_url,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{BASE_URL}/transaction/process",
                json=payload,
                headers=self._headers(),
            )
        if response.status_code >= 400:
            raise PlategaError(f"Platega вернула {response.status_code}: {response.text[:200]}")
        return response.json()

    async def get_transaction(self, transaction_id: str) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{BASE_URL}/transaction/{transaction_id}", headers=self._headers()
            )
        if response.status_code >= 400:
            raise PlategaError(f"Platega вернула {response.status_code}: {response.text[:200]}")
        return response.json()

    @staticmethod
    def is_paid(transaction: dict) -> bool:
        return str(transaction.get("status", "")).upper() in {"CONFIRMED", "PAID", "SUCCESS"}
