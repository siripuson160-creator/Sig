"""Decides whether a signal hit TP or SL (sections 17, 19, 20).

The core is :func:`evaluate`, a pure function over a list of candles, which is
what the tests exercise. The engine around it only supplies price data and
writes the verdict back to the database.

Rules
-----
* A trade must first fill: price has to touch the entry within
  ``ENTRY_FILL_WINDOW_HOURS``, otherwise the signal is CANCELLED (never taken).
* Levels are checked candle by candle in time order. TP levels are a ladder:
  reaching TP2 implies TP1.
* If a candle contains **both** a TP and the SL, the outcome is not guessed
  (section 19). The engine first tries to break the tie on a finer timeframe;
  if that is impossible it applies ``AMBIGUITY_RULE``:
  ``SL_FIRST`` (default, conservative), ``TP_FIRST``, or ``AMBIGUOUS``
  (recorded as such and excluded from win rate).
* P/L is measured in points, entry to exit (section 20). No money figures are
  produced anywhere (section 21).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    OPEN_STATUSES,
    Direction,
    Signal,
    SignalResult,
    SignalStatus,
    utcnow,
)
from app.db.session import session_scope
from app.prices import cache
from app.prices.base import TIMEFRAME_SECONDS, Candle, PriceProvider
from app.prices.providers import get_provider

log = logging.getLogger(__name__)

DrillDown = Callable[[datetime, datetime], Awaitable[list[Candle]]]


@dataclass
class SignalSpec:
    """The parts of a signal the evaluator needs."""

    direction: str
    entry: float
    sl: float
    tps: list[float]
    signal_time: datetime

    @classmethod
    def from_signal(cls, signal: Signal) -> "SignalSpec":
        tps = [tp for tp in (signal.tp1, signal.tp2, signal.tp3) if tp is not None]
        direction = signal.direction.value if isinstance(signal.direction, Direction) else str(signal.direction)
        return cls(
            direction=direction,
            entry=float(signal.entry),
            sl=float(signal.sl),
            tps=[float(tp) for tp in tps],
            signal_time=signal.signal_time,
        )


@dataclass
class Outcome:
    status: SignalStatus
    result: SignalResult
    profit_points: float | None = None
    loss_points: float | None = None
    entry_filled_at: datetime | None = None
    resolved_at: datetime | None = None
    max_tp_hit: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES and self.resolved_at is None

    @property
    def note(self) -> str | None:
        return "; ".join(self.notes) if self.notes else None


def points(direction: str, entry: float, exit_price: float) -> float:
    """Signed P/L in points for a move from ``entry`` to ``exit_price``."""
    delta = exit_price - entry if direction == "BUY" else entry - exit_price
    return round(delta / settings.point_size, 2)


def _hit_tp(direction: str, candle: Candle, level: float) -> bool:
    return candle.high >= level if direction == "BUY" else candle.low <= level


def _hit_sl(direction: str, candle: Candle, level: float) -> bool:
    return candle.low <= level if direction == "BUY" else candle.high >= level


def _win(spec: SignalSpec, tp_index: int, ts: datetime, notes: list[str]) -> Outcome:
    level = spec.tps[tp_index - 1]
    gain = points(spec.direction, spec.entry, level)
    status = {1: SignalStatus.TP1_HIT, 2: SignalStatus.TP2_HIT, 3: SignalStatus.TP3_HIT}.get(
        tp_index, SignalStatus.TP3_HIT
    )
    return Outcome(
        status=status,
        result=SignalResult.WIN if gain > 0 else SignalResult.BREAKEVEN,
        profit_points=max(gain, 0.0),
        loss_points=0.0 if gain >= 0 else abs(gain),
        resolved_at=ts,
        max_tp_hit=tp_index,
        notes=notes,
    )


def _loss(spec: SignalSpec, ts: datetime, notes: list[str]) -> Outcome:
    loss = points(spec.direction, spec.entry, spec.sl)
    return Outcome(
        status=SignalStatus.SL_HIT,
        result=SignalResult.LOSS if loss < 0 else SignalResult.BREAKEVEN,
        profit_points=0.0,
        loss_points=abs(min(loss, 0.0)),
        resolved_at=ts,
        notes=notes,
    )


async def evaluate(
    spec: SignalSpec,
    candles: Sequence[Candle],
    *,
    now: datetime | None = None,
    drilldown: DrillDown | None = None,
    ambiguity_rule: str | None = None,
    result_mode: str | None = None,
) -> Outcome:
    """Replay ``candles`` against ``spec`` and return the outcome."""
    now = now or utcnow()
    rule = (ambiguity_rule or settings.ambiguity_rule).upper()
    mode = (result_mode or settings.result_mode).upper()
    notes: list[str] = []

    entry_filled_at: datetime | None = None
    max_tp = 0
    last_close: float | None = None
    last_ts: datetime | None = None

    for candle in candles:
        if candle.ts < spec.signal_time:
            continue
        last_close, last_ts = candle.close, candle.ts

        if entry_filled_at is None:
            if candle.touches(spec.entry):
                entry_filled_at = candle.ts
                # The fill candle is not used for TP/SL: we cannot tell whether
                # the level was reached before or after the entry was taken.
                continue
            if (candle.ts - spec.signal_time) > timedelta(hours=settings.entry_fill_window_hours):
                notes.append(
                    f"entry {spec.entry} never traded within {settings.entry_fill_window_hours}h"
                )
                return Outcome(
                    status=SignalStatus.CANCELLED,
                    result=SignalResult.CANCELLED,
                    resolved_at=candle.ts,
                    notes=notes,
                )
            continue

        sl_hit = _hit_sl(spec.direction, candle, spec.sl)
        tps_hit = [i for i, tp in enumerate(spec.tps, start=1) if i > max_tp and _hit_tp(spec.direction, candle, tp)]

        if sl_hit and tps_hit:
            resolved = await _resolve_conflict(spec, candle, max_tp, drilldown)
            if resolved is not None:
                order_note, tp_index = resolved
                notes.append(order_note)
                if tp_index is None:
                    # The stop came first: nothing above it was ever reached.
                    outcome = _loss(spec, candle.ts, notes) if max_tp == 0 else _win(spec, max_tp, candle.ts, notes)
                else:
                    # The take profit came first and the stop followed inside the
                    # same candle, so the trade ends there, booked at that level.
                    outcome = _win(spec, max(max_tp, tp_index), candle.ts, notes)
                outcome.entry_filled_at = entry_filled_at
                return outcome

            notes.append(f"TP and SL both inside the {candle.ts:%Y-%m-%d %H:%M} candle")
            if rule == "TP_FIRST":
                notes.append("AMBIGUITY_RULE=TP_FIRST applied")
                return _win(spec, max(tps_hit), candle.ts, notes)
            if rule == "SL_FIRST":
                notes.append("AMBIGUITY_RULE=SL_FIRST applied")
                return _loss(spec, candle.ts, notes) if max_tp == 0 else _win(spec, max_tp, candle.ts, notes)
            notes.append("AMBIGUITY_RULE=AMBIGUOUS: excluded from win rate")
            return Outcome(
                status=SignalStatus.AMBIGUOUS,
                result=SignalResult.AMBIGUOUS,
                entry_filled_at=entry_filled_at,
                resolved_at=candle.ts,
                max_tp_hit=max_tp,
                notes=notes,
            )

        if tps_hit:
            max_tp = max(tps_hit)
            if max_tp >= len(spec.tps):
                outcome = _win(spec, max_tp, candle.ts, notes)
                outcome.entry_filled_at = entry_filled_at
                return outcome
            continue

        if sl_hit:
            if max_tp > 0 and mode == "BEST_TP":
                notes.append(f"SL touched after TP{max_tp}; counted as TP{max_tp}")
                outcome = _win(spec, max_tp, candle.ts, notes)
                outcome.entry_filled_at = entry_filled_at
                return outcome
            outcome = _loss(spec, candle.ts, notes)
            outcome.entry_filled_at = entry_filled_at
            outcome.max_tp_hit = max_tp
            return outcome

    # ------------------------------------------------------------- unresolved
    age = now - spec.signal_time
    if entry_filled_at is None:
        if age > timedelta(hours=settings.entry_fill_window_hours) and candles:
            notes.append(f"entry {spec.entry} never traded within {settings.entry_fill_window_hours}h")
            return Outcome(
                status=SignalStatus.CANCELLED,
                result=SignalResult.CANCELLED,
                resolved_at=last_ts or now,
                notes=notes,
            )
        return Outcome(status=SignalStatus.PENDING, result=SignalResult.PENDING_RESULT, notes=notes)

    if age > timedelta(hours=settings.signal_expiry_hours) and last_close is not None:
        if max_tp > 0:
            notes.append(f"open for {settings.signal_expiry_hours}h; booked at TP{max_tp}")
            outcome = _win(spec, max_tp, last_ts or now, notes)
            outcome.entry_filled_at = entry_filled_at
            return outcome
        mark = points(spec.direction, spec.entry, last_close)
        notes.append(f"open for {settings.signal_expiry_hours}h; closed at market {last_close}")
        return Outcome(
            status=SignalStatus.CLOSED,
            result=SignalResult.WIN if mark > 0 else SignalResult.LOSS if mark < 0 else SignalResult.BREAKEVEN,
            profit_points=max(mark, 0.0),
            loss_points=abs(min(mark, 0.0)),
            entry_filled_at=entry_filled_at,
            resolved_at=last_ts or now,
            notes=notes,
        )

    status = {0: SignalStatus.ACTIVE, 1: SignalStatus.TP1_HIT, 2: SignalStatus.TP2_HIT}.get(
        max_tp, SignalStatus.TP2_HIT
    )
    return Outcome(
        status=status,
        result=SignalResult.PENDING_RESULT,
        entry_filled_at=entry_filled_at,
        max_tp_hit=max_tp,
        notes=notes,
    )


async def _resolve_conflict(
    spec: SignalSpec, candle: Candle, max_tp: int, drilldown: DrillDown | None
) -> tuple[str, int | None] | None:
    """Try to order a same-candle TP/SL touch using finer candles.

    Returns ``(note, tp_index)`` where ``tp_index`` is ``None`` if the SL came
    first, or ``None`` (the whole tuple) when the tie cannot be broken.
    """
    if drilldown is None:
        return None

    step = TIMEFRAME_SECONDS.get(settings.price_timeframe, 60)
    finer = await drilldown(candle.ts, candle.ts + timedelta(seconds=step))
    if not finer:
        return None

    for sub in finer:
        sl_hit = _hit_sl(spec.direction, sub, spec.sl)
        tps_hit = [i for i, tp in enumerate(spec.tps, start=1) if i > max_tp and _hit_tp(spec.direction, sub, tp)]
        if sl_hit and tps_hit:
            return None  # still tied at the finer timeframe
        if tps_hit:
            return (f"tie broken on {settings.price_drilldown_timeframe}: TP{max(tps_hit)} first", max(tps_hit))
        if sl_hit:
            return (f"tie broken on {settings.price_drilldown_timeframe}: SL first", None)
    return None


# --------------------------------------------------------------------- engine
class ResultEngine:
    def __init__(self, provider: PriceProvider | None = None) -> None:
        self.provider = provider or get_provider()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        log.info("result engine started (provider=%s, available=%s)", self.provider.name, self.provider.available)
        if not self.provider.available:
            log.warning(
                "PRICE_DATA_PROVIDER=%s provides no price data; signals stay at PENDING_RESULT until one is configured",
                self.provider.name,
            )
        while not self._stop.is_set():
            try:
                async with session_scope() as session:
                    updated = await self.run_once(session)
                if updated:
                    log.info("result engine updated %s signal(s)", updated)
            except Exception:  # pragma: no cover - keep the loop alive
                log.exception("result engine iteration failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=settings.result_engine_interval_seconds)
            except asyncio.TimeoutError:
                pass
        await self.provider.close()
        log.info("result engine stopped")

    async def run_once(self, session: AsyncSession) -> int:
        """Evaluate every open signal. Returns the number that changed."""
        rows = await session.execute(
            select(Signal).where(
                Signal.status.in_(OPEN_STATUSES),
                Signal.is_complete.is_(True),
                Signal.manual_override.is_(False),
            )
        )
        changed = 0
        for signal in rows.scalars().all():
            if await self.evaluate_signal(session, signal):
                changed += 1
        return changed

    async def evaluate_signal(self, session: AsyncSession, signal: Signal) -> bool:
        signal.price_source = self.provider.name
        if not signal.is_complete or signal.entry is None or signal.sl is None:
            return False
        if not self.provider.available:
            return False

        spec = SignalSpec.from_signal(signal)
        symbol = signal.symbol or settings.price_symbol
        end = min(utcnow(), spec.signal_time + timedelta(hours=settings.signal_expiry_hours + 1))
        candles = await cache.get_candles(
            session, self.provider, symbol, settings.price_timeframe, spec.signal_time, end
        )
        if not candles:
            return False

        async def drilldown(start: datetime, stop: datetime) -> list[Candle]:
            tf = settings.price_drilldown_timeframe
            if tf == settings.price_timeframe or not self.provider.supports_timeframe(tf):
                return []
            return await cache.get_candles(session, self.provider, symbol, tf, start, stop)

        outcome = await evaluate(spec, candles, drilldown=drilldown)
        return _apply_outcome(signal, outcome)


def _apply_outcome(signal: Signal, outcome: Outcome) -> bool:
    before = (signal.status, signal.result, signal.profit_points, signal.loss_points, signal.max_tp_hit)

    signal.status = outcome.status
    signal.result = outcome.result
    signal.max_tp_hit = outcome.max_tp_hit
    if outcome.entry_filled_at is not None:
        signal.entry_filled_at = outcome.entry_filled_at
    if outcome.profit_points is not None:
        signal.profit_points = outcome.profit_points
    if outcome.loss_points is not None:
        signal.loss_points = outcome.loss_points
    if outcome.resolved_at is not None:
        signal.resolved_at = outcome.resolved_at
    if outcome.note:
        signal.evaluation_note = outcome.note
    signal.updated_at = utcnow()

    after = (signal.status, signal.result, signal.profit_points, signal.loss_points, signal.max_tp_hit)
    return before != after
