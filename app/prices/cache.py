"""Database-backed candle cache.

Price history is stored per provider so that a result can always be re-checked
against the same data that produced it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import PriceCandle
from app.prices.base import TIMEFRAME_SECONDS, Candle, PriceProvider

log = logging.getLogger(__name__)


async def _cached(
    session: AsyncSession, provider: str, symbol: str, timeframe: str, start: datetime, end: datetime
) -> list[Candle]:
    rows = await session.execute(
        select(PriceCandle)
        .where(
            PriceCandle.provider == provider,
            PriceCandle.symbol == symbol,
            PriceCandle.timeframe == timeframe,
            PriceCandle.ts >= start,
            PriceCandle.ts <= end,
        )
        .order_by(PriceCandle.ts.asc())
    )
    return [Candle(ts=r.ts, open=r.open, high=r.high, low=r.low, close=r.close) for r in rows.scalars().all()]


async def store_candles(
    session: AsyncSession, provider: str, symbol: str, timeframe: str, candles: list[Candle]
) -> int:
    if not candles:
        return 0
    payload = [
        {
            "provider": provider,
            "symbol": symbol,
            "timeframe": timeframe,
            "ts": c.ts,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
        }
        for c in candles
    ]
    insert = sqlite_insert if settings.is_sqlite else pg_insert
    statement = insert(PriceCandle).values(payload)
    statement = statement.on_conflict_do_nothing(index_elements=["provider", "symbol", "timeframe", "ts"])
    await session.execute(statement)
    return len(payload)


async def get_candles(
    session: AsyncSession,
    provider: PriceProvider,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    *,
    refresh: bool = True,
) -> list[Candle]:
    """Return candles for the window, fetching and caching whatever is missing."""
    if not provider.available:
        return []

    cached = await _cached(session, provider.name, symbol, timeframe, start, end)
    if not refresh:
        return cached

    step = timedelta(seconds=TIMEFRAME_SECONDS.get(timeframe, 60))
    fetch_from = start
    if cached:
        # Only the tail can be missing; earlier gaps are weekend/holiday closes.
        last = cached[-1].ts
        if last >= end - step:
            return cached
        fetch_from = last + step

    fresh = await provider.get_candles(symbol, timeframe, fetch_from, end)
    if fresh:
        await store_candles(session, provider.name, symbol, timeframe, fresh)
        await session.flush()
        merged = {c.ts: c for c in cached}
        merged.update({c.ts: c for c in fresh if start <= c.ts <= end})
        return [merged[ts] for ts in sorted(merged)]

    return cached
