"""LINE delivery tests: ordering, retries and idempotency."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.models import DeliveryStatus
from app.line.client import LineSendResult, retry_key
from app.line.queue_worker import LineQueueWorker
from app.processor.message_processor import ingest_message

CHAT = -1001234567890


@dataclass
class FakeLineClient:
    """Stands in for the LINE API; records what would have been pushed."""

    responses: list[LineSendResult] = field(default_factory=list)
    sent: list[tuple[str, str | None]] = field(default_factory=list)

    async def push_text(self, text: str, *, idempotency_key: str | None = None) -> LineSendResult:
        self.sent.append((text, idempotency_key))
        if self.responses:
            return self.responses.pop(0)
        return LineSendResult(ok=True, message_id=f"line-{len(self.sent)}", status_code=200)


async def _queue(session, message_id: int, content: str, *, is_edit: bool = False):
    result = await ingest_message(session, chat_id=CHAT, message_id=message_id, content=content, is_edit=is_edit)
    await session.commit()
    return result


async def test_queue_is_delivered_in_order(session):
    await _queue(session, 800, "Sell now")
    await _queue(session, 800, "SELL GOLD 3340\nSL 3350\nTP1 3330", is_edit=True)
    await _queue(session, 801, "Good morning")

    client = FakeLineClient()
    sent = await LineQueueWorker().drain_once(client)

    assert sent == 3
    assert [text for text, _ in client.sent] == [
        "Sell now",
        "EDITED\n\nSELL GOLD 3340\nSL 3350\nTP1 3330",
        "Good morning",
    ]


async def test_delivery_is_recorded_on_the_message(session):
    result = await _queue(session, 802, "Sell now")
    await LineQueueWorker().drain_once(FakeLineClient())

    await session.refresh(result.message)
    assert result.message.status == DeliveryStatus.SENT
    assert result.message.line_message_id == "line-1"
    assert result.message.sent_at is not None
    assert result.message.send_attempts == 1


async def test_nothing_is_sent_twice(session):
    await _queue(session, 803, "Sell now")
    worker = LineQueueWorker()
    client = FakeLineClient()

    assert await worker.drain_once(client) == 1
    assert await worker.drain_once(client) == 0  # queue is empty now
    assert len(client.sent) == 1


async def test_retry_key_is_stable_per_version(session):
    """A crash between sending and committing must not double-post."""
    first = retry_key(CHAT, 900, 1)
    assert first == retry_key(CHAT, 900, 1)
    assert first != retry_key(CHAT, 900, 2)  # an edit is a different message
    assert first != retry_key(CHAT, 901, 1)


async def test_transient_failure_is_retried_then_succeeds(session):
    result = await _queue(session, 804, "Sell now")
    client = FakeLineClient(
        responses=[
            LineSendResult(ok=False, status_code=429, error="rate limit", retryable=True),
            LineSendResult(ok=True, message_id="line-ok", status_code=200),
        ]
    )
    worker = LineQueueWorker()

    assert await worker.drain_once(client) == 0  # first pass fails
    await session.commit()

    # The retry is deferred rather than slept through, so the worker holds no
    # transaction while it waits.
    assert worker._retry_not_before is not None
    assert await worker.drain_once(client) == 0  # still backing off
    worker._retry_not_before = None  # simulate the wait elapsing

    assert await worker.drain_once(client) == 1  # second pass succeeds

    await session.refresh(result.message)
    assert result.message.status == DeliveryStatus.SENT
    assert result.message.send_attempts == 2


async def test_permanent_failure_stops_after_one_attempt(session):
    result = await _queue(session, 805, "Sell now")
    client = FakeLineClient(
        responses=[LineSendResult(ok=False, status_code=400, error="invalid target", retryable=False)]
    )

    await LineQueueWorker().drain_once(client)
    await session.commit()
    await session.refresh(result.message)

    assert result.message.status == DeliveryStatus.FAILED
    assert "invalid target" in result.message.last_error
    assert len(client.sent) == 1


async def test_a_failed_message_does_not_block_the_queue(session):
    await _queue(session, 806, "first")
    await _queue(session, 807, "second")
    client = FakeLineClient(
        responses=[
            LineSendResult(ok=False, status_code=400, error="rejected", retryable=False),
            LineSendResult(ok=True, message_id="line-2", status_code=200),
        ]
    )

    await LineQueueWorker().drain_once(client)
    assert [text for text, _ in client.sent] == ["first", "second"]
