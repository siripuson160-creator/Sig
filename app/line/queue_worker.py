"""Outbox worker: pushes queued Telegram messages to the destination chat.

The queue is the ``telegram_messages`` table itself (status PENDING -> SENT),
so delivery state survives a restart and nothing is sent twice.

Which chat app that is — a LINE group or a Telegram channel — is a deployment
choice (``DELIVERY_TARGET``). This module only knows it has a sender with a
``push_message``; see ``app.delivery``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app import audit
from app.config import settings
from app.db.models import AuditEvent, ComponentStatus, DeliveryStatus, TelegramMessage, utcnow
from app.db.session import session_scope
from app.delivery import destination_label, get_sender
from app.line.client import LineClient, LineConfigError, retry_key
from app.processor.message_processor import pending_deliveries, render_line_text

log = logging.getLogger(__name__)

#: Attempt N waits this many seconds before the next try (section 50).
_BACKOFF_SECONDS = [2, 5, 10]


class LineQueueWorker:
    def __init__(self, client: LineClient | None = None) -> None:
        self._client = client
        self._stop = asyncio.Event()
        self._paused_logged = False
        # Backoff is held here rather than slept through inside a transaction:
        # sleeping mid-transaction would pin a SQLite write lock for minutes.
        self._retry_not_before: datetime | None = None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        if settings.dry_run:
            log.warning("DRY_RUN=true: messages are received, parsed and stored but never sent to LINE")
            await self._idle("dry run")
            return
        if not settings.line_enabled:
            log.warning("delivery disabled (LINE_ENABLED=false); messages will be stored only")
            await self._idle("delivery disabled")
            return

        async with get_sender() if self._client is None else _null_ctx(self._client) as client:
            log.info("delivery worker started (target=%s -> %s)", settings.delivery_target, destination_label())
            while not self._stop.is_set():
                try:
                    sent = await self.drain_once(client)
                except Exception:  # pragma: no cover - keep the worker alive
                    log.exception("LINE worker iteration failed")
                    sent = 0
                await audit.heartbeat("line", ComponentStatus.UP)
                # Poll faster while there is a backlog.
                delay = 0.2 if sent else settings.line_worker_interval_seconds
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
            log.info("LINE queue worker stopped")

    async def _idle(self, reason: str) -> None:
        """Stay alive with nothing to send.

        Returning here would look like a component exiting, which stops the
        whole supervisor — and test mode must not take the listener and the
        dashboard down with it.
        """
        while not self._stop.is_set():
            await audit.heartbeat("line", ComponentStatus.DEGRADED, reason)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
        log.info("LINE queue worker stopped (%s)", reason)

    async def drain_once(self, client: LineClient) -> int:
        """Send every message currently queued. Returns how many were sent."""
        if self._retry_not_before is not None and utcnow() < self._retry_not_before:
            return 0

        async with session_scope() as session:
            batch = await pending_deliveries(session)
            if not batch:
                return 0

            sent = 0
            for message in batch:
                delivered = await self._deliver(client, message, session)
                if delivered:
                    sent += 1
                else:
                    # Preserve ordering: stop at the first failure so an edit is
                    # never delivered before the version it replaces.
                    break
            return sent

    async def _deliver(self, client: LineClient, message: TelegramMessage, session=None) -> bool:
        text = render_line_text(message)
        if not text.strip():
            message.status = DeliveryStatus.SKIPPED
            message.last_error = "empty message body"
            return True

        message.send_attempts += 1
        key = retry_key(message.chat_id, message.message_id, message.version)
        try:
            # push_message lets a destination that can carry media fetch it;
            # a text-only sender falls back to push_text itself.
            send = getattr(client, "push_message", None)
            if send is not None:
                result = await send(message, text, idempotency_key=key)
            else:
                result = await client.push_text(text, idempotency_key=key)
        except Exception as exc:  # configuration, not a delivery failure
            from app.delivery.telegram_channel import TelegramConfigError

            if not isinstance(exc, (LineConfigError, TelegramConfigError)):
                raise
            if not self._paused_logged:
                log.error("destination not configured: %s — messages stay queued", exc)
                self._paused_logged = True
            message.send_attempts -= 1  # configuration, not a delivery attempt
            return False

        self._paused_logged = False

        if result.ok:
            self._retry_not_before = None
            message.status = DeliveryStatus.SENT
            message.sent_at = utcnow()
            message.line_message_id = result.message_id
            message.last_error = None
            log.info(
                "delivered %s/%s v%s to %s (%s)",
                message.chat_id,
                message.message_id,
                message.version,
                destination_label(),
                message.line_message_id,
            )
            if session is not None:
                await audit.record(
                    session,
                    AuditEvent.LINE_SEND,
                    entity_type="message",
                    entity_id=f"{message.chat_id}/{message.message_id}v{message.version}",
                    summary=f"Delivered to LINE ({message.line_message_id})",
                    actor="line-worker",
                )
            return True

        message.last_error = f"{result.status_code}: {result.error}"[:2000]
        if not result.retryable or message.send_attempts >= settings.line_max_attempts:
            message.status = DeliveryStatus.FAILED
            log.error(
                "giving up on %s/%s v%s after %s attempts: %s",
                message.chat_id,
                message.message_id,
                message.version,
                message.send_attempts,
                message.last_error,
            )
            if session is not None:
                await audit.record(
                    session,
                    AuditEvent.LINE_FAILED,
                    entity_type="message",
                    entity_id=f"{message.chat_id}/{message.message_id}v{message.version}",
                    summary=f"LINE delivery failed after {message.send_attempts} attempts",
                    reason=message.last_error,
                    actor="line-worker",
                )
            # A permanently failed message must not block the queue behind it.
            return True

        wait = _BACKOFF_SECONDS[min(message.send_attempts - 1, len(_BACKOFF_SECONDS) - 1)]
        self._retry_not_before = utcnow() + timedelta(seconds=wait)
        log.warning("retrying %s/%s v%s in %ss", message.chat_id, message.message_id, message.version, wait)
        return False


class _null_ctx:
    """Async context manager that yields an already-built client."""

    def __init__(self, value: LineClient) -> None:
        self._value = value

    async def __aenter__(self) -> LineClient:
        return self._value

    async def __aexit__(self, *_exc) -> None:
        return None
