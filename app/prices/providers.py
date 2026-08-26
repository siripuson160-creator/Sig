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


# -------------------------------------------------------------------- yahoo
@register_provider("yahoo")
class YahooProvider(PriceProvider):
    """Yahoo Finance chart endpoint — no account and no API key.

    Good for crypto, FX majors and indices.

    **It cannot judge spot gold.** Yahoo carries ``GC=F``, the COMEX futures
    contract, which trades tens of dollars away from the XAUUSD spot price a
    signal group quotes. Judging a spot signal against futures prices would
    make every entry look missed and every result wrong, so this provider
    refuses XAUUSD rather than answer with the wrong instrument. Use
    ``twelvedata`` (free key) for gold, or set ``PRICE_SYMBOL=GC=F`` if your
    group really does post futures levels.

    Intraday history is limited by Yahoo: 1-minute candles only go back about
    7 days, 5-minute about 60. Older signals fall back to the coarsest
    timeframe that still covers them.
    """

    name = "yahoo"
    _BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
    _INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "60m", "4h": "1h", "1d": "1d"}
    #: How far back Yahoo will serve each interval.
    _MAX_AGE_DAYS = {"1m": 7, "5m": 59, "15m": 59, "30m": 59, "60m": 729, "1h": 729, "1d": 10000}
    #: Only instruments Yahoo actually carries at the right price.
    _SYMBOLS = {
        "EURUSD": "EURUSD=X",
        "GBPUSD": "GBPUSD=X",
        "USDJPY": "USDJPY=X",
        "BTCUSD": "BTC-USD",
        "ETHUSD": "ETH-USD",
        "US30": "^DJI",
        "NAS100": "^NDX",
        "SPX500": "^GSPC",
    }

    #: Symbols Yahoo has no spot price for. Answering with the futures contract
    #: instead would quietly produce wrong results, so these are refused.
    _NO_SPOT = {
        "XAUUSD": ("gold", "GC=F"),
        "XAGUSD": ("silver", "SI=F"),
    }

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def supports_timeframe(self, timeframe: str) -> bool:
        return timeframe in self._INTERVALS

    def _interval_for(self, timeframe: str, start: datetime) -> str:
        """Coarsen the interval when the window is older than Yahoo will serve."""
        interval = self._INTERVALS.get(timeframe, "1m")
        age_days = (datetime.now(timezone.utc) - start).days
        order = ["1m", "5m", "15m", "30m", "60m", "1d"]
        if interval not in order:
            return interval
        for candidate in order[order.index(interval) :]:
            if age_days <= self._MAX_AGE_DAYS.get(candidate, 0):
                return candidate
        return "1d"

    async def get_candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0),
                # The endpoint refuses requests without a browser-ish agent.
                headers={"User-Agent": "Mozilla/5.0 (compatible; signal-dashboard/1.0)"},
            )

        upper = symbol.upper()
        if upper in self._NO_SPOT:
            metal, future = self._NO_SPOT[upper]
            log.error(
                "PRICE_DATA_PROVIDER=yahoo cannot price %s: Yahoo only carries %s, the futures "
                "contract, which trades away from spot. Results would be wrong, so nothing is "
                "returned. Use PRICE_DATA_PROVIDER=twelvedata with a free API key for spot %s, "
                "or set PRICE_SYMBOL=%s if your group posts futures levels.",
                upper,
                future,
                metal,
                future,
            )
            return []

        ticker = self._SYMBOLS.get(upper, upper)
        interval = self._interval_for(timeframe, start)
        # A little padding either side; Yahoo is exclusive at the edges.
        params = {
            "interval": interval,
            "period1": int(start.timestamp()) - 300,
            "period2": int(end.timestamp()) + 300,
            "includePrePost": "false",
        }

        try:
            response = await self._client.get(f"{self._BASE}/{ticker}", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("yahoo request for %s failed: %s", ticker, exc)
            return []

        error = (payload.get("chart") or {}).get("error")
        if error:
            log.warning("yahoo error for %s: %s", ticker, error)
            return []

        results = (payload.get("chart") or {}).get("result") or []
        if not results:
            return []

        block = results[0]
        stamps = block.get("timestamp") or []
        quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]

        candles: list[Candle] = []
        for index, stamp in enumerate(stamps):
            values = [quote.get(field, [])[index] if index < len(quote.get(field, [])) else None
                      for field in ("open", "high", "low", "close")]
            if any(value is None for value in values):
                continue  # Yahoo pads gaps with nulls.
            candles.append(
                Candle(
                    ts=datetime.fromtimestamp(stamp, tz=timezone.utc),
                    open=float(values[0]),
                    high=float(values[1]),
                    low=float(values[2]),
                    close=float(values[3]),
                )
            )
        candles.sort(key=lambda c: c.ts)
        return candles

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


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
