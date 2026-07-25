"""Клиент CryptoBot (@CryptoBot / Crypto Pay API) — оплата USDT/TON.

Как и в Platega-клиенте: колбэку не доверяем, статус подтверждаем повторным
запросом `get_invoice` с собственным токеном приложения.
"""

import httpx

BASE_URL = "https://pay.crypt.bot/api"


class CryptoBotError(Exception):
    pass


class CryptoBotClient:
    def __init__(self, token: str, *, timeout: float = 15.0) -> None:
        self._token = token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Crypto-Pay-API-Token": self._token}

    async def _call(self, method: str, **params) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{BASE_URL}/{method}", json=params, headers=self._headers()
            )
        body = response.json()
        if not body.get("ok"):
            raise CryptoBotError(body.get("error", {}).get("name", "неизвестная ошибка"))
        return body["result"]

    async def create_invoice(
        self, *, amount: float, asset: str, description: str, payload: str
    ) -> dict:
        """asset: 'USDT' или 'TON'. Возвращает invoice с полем pay_url."""
        return await self._call(
            "createInvoice",
            asset=asset,
            amount=str(amount),
            description=description,
            payload=payload,
            expires_in=1800,
        )

    async def get_invoice(self, invoice_id: str) -> dict | None:
        result = await self._call("getInvoices", invoice_ids=str(invoice_id))
        items = result.get("items", [])
        return items[0] if items else None

    @staticmethod
    def is_paid(invoice: dict) -> bool:
        return invoice.get("status") == "paid"
