"""Delivering into a Telegram channel instead of a LINE group.

The account that reads the source group is the one that posts, so there is no
second token to obtain — and, unlike LINE, the source's chart images travel
rather than being flattened to "[photo]".
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.delivery import destination_label, get_sender
from app.delivery.telegram_channel import TelegramChannelSender, TelegramConfigError


class FakeMedia:
    pass


class FakeSent:
    def __init__(self, id_=4242):
        self.id = id_


class FakeEntity:
    title = "Gold Signals"


class FakeSourceMessage:
    def __init__(self, media=None):
        self.media = media


class FakeTelegram:
    """Records what was asked of Telegram, so the tests can assert on it."""

    def __init__(self, *, source_media=None, fail_with: Exception | None = None):
        self.messages: list[tuple[object, str]] = []
        self.files: list[tuple[object, object, str]] = []
        self.resolved: list[object] = []
        self._source_media = source_media
        self._fail_with = fail_with

    async def get_entity(self, target):
        self.resolved.append(target)
        return FakeEntity()

    async def send_message(self, entity, text, **kwargs):
        if self._fail_with:
            raise self._fail_with
        self.messages.append((entity, text))
        return FakeSent(len(self.messages))

    async def send_file(self, entity, media, caption=""):
        if self._fail_with:
            raise self._fail_with
        self.files.append((entity, media, caption))
        return FakeSent(900 + len(self.files))

    async def get_messages(self, chat_id, ids=None):
        return FakeSourceMessage(self._source_media)

    async def disconnect(self):
        return None


class Row:
    """The stored outbox row, as much of it as the sender reads."""

    def __init__(self, *, has_media=False, chat_id=-1001, message_id=500):
        self.has_media = has_media
        self.chat_id = chat_id
        self.message_id = message_id


@pytest.fixture
def to_channel(monkeypatch):
    monkeypatch.setattr(settings, "delivery_target", "telegram")
    monkeypatch.setattr(settings, "telegram_target_chat_id", "@goldsignals")
    return settings


# ---------------------------------------------------------------- selection
def test_the_destination_follows_the_setting(to_channel):
    assert isinstance(get_sender(), TelegramChannelSender)


def test_line_is_still_the_default(monkeypatch):
    from app.line.client import LineClient

    monkeypatch.setattr(settings, "delivery_target", "line")
    assert isinstance(get_sender(), LineClient)


def test_the_label_names_the_configured_destination(to_channel):
    assert destination_label() == "@goldsignals"


# ------------------------------------------------------------------ text
async def test_text_is_posted_to_the_channel(to_channel):
    fake = FakeTelegram()
    sender = TelegramChannelSender(client=fake)

    result = await sender.push_text("Gold Buy Now @ 4594 Sl: 4584")
    assert result.ok
    assert fake.messages[0][1] == "Gold Buy Now @ 4594 Sl: 4584"
    assert fake.resolved == ["@goldsignals"]


async def test_a_numeric_channel_id_is_passed_as_a_number(to_channel, monkeypatch):
    """Telethon needs an int for an id and a string for a @name."""
    monkeypatch.setattr(settings, "telegram_target_chat_id", "-1001234567890")
    fake = FakeTelegram()
    await TelegramChannelSender(client=fake).push_text("hello")
    assert fake.resolved == [-1001234567890]


async def test_the_channel_is_resolved_once_not_per_message(to_channel):
    """Telegram rate-limits resolution far harder than sending."""
    fake = FakeTelegram()
    sender = TelegramChannelSender(client=fake)
    for _ in range(5):
        await sender.push_text("a signal")
    assert len(fake.resolved) == 1
    assert len(fake.messages) == 5


async def test_an_over_long_message_is_trimmed(to_channel):
    fake = FakeTelegram()
    await TelegramChannelSender(client=fake).push_text("x" * 5000)
    sent = fake.messages[0][1]
    assert len(sent) == 4096
    assert sent.endswith("…")


async def test_no_destination_configured_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "delivery_target", "telegram")
    monkeypatch.setattr(settings, "telegram_target_chat_id", "")
    with pytest.raises(TelegramConfigError):
        await TelegramChannelSender(client=FakeTelegram()).push_text("hello")


# ----------------------------------------------------------------- media
async def test_a_photo_travels_instead_of_becoming_a_placeholder(to_channel):
    """The whole point of this destination: members see the chart."""
    media = FakeMedia()
    fake = FakeTelegram(source_media=media)
    sender = TelegramChannelSender(client=fake)

    result = await sender.push_message(Row(has_media=True), "[photo]\nGold Buy Now @ 4594")
    assert result.ok
    assert fake.files, "the media itself should have been sent"
    entity, sent_media, caption = fake.files[0]
    assert sent_media is media
    assert caption == "[photo]\nGold Buy Now @ 4594"
    assert not fake.messages, "no separate text post was needed"


async def test_a_message_without_media_takes_the_text_path(to_channel):
    fake = FakeTelegram()
    await TelegramChannelSender(client=fake).push_message(Row(has_media=False), "Gold Buy Now")
    assert fake.messages and not fake.files


async def test_unreadable_media_still_delivers_the_text(to_channel):
    """Better the words than nothing at all."""
    fake = FakeTelegram(source_media=None)   # the source message has no media now
    result = await TelegramChannelSender(client=fake).push_message(Row(has_media=True), "[photo]\nGold Buy")
    assert result.ok
    assert fake.messages and not fake.files


async def test_a_caption_too_long_for_one_post_overflows_into_a_second(to_channel):
    """Telegram caps a caption well below a message; nothing is dropped."""
    fake = FakeTelegram(source_media=FakeMedia())
    text = "A" * 1200
    await TelegramChannelSender(client=fake).push_message(Row(has_media=True), text)

    assert len(fake.files[0][2]) == 1024
    assert fake.messages[0][1] == "A" * 176
    assert fake.files[0][2] + fake.messages[0][1] == text


# --------------------------------------------------------------- failures
async def test_a_flood_wait_is_retryable(to_channel):
    class FloodWaitError(Exception):
        seconds = 30

    fake = FakeTelegram(fail_with=FloodWaitError())
    result = await TelegramChannelSender(client=fake).push_text("hello")
    assert not result.ok
    assert result.retryable is True


async def test_being_unable_to_post_there_is_not_retried_for_ever(to_channel):
    """A permissions problem will not fix itself, so it fails fast."""
    class ChatWriteForbiddenError(Exception):
        pass

    fake = FakeTelegram(fail_with=ChatWriteForbiddenError())
    result = await TelegramChannelSender(client=fake).push_text("hello")
    assert not result.ok
    assert result.retryable is False


async def test_an_unknown_error_is_retried(to_channel):
    fake = FakeTelegram(fail_with=RuntimeError("network hiccup"))
    result = await TelegramChannelSender(client=fake).push_text("hello")
    assert not result.ok
    assert result.retryable is True


async def test_verify_names_the_channel(to_channel):
    ok, detail = await TelegramChannelSender(client=FakeTelegram()).verify()
    assert ok and detail == "Gold Signals"


async def test_verify_reports_a_missing_destination(monkeypatch):
    monkeypatch.setattr(settings, "delivery_target", "telegram")
    monkeypatch.setattr(settings, "telegram_target_chat_id", "")
    ok, detail = await TelegramChannelSender(client=FakeTelegram()).verify()
    assert not ok and "TELEGRAM_TARGET_CHAT_ID" in detail


# ------------------------------------------------------- through the queue
async def test_the_outbox_delivers_through_whichever_sender(session, to_channel):
    """End to end: a stored message reaches the channel and is marked SENT."""
    from app.db.models import DeliveryStatus
    from app.line.queue_worker import LineQueueWorker
    from app.processor.message_processor import ingest_message

    await ingest_message(
        session, chat_id=-1001, message_id=8000, content="Gold Buy Now @ 4594 Sl: 4584 TP: 50/100Pips"
    )
    await session.commit()

    fake = FakeTelegram()
    worker = LineQueueWorker(client=TelegramChannelSender(client=fake))
    sent = await worker.drain_once(worker._client)

    assert sent == 1
    assert fake.messages[0][1].startswith("Gold Buy Now")
    rows = await worker_rows(session)
    assert rows[0].status == DeliveryStatus.SENT


async def worker_rows(session):
    from sqlalchemy import select

    from app.db.models import TelegramMessage

    result = await session.execute(select(TelegramMessage).order_by(TelegramMessage.id))
    return list(result.scalars().all())
