"""Read-only dashboard API for members (sections 22, 61).

Every route here is a GET. There is deliberately no write path: a member can
read the numbers and the history, and nothing else.
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.broadcast import _broadcast_page
from app.api.serializers import signal_detail, signal_summary
from app.config import settings
from app.db.models import Direction, Signal, SignalResult, SignalStatus, SignalVersion, TelegramMessage
from app.db.session import get_session
from app.engine import stats_engine
from app.signals.parser import describe_parsers

router = APIRouter(prefix="/api/public", tags=["public"])

#: Shown on the dashboard and returned with the methodology (section 76).
DISCLAIMER = (
    "Trading involves significant risk. Historical signal performance does not guarantee future "
    "results. Actual trading results may differ due to spread, slippage, commissions, execution "
    "speed, liquidity and other market conditions. Displayed performance is based on the stated "
    "calculation methodology and is not a guarantee of future profitability."
)


def _enum_value(enum_cls, raw: str, field: str):
    """Reject an unknown filter value with a 400 rather than a 500."""
    try:
        return enum_cls(raw.upper())
    except ValueError:
        allowed = ", ".join(member.value for member in enum_cls)
        raise HTTPException(status_code=400, detail=f"unknown {field}: {raw}. Allowed: {allowed}") from None


#: Period selectors from section 40, plus the equity-curve windows of section 29.
RANGE_PATTERN = r"^(all|today|yesterday|wtd|mtd|ytd|custom|\d{1,3}[dmy])$"


@router.get("/overview")
async def overview(
    range: str = Query("all", pattern=RANGE_PATTERN),
    date_from: str | None = Query(None, description="YYYY-MM-DD, inclusive"),
    date_to: str | None = Query(None, description="YYYY-MM-DD, inclusive"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await stats_engine.build_overview(session, range, date_from=date_from, date_to=date_to)


@router.get("/signals")
async def list_signals(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    result: str | None = None,
    direction: str | None = None,
    range: str = Query("all", pattern=RANGE_PATTERN),
    date_from: str | None = None,
    date_to: str | None = None,
    complete_only: bool = True,
    session: AsyncSession = Depends(get_session),
) -> dict:
    query = select(Signal)
    count_query = select(func.count()).select_from(Signal)

    filters = []
    start, end = stats_engine.range_bounds(range, date_from=date_from, date_to=date_to)
    if start is not None:
        filters.append(Signal.signal_time >= start)
    if end is not None:
        filters.append(Signal.signal_time <= end)
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
    range: str = Query("all", pattern=RANGE_PATTERN),
    date_from: str | None = None,
    date_to: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await stats_engine.build_analytics(session, range, date_from=date_from, date_to=date_to)


@router.get("/equity")
async def equity(
    range: str = Query("all", pattern=RANGE_PATTERN),
    date_from: str | None = None,
    date_to: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cumulative P/L curve on its own (section 29)."""
    start, end = stats_engine.range_bounds(range, date_from=date_from, date_to=date_to)
    rows = await stats_engine.load_rows(session, start=start, end=end)
    return {"range": range, "timezone": settings.timezone, "points": stats_engine.equity_curve(rows)}


@router.get("/broadcast")
async def broadcast(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The archive of what was posted to the LINE group.

    Off unless PUBLIC_BROADCAST_ENABLED is set: the signal text is what members
    pay for, and this dashboard is readable by anyone with the URL.
    """
    if not settings.public_broadcast_enabled:
        raise HTTPException(status_code=404, detail="the broadcast archive is not published")
    return await _broadcast_page(session, limit=limit, offset=offset)


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
        "result_source": settings.result_source,
        "results_are_verified": settings.result_source == "price",
        "entry_fill_window_hours": settings.entry_fill_window_hours,
        "signal_expiry_hours": settings.signal_expiry_hours,
        "parsers": describe_parsers(),
        "rules": [
            (
                "Results are worked out from price history: each signal is replayed against the "
                "market and judged on what the price actually did."
                if settings.result_source == "price"
                else "Results are taken from what the signal provider reports about its own trades "
                "— a message such as \"90 Pips! Can secure as TP2\" decides the outcome. These "
                "figures are self-reported and are not checked against price history."
            ),
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
            "Win rate = wins / (wins + losses) x 100. Pending, active and cancelled signals are excluded.",
            "Profit factor = gross profit / gross loss.",
            "Maximum drawdown is the largest peak-to-trough fall of the cumulative points curve.",
            "Risk and reward are measured from the posted levels: entry to stop, and entry to TP1.",
            "Take-profit counts are cumulative: a signal that reached TP2 is counted under TP1 as well.",
            "Corrections made by hand are recorded in the audit log with the old value, the new value, "
            "who made the change, when, and why. Nothing is edited silently.",
        ],
        "disclaimer": DISCLAIMER,
    }


async def _fingerprint(session: AsyncSession) -> str:
    """Cheap summary of "has anything changed?"."""
    signals, messages, latest = (
        await session.execute(
            select(
                func.count(Signal.signal_id),
                select(func.count()).select_from(TelegramMessage).scalar_subquery(),
                func.max(Signal.updated_at),
            )
        )
    ).one()
    return f"{signals}:{messages}:{latest.isoformat() if latest else '-'}"


@router.get("/stream")
async def stream(request: Request, session: AsyncSession = Depends(get_session)) -> StreamingResponse:
    """Server-sent events: one message whenever the data changes (section 47).

    The dashboard falls back to polling if the connection drops, so this is an
    optimisation rather than a requirement.
    """

    async def events():
        last = ""
        # Tell the client how long to wait before reconnecting.
        yield f"retry: {settings.dashboard_refresh_seconds * 1000}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                current = await _fingerprint(session)
            except Exception:  # pragma: no cover - transient database issue
                current = last
            if current != last:
                last = current
                yield f"event: changed\ndata: {json.dumps({'fingerprint': current})}\n\n"
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(settings.dashboard_refresh_seconds)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    total = (await session.execute(select(func.count()).select_from(Signal))).scalar_one()
    messages = (await session.execute(select(func.count()).select_from(TelegramMessage))).scalar_one()
    return {
        "status": "ok",
        "signals": int(total),
        "messages": int(messages),
        "timezone": settings.timezone,
        "refresh_seconds": settings.dashboard_refresh_seconds,
        "dry_run": settings.dry_run,
    }
