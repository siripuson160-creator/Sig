"""LINE Messaging API client (push messages to a group)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import httpx

from app.config import settings

log = logging.getLogger(__name__)

#: Namespace for deriving stable retry keys from a message identity.
_RETRY_NS = uuid.UUID("6f9d4a4e-6b3f-5c22-9a1a-6d2c4a1f0b77")


class LineConfigError(RuntimeError):
    """Raised when LINE credentials are missing."""


@dataclass
class LineSendResult:
    ok: bool
    message_id: str | None = None
    status_code: int | None = None
    error: str | None = None
    retryable: bool = False


def retry_key(chat_id: int, message_id: int, version: int) -> str:
    """Deterministic idempotency key.

    LINE de-duplicates pushes carrying the same ``X-Line-Retry-Key`` for 24h, so
    a crash between "sent" and "marked as sent" cannot double-post.
    """
    return str(uuid.uuid5(_RETRY_NS, f"{chat_id}:{message_id}:{version}"))


class LineClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "LineClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_config(self) -> None:
        if not settings.line_channel_access_token:
            raise LineConfigError("LINE_CHANNEL_ACCESS_TOKEN is not set")
        if not settings.line_destination:
            raise LineConfigError("LINE_GROUP_ID is not set")

    async def push_text(self, text: str, *, idempotency_key: str | None = None) -> LineSendResult:
        """Push one text message. Never raises for HTTP failures."""
        self._require_config()
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))

        headers = {
            "Authorization": f"Bearer {settings.line_channel_access_token}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Line-Retry-Key"] = idempotency_key

        payload = {"to": settings.line_destination, "messages": [{"type": "text", "text": text}]}

        try:
            response = await self._client.post(
                f"{settings.line_api_base}/v2/bot/message/push", headers=headers, json=payload
            )
        except httpx.HTTPError as exc:
            log.warning("LINE push failed (network): %s", exc)
            return LineSendResult(ok=False, error=f"network: {exc}", retryable=True)

        if response.status_code in (200, 409):
            # 409 = this retry key was already accepted; the message is delivered.
            return LineSendResult(ok=True, message_id=_extract_message_id(response), status_code=response.status_code)

        retryable = response.status_code == 429 or response.status_code >= 500
        body = response.text[:500]
        log.warning("LINE push rejected: %s %s", response.status_code, body)
        return LineSendResult(ok=False, status_code=response.status_code, error=body, retryable=retryable)

    async def verify(self) -> tuple[bool, str]:
        """Check the access token by calling the bot info endpoint."""
        try:
            self._require_config()
        except LineConfigError as exc:
            return False, str(exc)
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        try:
            response = await self._client.get(
                f"{settings.line_api_base}/v2/bot/info",
                headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
            )
        except httpx.HTTPError as exc:
            return False, f"network: {exc}"
        if response.status_code == 200:
            return True, response.json().get("displayName", "ok")
        return False, f"{response.status_code}: {response.text[:200]}"


def _extract_message_id(response: httpx.Response) -> str | None:
    try:
        sent = response.json().get("sentMessages") or []
        if sent:
            return sent[0].get("id")
    except (ValueError, AttributeError):
        pass
    return response.headers.get("x-line-request-id")
