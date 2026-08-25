"""Price provider interface (section 18).

A provider only has to answer one question: "what did price do between A and
B?". Everything else — caching, TP/SL evaluation, statistics — is provider
agnostic, and each signal records which provider judged it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

#: Timeframe label -> length in seconds.
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float

    def touches(self, price: float) -> bool:
        return self.low <= price <= self.high


class PriceProvider(ABC):
    """Base class for price sources."""

    #: Stored on every signal it judges, so results stay auditable.
    name: str = "base"
    #: False means "no price data available" — signals stay PENDING_RESULT.
    available: bool = True

    @abstractmethod
    async def get_candles(
        self, symbol: str, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """Return candles in ascending time order, inclusive of ``start``."""

    def supports_timeframe(self, timeframe: str) -> bool:
        return timeframe in TIMEFRAME_SECONDS

    async def close(self) -> None:  # pragma: no cover - most providers are stateless
        return None

    def describe(self) -> dict:
        return {"name": self.name, "available": self.available}
