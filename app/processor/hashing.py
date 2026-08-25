"""Content hashing used for duplicate detection (section 8)."""

from __future__ import annotations

import hashlib
import re

_WS_RE = re.compile(r"[ \t\r\f\v]+")


def canonical_content(text: str | None) -> str:
    """Whitespace-normalised text.

    Telegram sometimes re-delivers a message with cosmetic differences (a
    trailing space, CRLF instead of LF). Those must not count as an edit, while
    any real change of wording must.
    """
    if not text:
        return ""
    lines = [_WS_RE.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


def content_hash(chat_id: int, message_id: int, text: str | None) -> str:
    """Hash of chat + message + content.

    Including the identifiers means two different messages with identical text
    hash differently, so section 9 (same text, different message id must both be
    delivered) falls out of the key itself.
    """
    payload = f"{chat_id}:{message_id}:{canonical_content(text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
