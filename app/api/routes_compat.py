"""The endpoint names listed in section 61.

The dashboard itself talks to ``/api/public/*``. These are the paths the brief
specifies, kept as thin wrappers so both spellings work and neither can drift
from the other.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import routes_public as public
from app.db.session import get_session

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard/summary")
async def summary(
    range: str = Query("all", pattern=public.RANGE_PATTERN),
    date_from: str | None = None,
    date_to: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await public.overview(range=range, date_from=date_from, date_to=date_to, session=session)


@router.get("/api/dashboard/daily")
async def daily(limit: int = Query(60, ge=1, le=400), session: AsyncSession = Depends(get_session)) -> dict:
    return await public.performance(granularity="daily", limit=limit, session=session)


@router.get("/api/dashboard/weekly")
async def weekly(limit: int = Query(60, ge=1, le=400), session: AsyncSession = Depends(get_session)) -> dict:
    return await public.performance(granularity="weekly", limit=limit, session=session)


@router.get("/api/dashboard/monthly")
async def monthly(limit: int = Query(60, ge=1, le=400), session: AsyncSession = Depends(get_session)) -> dict:
    return await public.performance(granularity="monthly", limit=limit, session=session)


@router.get("/api/dashboard/equity")
async def equity(
    range: str = Query("all", pattern=public.RANGE_PATTERN),
    date_from: str | None = None,
    date_to: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await public.equity(range=range, date_from=date_from, date_to=date_to, session=session)


@router.get("/api/signals")
async def signals(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    result: str | None = None,
    direction: str | None = None,
    range: str = Query("all", pattern=public.RANGE_PATTERN),
    date_from: str | None = None,
    date_to: str | None = None,
    complete_only: bool = True,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await public.list_signals(
        limit=limit,
        offset=offset,
        status=status,
        result=result,
        direction=direction,
        range=range,
        date_from=date_from,
        date_to=date_to,
        complete_only=complete_only,
        session=session,
    )


@router.get("/api/signals/{signal_id}")
async def signal(signal_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    return await public.signal_by_id(signal_id=signal_id, session=session)


@router.get("/api/signals/{signal_id}/history")
async def signal_history(signal_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Edit history of one signal: every Telegram version and every parse."""
    detail = await public.signal_by_id(signal_id=signal_id, session=session)
    return {
        "signal_id": detail["signal_id"],
        "telegram_message_id": detail["telegram_message_id"],
        "versions": detail["message_history"],
        "parses": detail["parse_history"],
    }


@router.get("/api/statistics")
async def statistics(
    range: str = Query("all", pattern=public.RANGE_PATTERN),
    date_from: str | None = None,
    date_to: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Summary plus the breakdowns, in one call."""
    overview = await public.overview(range=range, date_from=date_from, date_to=date_to, session=session)
    analytics = await public.analytics(range=range, date_from=date_from, date_to=date_to, session=session)
    return {"overview": overview, "analytics": analytics}
