"""Ingestion tests: duplicates, edits and LINE rendering (sections 5-12)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import DeliveryStatus, EventType, Signal, SignalVersion, TelegramMessage
from app.processor.message_processor import ingest_message, pending_deliveries, render_line_text

CHAT = -1001234567890
BASE_TIME = datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc)


async def _ingest(session, message_id: int, content: str, *, version_offset: int = 0, is_edit: bool = False):
    return await ingest_message(
        session,
        chat_id=CHAT,
        message_id=message_id,
        content=content,
        tg_created_at=BASE_TIME,
        tg_edited_at=BASE_TIME + timedelta(minutes=version_offset) if is_edit else None,
        is_edit=is_edit,
    )


async def test_plain_message_is_stored_and_queued(session):
    """Every message goes to LINE, signal or not (section 5)."""
    result = await _ingest(session, 100, "Good morning")
    await session.commit()

    assert result.created
    assert result.message.version == 1
    assert result.message.event_type == EventType.NEW
    assert result.message.status == DeliveryStatus.PENDING
    assert result.signal is None  # a greeting is not a trade
    assert render_line_text(result.message) == "Good morning"


async def test_edit_creates_new_version_and_new_line_message(session):
    """Section 6: the original LINE message is untouched; a new one is queued."""
    await _ingest(session, 500, "Sell now")
    edited = await _ingest(
        session, 500, "SELL GOLD 3340\nSL 3330\nTP1 3350\nTP2 3360", version_offset=5, is_edit=True
    )
    await session.commit()

    assert edited.created
    assert edited.message.version == 2
    assert edited.message.event_type == EventType.EDIT
    assert render_line_text(edited.message) == "EDITED\n\nSELL GOLD 3340\nSL 3330\nTP1 3350\nTP2 3360"

    queue = await pending_deliveries(session)
    assert [m.version for m in queue] == [1, 2]
    assert queue[0].content == "Sell now"  # version 1 is still queued as posted


async def test_multiple_edits_keep_every_version(session):
    """Section 7: four versions, four LINE messages, nothing overwritten."""
    versions = [
        "Sell now",
        "SELL GOLD 3340",
        "SELL GOLD 3340\nSL 3330",
        "SELL GOLD 3340\nSL 3330\nTP1 3350\nTP2 3360",
    ]
    for index, content in enumerate(versions):
        await _ingest(session, 501, content, version_offset=index, is_edit=index > 0)
    await session.commit()

    rows = (
        (
            await session.execute(
                select(TelegramMessage)
                .where(TelegramMessage.message_id == 501)
                .order_by(TelegramMessage.version)
            )
        )
        .scalars()
        .all()
    )
    assert [r.version for r in rows] == [1, 2, 3, 4]
    assert [r.content for r in rows] == versions
    assert [r.event_type for r in rows] == [EventType.NEW, EventType.EDIT, EventType.EDIT, EventType.EDIT]

    rendered = [render_line_text(r) for r in rows]
    assert rendered[0] == "Sell now"
    assert all(text.startswith("EDITED\n\n") for text in rendered[1:])


async def test_restart_does_not_resend_the_same_message(session):
    """Section 8: same chat + message + content is ignored on replay."""
    first = await _ingest(session, 502, "Sell now")
    await session.commit()
    replay = await _ingest(session, 502, "Sell now")
    await session.commit()

    assert first.created
    assert not replay.created
    assert replay.reason == "duplicate_content"

    rows = (
        await session.execute(select(TelegramMessage).where(TelegramMessage.message_id == 502))
    ).scalars().all()
    assert len(rows) == 1


async def test_identical_text_in_different_messages_is_delivered_twice(session):
    """Section 9: same words, different message id -> two deliveries."""
    a = await _ingest(session, 100, "Sell now")
    b = await _ingest(session, 101, "Sell now")
    await session.commit()

    assert a.created and b.created
    assert a.message.content_hash != b.message.content_hash
    assert len(await pending_deliveries(session)) == 2


async def test_cosmetic_edit_is_not_resent(session):
    await _ingest(session, 503, "BUY GOLD 3340")
    same = await _ingest(session, 503, "BUY GOLD 3340  ", is_edit=True)
    await session.commit()
    assert not same.created


async def test_edit_links_to_the_same_signal(session):
    """Section 16: an edit must not create a second, unrelated signal."""
    first = await _ingest(session, 600, "Sell now")
    assert first.signal is not None
    assert not first.signal.is_complete

    second = await _ingest(
        session, 600, "SELL GOLD 3340\nSL 3350\nTP1 3330\nTP2 3320", version_offset=3, is_edit=True
    )
    await session.commit()

    assert second.signal.signal_id == first.signal.signal_id
    assert second.signal.is_complete
    assert second.signal.entry == 3340
    assert second.signal.source_version == 2

    signals = (await session.execute(select(Signal).where(Signal.telegram_message_id == 600))).scalars().all()
    assert len(signals) == 1

    history = (
        (
            await session.execute(
                select(SignalVersion)
                .where(SignalVersion.signal_id == first.signal.signal_id)
                .order_by(SignalVersion.version)
            )
        )
        .scalars()
        .all()
    )
    assert [v.version for v in history] == [1, 2]
    assert history[0].parsed["entry"] is None
    assert history[1].parsed["entry"] == 3340


async def test_long_message_is_truncated_for_line(session):
    result = await _ingest(session, 700, "x" * 6000)
    text = render_line_text(result.message)
    assert len(text) <= 4900
    assert text.endswith("…")
