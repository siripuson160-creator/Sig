"""Results taken from what the source says about its own trade (RESULT_SOURCE=message).

The thread these tests replay is a real one: a gold signal, then the desk's own
follow-ups reporting how it went. What matters is that the numbers published
are the ones the source announced, and that they are labelled as such rather
than passed off as measured.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.db.models import SignalResult, SignalStatus
from app.processor.message_processor import ingest_message
from app.signals.outcomes import parse_outcome

SIGNAL = "Gold Buy Now @ 4601 - 4596  Sl: 4590  TP: 50/100Pips"
CHAT = -1001234567890


@pytest.fixture
def by_message(monkeypatch):
    monkeypatch.setattr(settings, "result_source", "message")
    return settings


async def post(session, message_id, text, *, reply_to=None):
    return await ingest_message(
        session,
        chat_id=CHAT,
        message_id=message_id,
        content=text,
        reply_to_message_id=reply_to,
    )


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize(
    "text,expected",
    [
        ("+50Pips now, make a good profit.", {"claimed_pips": 50.0, "tp_hit": None}),
        ("90Pips ! Can secure as TP2 now guys.", {"claimed_pips": 90.0, "tp_hit": 2}),
        ("TP1 hit ✅", {"tp_hit": 1}),
        ("Close now, -30 pips", {"claimed_pips": -30.0, "closed": True}),
    ],
)
def test_what_the_source_announces_is_read(text, expected):
    outcome = parse_outcome(text)
    for attribute, value in expected.items():
        assert getattr(outcome, attribute) == value


def test_an_ordinary_chat_message_claims_nothing():
    assert parse_outcome("Good morning everyone, ready for London session?").is_empty
    assert parse_outcome("").is_empty


def test_a_minus_sign_is_read_as_a_loss():
    assert parse_outcome("closed -30 pips").claimed_pips == -30.0


def test_announced_pips_become_the_units_the_statistics_use():
    """90 pips at 0.1 per pip is 9 points of gold."""
    assert parse_outcome("90 Pips!").claimed_points() == 9.0


def test_securing_at_breakeven_is_not_the_end_of_the_trade():
    """"Set breakeven" protects a position; it does not close it."""
    outcome = parse_outcome("Good Job guys. Now set breakeven to hold longer")
    assert outcome.breakeven is True
    assert outcome.decides_the_trade is False


# -------------------------------------------------------------- the thread
async def test_the_sources_own_report_decides_the_result(session, by_message):
    """The real thread: signal, "+50 Pips", then "90Pips ! ... secure as TP2"."""
    created = await post(session, 30604, SIGNAL)
    signal = created.signal
    assert signal is not None
    assert signal.tp2 == 4611  # 100 pips from 4601

    # Progress report: the trade is running, but nothing is decided yet.
    await post(session, 30605, "+50Pips now, make a good profit.", reply_to=30604)
    assert signal.status == SignalStatus.ACTIVE
    assert signal.result == SignalResult.PENDING_RESULT

    # The verdict.
    await post(session, 30606, "90Pips ! Can secure as TP2 now guys.", reply_to=30604)
    assert signal.status == SignalStatus.TP2_HIT
    assert signal.result == SignalResult.WIN
    assert signal.profit_points == 9.0          # 90 announced pips
    assert signal.loss_points == 0.0
    assert signal.result_source == "MESSAGE"    # never mistaken for measured
    assert "reported by the source" in signal.evaluation_note


async def test_a_reported_stop_is_a_loss(session, by_message):
    created = await post(session, 31000, SIGNAL)
    await post(session, 31001, "SL hit guys, next one soon", reply_to=31000)
    assert created.signal.status == SignalStatus.SL_HIT
    assert created.signal.result == SignalResult.LOSS
    # No number was announced, so the distance to the stop is used.
    assert created.signal.loss_points == 11.0   # 4601 -> 4590


async def test_a_cancelled_signal_is_not_counted_as_a_loss(session, by_message):
    created = await post(session, 31100, SIGNAL)
    await post(session, 31101, "cancel this one, no trade", reply_to=31100)
    assert created.signal.status == SignalStatus.CANCELLED
    assert created.signal.result == SignalResult.CANCELLED
    assert created.signal.profit_points is None


async def test_a_verdict_is_only_reached_once(session, by_message):
    """A later message must not rewrite a booked result."""
    created = await post(session, 31200, SIGNAL)
    await post(session, 31201, "TP2 hit", reply_to=31200)
    booked = created.signal.profit_points
    await post(session, 31202, "SL hit", reply_to=31200)
    assert created.signal.status == SignalStatus.TP2_HIT
    assert created.signal.profit_points == booked


async def test_a_reply_to_something_that_is_not_a_signal_is_ignored(session, by_message):
    result = await post(session, 31300, "90 Pips! TP2 hit", reply_to=999999)
    assert result.signal is None


async def test_a_report_with_no_reply_link_is_ignored(session, by_message):
    """Without the reply there is no way to know which trade is meant."""
    await post(session, 31400, SIGNAL)
    loose = await post(session, 31401, "TP2 hit")
    assert loose.signal is None


async def test_chatter_in_the_group_changes_nothing(session, by_message):
    created = await post(session, 31500, SIGNAL)
    await post(session, 31501, "Good morning everyone", reply_to=31500)
    assert created.signal.status == SignalStatus.PENDING
    assert created.signal.result == SignalResult.PENDING_RESULT


# ------------------------------------------------------- the other mode
async def test_price_mode_ignores_what_the_source_claims(session, monkeypatch):
    """With RESULT_SOURCE=price the group's own reports decide nothing."""
    monkeypatch.setattr(settings, "result_source", "price")
    created = await post(session, 31600, SIGNAL)
    await post(session, 31601, "90Pips ! Can secure as TP2 now guys.", reply_to=31600)
    assert created.signal.status == SignalStatus.PENDING
    assert created.signal.result_source is None
