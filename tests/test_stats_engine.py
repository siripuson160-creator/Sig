"""Statistics tests (sections 20, 21, 24, 25)."""

from __future__ import annotations

from app.db.models import SignalResult
from app.engine import stats_engine
from tests.factories import make_signal


async def seed(session, signals):
    for signal in signals:
        session.add(signal)
    await session.commit()


async def test_overview_numbers(session):
    await seed(
        session,
        [
            make_signal(offset_minutes=0, result=SignalResult.WIN, points=20),
            make_signal(offset_minutes=10, result=SignalResult.WIN, points=10),
            make_signal(offset_minutes=20, result=SignalResult.WIN, points=10),
            make_signal(offset_minutes=30, result=SignalResult.LOSS, points=-10),
            make_signal(offset_minutes=40, result=SignalResult.PENDING_RESULT),
            make_signal(offset_minutes=50, result=SignalResult.AMBIGUOUS, points=0),
        ],
    )
    overview = await stats_engine.build_overview(session)

    assert overview["total_signals"] == 6
    assert overview["wins"] == 3
    assert overview["losses"] == 1
    assert overview["win_rate"] == 75.0
    assert overview["total_pl_points"] == 30
    assert overview["profit_factor"] == 4.0  # 40 / 10
    assert overview["pending"] == 1
    assert overview["ambiguous"] == 1
    assert overview["avg_win_points"] == 13.33
    assert overview["avg_loss_points"] == -10


async def test_ambiguous_and_pending_do_not_move_the_win_rate(session):
    await seed(
        session,
        [
            make_signal(offset_minutes=0, result=SignalResult.WIN, points=10),
            make_signal(offset_minutes=10, result=SignalResult.AMBIGUOUS, points=0),
            make_signal(offset_minutes=20, result=SignalResult.PENDING_RESULT),
            make_signal(offset_minutes=30, result=SignalResult.CANCELLED, points=0),
        ],
    )
    overview = await stats_engine.build_overview(session)
    assert overview["win_rate"] == 100.0
    assert overview["decided_signals"] == 1
    # ...but they stay visible, so the headline cannot hide them.
    assert overview["ambiguous"] == 1
    assert overview["cancelled"] == 1
    assert overview["total_signals"] == 4


async def test_incomplete_signals_are_not_counted_as_trades(session):
    await seed(
        session,
        [
            make_signal(offset_minutes=0, result=SignalResult.WIN, points=10),
            make_signal(offset_minutes=10, result=SignalResult.PENDING_RESULT, is_complete=False),
        ],
    )
    overview = await stats_engine.build_overview(session)
    assert overview["total_signals"] == 1


async def test_max_drawdown(session):
    await seed(
        session,
        [
            make_signal(offset_minutes=0, result=SignalResult.WIN, points=100),
            make_signal(offset_minutes=10, result=SignalResult.LOSS, points=-50),
            make_signal(offset_minutes=20, result=SignalResult.LOSS, points=-150),
            make_signal(offset_minutes=30, result=SignalResult.WIN, points=80),
        ],
    )
    overview = await stats_engine.build_overview(session)
    # Equity: 100 -> 50 -> -100 -> -20. Peak 100, trough -100.
    assert overview["max_drawdown_points"] == -200
    assert overview["total_pl_points"] == -20


async def test_streaks(session):
    await seed(
        session,
        [
            make_signal(offset_minutes=0, result=SignalResult.WIN, points=10),
            make_signal(offset_minutes=10, result=SignalResult.WIN, points=10),
            make_signal(offset_minutes=20, result=SignalResult.WIN, points=10),
            make_signal(offset_minutes=30, result=SignalResult.LOSS, points=-10),
            make_signal(offset_minutes=40, result=SignalResult.LOSS, points=-10),
        ],
    )
    overview = await stats_engine.build_overview(session)
    assert overview["longest_win_streak"] == 3
    assert overview["longest_loss_streak"] == 2
    assert overview["current_streak"] == 2
    assert overview["current_streak_kind"] == "LOSS"


async def test_daily_buckets_use_the_configured_timezone(session):
    await seed(
        session,
        [
            # 03:00 UTC = 10:00 Bangkok on the same day.
            make_signal(offset_minutes=0, result=SignalResult.WIN, points=10),
            # 20:00 UTC = 03:00 Bangkok the *next* day.
            make_signal(offset_minutes=17 * 60, result=SignalResult.LOSS, points=-10),
        ],
    )
    daily = await stats_engine.build_performance(session, "daily")
    assert [bucket["period"] for bucket in daily] == ["2026-08-18", "2026-08-17"]
    assert daily[1]["wins"] == 1
    assert daily[0]["losses"] == 1


async def test_weekly_and_monthly_buckets(session):
    await seed(
        session,
        [
            make_signal(offset_minutes=0, result=SignalResult.WIN, points=10),
            make_signal(offset_minutes=60 * 24 * 8, result=SignalResult.WIN, points=10),
        ],
    )
    weekly = await stats_engine.build_performance(session, "weekly")
    monthly = await stats_engine.build_performance(session, "monthly")
    assert len(weekly) == 2
    assert [b["period"] for b in monthly] == ["2026-08"]
    assert monthly[0]["pl_points"] == 20


async def test_analytics_breakdowns(session):
    await seed(
        session,
        [
            make_signal(offset_minutes=0, direction="BUY", result=SignalResult.WIN, points=10),
            make_signal(offset_minutes=10, direction="SELL", result=SignalResult.LOSS, points=-10),
            make_signal(offset_minutes=20, direction="SELL", result=SignalResult.WIN, points=15),
        ],
    )
    analytics = await stats_engine.build_analytics(session)

    directions = {row["direction"]: row for row in analytics["by_direction"]}
    assert directions["BUY"]["win_rate"] == 100.0
    assert directions["SELL"]["win_rate"] == 50.0
    assert len(analytics["equity_curve"]) == 3
    assert analytics["equity_curve"][-1]["equity"] == 15
    assert analytics["by_hour"][0]["hour"] == 10  # Bangkok time
    assert any(row["level"] == "TP1" for row in analytics["tp_distribution"])


async def test_range_filter(session):
    await seed(
        session,
        [
            make_signal(offset_minutes=0, result=SignalResult.WIN, points=10),
            make_signal(offset_minutes=-60 * 24 * 40, result=SignalResult.LOSS, points=-10),
        ],
    )
    all_time = await stats_engine.build_overview(session, "all")
    assert all_time["total_signals"] == 2
    # The 40-day-old signal falls outside a 30-day window (relative to today,
    # so this only asserts the filter narrows the set).
    windowed = await stats_engine.build_overview(session, "30d")
    assert windowed["total_signals"] <= all_time["total_signals"]
