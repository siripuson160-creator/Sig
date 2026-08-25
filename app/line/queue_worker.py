"""Outbox worker: pushes queued Telegram messages to the LINE group.

The queue is the ``telegram_messages`` table itself (status PENDING -> SENT),
so delivery state survives a restart and nothing is sent twice.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app.config import settings
from app.db.models import DeliveryStatus, TelegramMessage, utcnow
from app.db.session import session_scope
from app.line.client import LineClient, LineConfigError, retry_key
from app.processor.message_processor import pending_deliveries, render_line_text

log = logging.getLogger(__name__)

#: Attempt N waits this many seconds before being retried.
_BACKOFF_SECONDS = [2, 5, 15, 60, 300]


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
        if not settings.line_enabled:
            log.warning("LINE delivery disabled (LINE_ENABLED=false); messages will be stored only")
            return

        async with LineClient() if self._client is None else _null_ctx(self._client) as client:
            log.info("LINE queue worker started")
            while not self._stop.is_set():
                try:
                    sent = await self.drain_once(client)
                except Exception:  # pragma: no cover - keep the worker alive
                    log.exception("LINE worker iteration failed")
                    sent = 0
                # Poll faster while there is a backlog.
                delay = 0.2 if sent else settings.line_worker_interval_seconds
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
            log.info("LINE queue worker stopped")

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
                delivered = await self._deliver(client, message)
                if delivered:
                    sent += 1
                else:
                    # Preserve ordering: stop at the first failure so an edit is
                    # never delivered before the version it replaces.
                    break
            return sent

    async def _deliver(self, client: LineClient, message: TelegramMessage) -> bool:
        text = render_line_text(message)
        if not text.strip():
            message.status = DeliveryStatus.SKIPPED
            message.last_error = "empty message body"
            return True

        message.send_attempts += 1
        try:
            result = await client.push_text(
                text, idempotency_key=retry_key(message.chat_id, message.message_id, message.version)
            )
        except LineConfigError as exc:
            if not self._paused_logged:
                log.error("LINE not configured: %s — messages stay queued", exc)
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
                "delivered %s/%s v%s to LINE (%s)",
                message.chat_id,
                message.message_id,
                message.version,
                message.line_message_id,
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
