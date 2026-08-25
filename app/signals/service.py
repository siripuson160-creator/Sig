"""Persistence for parsed signals.

A signal is keyed by its Telegram message thread, so every edit of that message
updates the same signal row and appends a new immutable ``signal_versions``
snapshot (sections 12 and 16).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Direction, Signal, SignalResult, SignalStatus, SignalVersion, TelegramMessage, utcnow
from app.signals.parser import ParsedSignal, parse_signal

log = logging.getLogger(__name__)

#: Once a signal has been judged we keep the verdict; a later edit of the
#: Telegram message must not rewrite history.
_LOCKED_STATUSES = {
    SignalStatus.TP1_HIT,
    SignalStatus.TP2_HIT,
    SignalStatus.TP3_HIT,
    SignalStatus.SL_HIT,
    SignalStatus.CLOSED,
    SignalStatus.AMBIGUOUS,
}


async def get_signal_for_message(session: AsyncSession, chat_id: int, message_id: int) -> Signal | None:
    result = await session.execute(
        select(Signal).where(Signal.telegram_chat_id == chat_id, Signal.telegram_message_id == message_id)
    )
    return result.scalar_one_or_none()


async def upsert_signal_from_message(session: AsyncSession, message: TelegramMessage) -> Signal | None:
    """Parse ``message`` and create or update the signal behind it.

    Returns the signal, or ``None`` when the text is not a signal at all (a
    greeting, a "close half" instruction). Delivery to LINE happens regardless —
    this only decides whether the message also becomes a tracked trade.
    """
    parsed = parse_signal(message.content)
    existing = await get_signal_for_message(session, message.chat_id, message.message_id)

    if parsed is None:
        if existing is not None:
            log.info(
                "signal %s: edit v%s no longer parses as a signal; previous parse kept",
                existing.signal_id,
                message.version,
            )
        return existing

    if existing is None:
        return await _create_signal(session, message, parsed)
    return await _update_signal(session, existing, message, parsed)


async def _create_signal(session: AsyncSession, message: TelegramMessage, parsed: ParsedSignal) -> Signal:
    signal = Signal(
        signal_id=str(uuid.uuid4()),
        telegram_chat_id=message.chat_id,
        telegram_message_id=message.message_id,
        source_version=message.version,
        signal_time=message.created_at or message.received_at,
        price_source=settings.price_data_provider,
        status=SignalStatus.PENDING,
        result=SignalResult.PENDING_RESULT,
    )
    _apply_parse(signal, parsed, message)
    session.add(signal)
    await session.flush()
    await _snapshot(session, signal, message, parsed, version=1)
    log.info(
        "signal %s created from %s/%s v%s (complete=%s)",
        signal.signal_id,
        message.chat_id,
        message.message_id,
        message.version,
        signal.is_complete,
    )
    return signal


async def _update_signal(
    session: AsyncSession, signal: Signal, message: TelegramMessage, parsed: ParsedSignal
) -> Signal:
    if signal.manual_override:
        log.info("signal %s has a manual override; edit v%s recorded but not applied", signal.signal_id, message.version)
        await _snapshot(session, signal, message, parsed)
        return signal

    if signal.status in _LOCKED_STATUSES:
        # The trade already played out. Keep the verdict, keep the history.
        log.info(
            "signal %s already resolved as %s; edit v%s recorded only",
            signal.signal_id,
            signal.status,
            message.version,
        )
        await _snapshot(session, signal, message, parsed)
        return signal

    _apply_parse(signal, parsed, message)
    signal.source_version = message.version
    signal.signal_time = message.created_at or message.received_at
    signal.updated_at = utcnow()
    await session.flush()
    await _snapshot(session, signal, message, parsed)
    log.info("signal %s updated from edit v%s (complete=%s)", signal.signal_id, message.version, signal.is_complete)
    return signal


def _apply_parse(signal: Signal, parsed: ParsedSignal, message: TelegramMessage) -> None:
    signal.direction = Direction(parsed.direction) if parsed.direction else None
    signal.symbol = parsed.symbol
    signal.entry = parsed.entry
    signal.sl = parsed.sl
    signal.tp1 = parsed.tp1
    signal.tp2 = parsed.tp2
    signal.tp3 = parsed.tp3
    signal.parser_name = parsed.parser_name
    signal.confidence = parsed.confidence
    signal.is_complete = parsed.is_complete
    signal.raw_text = message.content
    signal.evaluation_note = "; ".join(parsed.notes) if parsed.notes else None


async def _snapshot(
    session: AsyncSession,
    signal: Signal,
    message: TelegramMessage,
    parsed: ParsedSignal,
    version: int | None = None,
) -> SignalVersion:
    if version is None:
        current = await session.execute(
            select(func.coalesce(func.max(SignalVersion.version), 0)).where(SignalVersion.signal_id == signal.signal_id)
        )
        version = int(current.scalar_one()) + 1

    snapshot = SignalVersion(
        signal_id=signal.signal_id,
        version=version,
        telegram_version=message.version,
        raw_text=message.content,
        parsed=parsed.to_dict(),
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def reparse_signal(session: AsyncSession, signal: Signal) -> Signal | None:
    """Re-run the parser over the latest version of the source message.

    Used by the admin dashboard after a parser change.
    """
    latest = await session.execute(
        select(TelegramMessage)
        .where(
            TelegramMessage.chat_id == signal.telegram_chat_id,
            TelegramMessage.message_id == signal.telegram_message_id,
        )
        .order_by(TelegramMessage.version.desc())
        .limit(1)
    )
    message = latest.scalar_one_or_none()
    if message is None:
        return None
    parsed = parse_signal(message.content)
    if parsed is None:
        return signal
    signal.manual_override = False
    _apply_parse(signal, parsed, message)
    signal.source_version = message.version
    signal.updated_at = utcnow()
    await session.flush()
    await _snapshot(session, signal, message, parsed)
    return signal


