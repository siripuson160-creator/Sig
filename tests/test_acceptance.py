"""The acceptance tests from the brief, one test per numbered section.

These duplicate a little of what the unit tests cover. That is deliberate:
each one is written to read like the section it comes from, so the brief can be
checked off against a test run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest_asyncio
from sqlalchemy import func, select

from app.api.main import create_app
from app.db.models import AuditEvent, AuditLog, DeliveryStatus, EventType, Signal, SignalResult, TelegramMessage
from app.engine import stats_engine
from app.line.client import LineSendResult
from app.line.queue_worker import LineQueueWorker
from app.processor.message_processor import ingest_message, render_line_text
from tests.factories import make_signal
from tests.test_line_worker import FakeLineClient

CHAT = -1001234567890
ADMIN = {"X-Admin-Key": "test-admin-key"}


@pytest_asyncio.fixture
async def client(session):
    app = create_app(init_database=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def post(session, message_id: int, content: str, *, is_edit: bool = False, minutes: int = 0):
    return await ingest_message(
        session,
        chat_id=CHAT,
        message_id=message_id,
        content=content,
        tg_created_at=datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes),
        is_edit=is_edit,
    )


async def sent_texts(session) -> list[str]:
    """Everything that would reach the LINE group, in order."""
    client = FakeLineClient()
    await session.commit()
    await LineQueueWorker().drain_once(client)
    return [text for text, _ in client.sent]


# ---------------------------------------------------------------- section 65
async def test_65_plain_message_reaches_line(session):
    """Telegram: "Sell now"  ->  LINE: "Sell now"."""
    await post(session, 100, "Sell now")
    assert await sent_texts(session) == ["Sell now"]


# ---------------------------------------------------------------- section 66
async def test_66_edit_arrives_as_a_second_message(session):
    """The first LINE message is left alone; the edit is a new one."""
    await post(session, 500, "Sell now")
    await post(session, 500, "SELL GOLD 3340\nSL 3330\nTP1 3350\nTP2 3360", is_edit=True, minutes=1)

    assert await sent_texts(session) == [
        "Sell now",
        "EDITED\n\nSELL GOLD 3340\nSL 3330\nTP1 3350\nTP2 3360",
    ]

    # The stored first version is untouched — nothing rewrites history.
    first = (
        await session.execute(
            select(TelegramMessage).where(TelegramMessage.message_id == 500, TelegramMessage.version == 1)
        )
    ).scalar_one()
    assert first.content == "Sell now"
    assert first.event_type == EventType.NEW


# ---------------------------------------------------------------- section 67
async def test_67_four_versions_produce_four_line_messages(session):
    versions = [
        "Sell now",
        "SELL GOLD 3340",
        "SELL GOLD 3340\nSL 3330",
        "SELL GOLD 3340\nSL 3330\nTP1 3350\nTP2 3360",
    ]
    for index, content in enumerate(versions):
        await post(session, 501, content, is_edit=index > 0, minutes=index)

    texts = await sent_texts(session)
    assert len(texts) == 4
    assert texts[0] == "Sell now"
    assert texts[1] == "EDITED\n\nSELL GOLD 3340"
    assert texts[2] == "EDITED\n\nSELL GOLD 3340\nSL 3330"
    assert texts[3] == "EDITED\n\nSELL GOLD 3340\nSL 3330\nTP1 3350\nTP2 3360"


# ---------------------------------------------------------------- section 68
async def test_68_restart_does_not_resend(session):
    """A replay after restart delivers nothing a second time."""
    await post(session, 502, "Sell now")
    assert await sent_texts(session) == ["Sell now"]

    # Telegram replays the same message when the listener reconnects.
    replay = await post(session, 502, "Sell now")
    assert not replay.created
    assert await sent_texts(session) == []


# ---------------------------------------------------------------- section 69
async def test_69_same_text_different_message_id(session):
    await post(session, 100, "Sell now")
    await post(session, 101, "Sell now")
    assert await sent_texts(session) == ["Sell now", "Sell now"]


# ---------------------------------------------------------------- section 70
async def test_70_signal_appears_on_the_dashboard(client, session):
    result = await post(session, 600, "BUY GOLD 3340\nSL 3330\nTP1 3350\nTP2 3360")
    await session.commit()

    body = (await client.get(f"/api/public/signals/{result.signal.signal_id}")).json()
    assert body["direction"] == "BUY"
    assert body["symbol"] == "XAUUSD"
    assert (body["entry"], body["sl"], body["tp1"], body["tp2"]) == (3340, 3330, 3350, 3360)
    assert body["result"] == "PENDING_RESULT"

    # When the trade plays out, the result, the P/L and the statistics follow.
    signal = (await session.execute(select(Signal).where(Signal.signal_id == result.signal.signal_id))).scalar_one()
    signal.status = "TP2_HIT"
    signal.result = SignalResult.WIN
    signal.profit_points = 20
    signal.loss_points = 0
    signal.resolved_at = signal.signal_time + timedelta(hours=1)
    await session.commit()

    detail = (await client.get(f"/api/public/signals/{result.signal.signal_id}")).json()
    assert detail["result"] == "WIN"
    assert detail["net_points"] == 20

    overview = (await client.get("/api/public/overview")).json()
    assert overview["wins"] == 1
    assert overview["total_pl_points"] == 20


# ---------------------------------------------------------------- section 71
async def test_71_win_rate_comes_from_the_database(client, session):
    """100 signals, 70 wins, 30 losses -> the dashboard says 70%."""
    for index in range(70):
        session.add(make_signal(offset_minutes=index, result=SignalResult.WIN, points=10, message_id=2000 + index))
    for index in range(30):
        session.add(make_signal(offset_minutes=100 + index, result=SignalResult.LOSS, points=-10, message_id=3000 + index))
    await session.commit()

    overview = (await client.get("/api/public/overview")).json()
    assert overview["total_signals"] == 100
    assert overview["wins"] == 70
    assert overview["losses"] == 30
    assert overview["win_rate"] == 70.0

    # Same numbers through the section 61 path, and through the admin view.
    assert (await client.get("/api/dashboard/summary")).json()["win_rate"] == 70.0
    assert (await client.get("/api/admin/statistics", headers=ADMIN)).json()["overview"]["win_rate"] == 70.0


# ---------------------------------------------------------------- section 72
async def test_72_dashboard_shows_every_version(client, session):
    for index, content in enumerate(["Sell now", "SELL GOLD 3340", "SELL GOLD 3340\nSL 3330", "SELL GOLD 3340\nSL 3330\nTP1 3350"]):
        result = await post(session, 700, content, is_edit=index > 0, minutes=index)
    await session.commit()

    detail = (await client.get(f"/api/public/signals/{result.signal.signal_id}")).json()
    assert [m["version"] for m in detail["message_history"]] == [1, 2, 3, 4]
    assert [m["content"] for m in detail["message_history"]][0] == "Sell now"
    assert [v["version"] for v in detail["parse_history"]] == [1, 2, 3, 4]

    history = (await client.get(f"/api/signals/{result.signal.signal_id}/history")).json()
    assert len(history["versions"]) == 4


# ---------------------------------------------------------------- section 73
async def test_73_pages_and_data_load_for_a_mobile_client(client, session):
    """The dashboard is one page plus JSON; every piece it needs must load."""
    session.add(make_signal(offset_minutes=0, result=SignalResult.WIN, points=10))
    await session.commit()

    page = await client.get("/dashboard")
    assert page.status_code == 200
    assert 'name="viewport"' in page.text  # responsive meta tag present

    for asset in ("/static/css/app.css", "/static/js/dashboard.js", "/static/js/charts.js", "/static/js/util.js"):
        assert (await client.get(asset)).status_code == 200

    for endpoint in (
        "/api/public/overview",
        "/api/public/signals",
        "/api/public/performance/daily",
        "/api/public/analytics",
        "/api/public/methodology",
    ):
        assert (await client.get(endpoint)).status_code == 200

    # Filters used by the mobile UI.
    assert (await client.get("/api/public/signals?direction=BUY&result=WIN")).status_code == 200
    assert (await client.get("/api/public/signals?status=TP1_HIT")).status_code == 200
    assert (await client.get("/api/public/overview?range=custom&date_from=2026-08-01&date_to=2026-08-31")).status_code == 200


# ---------------------------------------------------------------- section 74
async def test_74_state_survives_a_restart(session):
    """After a reboot the queue, the history and the results are all still there."""
    await post(session, 800, "BUY GOLD 3340\nSL 3330\nTP1 3350")
    await session.commit()

    messages_before = (await session.execute(select(func.count()).select_from(TelegramMessage))).scalar_one()
    signals_before = (await session.execute(select(func.count()).select_from(Signal))).scalar_one()

    # A restart is just a new session against the same database.
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as fresh:
        assert (await fresh.execute(select(func.count()).select_from(TelegramMessage))).scalar_one() == messages_before
        assert (await fresh.execute(select(func.count()).select_from(Signal))).scalar_one() == signals_before

        # Anything still queued is picked up again rather than lost.
        pending = (
            await fresh.execute(
                select(func.count()).select_from(TelegramMessage).where(TelegramMessage.status == DeliveryStatus.PENDING)
            )
        ).scalar_one()
        assert pending == 1


# ---------------------------------------------------------------- section 75
async def test_75_a_line_outage_does_not_lose_messages(session):
    """Delivery fails, the message waits, and it goes out when LINE returns."""
    result = await post(session, 900, "Sell now")
    await session.commit()

    worker = LineQueueWorker()
    offline = FakeLineClient(
        responses=[LineSendResult(ok=False, status_code=503, error="service unavailable", retryable=True)]
    )
    assert await worker.drain_once(offline) == 0

    await session.refresh(result.message)
    assert result.message.status == DeliveryStatus.PENDING  # still queued, not dropped

    worker._retry_not_before = None  # the backoff elapses
    assert await worker.drain_once(FakeLineClient()) == 1

    await session.refresh(result.message)
    assert result.message.status == DeliveryStatus.SENT


# ------------------------------------------------- sections 43, 44 and 46
async def test_43_no_route_accepts_a_typed_in_statistic(client, session):
    """There is nowhere to enter a win rate, a profit total or a count."""
    schema = (await client.get("/api/openapi.json")).json()
    forbidden = {"win_rate", "total_pl_points", "wins", "losses", "total_signals", "profit_factor"}

    for name, component in schema.get("components", {}).get("schemas", {}).items():
        fields = set(component.get("properties", {}))
        assert not (fields & forbidden), f"{name} exposes a writable statistic: {fields & forbidden}"


async def test_44_every_change_is_recorded(client, session):
    """Signal created, edited, and corrected — each one leaves a trail."""
    result = await post(session, 1000, "BUY GOLD 3340\nSL 3330\nTP1 3350")
    await post(session, 1000, "BUY GOLD 3341\nSL 3330\nTP1 3350", is_edit=True, minutes=1)
    await session.commit()
    signal_id = result.signal.signal_id

    await client.patch(
        f"/api/admin/signals/{signal_id}",
        headers=ADMIN,
        json={"result": "WIN", "profit_points": 10, "reason": "confirmed on the broker chart"},
    )

    entries = (
        (await session.execute(select(AuditLog).where(AuditLog.entity_id == signal_id).order_by(AuditLog.id)))
        .scalars()
        .all()
    )
    events = [entry.event for entry in entries]
    assert AuditEvent.SIGNAL_CREATED in events
    assert AuditEvent.SIGNAL_EDITED in events
    assert AuditEvent.SIGNAL_RESULT_UPDATED in events

    correction = next(e for e in entries if e.event == AuditEvent.SIGNAL_RESULT_UPDATED)
    assert correction.old_value and correction.new_value  # what it was, what it became
    assert correction.reason == "confirmed on the broker chart"
    assert correction.actor  # who
    assert correction.ts  # when


async def test_46_history_cannot_be_deleted_through_the_api(client, session):
    """No delete route exists for signals, messages or the audit log."""
    schema = (await client.get("/api/openapi.json")).json()
    deletes = [path for path, methods in schema["paths"].items() if "delete" in methods]
    assert deletes == []


# ------------------------------------------------------------- section 76
async def test_76_disclaimer_is_published(client):
    body = (await client.get("/api/public/methodology")).json()
    assert "Trading involves significant risk" in body["disclaimer"]
    assert "does not guarantee future results" in body["disclaimer"]


# ------------------------------------------------------------- section 41
async def test_41_periods_are_bucketed_in_bangkok_time(session):
    """20:00 UTC belongs to the next day in Asia/Bangkok."""
    session.add(make_signal(offset_minutes=0, result=SignalResult.WIN, points=10))  # 10:00 local
    session.add(make_signal(offset_minutes=17 * 60, result=SignalResult.LOSS, points=-10))  # 03:00 next day
    await session.commit()

    daily = await stats_engine.build_performance(session, "daily")
    assert [bucket["period"] for bucket in daily] == ["2026-08-18", "2026-08-17"]
