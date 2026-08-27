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

from app import audit
from app.config import settings
from app.db.models import (
    AuditEvent,
    Direction,
    Signal,
    SignalResult,
    SignalStatus,
    SignalVersion,
    TelegramMessage,
    utcnow,
)
from app.signals.outcomes import describe as describe_outcome
from app.signals.outcomes import parse_outcome
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
    await audit.record(
        session,
        AuditEvent.SIGNAL_CREATED,
        entity_type="signal",
        entity_id=signal.signal_id,
        summary=f"Signal created from message {message.chat_id}/{message.message_id} v{message.version}",
        new_value=audit.signal_snapshot(signal),
        actor="telegram",
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

    before = audit.signal_snapshot(signal)
    _apply_parse(signal, parsed, message)
    signal.source_version = message.version
    signal.signal_time = message.created_at or message.received_at
    signal.updated_at = utcnow()
    await session.flush()
    await _snapshot(session, signal, message, parsed)
    log.info("signal %s updated from edit v%s (complete=%s)", signal.signal_id, message.version, signal.is_complete)

    old, new = audit.diff(before, audit.signal_snapshot(signal))
    await audit.record(
        session,
        AuditEvent.SIGNAL_EDITED,
        entity_type="signal",
        entity_id=signal.signal_id,
        summary=f"Signal updated from Telegram edit v{message.version}",
        old_value=old,
        new_value=new,
        reason="source message was edited",
        actor="telegram",
    )
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




# --------------------------------------- results the source announces itself
async def apply_claimed_outcome(session: AsyncSession, message: TelegramMessage) -> Signal | None:
    """Update the signal a follow-up message reports on.

    Only runs when ``RESULT_SOURCE=message``. The link is the Telegram reply:
    "90 Pips! Can secure as TP2" is posted as a reply to the signal, and that
    is what says which trade it is about. A message that replies to nothing, or
    to something that is not a tracked signal, is left alone.

    Returns the signal that was updated, or ``None``.
    """
    if settings.result_source != "message":
        return None
    if not message.reply_to_message_id:
        return None

    signal = await get_signal_for_message(session, message.chat_id, message.reply_to_message_id)
    if signal is None:
        return None
    if signal.manual_override:
        log.info("signal %s has a manual override; claim ignored", signal.signal_id)
        return signal

    outcome = parse_outcome(message.content)
    if outcome.is_empty:
        return None

    before = audit.signal_snapshot(signal)
    changed = _apply_claim(signal, outcome)
    if not changed:
        return signal

    signal.result_source = "MESSAGE"
    signal.updated_at = utcnow()
    note = describe_outcome(outcome)
    signal.evaluation_note = f"reported by the source: {note}"
    log.info("signal %s updated from the source's own report: %s", signal.signal_id, note)

    await audit.record(
        session,
        AuditEvent.SIGNAL_RESULT_UPDATED,
        entity_type="signal",
        entity_id=signal.signal_id,
        summary=f"Result taken from the source's message {message.chat_id}/{message.message_id}: {note}",
        old_value=before,
        new_value=audit.signal_snapshot(signal),
        actor="telegram",
    )
    return signal


def _apply_claim(signal: Signal, outcome) -> bool:
    """Write a claimed outcome onto the signal. Returns True if anything moved.

    A trade is only decided once: a later "SL hit" after a reported TP does not
    rewrite the booked result, matching how the price engine treats its own
    verdicts.
    """
    if signal.status in _LOCKED_STATUSES:
        return False

    claimed = outcome.claimed_points()

    if outcome.cancelled:
        signal.status = SignalStatus.CANCELLED
        signal.result = SignalResult.CANCELLED
        signal.profit_points = None
        signal.loss_points = None
        signal.resolved_at = utcnow()
        return True

    if outcome.sl_hit:
        signal.status = SignalStatus.SL_HIT
        signal.result = SignalResult.LOSS
        # A loss announced as "-30 pips" is used as given; otherwise the
        # distance from entry to the stop is the honest fallback.
        loss = abs(claimed) if claimed is not None else _distance(signal.entry, signal.sl)
        signal.profit_points = 0.0
        signal.loss_points = loss
        signal.resolved_at = utcnow()
        return True

    if outcome.tp_hit:
        level = {1: signal.tp1, 2: signal.tp2, 3: signal.tp3}.get(outcome.tp_hit)
        gain = claimed if claimed is not None else _distance(signal.entry, level)
        if gain is None:
            return False
        signal.status = {
            1: SignalStatus.TP1_HIT,
            2: SignalStatus.TP2_HIT,
            3: SignalStatus.TP3_HIT,
        }.get(outcome.tp_hit, SignalStatus.TP3_HIT)
        signal.result = SignalResult.WIN if gain > 0 else SignalResult.BREAKEVEN
        signal.profit_points = max(gain, 0.0)
        signal.loss_points = 0.0
        signal.max_tp_hit = max(signal.max_tp_hit, outcome.tp_hit)
        signal.resolved_at = utcnow()
        return True

    if outcome.closed:
        signal.status = SignalStatus.CLOSED
        if claimed is None:
            # Closed with no number attached: nothing to publish but the fact.
            signal.result = SignalResult.PENDING_RESULT
            signal.resolved_at = utcnow()
            return True
        signal.result = (
            SignalResult.WIN if claimed > 0 else SignalResult.LOSS if claimed < 0 else SignalResult.BREAKEVEN
        )
        signal.profit_points = max(claimed, 0.0)
        signal.loss_points = abs(min(claimed, 0.0))
        signal.resolved_at = utcnow()
        return True

    if claimed is not None:
        # An announced figure is the result the source is claiming:
        #
        #     "+70Pips making profit again. Be secure and set your breakeven."
        #
        # This desk reports its wins as a pip count rather than by naming a
        # target, so waiting for the words "TP1" would leave a won trade
        # sitting at PENDING for ever. The number is booked.
        #
        # The trade stays ACTIVE because it has not been closed — they are
        # still holding — so a later, larger announcement on the same trade
        # replaces this one. That is the trade improving, not a second
        # verdict, and it is why this is not in _LOCKED_STATUSES.
        booked = signal.profit_points if signal.result == SignalResult.WIN else None
        if booked is not None and claimed <= booked:
            return False  # already counted at least this much

        signal.status = SignalStatus.ACTIVE
        signal.result = (
            SignalResult.WIN if claimed > 0 else SignalResult.LOSS if claimed < 0 else SignalResult.BREAKEVEN
        )
        signal.profit_points = max(claimed, 0.0)
        signal.loss_points = abs(min(claimed, 0.0))
        return True

    if outcome.breakeven:
        # "Set breakeven" with no figure protects the trade without ending it
        # and without saying what it is worth, so there is nothing to publish.
        return False

    return False


def _distance(entry: float | None, level: float | None) -> float | None:
    if entry is None or level is None:
        return None
    points = abs(level - entry)
    return round(points / settings.point_size, 4) if settings.point_size else points
