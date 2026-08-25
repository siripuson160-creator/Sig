"""Admin API. Everything here requires the ``X-Admin-Key`` header."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serializers import signal_summary, telegram_message
from app.config import settings
from app.db.models import (
    DeliveryStatus,
    Signal,
    SignalResult,
    SignalStatus,
    TelegramMessage,
    utcnow,
)
from app.db.session import get_session, healthcheck
from app.engine.result_engine import ResultEngine
from app.line.client import LineClient
from app.prices.providers import available_providers, get_provider
from app.processor.message_processor import delivery_stats
from app.signals.service import reparse_signal

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="admin API disabled (ADMIN_API_KEY is not set)")
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="invalid admin key")


@router.get("/status", dependencies=[Depends(require_admin)])
async def status(session: AsyncSession = Depends(get_session)) -> dict:
    provider = get_provider()
    counts = await delivery_stats(session)
    open_signals = (
        await session.execute(
            select(func.count())
            .select_from(Signal)
            .where(Signal.status.in_([SignalStatus.PENDING, SignalStatus.ACTIVE]))
        )
    ).scalar_one()
    return {
        "database": await healthcheck(),
        "delivery": counts,
        "open_signals": int(open_signals),
        "line_configured": bool(settings.line_channel_access_token and settings.line_target_id),
        "line_enabled": settings.line_enabled,
        "telegram_source": settings.source_chat_ids,
        "price_provider": provider.describe(),
        "available_price_providers": available_providers(),
        "timezone": settings.timezone,
    }


@router.get("/messages", dependencies=[Depends(require_admin)])
async def messages(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    query = select(TelegramMessage)
    count_query = select(func.count()).select_from(TelegramMessage)
    if status:
        query = query.where(TelegramMessage.status == status.upper())
        count_query = count_query.where(TelegramMessage.status == status.upper())
    total = (await session.execute(count_query)).scalar_one()
    rows = await session.execute(query.order_by(TelegramMessage.id.desc()).limit(limit).offset(offset))
    return {
        "total": int(total),
        "items": [telegram_message(m) for m in rows.scalars().all()],
    }


@router.post("/messages/{row_id}/requeue", dependencies=[Depends(require_admin)])
async def requeue(row_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    message = await session.get(TelegramMessage, row_id)
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")
    if message.status == DeliveryStatus.SENT:
        raise HTTPException(status_code=409, detail="message was already delivered to LINE")
    message.status = DeliveryStatus.PENDING
    message.send_attempts = 0
    message.last_error = None
    await session.commit()
    return telegram_message(message)


class SignalPatch(BaseModel):
    """Manual correction of a signal.

    Setting any field marks the signal as manually overridden so the result
    engine and later Telegram edits stop touching it.
    """

    status: SignalStatus | None = None
    result: SignalResult | None = None
    profit_points: float | None = Field(default=None, ge=0)
    loss_points: float | None = Field(default=None, ge=0)
    note: str | None = None
    release_override: bool = False


@router.patch("/signals/{signal_id}", dependencies=[Depends(require_admin)])
async def patch_signal(
    signal_id: str, patch: SignalPatch, session: AsyncSession = Depends(get_session)
) -> dict:
    signal = (await session.execute(select(Signal).where(Signal.signal_id == signal_id))).scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")

    if patch.release_override:
        signal.manual_override = False
        signal.evaluation_note = patch.note or signal.evaluation_note
        await session.commit()
        return signal_summary(signal)

    changed = False
    for field_name in ("status", "result", "profit_points", "loss_points"):
        value = getattr(patch, field_name)
        if value is not None:
            setattr(signal, field_name, value)
            changed = True
    if patch.note is not None:
        signal.evaluation_note = patch.note
        changed = True
    if not changed:
        raise HTTPException(status_code=400, detail="no fields to update")

    signal.manual_override = True
    signal.updated_at = utcnow()
    if patch.result in (SignalResult.WIN, SignalResult.LOSS, SignalResult.BREAKEVEN) and signal.resolved_at is None:
        signal.resolved_at = utcnow()
    await session.commit()
    return signal_summary(signal)


@router.post("/signals/{signal_id}/reparse", dependencies=[Depends(require_admin)])
async def reparse(signal_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    signal = (await session.execute(select(Signal).where(Signal.signal_id == signal_id))).scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")
    updated = await reparse_signal(session, signal)
    if updated is None:
        raise HTTPException(status_code=409, detail="source Telegram message is missing")
    await session.commit()
    return signal_summary(updated)


@router.post("/signals/{signal_id}/evaluate", dependencies=[Depends(require_admin)])
async def evaluate_now(signal_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    signal = (await session.execute(select(Signal).where(Signal.signal_id == signal_id))).scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")
    engine = ResultEngine()
    changed = await engine.evaluate_signal(session, signal)
    await session.commit()
    return {"changed": changed, "signal": signal_summary(signal)}


@router.post("/line/test", dependencies=[Depends(require_admin)])
async def line_test() -> dict:
    """Verify the LINE credentials without posting to the group."""
    async with LineClient() as client:
        ok, detail = await client.verify()
    return {"ok": ok, "detail": detail}
