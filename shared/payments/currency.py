"""Курс рубля к криптовалюте для расчёта суммы счёта в CryptoBot.

CoinGecko — публичный API без ключа. Кэш на 5 минут в памяти процесса:
частые платежи не должны заваливать CoinGecko запросами, а курс за пять
минут для суммы счёта меняется несущественно.
"""

import time

import httpx

_ASSET_IDS = {"USDT": "tether", "TON": "the-open-network"}
_CACHE_TTL = 300
_cache: dict[str, tuple[float, float]] = {}  # asset -> (rate_rub, fetched_at)

# Небольшая наценка компенsирует комиссию и колебания курса за время оплаты.
_MARKUP = {"USDT": 1.02, "TON": 1.05}


class CurrencyError(Exception):
    pass


async def rub_to_crypto(amount_rub: float, asset: str) -> float:
    if asset not in _ASSET_IDS:
        raise CurrencyError(f"неизвестный актив {asset}")

    rate = await _rate_rub(asset)
    amount = (amount_rub / rate) * _MARKUP[asset]
    # 2 знака для USDT, 4 для TON — соответствует шагу цены в CryptoBot.
    return round(amount, 2 if asset == "USDT" else 4)


async def _rate_rub(asset: str) -> float:
    cached = _cache.get(asset)
    if cached and time.monotonic() - cached[1] < _CACHE_TTL:
        return cached[0]

    coingecko_id = _ASSET_IDS[asset]
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coingecko_id, "vs_currencies": "rub"},
        )
    if response.status_code >= 400:
        if cached:
            return cached[0]  # протухший курс лучше, чем отказ в оплате
        raise CurrencyError("не удалось получить курс валюты")

    rate = response.json().get(coingecko_id, {}).get("rub")
    if not rate:
        if cached:
            return cached[0]
        raise CurrencyError("CoinGecko не вернул курс")

    _cache[asset] = (rate, time.monotonic())
    return rate
