"""The archive of what was pushed to the LINE group.

The point of this page is that it shows what members *received*, not what
Telegram held: the EDITED prefix and the length cap are part of the record.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.models import DeliveryStatus
from app.processor.message_processor import ingest_message

CHAT = -1001234567890


@pytest.fixture
def client():
    from app.api.main import create_app

    # init_database=True so the lifespan creates the schema: the access tests
    # below query the table rather than mocking it away.
    with TestClient(create_app(init_database=True)) as test_client:
        yield test_client


def admin(client):
    token = client.post("/api/admin/login", json={"password": settings.admin_secret}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


async def post(session, message_id, text, *, is_edit=False):
    return await ingest_message(
        session, chat_id=CHAT, message_id=message_id, content=text, is_edit=is_edit
    )


# ------------------------------------------------------------------- access
def test_the_archive_needs_an_admin(client):
    assert client.get("/api/admin/broadcast").status_code == 401


def test_members_do_not_see_the_archive_by_default(client):
    """The signal text is what members pay for; the dashboard is public."""
    assert settings.public_broadcast_enabled is False
    assert client.get("/api/public/broadcast").status_code == 404


def test_members_see_it_once_it_is_published(client, monkeypatch):
    monkeypatch.setattr(settings, "public_broadcast_enabled", True)
    response = client.get("/api/public/broadcast")
    assert response.status_code == 200
    assert "items" in response.json()


# ------------------------------------------------------------------ content
async def test_the_archive_shows_the_text_line_received(session):
    """An edit is delivered with the EDITED prefix, so that is what is recorded."""
    from app.api.broadcast import _broadcast_page

    await post(session, 60000, "Gold Buy Now @ 4601 Sl: 4590 TP: 50/100Pips")
    await post(session, 60000, "Gold Buy Now @ 4602 Sl: 4592 TP: 50/100Pips", is_edit=True)
    await session.commit()

    page = await _broadcast_page(session, limit=10, offset=0)
    assert page["total"] == 2
    newest, original = page["items"]

    assert newest["is_edit"] is True
    assert newest["line_text"].startswith("EDITED\n\n")
    assert "4602" in newest["line_text"]
    assert newest["version"] == 2

    assert original["is_edit"] is False
    assert original["line_text"].startswith("Gold Buy Now")
    assert original["characters"] == len(original["line_text"])


async def test_a_long_message_is_recorded_as_truncated(session, monkeypatch):
    """LINE caps a text message, so the archive shows the capped version."""
    from app.api.broadcast import _broadcast_page

    monkeypatch.setattr(settings, "line_max_chars", 50)
    await post(session, 60100, "x" * 400)
    await session.commit()

    page = await _broadcast_page(session, limit=10, offset=0)
    entry = page["items"][0]
    assert entry["characters"] == 50
    assert entry["line_text"].endswith("…")


async def test_newest_first(session):
    from app.api.broadcast import _broadcast_page

    for index in range(3):
        await post(session, 60200 + index, f"signal {index}")
    await session.commit()

    page = await _broadcast_page(session, limit=10, offset=0)
    assert [item["message_id"] for item in page["items"]] == [60202, 60201, 60200]


# ------------------------------------------------------------------ filters
async def test_search_matches_the_text(session):
    from app.api.broadcast import _broadcast_page

    await post(session, 60300, "Gold Buy Now @ 4601")
    await post(session, 60301, "Good morning everyone")
    await session.commit()

    page = await _broadcast_page(session, limit=10, offset=0, search="Buy Now")
    assert page["total"] == 1
    assert page["items"][0]["message_id"] == 60300


async def test_search_matches_a_message_id(session):
    """Paste the id from a complaint and find the entry."""
    from app.api.broadcast import _broadcast_page

    await post(session, 60400, "Gold Buy Now @ 4601")
    await post(session, 60401, "Gold Sell Now @ 4610")
    await session.commit()

    page = await _broadcast_page(session, limit=10, offset=0, search="60401")
    assert [item["message_id"] for item in page["items"]] == [60401]


async def test_filtering_by_delivery_status(session):
    from app.api.broadcast import _broadcast_page

    result = await post(session, 60500, "Gold Buy Now @ 4601")
    result.message.status = DeliveryStatus.SENT
    await post(session, 60501, "Gold Sell Now @ 4610")
    await session.commit()

    sent = await _broadcast_page(session, limit=10, offset=0, status="sent")
    assert [item["message_id"] for item in sent["items"]] == [60500]


async def test_paging_walks_the_whole_archive(session):
    from app.api.broadcast import _broadcast_page

    for index in range(5):
        await post(session, 60600 + index, f"signal {index}")
    await session.commit()

    first = await _broadcast_page(session, limit=2, offset=0)
    second = await _broadcast_page(session, limit=2, offset=2)
    assert first["total"] == second["total"] == 5
    assert not set(i["id"] for i in first["items"]) & set(i["id"] for i in second["items"])


# ------------------------------------------------------- schema upgrades
async def test_a_new_column_is_added_to_an_existing_database(tmp_path):
    """An upgrade must not need a hand-written ALTER for an added column.

    create_all only creates missing *tables*, so before this an existing
    install would start against its old database and fail on the first query
    with "no such column".
    """
    import sqlite3

    from sqlalchemy import create_engine, inspect

    from app.db.models import Base
    from app.db.session import _add_missing_columns

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE telegram_messages ("
        " id INTEGER PRIMARY KEY, chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL,"
        " version INTEGER NOT NULL, content TEXT NOT NULL, content_hash VARCHAR(64) NOT NULL,"
        " event_type VARCHAR(8) NOT NULL, received_at DATETIME NOT NULL,"
        " status VARCHAR(16) NOT NULL, send_attempts INTEGER NOT NULL, has_media BOOLEAN NOT NULL)"
    )
    old.execute(
        "INSERT INTO telegram_messages VALUES"
        " (1,-100,500,1,'Gold Buy Now','hash','NEW','2026-08-27 10:00:00','SENT',1,0)"
    )
    old.commit()
    old.close()

    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        added = _add_missing_columns(conn)

    assert any(name.endswith("reply_to_message_id") for name in added)
    with engine.connect() as conn:
        columns = {c["name"] for c in inspect(conn).get_columns("telegram_messages")}
        assert "reply_to_message_id" in columns
        # The row that was already there is untouched.
        row = conn.exec_driver_sql("SELECT chat_id, content FROM telegram_messages").one()
        assert row == (-100, "Gold Buy Now")
    engine.dispose()


async def test_upgrading_twice_changes_nothing(tmp_path):
    from sqlalchemy import create_engine

    from app.db.models import Base
    from app.db.session import _add_missing_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        assert _add_missing_columns(conn) == []
        assert _add_missing_columns(conn) == []
    engine.dispose()
