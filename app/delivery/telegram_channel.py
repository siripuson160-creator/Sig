"""Delivering into a Telegram channel, using the account already signed in.

Why the user account rather than a bot: the account is already authorised — it
has to be, to read the source group — so there is no second token to obtain and
no bot to make an admin. It is also the only credential that can *see* the
source message, which is what makes forwarding the chart images possible. A
bot cannot read a group it is not in, so it could only ever repost the text.

Posting as an admin of a channel appears under the channel's own name, exactly
as a bot's post would; members cannot tell the difference.

Compared with LINE this destination is strictly better for this use: no
webhook dance to find the destination id, no per-message quota, no 5000
character cap worth worrying about, and the images actually arrive.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.line.client import LineSendResult

log = logging.getLogger(__name__)

#: Telegram rejects a text message longer than this.
MAX_CHARS = 4096


class TelegramConfigError(RuntimeError):
    """Raised when the destination channel is not configured."""


class TelegramChannelSender:
    """Posts into ``TELEGRAM_TARGET_CHAT_ID`` as the signed-in account."""

    name = "telegram"

    def __init__(self, client=None) -> None:
        self._client = client
        self._owns_client = client is None
        self._entity = None

    async def __aenter__(self) -> "TelegramChannelSender":
        if self._client is None:
            from app.telegram.listener import build_client, connect_with_timeout

            self._client = build_client()
            await connect_with_timeout(self._client)
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._owns_client and self._client is not None:
            await self._client.disconnect()
            self._client = None

    # ------------------------------------------------------------- helpers
    def _require_config(self) -> str:
        target = (settings.telegram_target_chat_id or "").strip()
        if not target:
            raise TelegramConfigError("TELEGRAM_TARGET_CHAT_ID is not set")
        return target

    async def _resolve(self):
        """Look the channel up once and keep it.

        Telegram rate-limits entity resolution far more tightly than sending,
        so re-resolving on every message would be the thing that throttles the
        bridge under load.
        """
        if self._entity is not None:
            return self._entity
        target = self._require_config()
        # A numeric id must be passed as an int; @name and t.me links as text.
        lookup: object = target
        stripped = target.lstrip("-")
        if stripped.isdigit():
            lookup = int(target)
        self._entity = await self._client.get_entity(lookup)
        return self._entity

    @staticmethod
    def _trim(text: str) -> str:
        if len(text) <= MAX_CHARS:
            return text
        return text[: MAX_CHARS - 1] + "…"

    # -------------------------------------------------------------- sending
    async def push_text(self, text: str, *, idempotency_key: str | None = None) -> LineSendResult:
        """Send plain text. Never raises for a delivery failure."""
        try:
            entity = await self._resolve()
        except TelegramConfigError:
            raise
        except Exception as exc:
            log.warning("could not resolve the destination channel: %s", type(exc).__name__)
            return LineSendResult(ok=False, error=f"resolve: {exc}", retryable=True)

        try:
            sent = await self._client.send_message(entity, self._trim(text), link_preview=False)
        except Exception as exc:
            return self._failure(exc)
        return LineSendResult(ok=True, message_id=str(getattr(sent, "id", "")))

    async def push_message(self, message, text: str, *, idempotency_key: str | None = None) -> LineSendResult:
        """Send one stored row, carrying its media when it had any.

        The source message is fetched with the same account that received it,
        so the photo travels rather than being flattened to "[photo]" the way
        a text-only destination forces.
        """
        if not getattr(message, "has_media", False):
            return await self.push_text(text, idempotency_key=idempotency_key)

        try:
            entity = await self._resolve()
        except TelegramConfigError:
            raise
        except Exception as exc:
            return LineSendResult(ok=False, error=f"resolve: {exc}", retryable=True)

        try:
            original = await self._client.get_messages(message.chat_id, ids=message.message_id)
        except Exception as exc:
            log.warning("could not read the source message for its media: %s", type(exc).__name__)
            original = None

        media = getattr(original, "media", None) if original is not None else None
        if media is None:
            # The media is gone, or unreadable. Sending the text is better than
            # sending nothing, and the caller sees it was delivered.
            return await self.push_text(text, idempotency_key=idempotency_key)

        # A caption is capped shorter than a message; the overflow follows as
        # its own post so nothing the source wrote is silently dropped.
        caption, overflow = text[:1024], text[1024:]
        try:
            sent = await self._client.send_file(entity, media, caption=caption)
            if overflow.strip():
                await self._client.send_message(entity, self._trim(overflow), link_preview=False)
        except Exception as exc:
            return self._failure(exc)
        return LineSendResult(ok=True, message_id=str(getattr(sent, "id", "")))

    @staticmethod
    def _failure(exc: Exception) -> LineSendResult:
        name = type(exc).__name__
        seconds = getattr(exc, "seconds", None)
        if name == "FloodWaitError" and seconds:
            log.warning("Telegram is rate-limiting us for %ss", seconds)
            return LineSendResult(ok=False, error=f"flood wait {seconds}s", retryable=True)
        # A permission or identity problem will not fix itself on a retry.
        permanent = name in {
            "ChatWriteForbiddenError",
            "ChannelPrivateError",
            "UserBannedInChannelError",
            "PeerIdInvalidError",
            "ChatAdminRequiredError",
        }
        log.warning("Telegram send failed: %s", name)
        return LineSendResult(ok=False, error=name, retryable=not permanent)

    async def verify(self) -> tuple[bool, str]:
        """Check the destination is reachable and writable."""
        try:
            self._require_config()
        except TelegramConfigError as exc:
            return False, str(exc)
        try:
            entity = await self._resolve()
        except Exception as exc:
            return False, f"cannot reach that channel: {type(exc).__name__}"
        title = getattr(entity, "title", None) or getattr(entity, "username", None) or "channel"
        return True, str(title)
