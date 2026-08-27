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

    # An announced figure is counted straight away — this desk reports its
    # wins as a pip count, so waiting for the word "TP" would leave a winning
    # trade at PENDING for ever.
    await post(session, 30605, "+50Pips now, make a good profit.", reply_to=30604)
    assert signal.status == SignalStatus.ACTIVE   # held, not closed
    assert signal.result == SignalResult.WIN
    assert signal.profit_points == 5.0

    # Naming the target closes it, and the larger figure replaces the earlier.
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


# ------------------------------- profit announced as a pip count, not a TP
# This desk reports its wins as "+70Pips", never by naming a target. Waiting
# for the words "TP1" left a won trade sitting at PENDING for ever.
GARY_SIGNAL = "Gold Buy Now @ 4594 - 4588\n\nSl: 4584\n\nTP: 50/100Pips"
GARY_RESULT = (
    "+70Pips making profit again.\n\nBe secure and set your breakeven. "
    "Today 4/4 winning setup. Good job everyone"
)


async def test_an_announced_profit_is_counted_as_a_win(session, by_message):
    """The real thread from the source group, start to finish."""
    created = await post(session, 70100, GARY_SIGNAL)
    signal = created.signal
    assert (signal.entry, signal.sl, signal.tp1, signal.tp2) == (4594, 4584, 4599, 4604)
    assert signal.result == SignalResult.PENDING_RESULT

    await post(session, 70101, GARY_RESULT, reply_to=70100)
    assert signal.result == SignalResult.WIN
    assert signal.profit_points == 7.0        # 70 announced pips
    assert signal.loss_points == 0.0
    assert signal.result_source == "MESSAGE"
    # Still held, not closed: they set breakeven and kept the position.
    assert signal.status == SignalStatus.ACTIVE


async def test_an_announced_profit_reaches_the_statistics(session, by_message):
    from app.engine import stats_engine

    await post(session, 70200, GARY_SIGNAL)
    await post(session, 70201, GARY_RESULT, reply_to=70200)
    await session.commit()

    overview = await stats_engine.build_overview(session, "all")
    assert overview["wins"] == 1
    assert overview["losses"] == 0
    assert overview["win_rate"] == 100.0
    assert overview["total_pl_points"] == 7.0


async def test_a_growing_profit_replaces_the_earlier_figure(session, by_message):
    """"+50Pips" then "+90Pips" is one trade improving, not two results."""
    created = await post(session, 70300, GARY_SIGNAL)
    await post(session, 70301, "+50Pips now, make a good profit.", reply_to=70300)
    assert created.signal.profit_points == 5.0

    await post(session, 70302, "+90Pips ! still running", reply_to=70300)
    assert created.signal.profit_points == 9.0
    assert created.signal.result == SignalResult.WIN


async def test_a_smaller_later_figure_does_not_shrink_the_result(session, by_message):
    created = await post(session, 70400, GARY_SIGNAL)
    await post(session, 70401, "+90Pips !", reply_to=70400)
    await post(session, 70402, "+40Pips still", reply_to=70400)
    assert created.signal.profit_points == 9.0


async def test_a_named_target_still_books_the_trade(session, by_message):
    """Naming TP2 closes it; a bare pip count leaves it running."""
    created = await post(session, 70500, GARY_SIGNAL)
    await post(session, 70501, "90Pips ! Can secure as TP2 now guys.", reply_to=70500)
    assert created.signal.status == SignalStatus.TP2_HIT
    assert created.signal.profit_points == 9.0


async def test_setting_breakeven_alone_publishes_nothing(session, by_message):
    """No figure announced means nothing to count."""
    created = await post(session, 70600, GARY_SIGNAL)
    await post(session, 70601, "Be secure and set your breakeven.", reply_to=70600)
    assert created.signal.result == SignalResult.PENDING_RESULT
    assert created.signal.profit_points is None


async def test_the_day_summary_is_not_read_as_a_result(session, by_message):
    """"Today 4/4 winning setup" is about the day, not this trade."""
    assert parse_outcome("Today 4/4 winning setup. Good job everyone").is_empty
