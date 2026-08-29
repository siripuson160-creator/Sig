"""Where forwarded messages are delivered.

The outbox worker does not care which chat app it is pushing into: it hands a
rendered string to a sender and reads back whether it landed. That seam is what
lets the destination be a deployment choice (`DELIVERY_TARGET`) rather than a
rewrite — a LINE group, or a Telegram channel.

A sender implements:

    async def push_text(text, *, idempotency_key=None) -> SendResult
    async def push_message(message, text, *, idempotency_key=None) -> SendResult
    async def verify() -> tuple[bool, str]

`push_message` is given the stored row as well, so a sender that *can* carry
media has what it needs to fetch it; `push_text` is the text-only floor that
every sender supports.
"""

from __future__ import annotations

from app.config import settings


def get_sender():
    """Build the sender for the configured destination.

    Returned as an async context manager, so the worker's `async with` works
    the same whichever destination is in force.
    """
    if settings.delivery_target == "telegram":
        from app.delivery.telegram_channel import TelegramChannelSender

        return TelegramChannelSender()

    from app.line.client import LineClient

    return LineClient()


def destination_label() -> str:
    """What to call the destination in a log line or on the dashboard."""
    if settings.delivery_target == "telegram":
        return settings.telegram_target_chat_id or "Telegram channel (not set)"
    return settings.line_destination or "LINE group (not set)"
