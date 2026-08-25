"""Turns a Telegram event into a stored version + a queued LINE message.

Rules implemented here
----------------------
* Every message is forwarded, signal or not (section 5).
* An edit never touches the LINE message already sent; it is queued as a new
  message prefixed with ``EDITED`` (sections 6 and 7).
* A message is identified by chat id + message id + version + content hash, so
  a restart cannot re-send what was already delivered (section 8), while two
  different messages with identical text are both delivered (section 9).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import DeliveryStatus, EventType, Signal, TelegramMessage, utcnow
from app.processor.hashing import content_hash
from app.signals.service import upsert_signal_from_message

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    message: TelegramMessage | None
    signal: Signal | None
    created: bool
    reason: str

    @property
    def duplicate(self) -> bool:
        return not self.created


async def latest_version_row(session: AsyncSession, chat_id: int, message_id: int) -> TelegramMessage | None:
    result = await session.execute(
        select(TelegramMessage)
        .where(TelegramMessage.chat_id == chat_id, TelegramMessage.message_id == message_id)
        .order_by(TelegramMessage.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ingest_message(
    session: AsyncSession,
    *,
    chat_id: int,
    message_id: int,
    content: str,
    tg_created_at: datetime | None = None,
    tg_edited_at: datetime | None = None,
    sender_name: str | None = None,
    has_media: bool = False,
    is_edit: bool = False,
) -> IngestResult:
    """Store a new version of a Telegram message and queue it for LINE."""
    digest = content_hash(chat_id, message_id, content)
    previous = await latest_version_row(session, chat_id, message_id)

    if previous is not None and previous.content_hash == digest:
        # Same content we already have: a restart replay, or an edit that only
        # changed formatting/entities. Nothing to send.
        log.debug("duplicate %s/%s v%s ignored", chat_id, message_id, previous.version)
        return IngestResult(previous, None, created=False, reason="duplicate_content")

    if previous is None and is_edit:
        # We never saw the original (bot started after it was posted). Record it
        # as the first version we know about rather than dropping it.
        log.info("edit for unknown message %s/%s stored as version 1", chat_id, message_id)

    version = 1 if previous is None else previous.version + 1
    event_type = EventType.NEW if previous is None else EventType.EDIT

    row = TelegramMessage(
        chat_id=chat_id,
        message_id=message_id,
        version=version,
        content=content or "",
        content_hash=digest,
        event_type=event_type,
        created_at=tg_created_at,
        edited_at=tg_edited_at,
        received_at=utcnow(),
        status=DeliveryStatus.PENDING if settings.line_enabled else DeliveryStatus.SKIPPED,
        sender_name=sender_name,
        has_media=has_media,
    )
    try:
        # A savepoint, so losing a race here cannot roll back work the caller
        # has already done in this transaction.
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        # Another worker (or a replayed event) inserted this exact version first.
        log.info("race on %s/%s v%s; treating as duplicate", chat_id, message_id, version)
        existing = await latest_version_row(session, chat_id, message_id)
        return IngestResult(existing, None, created=False, reason="duplicate_race")

    signal = await upsert_signal_from_message(session, row)
    return IngestResult(row, signal, created=True, reason="stored")


def render_line_text(message: TelegramMessage) -> str:
    """The exact text pushed to LINE.

    A first version is forwarded verbatim; an edit is prefixed with ``EDITED``
    and a blank line so members can see the message was revised (section 6).
    """
    body = message.content or ""
    if message.event_type == EventType.EDIT:
        body = f"{settings.line_edit_prefix}\n\n{body}"
    if len(body) > settings.line_max_chars:
        body = body[: settings.line_max_chars - 1] + "…"
    return body


async def pending_deliveries(session: AsyncSession, limit: int = 20) -> list[TelegramMessage]:
    """Oldest-first queue of messages still to be pushed to LINE."""
    result = await session.execute(
        select(TelegramMessage)
        .where(
            TelegramMessage.status == DeliveryStatus.PENDING,
            TelegramMessage.send_attempts < settings.line_max_attempts,
        )
        .order_by(TelegramMessage.id.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def delivery_stats(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(TelegramMessage.status, func.count()).group_by(TelegramMessage.status)
    )
    counts = {status.value: 0 for status in DeliveryStatus}
    for status, count in result.all():
        key = status.value if isinstance(status, DeliveryStatus) else str(status)
        counts[key] = int(count)
    return counts
