"""Audit trail and component heartbeats (sections 44, 46, 56).

The audit log is append-only. Nothing in this module updates or deletes a row,
and no route exposes a way to do so: a published number can always be traced
back to the events that produced it.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, AuditLog, ComponentHeartbeat, ComponentStatus, utcnow
from app.db.session import session_scope

log = logging.getLogger(__name__)

#: A component is considered down if it has not checked in for this long.
HEARTBEAT_STALE_SECONDS = 120


async def record(
    session: AsyncSession,
    event: AuditEvent,
    *,
    summary: str,
    entity_type: str = "system",
    entity_id: str | None = None,
    actor: str = "system",
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    reason: str | None = None,
    source_ip: str | None = None,
) -> AuditLog:
    """Append one entry. Uses the caller's session so it shares its transaction."""
    entry = AuditLog(
        event=event,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=summary[:2000],
        actor=actor[:64],
        old_value=old_value,
        new_value=new_value,
        reason=reason,
        source_ip=source_ip,
    )
    session.add(entry)
    return entry


async def record_standalone(event: AuditEvent, *, summary: str, **kwargs: Any) -> None:
    """Append an entry in its own transaction.

    For callers outside a request or unit of work — the Telegram listener
    reconnecting, for example. Never raises: an audit write must not be able to
    take the bridge down.
    """
    try:
        async with session_scope() as session:
            await record(session, event, summary=summary, **kwargs)
    except Exception:  # pragma: no cover - best effort
        log.exception("could not write audit entry %s", event)


def signal_snapshot(signal) -> dict[str, Any]:
    """The fields worth diffing when a signal changes."""
    value = lambda v: v.value if hasattr(v, "value") else v  # noqa: E731
    return {
        "direction": value(signal.direction),
        "symbol": signal.symbol,
        "entry": signal.entry,
        "sl": signal.sl,
        "tp1": signal.tp1,
        "tp2": signal.tp2,
        "tp3": signal.tp3,
        "status": value(signal.status),
        "result": value(signal.result),
        "profit_points": signal.profit_points,
        "loss_points": signal.loss_points,
    }


def diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict, dict]:
    """Only the keys that actually changed, so the log stays readable."""
    changed = [key for key in after if before.get(key) != after.get(key)]
    return ({k: before.get(k) for k in changed}, {k: after.get(k) for k in changed})


async def list_entries(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    event: str | None = None,
    entity_id: str | None = None,
) -> list[AuditLog]:
    query = select(AuditLog)
    if event:
        query = query.where(AuditLog.event == event.upper())
    if entity_id:
        query = query.where(AuditLog.entity_id == str(entity_id))
    result = await session.execute(query.order_by(AuditLog.id.desc()).limit(limit).offset(offset))
    return list(result.scalars().all())


# ---------------------------------------------------------------- heartbeats
async def heartbeat(
    component: str,
    status: ComponentStatus = ComponentStatus.UP,
    detail: str | None = None,
) -> None:
    """Record that ``component`` is alive. Never raises."""
    try:
        async with session_scope() as session:
            row = await session.get(ComponentHeartbeat, component)
            if row is None:
                row = ComponentHeartbeat(component=component)
                session.add(row)
            row.status = status
            row.detail = detail
            row.last_seen = utcnow()
    except Exception:  # pragma: no cover - best effort
        log.debug("heartbeat for %s failed", component, exc_info=True)


async def component_health(session: AsyncSession) -> dict[str, dict]:
    """Current status of every component that has ever checked in."""
    rows = (await session.execute(select(ComponentHeartbeat))).scalars().all()
    now = utcnow()
    health: dict[str, dict] = {}
    for row in rows:
        age = (now - row.last_seen).total_seconds()
        stale = age > HEARTBEAT_STALE_SECONDS
        status = row.status.value if isinstance(row.status, ComponentStatus) else str(row.status)
        health[row.component] = {
            "status": "DOWN" if stale else status,
            "detail": "no heartbeat" if stale else row.detail,
            "last_seen": row.last_seen.isoformat(),
            "seconds_ago": round(age, 1),
        }
    return health
