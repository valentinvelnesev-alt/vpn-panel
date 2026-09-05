"""Клиент Platega (приём СБП/карт).

Публичный API `https://app.platega.io` — не привязан к конкретному проекту,
в отличие от старого бота, где сюда же были вписаны чужие мерчант-данные.

Формат тела запроса проверен на боевом мерчанте: сумма идёт вложенным
объектом `paymentDetails`, а не полем `amount` верхнего уровня — иначе
Platega отвечает 400 «The PaymentDetails field is required». Идентификатор
транзакции в ответе называется `transactionId`, ссылка на оплату —
`redirect` (у эндпоинта статуса тот же идентификатор приходит как `id`).

Статус платежа из вебхука не считается доверенным сам по себе: после
получения колбэка мы всегда перезапрашиваем транзакцию через `get_transaction`
своими же учётными данными и верим только этому ответу. Это надёжнее, чем
проверять подпись колбэка по недокументированной схеме, и не хуже: подделать
такой ответ может только тот, у кого есть наш секрет мерчанта.
"""

from typing import Any

import httpx

BASE_URL = "https://app.platega.io"

# Способ оплаты: 2 — СБП QR.
PAYMENT_METHOD_SBP = 2


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
    ) -> dict[str, Any]:
        """Возвращает тело ответа Platega с `transactionId` и `redirect`.

        Комиссию Platega добавляет к сумме сама (её платит клиент), поэтому
        здесь передаётся именно та сумма, которую должен получить мерчант.
        """
        payload = {
            "paymentMethod": PAYMENT_METHOD_SBP,
            "paymentDetails": {"amount": round(amount_rub, 2), "currency": "RUB"},
            "description": description,
            # Ключи возврата называются именно так — `returnUrl` Platega игнорирует.
            "return": return_url,
            "failedUrl": return_url,
            # Свой идентификатор заказа: виден в ответе статуса как `payload`.
            "payload": order_id,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{BASE_URL}/transaction/process",
                json=payload,
                headers=self._headers(),
            )
        if response.status_code >= 400:
            raise PlategaError(f"Platega вернула {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise PlategaError("Platega вернула не JSON") from exc

    async def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{BASE_URL}/transaction/{transaction_id}", headers=self._headers()
            )
        if response.status_code >= 400:
            raise PlategaError(f"Platega вернула {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise PlategaError("Platega вернула не JSON") from exc

    @staticmethod
    def transaction_id(body: dict[str, Any]) -> str:
        """id транзакции из ответа. `id` — запасной ключ: так его называет
        эндпоинт статуса."""
        return str(body.get("transactionId") or body.get("id") or "")

    @staticmethod
    def pay_url(body: dict[str, Any]) -> str:
        return body.get("redirect") or body.get("redirectUrl") or body.get("url") or ""

    @staticmethod
    def is_paid(transaction: dict[str, Any]) -> bool:
        return str(transaction.get("status", "")).upper() in {"CONFIRMED", "PAID", "SUCCESS"}
