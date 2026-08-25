"""Helpers for building database fixtures in tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db.models import Direction, Signal, SignalResult, SignalStatus

BASE = datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc)  # 10:00 in Asia/Bangkok


def make_signal(
    *,
    offset_minutes: int = 0,
    direction: str = "BUY",
    entry: float = 3340,
    sl: float = 3330,
    tp1: float | None = 3350,
    tp2: float | None = 3360,
    result: SignalResult = SignalResult.WIN,
    status: SignalStatus | None = None,
    points: float = 10.0,
    message_id: int | None = None,
    is_complete: bool = True,
) -> Signal:
    """A signal already carrying its verdict, for statistics tests."""
    signal_time = BASE + timedelta(minutes=offset_minutes)
    resolved = signal_time + timedelta(minutes=30) if result != SignalResult.PENDING_RESULT else None
    if status is None:
        status = {
            SignalResult.WIN: SignalStatus.TP1_HIT,
            SignalResult.LOSS: SignalStatus.SL_HIT,
            SignalResult.BREAKEVEN: SignalStatus.CLOSED,
            SignalResult.AMBIGUOUS: SignalStatus.AMBIGUOUS,
            SignalResult.CANCELLED: SignalStatus.CANCELLED,
            SignalResult.PENDING_RESULT: SignalStatus.ACTIVE,
        }[result]

    return Signal(
        signal_id=str(uuid.uuid4()),
        telegram_chat_id=-1001234567890,
        telegram_message_id=message_id if message_id is not None else 1000 + offset_minutes,
        source_version=1,
        direction=Direction(direction),
        symbol="XAUUSD",
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        signal_time=signal_time,
        created_at=signal_time,
        updated_at=signal_time,
        resolved_at=resolved,
        status=status,
        result=result,
        profit_points=max(points, 0.0) if result != SignalResult.PENDING_RESULT else None,
        loss_points=abs(min(points, 0.0)) if result != SignalResult.PENDING_RESULT else None,
        max_tp_hit=1 if result == SignalResult.WIN else 0,
        is_complete=is_complete,
        confidence=1.0 if is_complete else 0.3,
        price_source="csv",
        raw_text=f"{direction} GOLD {entry} SL {sl} TP1 {tp1}",
    )
