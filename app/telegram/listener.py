"""Telegram listener built on Telethon (MTProto user account).

The account is a normal member of the source group, not an admin, so a bot
token cannot be used — section 3. Login is interactive and happens once via
``python -m app.cli login``; the resulting session file is what this listener
loads. It never asks for, stores or logs an OTP or 2FA password.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timezone

from telethon import TelegramClient, events
from telethon.errors import AuthKeyUnregisteredError
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from app.config import settings
from app.db.session import session_scope
from app.processor.message_processor import ingest_message

log = logging.getLogger(__name__)


class TelegramNotAuthorized(RuntimeError):
    pass


class TelegramUnreachable(RuntimeError):
    pass


async def connect_with_timeout(client: TelegramClient, seconds: float = 20.0) -> None:
    """Connect, but give up instead of retrying forever.

    The listener is configured to reconnect indefinitely, which is right for
    the daemon but wrong for the CLI: on a VPS with Telegram blocked, `check`
    would hang with no output at all.
    """
    try:
        await asyncio.wait_for(client.connect(), timeout=seconds)
    except asyncio.TimeoutError as exc:
        raise TelegramUnreachable(
            f"could not reach Telegram within {seconds:.0f}s — check the server's outbound "
            "network access or firewall"
        ) from exc


def build_client() -> TelegramClient:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH are not configured")

    session_dir = os.path.dirname(os.path.abspath(settings.telegram_session))
    if session_dir:
        os.makedirs(session_dir, exist_ok=True)

    return TelegramClient(
        settings.telegram_session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        connection_retries=None,  # retry forever
        retry_delay=5,
        auto_reconnect=True,
        request_retries=5,
        # Replay updates missed while the process was down; duplicate detection
        # makes the replay harmless (section 8).
        catch_up=True,
    )


def parse_chat_ref(raw: str) -> int | str:
    """Accept ``-1001234567890``, ``1234567890`` or ``@groupname``."""
    value = raw.strip()
    if value.startswith("@"):
        return value
    try:
        return int(value)
    except ValueError:
        return value


def message_text(message) -> tuple[str, bool]:
    """Text to forward, plus whether the message carried media.

    Media without a caption still reaches LINE as a short placeholder so the
    group's timeline stays complete (section 5).
    """
    text = (message.message or "").strip()
    media = message.media
    if media is None:
        return text, False

    label = "media"
    if isinstance(media, MessageMediaPhoto):
        label = "photo"
    elif isinstance(media, MessageMediaDocument):
        doc = getattr(media, "document", None)
        attributes = getattr(doc, "attributes", []) or []
        if any(isinstance(a, DocumentAttributeVideo) for a in attributes):
            label = "video"
        elif any(isinstance(a, DocumentAttributeAudio) for a in attributes):
            label = "voice message"
        else:
            label = "file"

    placeholder = f"[{label}]"
    return (f"{placeholder}\n{text}" if text else placeholder), True


async def _sender_name(event) -> str | None:
    try:
        sender = await event.get_sender()
    except Exception:  # pragma: no cover - network/permission dependent
        return None
    if sender is None:
        return None
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join(p for p in parts if p)
    return name or getattr(sender, "title", None) or getattr(sender, "username", None)


def _as_utc(value):
    if value is None:
        return None
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


class TelegramListener:
    def __init__(self, client: TelegramClient | None = None) -> None:
        self.client = client or build_client()
        self._stop = asyncio.Event()

    async def handle(self, event, *, is_edit: bool) -> None:
        message = event.message
        chat_id = int(event.chat_id)
        content, has_media = message_text(message)

        async with session_scope() as session:
            result = await ingest_message(
                session,
                chat_id=chat_id,
                message_id=int(message.id),
                content=content,
                tg_created_at=_as_utc(message.date),
                tg_edited_at=_as_utc(getattr(message, "edit_date", None)),
                sender_name=await _sender_name(event),
                has_media=has_media,
                is_edit=is_edit,
            )

        if result.created:
            log.info(
                "%s %s/%s v%s queued for LINE",
                "EDIT" if is_edit else "NEW",
                chat_id,
                message.id,
                result.message.version if result.message else "?",
            )
        else:
            log.debug("%s/%s ignored (%s)", chat_id, message.id, result.reason)

    def register_handlers(self) -> None:
        chats = [parse_chat_ref(c) for c in settings.source_chat_ids]
        if not chats:
            raise RuntimeError("TELEGRAM_SOURCE_CHAT_ID is not configured")

        @self.client.on(events.NewMessage(chats=chats))
        async def _on_new(event):  # pragma: no cover - exercised against Telegram
            try:
                await self.handle(event, is_edit=False)
            except Exception:
                log.exception("failed to ingest new message %s", getattr(event, "chat_id", "?"))

        @self.client.on(events.MessageEdited(chats=chats))
        async def _on_edit(event):  # pragma: no cover - exercised against Telegram
            try:
                await self.handle(event, is_edit=True)
            except Exception:
                log.exception("failed to ingest edited message %s", getattr(event, "chat_id", "?"))

        log.info("listening to chats: %s", ", ".join(str(c) for c in chats))

    async def run(self) -> None:
        await self.client.connect()
        try:
            if not await self.client.is_user_authorized():
                raise TelegramNotAuthorized(
                    "Telegram session is not authorised. Run 'python -m app.cli login' once "
                    "and enter the code on your own device."
                )
        except AuthKeyUnregisteredError as exc:  # pragma: no cover - session revoked
            raise TelegramNotAuthorized("Telegram session was revoked; run 'python -m app.cli login'") from exc

        me = await self.client.get_me()
        log.info("telegram connected as %s (id=%s)", getattr(me, "username", None) or me.first_name, me.id)

        self.register_handlers()
        await self.client.run_until_disconnected()

    async def stop(self) -> None:
        self._stop.set()
        if self.client.is_connected():
            await self.client.disconnect()
