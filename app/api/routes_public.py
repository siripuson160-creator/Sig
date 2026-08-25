"""Read-only dashboard API for members (section 22)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serializers import signal_detail, signal_summary
from app.config import settings
from app.db.models import Direction, Signal, SignalResult, SignalStatus, SignalVersion, TelegramMessage
from app.db.session import get_session
from app.engine import stats_engine
from app.signals.parser import describe_parsers

router = APIRouter(prefix="/api/public", tags=["public"])


def _enum_value(enum_cls, raw: str, field: str):
    """Reject an unknown filter value with a 400 rather than a 500."""
    try:
        return enum_cls(raw.upper())
    except ValueError:
        allowed = ", ".join(member.value for member in enum_cls)
        raise HTTPException(status_code=400, detail=f"unknown {field}: {raw}. Allowed: {allowed}") from None


@router.get("/overview")
async def overview(
    range: str = Query("all", pattern="^(all|today|yesterday|mtd|\\d{1,3}d)$"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await stats_engine.build_overview(session, range)


@router.get("/signals")
async def list_signals(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    result: str | None = None,
    direction: str | None = None,
    complete_only: bool = True,
    session: AsyncSession = Depends(get_session),
) -> dict:
    query = select(Signal)
    count_query = select(func.count()).select_from(Signal)

    filters = []
    if complete_only:
        filters.append(Signal.is_complete.is_(True))
    if status:
        filters.append(Signal.status == _enum_value(SignalStatus, status, "status"))
    if result:
        filters.append(Signal.result == _enum_value(SignalResult, result, "result"))
    if direction:
        filters.append(Signal.direction == _enum_value(Direction, direction, "direction"))
    for condition in filters:
        query = query.where(condition)
        count_query = count_query.where(condition)

    total = (await session.execute(count_query)).scalar_one()
    rows = await session.execute(query.order_by(Signal.signal_time.desc()).limit(limit).offset(offset))
    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "items": [signal_summary(s) for s in rows.scalars().all()],
    }


@router.get("/signals/{signal_id}")
async def signal_by_id(signal_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    signal = (await session.execute(select(Signal).where(Signal.signal_id == signal_id))).scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")

    versions = (
        (
            await session.execute(
                select(SignalVersion)
                .where(SignalVersion.signal_id == signal_id)
                .order_by(SignalVersion.version.asc())
            )
        )
        .scalars()
        .all()
    )
    messages = (
        (
            await session.execute(
                select(TelegramMessage)
                .where(
                    TelegramMessage.chat_id == signal.telegram_chat_id,
                    TelegramMessage.message_id == signal.telegram_message_id,
                )
                .order_by(TelegramMessage.version.asc())
            )
        )
        .scalars()
        .all()
    )
    return signal_detail(signal, list(versions), list(messages))


@router.get("/performance/{granularity}")
async def performance(
    granularity: Literal["daily", "weekly", "monthly"],
    limit: int = Query(60, ge=1, le=400),
    session: AsyncSession = Depends(get_session),
) -> dict:
    buckets = await stats_engine.build_performance(session, granularity, limit)
    return {"granularity": granularity, "timezone": settings.timezone, "items": buckets}


@router.get("/analytics")
async def analytics(
    range: str = Query("all", pattern="^(all|today|yesterday|mtd|\\d{1,3}d)$"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await stats_engine.build_analytics(session, range)


@router.get("/methodology")
async def methodology() -> dict:
    """How the numbers are produced — shown verbatim on the dashboard."""
    return {
        "timezone": settings.timezone,
        "unit": "points",
        "point_size": settings.point_size,
        "price_source": settings.price_data_provider,
        "price_timeframe": settings.price_timeframe,
        "ambiguity_rule": settings.ambiguity_rule,
        "result_mode": settings.result_mode,
        "entry_fill_window_hours": settings.entry_fill_window_hours,
        "signal_expiry_hours": settings.signal_expiry_hours,
        "parsers": describe_parsers(),
        "rules": [
            "Every message from the source group is forwarded to LINE, whether or not it is a signal.",
            "An edited Telegram message is delivered as a new LINE message prefixed with EDITED; "
            "nothing already sent is edited or deleted.",
            "A signal counts as a trade only when it states a direction, an entry, a stop loss "
            "and at least one take profit.",
            "A trade must fill: price has to touch the entry within "
            f"{settings.entry_fill_window_hours} hours, otherwise the signal is marked CANCELLED.",
            "Take profits form a ladder: reaching TP2 implies TP1.",
            f"Result mode {settings.result_mode}: a stop loss touched after a take profit is booked "
            "at the best take profit reached.",
            f"When one candle contains both a take profit and the stop loss, the engine first tries a "
            f"finer timeframe; if the order still cannot be established, rule {settings.ambiguity_rule} applies.",
            f"Trades still open after {settings.signal_expiry_hours} hours are closed at the last known price.",
            "P/L is measured in points from entry to exit. No money figures are shown because lot size, "
            "spread, commission and swap are not known to this system.",
            "PENDING, AMBIGUOUS and CANCELLED signals are reported separately and are never hidden from the totals.",
        ],
    }


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    total = (await session.execute(select(func.count()).select_from(Signal))).scalar_one()
    messages = (await session.execute(select(func.count()).select_from(TelegramMessage))).scalar_one()
    return {"status": "ok", "signals": int(total), "messages": int(messages), "timezone": settings.timezone}
