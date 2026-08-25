"""Dashboard API tests (sections 22 and the admin surface)."""

from __future__ import annotations

import httpx
import pytest_asyncio

from app.api.main import create_app
from app.db.models import SignalResult
from app.processor.message_processor import ingest_message
from tests.factories import make_signal

ADMIN = {"X-Admin-Key": "test-admin-key"}


@pytest_asyncio.fixture
async def client(session):
    app = create_app(init_database=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_overview_endpoint(client, session):
    session.add(make_signal(offset_minutes=0, result=SignalResult.WIN, points=25))
    session.add(make_signal(offset_minutes=5, result=SignalResult.LOSS, points=-10))
    await session.commit()

    response = await client.get("/api/public/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["wins"] == 1 and body["losses"] == 1
    assert body["win_rate"] == 50.0
    assert body["total_pl_points"] == 15
    assert body["timezone"] == "Asia/Bangkok"


async def test_signals_list_and_detail(client, session):
    result = await ingest_message(
        session,
        chat_id=-1001234567890,
        message_id=900,
        content="Sell now",
    )
    await ingest_message(
        session,
        chat_id=-1001234567890,
        message_id=900,
        content="SELL GOLD 3340\nSL 3350\nTP1 3330\nTP2 3320",
        is_edit=True,
    )
    await session.commit()
    signal_id = result.signal.signal_id

    listing = await client.get("/api/public/signals")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    detail = await client.get(f"/api/public/signals/{signal_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["direction"] == "SELL"
    assert body["entry"] == 3340
    # Both the Telegram versions and both parses are visible (sections 12, 22).
    assert [m["version"] for m in body["message_history"]] == [1, 2]
    assert [m["event_type"] for m in body["message_history"]] == ["NEW", "EDIT"]
    assert [v["version"] for v in body["parse_history"]] == [1, 2]

    missing = await client.get("/api/public/signals/does-not-exist")
    assert missing.status_code == 404


async def test_performance_endpoints(client, session):
    session.add(make_signal(offset_minutes=0, result=SignalResult.WIN, points=10))
    await session.commit()

    for granularity in ("daily", "weekly", "monthly"):
        response = await client.get(f"/api/public/performance/{granularity}")
        assert response.status_code == 200
        assert response.json()["items"][0]["wins"] == 1

    assert (await client.get("/api/public/performance/hourly")).status_code == 422


async def test_analytics_endpoint(client, session):
    session.add(make_signal(offset_minutes=0, result=SignalResult.WIN, points=10))
    await session.commit()
    body = (await client.get("/api/public/analytics")).json()
    assert body["equity_curve"][0]["equity"] == 10
    assert body["by_direction"][0]["direction"] == "BUY"


async def test_methodology_is_published(client):
    body = (await client.get("/api/public/methodology")).json()
    assert body["unit"] == "points"
    assert body["ambiguity_rule"] == "SL_FIRST"
    assert any("EDITED" in rule for rule in body["rules"])
    assert body["parsers"]


async def test_public_api_is_read_only(client, session):
    session.add(make_signal(offset_minutes=0, result=SignalResult.WIN, points=10))
    await session.commit()
    signal_id = (await client.get("/api/public/signals")).json()["items"][0]["signal_id"]

    # Members have no write route at all.
    assert (await client.post(f"/api/public/signals/{signal_id}")).status_code == 405
    assert (await client.patch(f"/api/public/signals/{signal_id}", json={"result": "WIN"})).status_code == 405


async def test_admin_requires_a_key(client):
    assert (await client.get("/api/admin/status")).status_code == 401
    assert (await client.get("/api/admin/status", headers={"X-Admin-Key": "wrong"})).status_code == 401
    assert (await client.get("/api/admin/status", headers=ADMIN)).status_code == 200


async def test_admin_can_override_a_result(client, session):
    session.add(make_signal(offset_minutes=0, result=SignalResult.PENDING_RESULT))
    await session.commit()
    signal_id = (await client.get("/api/public/signals")).json()["items"][0]["signal_id"]

    response = await client.patch(
        f"/api/admin/signals/{signal_id}",
        headers=ADMIN,
        json={"result": "WIN", "status": "TP1_HIT", "profit_points": 10, "note": "checked against broker chart"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "WIN"
    assert body["manual_override"] is True
    assert body["note"] == "checked against broker chart"


async def test_admin_message_queue(client, session):
    await ingest_message(session, chat_id=-1001234567890, message_id=910, content="Good morning")
    await session.commit()

    body = (await client.get("/api/admin/messages", headers=ADMIN)).json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["status"] == "PENDING"

    requeued = await client.post(f"/api/admin/messages/{row['id']}/requeue", headers=ADMIN)
    assert requeued.status_code == 200
    assert requeued.json()["send_attempts"] == 0


async def test_dashboard_pages_are_served(client):
    for path in ("/dashboard", "/admin"):
        response = await client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    assert (await client.get("/healthz")).json()["status"] == "ok"
