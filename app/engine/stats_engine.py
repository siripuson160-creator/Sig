"""Performance statistics (sections 24, 25 and the analytics views).

Everything is expressed in **points** — never money — because lot size, spread,
commission and swap are unknown (section 21).

Conventions
-----------
* Only *complete* signals (direction + entry + SL + at least one TP) count.
  A heads-up like "Sell now" is stored and shown, but it is not a trade.
* A signal counts towards win rate once it is decided (WIN / LOSS / BREAKEVEN).
  PENDING_RESULT, AMBIGUOUS and CANCELLED signals are reported separately so
  the headline numbers cannot be inflated by hiding them.
* Signals are bucketed by the day the signal was posted, in ``TIMEZONE``.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Direction, Signal, SignalResult, SignalStatus

_DECIDED = (SignalResult.WIN, SignalResult.LOSS, SignalResult.BREAKEVEN)


@dataclass
class Row:
    """A flattened signal, already converted to local time."""

    signal_id: str
    direction: str | None
    symbol: str | None
    status: str
    result: str
    net_points: float
    local_time: datetime
    resolved_local: datetime | None
    max_tp_hit: int

    @property
    def decided(self) -> bool:
        return self.result in {r.value for r in _DECIDED}

    @property
    def is_win(self) -> bool:
        return self.result == SignalResult.WIN.value

    @property
    def is_loss(self) -> bool:
        return self.result == SignalResult.LOSS.value


def _local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(settings.tz)


def _to_row(signal: Signal) -> Row:
    status = signal.status.value if isinstance(signal.status, SignalStatus) else str(signal.status)
    result = signal.result.value if isinstance(signal.result, SignalResult) else str(signal.result)
    direction = signal.direction.value if isinstance(signal.direction, Direction) else signal.direction
    return Row(
        signal_id=signal.signal_id,
        direction=direction,
        symbol=signal.symbol,
        status=status,
        result=result,
        net_points=round((signal.profit_points or 0.0) - (signal.loss_points or 0.0), 2),
        local_time=_local(signal.signal_time),
        resolved_local=_local(signal.resolved_at),
        max_tp_hit=signal.max_tp_hit or 0,
    )


async def load_rows(
    session: AsyncSession, *, start: datetime | None = None, end: datetime | None = None
) -> list[Row]:
    query = select(Signal).where(Signal.is_complete.is_(True))
    if start is not None:
        query = query.where(Signal.signal_time >= start)
    if end is not None:
        query = query.where(Signal.signal_time <= end)
    result = await session.execute(query.order_by(Signal.signal_time.asc()))
    return [_to_row(s) for s in result.scalars().all()]


# ----------------------------------------------------------------- primitives
def summarize(rows: list[Row]) -> dict:
    """The summary-card numbers (section 24)."""
    decided = [r for r in rows if r.decided]
    wins = [r for r in decided if r.is_win]
    losses = [r for r in decided if r.is_loss]
    breakeven = [r for r in decided if not r.is_win and not r.is_loss]

    gross_profit = round(sum(r.net_points for r in wins), 2)
    gross_loss = round(abs(sum(r.net_points for r in losses)), 2)
    total_pl = round(sum(r.net_points for r in decided), 2)
    decisive = len(wins) + len(losses)

    return {
        "total_signals": len(rows),
        "decided_signals": len(decided),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": round(len(wins) / decisive * 100, 2) if decisive else None,
        "total_pl_points": total_pl,
        "gross_profit_points": gross_profit,
        "gross_loss_points": gross_loss,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown_points": max_drawdown(decided),
        "avg_win_points": round(gross_profit / len(wins), 2) if wins else None,
        "avg_loss_points": round(-gross_loss / len(losses), 2) if losses else None,
        "expectancy_points": round(total_pl / decisive, 2) if decisive else None,
        "best_points": round(max((r.net_points for r in decided), default=0.0), 2) if decided else None,
        "worst_points": round(min((r.net_points for r in decided), default=0.0), 2) if decided else None,
        "pending": sum(1 for r in rows if r.result == SignalResult.PENDING_RESULT.value),
        "ambiguous": sum(1 for r in rows if r.result == SignalResult.AMBIGUOUS.value),
        "cancelled": sum(1 for r in rows if r.result == SignalResult.CANCELLED.value),
        **streaks(decided),
    }


def max_drawdown(decided: list[Row]) -> float:
    """Largest peak-to-trough drop of the cumulative points curve (negative)."""
    ordered = sorted(decided, key=lambda r: r.resolved_local or r.local_time)
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for row in ordered:
        equity += row.net_points
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(worst, 2)


def streaks(decided: list[Row]) -> dict:
    ordered = sorted(decided, key=lambda r: r.resolved_local or r.local_time)
    longest_win = longest_loss = current = 0
    current_kind: str | None = None
    for row in ordered:
        kind = "WIN" if row.is_win else "LOSS" if row.is_loss else None
        if kind is None:
            continue
        current = current + 1 if kind == current_kind else 1
        current_kind = kind
        if kind == "WIN":
            longest_win = max(longest_win, current)
        else:
            longest_loss = max(longest_loss, current)
    return {
        "current_streak": current if current_kind else 0,
        "current_streak_kind": current_kind,
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
    }


def _bucket_stats(label: str, rows: list[Row]) -> dict:
    decided = [r for r in rows if r.decided]
    wins = sum(1 for r in decided if r.is_win)
    losses = sum(1 for r in decided if r.is_loss)
    decisive = wins + losses
    return {
        "period": label,
        "signals": len(rows),
        "decided": len(decided),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / decisive * 100, 2) if decisive else None,
        "pl_points": round(sum(r.net_points for r in decided), 2),
        "pending": sum(1 for r in rows if r.result == SignalResult.PENDING_RESULT.value),
    }


def group_by(rows: list[Row], granularity: str) -> list[dict]:
    """Bucket rows by day / week / month in the configured timezone."""
    buckets: "OrderedDict[str, list[Row]]" = OrderedDict()
    for row in sorted(rows, key=lambda r: r.local_time):
        buckets.setdefault(_period_key(row.local_time.date(), granularity), []).append(row)
    return [_bucket_stats(label, items) for label, items in buckets.items()]


def _period_key(day: date, granularity: str) -> str:
    if granularity == "weekly":
        monday = day - timedelta(days=day.weekday())
        return monday.isoformat()
    if granularity == "monthly":
        return f"{day.year:04d}-{day.month:02d}"
    return day.isoformat()


def equity_curve(rows: list[Row]) -> list[dict]:
    """Cumulative points after each decided signal."""
    decided = sorted((r for r in rows if r.decided), key=lambda r: r.resolved_local or r.local_time)
    curve: list[dict] = []
    equity = 0.0
    for row in decided:
        equity = round(equity + row.net_points, 2)
        moment = row.resolved_local or row.local_time
        curve.append(
            {
                "signal_id": row.signal_id,
                "time": moment.isoformat(),
                "points": row.net_points,
                "equity": equity,
                "result": row.result,
            }
        )
    return curve


def analytics(rows: list[Row]) -> dict:
    """Breakdowns for the Analytics tab."""
    decided = [r for r in rows if r.decided]

    by_direction = []
    for direction in ("BUY", "SELL"):
        subset = [r for r in rows if r.direction == direction]
        if subset:
            stats = _bucket_stats(direction, subset)
            stats["direction"] = direction
            by_direction.append(stats)

    by_hour = []
    for hour in range(24):
        subset = [r for r in rows if r.local_time.hour == hour]
        if subset:
            stats = _bucket_stats(f"{hour:02d}:00", subset)
            stats["hour"] = hour
            by_hour.append(stats)

    by_weekday = []
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for index, name in enumerate(names):
        subset = [r for r in rows if r.local_time.weekday() == index]
        if subset:
            stats = _bucket_stats(name, subset)
            stats["weekday"] = index
            by_weekday.append(stats)

    by_symbol = []
    for symbol in sorted({r.symbol for r in rows if r.symbol}):
        subset = [r for r in rows if r.symbol == symbol]
        stats = _bucket_stats(symbol, subset)
        stats["symbol"] = symbol
        by_symbol.append(stats)

    tp_distribution = []
    for level in (1, 2, 3):
        count = sum(1 for r in decided if r.max_tp_hit >= level)
        tp_distribution.append({"level": f"TP{level}", "count": count})
    tp_distribution.append({"level": "SL", "count": sum(1 for r in decided if r.is_loss)})

    return {
        "by_direction": by_direction,
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "by_symbol": by_symbol,
        "tp_distribution": tp_distribution,
        "points_distribution": _histogram([r.net_points for r in decided]),
    }


def _histogram(values: list[float], buckets: int = 9) -> list[dict]:
    if not values:
        return []
    low, high = min(values), max(values)
    if low == high:
        return [{"from": round(low, 2), "to": round(high, 2), "count": len(values)}]
    width = (high - low) / buckets
    out = []
    for index in range(buckets):
        start = low + index * width
        stop = start + width
        count = sum(1 for v in values if (start <= v < stop or (index == buckets - 1 and v == high)))
        out.append({"from": round(start, 2), "to": round(stop, 2), "count": count})
    return out


def range_bounds(range_key: str) -> tuple[datetime | None, datetime | None]:
    """Translate ``today``/``7d``/``30d``/``mtd``/``all`` into UTC bounds."""
    now_local = datetime.now(settings.tz)
    key = (range_key or "all").lower()
    if key == "all":
        return None, None
    if key == "today":
        start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif key == "yesterday":
        start = (now_local - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    elif key == "mtd":
        start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif key.endswith("d") and key[:-1].isdigit():
        start = now_local - timedelta(days=int(key[:-1]))
    else:
        return None, None
    return start.astimezone(timezone.utc), None


# ------------------------------------------------------------------ top level
async def build_overview(session: AsyncSession, range_key: str = "all") -> dict:
    start, end = range_bounds(range_key)
    rows = await load_rows(session, start=start, end=end)
    summary = summarize(rows)
    summary["range"] = range_key
    summary["timezone"] = settings.timezone
    summary["price_source"] = settings.price_data_provider
    return summary


async def build_performance(session: AsyncSession, granularity: str, limit: int | None = None) -> list[dict]:
    rows = await load_rows(session)
    buckets = group_by(rows, granularity)
    if limit:
        buckets = buckets[-limit:]
    return list(reversed(buckets))


async def build_analytics(session: AsyncSession, range_key: str = "all") -> dict:
    start, end = range_bounds(range_key)
    rows = await load_rows(session, start=start, end=end)
    payload = analytics(rows)
    payload["equity_curve"] = equity_curve(rows)
    payload["range"] = range_key
    return payload
