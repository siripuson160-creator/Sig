"""Result engine tests (sections 17, 19, 20)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import SignalResult, SignalStatus
from app.engine.result_engine import Outcome, SignalSpec, evaluate, points
from app.prices.base import Candle

START = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)


def candle(minute: int, low: float, high: float, close: float | None = None) -> Candle:
    return Candle(
        ts=START + timedelta(minutes=minute),
        open=(low + high) / 2,
        high=high,
        low=low,
        close=close if close is not None else (low + high) / 2,
    )


def buy_spec() -> SignalSpec:
    return SignalSpec(direction="BUY", entry=3340, sl=3330, tps=[3350, 3360], signal_time=START)


def sell_spec() -> SignalSpec:
    return SignalSpec(direction="SELL", entry=3340, sl=3350, tps=[3330, 3320], signal_time=START)


async def run(spec, candles, **kwargs) -> Outcome:
    return await evaluate(spec, candles, now=START + timedelta(hours=1), **kwargs)


# ------------------------------------------------------------------------ BUY
async def test_buy_reaching_tp1_then_tp2_is_a_win_at_tp2():
    outcome = await run(
        buy_spec(),
        [candle(1, 3339, 3341), candle(2, 3341, 3351), candle(3, 3352, 3361)],
    )
    assert outcome.status == SignalStatus.TP2_HIT
    assert outcome.result == SignalResult.WIN
    assert outcome.profit_points == 20  # 3360 - 3340


async def test_buy_hitting_sl_is_a_loss_of_ten_points():
    outcome = await run(buy_spec(), [candle(1, 3339, 3341), candle(2, 3329, 3338)])
    assert outcome.status == SignalStatus.SL_HIT
    assert outcome.result == SignalResult.LOSS
    assert outcome.loss_points == 10
    assert outcome.profit_points == 0


async def test_buy_stops_at_tp1_while_tp2_is_still_open():
    outcome = await run(buy_spec(), [candle(1, 3339, 3341), candle(2, 3341, 3351)])
    assert outcome.status == SignalStatus.TP1_HIT
    assert outcome.result == SignalResult.PENDING_RESULT  # TP2 may still print
    assert outcome.max_tp_hit == 1


# ----------------------------------------------------------------------- SELL
async def test_sell_reaching_tp1_and_tp2():
    outcome = await run(
        sell_spec(),
        [candle(1, 3339, 3341), candle(2, 3329, 3339), candle(3, 3319, 3328)],
    )
    assert outcome.status == SignalStatus.TP2_HIT
    assert outcome.result == SignalResult.WIN
    assert outcome.profit_points == 20  # 3340 - 3320


async def test_sell_hitting_sl():
    outcome = await run(sell_spec(), [candle(1, 3339, 3341), candle(2, 3342, 3351)])
    assert outcome.status == SignalStatus.SL_HIT
    assert outcome.result == SignalResult.LOSS
    assert outcome.loss_points == 10


# ------------------------------------------------------------------ P/L units
@pytest.mark.parametrize(
    "direction,entry,exit_price,expected",
    [("BUY", 3340, 3350, 10), ("BUY", 3340, 3330, -10), ("SELL", 3340, 3330, 10), ("SELL", 3340, 3350, -10)],
)
def test_points_calculation(direction, entry, exit_price, expected):
    assert points(direction, entry, exit_price) == expected


# ------------------------------------------------------- same-candle handling
def conflicting_candles():
    """One candle that contains both TP1 and the SL."""
    return [candle(1, 3339, 3341), candle(2, 3329, 3351)]


async def test_same_candle_defaults_to_sl_first():
    outcome = await run(buy_spec(), conflicting_candles(), ambiguity_rule="SL_FIRST")
    assert outcome.status == SignalStatus.SL_HIT
    assert outcome.result == SignalResult.LOSS
    assert any("SL_FIRST" in note for note in outcome.notes)


async def test_same_candle_tp_first_rule():
    outcome = await run(buy_spec(), conflicting_candles(), ambiguity_rule="TP_FIRST")
    assert outcome.result == SignalResult.WIN
    assert any("TP_FIRST" in note for note in outcome.notes)


async def test_same_candle_ambiguous_rule_marks_the_signal():
    """Section 19: the outcome is never guessed at random."""
    outcome = await run(buy_spec(), conflicting_candles(), ambiguity_rule="AMBIGUOUS")
    assert outcome.status == SignalStatus.AMBIGUOUS
    assert outcome.result == SignalResult.AMBIGUOUS
    assert outcome.profit_points is None and outcome.loss_points is None


async def test_drilldown_breaks_the_tie_when_finer_data_exists():
    async def drilldown(start, stop):
        # Inside the conflicting minute: TP printed first, SL afterwards.
        return [
            Candle(ts=start, open=3345, high=3351, low=3344, close=3350),
            Candle(ts=start + timedelta(seconds=30), open=3350, high=3350, low=3329, close=3330),
        ]

    outcome = await run(buy_spec(), conflicting_candles(), drilldown=drilldown)
    # TP1 printed first and the stop followed in the same minute, so the trade
    # ends there — booked at TP1 rather than guessed.
    assert outcome.status == SignalStatus.TP1_HIT
    assert outcome.result == SignalResult.WIN
    assert outcome.profit_points == 10
    assert any("tie broken" in note for note in outcome.notes)


async def test_drilldown_that_stays_tied_falls_back_to_the_rule():
    async def drilldown(start, stop):
        return [Candle(ts=start, open=3340, high=3351, low=3329, close=3340)]

    outcome = await run(buy_spec(), conflicting_candles(), drilldown=drilldown, ambiguity_rule="AMBIGUOUS")
    assert outcome.status == SignalStatus.AMBIGUOUS


# ---------------------------------------------------------------- lifecycle
async def test_signal_that_never_fills_is_cancelled():
    far_away = [candle(minute, 3400, 3410) for minute in range(1, 5)]
    outcome = await evaluate(
        SignalSpec(direction="BUY", entry=3340, sl=3330, tps=[3350], signal_time=START),
        far_away + [Candle(ts=START + timedelta(hours=20), open=3400, high=3410, low=3400, close=3405)],
        now=START + timedelta(hours=21),
    )
    assert outcome.status == SignalStatus.CANCELLED
    assert outcome.result == SignalResult.CANCELLED


async def test_open_trade_stays_active():
    outcome = await run(buy_spec(), [candle(1, 3339, 3341), candle(2, 3338, 3345)])
    assert outcome.status == SignalStatus.ACTIVE
    assert outcome.result == SignalResult.PENDING_RESULT
    assert outcome.is_open


async def test_stale_trade_is_closed_at_the_last_price():
    candles = [candle(1, 3339, 3341), candle(2, 3341, 3345, close=3344)]
    outcome = await evaluate(buy_spec(), candles, now=START + timedelta(hours=200))
    assert outcome.status == SignalStatus.CLOSED
    assert outcome.result == SignalResult.WIN
    assert outcome.profit_points == 4  # marked out at 3344
    assert any("closed at market" in note for note in outcome.notes)


async def test_sl_after_tp1_is_booked_as_tp1():
    outcome = await run(
        buy_spec(),
        [candle(1, 3339, 3341), candle(2, 3341, 3351), candle(3, 3329, 3345)],
        result_mode="BEST_TP",
    )
    assert outcome.status == SignalStatus.TP1_HIT
    assert outcome.result == SignalResult.WIN
    assert outcome.profit_points == 10
    assert any("counted as TP1" in note for note in outcome.notes)


async def test_no_candles_leaves_the_signal_pending():
    outcome = await run(buy_spec(), [])
    assert outcome.status == SignalStatus.PENDING
    assert outcome.result == SignalResult.PENDING_RESULT


# ------------------------------------------------- price-feed request budget
class CountingProvider:
    """A provider that records how often it is actually asked for prices."""

    name = "counting"
    available = True

    def __init__(self, candles):
        self._candles = candles
        self.calls = 0

    async def get_candles(self, symbol, timeframe, start, end):
        self.calls += 1
        return list(self._candles)

    def supports_timeframe(self, timeframe):
        return True

    async def close(self):
        return None

    def describe(self):
        return {"name": self.name, "available": True}


async def test_one_price_request_per_symbol_not_per_signal(session):
    """A metered free plan must not be drained by having several signals open."""
    from app.db.models import Direction, SignalStatus
    from app.engine.result_engine import ResultEngine
    from tests.factories import make_signal

    for index in range(5):
        signal = make_signal(
            offset_minutes=index,
            result=SignalResult.PENDING_RESULT,
            status=SignalStatus.PENDING,
            message_id=7000 + index,
        )
        signal.direction = Direction.BUY
        session.add(signal)
    await session.commit()

    engine = ResultEngine(provider=CountingProvider([candle(1, 3339, 3341)]))
    await engine.run_once(session)

    assert engine.provider.calls == 1, "five open signals on one symbol must cost one request"


async def test_no_open_signals_costs_no_requests(session):
    """Quiet periods should not touch the price feed at all."""
    from app.engine.result_engine import ResultEngine

    engine = ResultEngine(provider=CountingProvider([]))
    assert await engine.run_once(session) == 0
    assert engine.provider.calls == 0
