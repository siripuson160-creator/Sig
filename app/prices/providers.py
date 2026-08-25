"""Concrete price providers and the provider registry.

Adding a provider is one class plus one decorator; ``PRICE_DATA_PROVIDER`` in
the environment selects it at runtime.
"""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from typing import Callable

import httpx

from app.config import settings
from app.prices.base import Candle, PriceProvider

log = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[PriceProvider]] = {}


def register_provider(key: str) -> Callable[[type[PriceProvider]], type[PriceProvider]]:
    def decorator(cls: type[PriceProvider]) -> type[PriceProvider]:
        _PROVIDERS[key.lower()] = cls
        return cls

    return decorator


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)


def get_provider(name: str | None = None) -> PriceProvider:
    key = (name or settings.price_data_provider or "none").lower()
    cls = _PROVIDERS.get(key)
    if cls is None:
        log.error("unknown PRICE_DATA_PROVIDER=%r; falling back to 'none'", key)
        cls = _PROVIDERS["none"]
    return cls()


# --------------------------------------------------------------------- none
@register_provider("none")
class NullProvider(PriceProvider):
    """MVP default: no price feed configured.

    Signals are still parsed and stored; they simply stay at
    ``result=PENDING_RESULT`` until a real provider is configured, at which
    point the result engine judges them retroactively.
    """

    name = "none"
    available = False

    async def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        return []


# ---------------------------------------------------------------------- csv
@register_provider("csv")
class CsvProvider(PriceProvider):
    """Reads ``{PRICE_CSV_PATH}/{SYMBOL}_{TIMEFRAME}.csv``.

    Columns: ``timestamp,open,high,low,close`` where timestamp is ISO-8601 or a
    unix epoch. Useful for backfilling history exported from a broker/MT5.
    """

    name = "csv"

    async def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        path = os.path.join(settings.price_csv_path, f"{symbol.upper()}_{timeframe}.csv")
        if not os.path.exists(path):
            log.warning("csv price file not found: %s", path)
            return []

        candles: list[Candle] = []
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                ts = _parse_timestamp(row.get("timestamp") or row.get("time") or row.get("date"))
                if ts is None or ts < start or ts > end:
                    continue
                try:
                    candles.append(
                        Candle(
                            ts=ts,
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        candles.sort(key=lambda c: c.ts)
        return candles


# -------------------------------------------------------------- twelve data
@register_provider("twelvedata")
class TwelveDataProvider(PriceProvider):
    """Twelve Data time_series endpoint (needs ``PRICE_API_KEY``)."""

    name = "twelvedata"
    _INTERVALS = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1day"}
    _SYMBOLS = {"XAUUSD": "XAU/USD", "XAGUSD": "XAG/USD", "BTCUSD": "BTC/USD"}

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:  # type: ignore[override]
        return bool(settings.price_api_key)

    def supports_timeframe(self, timeframe: str) -> bool:
        return timeframe in self._INTERVALS

    async def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        if not settings.price_api_key:
            log.warning("PRICE_API_KEY missing; twelvedata provider returns no data")
            return []
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))

        params = {
            "symbol": self._SYMBOLS.get(symbol.upper(), symbol.upper()),
            "interval": self._INTERVALS.get(timeframe, "1min"),
            "start_date": start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": "UTC",
            "order": "ASC",
            "outputsize": 5000,
            "apikey": settings.price_api_key,
        }
        try:
            response = await self._client.get("https://api.twelvedata.com/time_series", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("twelvedata request failed: %s", exc)
            return []

        if payload.get("status") == "error":
            log.warning("twelvedata error: %s", payload.get("message"))
            return []

        candles: list[Candle] = []
        for row in payload.get("values", []):
            ts = _parse_timestamp(row.get("datetime"))
            if ts is None:
                continue
            try:
                candles.append(
                    Candle(
                        ts=ts,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        candles.sort(key=lambda c: c.ts)
        return candles

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.isdigit():
        seconds = int(raw)
        if seconds > 10_000_000_000:  # milliseconds
            seconds //= 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
