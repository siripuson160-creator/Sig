"""Admin API (sections 42, 43, 44, 46, 56).

Everything here needs an authenticated operator. Two rules shape this module:

* **No statistic can be typed in** (section 43). There is no route that sets a
  win rate, a total profit or a signal count — those only ever come out of the
  result and statistics engines.
* **Every change is recorded** (sections 44 and 46). Correcting one signal by
  hand is allowed, but it writes an audit entry carrying the old value, the new
  value, who did it, when, and the reason they gave. A correction without a
  reason is refused.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.api.security import AdminDep, check_password, client_ip, issue_token, new_login_delay, require_admin
from app.api.broadcast import _broadcast_page
from app.api.serializers import audit_entry, signal_summary, telegram_message
from app.config import settings
from app.db.models import (
    AuditEvent,
    AuditLog,
    DeliveryStatus,
    Signal,
    SignalResult,
    SignalStatus,
    SignalVersion,
    TelegramMessage,
    utcnow,
)
from app.db.session import get_session, healthcheck
from app.engine import stats_engine
from app.engine.result_engine import ResultEngine
from app.line.client import LineClient
from app.prices.providers import available_providers, get_provider
from app.processor.message_processor import delivery_stats
from app.signals.service import reparse_signal

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ------------------------------------------------------------------- login
class LoginRequest(BaseModel):
    password: str


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    """Exchange the admin password for a session token."""
    if not settings.admin_secret:
        raise HTTPException(status_code=503, detail="admin disabled: ADMIN_PASSWORD is not set")

    ip = client_ip(request)
    if not check_password(body.password):
        await audit.record(
            session,
            AuditEvent.ADMIN_LOGIN_FAILED,
            entity_type="admin",
            summary="Failed admin sign-in",
            actor="unknown",
            source_ip=ip,
        )
        await session.commit()
        await asyncio.sleep(new_login_delay())
        raise HTTPException(status_code=401, detail="wrong password")

    token, expires = issue_token()
    await audit.record(
        session,
        AuditEvent.ADMIN_LOGIN,
        entity_type="admin",
        summary="Admin signed in",
        actor="admin",
        source_ip=ip,
    )
    await session.commit()
    return {"token": token, "expires_at": expires}


# ------------------------------------------------------------------ status
@router.get("/status")
async def status(identity: AdminDep, session: AsyncSession = Depends(get_session)) -> dict:
    """The status lights on the admin overview (section 56)."""
    provider = get_provider()
    counts = await delivery_stats(session)
    components = await audit.component_health(session)

    open_signals = (
        await session.execute(
            select(func.count())
            .select_from(Signal)
            .where(Signal.status.in_([SignalStatus.PENDING, SignalStatus.ACTIVE]))
        )
    ).scalar_one()
    database_ok = await healthcheck()
    queue_blocked = counts.get("FAILED", 0) > 0

    def light(ok: bool, degraded: bool = False) -> str:
        return "GREEN" if ok and not degraded else ("YELLOW" if degraded else "RED")

    telegram = components.get("telegram", {"status": "UNKNOWN", "detail": "listener not running here"})
    line_component = components.get("line", {"status": "UNKNOWN", "detail": "worker not running here"})

    return {
        "lights": {
            "telegram": light(telegram["status"] == "UP", telegram["status"] == "DEGRADED"),
            "line": light(line_component["status"] == "UP", line_component["status"] == "DEGRADED"),
            "database": light(database_ok),
            "queue": light(not queue_blocked, counts.get("PENDING", 0) > 50),
            "dashboard": "GREEN",
        },
        "components": components,
        "database": database_ok,
        "delivery": counts,
        "open_signals": int(open_signals),
        "delivery_target": settings.delivery_target,
        "delivery_configured": (
            bool(settings.telegram_target_chat_id)
            if settings.delivery_target == "telegram"
            else bool(settings.line_channel_access_token and settings.line_destination)
        ),
        "line_enabled": settings.line_enabled,
        "dry_run": settings.dry_run,
        "telegram_source": settings.source_chat_ids,
        "price_provider": provider.describe(),
        "available_price_providers": available_providers(),
        "timezone": settings.timezone,
        "signed_in_as": identity.actor,
    }


# ---------------------------------------------------------------- messages
@router.get("/messages")
async def messages(
    identity: AdminDep,
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
    return {"total": int(total), "items": [telegram_message(m) for m in rows.scalars().all()]}


@router.get("/broadcast")
async def broadcast(
    identity: AdminDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The archive of what was pushed to the LINE group.

    Shows the exact text delivered rather than the raw Telegram content, so an
    operator can check what members actually received — the EDITED prefix
    included.
    """
    return await _broadcast_page(session, limit=limit, offset=offset, status=status, search=q)


@router.post("/messages/{row_id}/requeue")
async def requeue(
    identity: AdminDep, row_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    message = await session.get(TelegramMessage, row_id)
    if message is None:
        raise HTTPException(status_code=404, detail="message not found")
    if message.status == DeliveryStatus.SENT:
        raise HTTPException(status_code=409, detail="message was already delivered to LINE")

    message.status = DeliveryStatus.PENDING
    message.send_attempts = 0
    message.last_error = None
    await audit.record(
        session,
        AuditEvent.ADMIN_ACTION,
        entity_type="message",
        entity_id=str(row_id),
        summary=f"Requeued message {message.chat_id}/{message.message_id} v{message.version} for LINE",
        actor=identity.actor,
        source_ip=identity.ip,
    )
    await session.commit()
    return telegram_message(message)


# ------------------------------------------------------------ edit history
@router.get("/edit-history")
async def edit_history(
    identity: AdminDep,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Message threads that were edited, newest first (section 64)."""
    threads = await session.execute(
        select(
            TelegramMessage.chat_id,
            TelegramMessage.message_id,
            func.count().label("versions"),
            func.max(TelegramMessage.received_at).label("last_seen"),
        )
        .group_by(TelegramMessage.chat_id, TelegramMessage.message_id)
        .having(func.count() > 1)
        .order_by(func.max(TelegramMessage.received_at).desc())
        .limit(limit)
    )

    items = []
    for chat_id, message_id, versions, _last_seen in threads.all():
        rows = (
            (
                await session.execute(
                    select(TelegramMessage)
                    .where(TelegramMessage.chat_id == chat_id, TelegramMessage.message_id == message_id)
                    .order_by(TelegramMessage.version.asc())
                )
            )
            .scalars()
            .all()
        )
        items.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "versions": int(versions),
                "history": [telegram_message(row) for row in rows],
            }
        )
    return {"items": items}


# --------------------------------------------------------------- audit log
@router.get("/audit")
async def audit_log(
    identity: AdminDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event: str | None = None,
    entity_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The append-only trail (section 44). There is no delete route by design."""
    total = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    entries = await audit.list_entries(session, limit=limit, offset=offset, event=event, entity_id=entity_id)
    return {
        "total": int(total),
        "items": [audit_entry(entry) for entry in entries],
        "events": [member.value for member in AuditEvent],
    }


# ----------------------------------------------------------------- signals
class SignalPatch(BaseModel):
    """Manual correction of one signal's outcome.

    Statistics are never edited directly (section 43); only the facts of a
    single trade can be corrected, and only with a stated reason (section 46).
    """

    status: SignalStatus | None = None
    result: SignalResult | None = None
    profit_points: float | None = Field(default=None, ge=0)
    loss_points: float | None = Field(default=None, ge=0)
    reason: str = Field(min_length=3, max_length=500)
    release_override: bool = False


@router.patch("/signals/{signal_id}")
async def patch_signal(
    identity: AdminDep,
    signal_id: str,
    patch: SignalPatch,
    session: AsyncSession = Depends(get_session),
) -> dict:
    signal = (await session.execute(select(Signal).where(Signal.signal_id == signal_id))).scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")

    before = audit.signal_snapshot(signal)

    if patch.release_override:
        signal.manual_override = False
        await audit.record(
            session,
            AuditEvent.ADMIN_ACTION,
            entity_type="signal",
            entity_id=signal_id,
            summary="Manual override released; the result engine owns this signal again",
            old_value={"manual_override": True},
            new_value={"manual_override": False},
            reason=patch.reason,
            actor=identity.actor,
            source_ip=identity.ip,
        )
        await session.commit()
        return signal_summary(signal)

    changed = False
    for field_name in ("status", "result", "profit_points", "loss_points"):
        value = getattr(patch, field_name)
        if value is not None:
            setattr(signal, field_name, value)
            changed = True
    if not changed:
        raise HTTPException(status_code=400, detail="no fields to update")

    signal.manual_override = True
    signal.updated_at = utcnow()
    if patch.result in (SignalResult.WIN, SignalResult.LOSS, SignalResult.BREAKEVEN) and signal.resolved_at is None:
        signal.resolved_at = utcnow()

    old, new = audit.diff(before, audit.signal_snapshot(signal))
    await audit.record(
        session,
        AuditEvent.SIGNAL_RESULT_UPDATED,
        entity_type="signal",
        entity_id=signal_id,
        summary="Result corrected by hand",
        old_value=old,
        new_value=new,
        reason=patch.reason,
        actor=identity.actor,
        source_ip=identity.ip,
    )
    await session.commit()
    return signal_summary(signal)


@router.post("/signals/{signal_id}/reparse")
async def reparse(
    identity: AdminDep, signal_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    signal = (await session.execute(select(Signal).where(Signal.signal_id == signal_id))).scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")

    before = audit.signal_snapshot(signal)
    updated = await reparse_signal(session, signal)
    if updated is None:
        raise HTTPException(status_code=409, detail="source Telegram message is missing")

    old, new = audit.diff(before, audit.signal_snapshot(updated))
    await audit.record(
        session,
        AuditEvent.ADMIN_ACTION,
        entity_type="signal",
        entity_id=signal_id,
        summary="Signal re-parsed from its latest Telegram version",
        old_value=old,
        new_value=new,
        reason="operator requested a re-parse",
        actor=identity.actor,
        source_ip=identity.ip,
    )
    await session.commit()
    return signal_summary(updated)


@router.post("/signals/{signal_id}/evaluate")
async def evaluate_now(
    identity: AdminDep, signal_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    signal = (await session.execute(select(Signal).where(Signal.signal_id == signal_id))).scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")
    engine = ResultEngine()
    changed = await engine.evaluate_signal(session, signal)
    await engine.provider.close()
    await session.commit()
    return {"changed": changed, "signal": signal_summary(signal)}


@router.get("/signals/{signal_id}/history")
async def signal_history(
    identity: AdminDep, signal_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Parses, message versions and audit entries for one signal."""
    signal = (await session.execute(select(Signal).where(Signal.signal_id == signal_id))).scalar_one_or_none()
    if signal is None:
        raise HTTPException(status_code=404, detail="signal not found")

    versions = (
        (
            await session.execute(
                select(SignalVersion).where(SignalVersion.signal_id == signal_id).order_by(SignalVersion.version)
            )
        )
        .scalars()
        .all()
    )
    entries = await audit.list_entries(session, entity_id=signal_id, limit=200)
    return {
        "signal": signal_summary(signal),
        "parses": [
            {"version": v.version, "telegram_version": v.telegram_version, "parsed": v.parsed} for v in versions
        ],
        "audit": [audit_entry(entry) for entry in entries],
    }


# -------------------------------------------------------------- statistics
@router.get("/statistics")
async def statistics(
    identity: AdminDep,
    range: str = Query("all"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Same numbers as the public dashboard — computed, never entered."""
    return {
        "overview": await stats_engine.build_overview(session, range),
        "source": "computed from the signals table by the statistics engine",
    }


@router.post("/line/test")
async def delivery_test(identity: AdminDep) -> dict:
    """Verify the configured destination without posting to it.

    Kept at the historical /line/test path so existing scripts and the admin
    page keep working after the destination became a choice.
    """
    from app.delivery import get_sender

    async with get_sender() as client:
        ok, detail = await client.verify()
    return {"ok": ok, "detail": detail, "target": settings.delivery_target}


@router.get("/settings")
async def read_settings(identity: AdminDep) -> dict:
    """Read-only view of the configuration in force. No secrets are returned."""
    return {
        "timezone": settings.timezone,
        "dry_run": settings.dry_run,
        "line_enabled": settings.line_enabled,
        "add_edited_prefix": settings.add_edited_prefix,
        "line_edit_prefix": settings.line_edit_prefix,
        # Only for labelling the LINE preview; never the token itself.
        "line_destination": settings.line_destination,
        "pip_size": settings.pip_size,
        "result_source": settings.result_source,
        "line_max_attempts": settings.line_max_attempts,
        "price_data_provider": settings.price_data_provider,
        "price_symbol": settings.price_symbol,
        "price_timeframe": settings.price_timeframe,
        "point_size": settings.point_size,
        "ambiguity_rule": settings.ambiguity_rule,
        "result_mode": settings.result_mode,
        "entry_fill_window_hours": settings.entry_fill_window_hours,
        "signal_expiry_hours": settings.signal_expiry_hours,
        "database": "postgresql" if not settings.is_sqlite else "sqlite",
        "note": "Settings are changed in .env and take effect on restart, so a "
        "running configuration can always be reproduced from the file.",
    }


# Kept so existing scripts that only hold X-Admin-Key keep working.
__all__ = ["router", "require_admin"]
