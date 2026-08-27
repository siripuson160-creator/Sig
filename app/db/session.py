"""Async engine / session management."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _ensure_sqlite_dir(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    path = url.split("///", 1)[-1]
    if path and path != ":memory:":
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _ensure_sqlite_dir(settings.database_url)
        kwargs: dict = {"echo": False, "future": True}
        if settings.is_sqlite:
            # A single writer with WAL keeps the listener, the LINE worker and
            # the API from tripping over "database is locked".
            kwargs["connect_args"] = {"timeout": 30}
        else:
            kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)
        _engine = create_async_engine(settings.database_url, **kwargs)

        if settings.is_sqlite:

            @event.listens_for(_engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()

    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope: commits on success, rolls back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency (read-mostly; routes commit explicitly)."""
    async with get_sessionmaker()() as session:
        yield session


def _add_missing_columns(conn) -> list[str]:
    """Add columns the models have gained since the database was created.

    ``create_all`` only creates missing *tables*, so a release that adds a
    column would otherwise start against an old database and fail on the first
    query with "no such column". Both SQLite and PostgreSQL support
    ``ALTER TABLE ... ADD COLUMN``, so the additive case is handled here and
    an upgrade needs no manual step.

    Deliberately additive only: nothing is dropped, renamed or retyped, and a
    column that is NOT NULL without a default is skipped rather than guessed
    at, because there is no safe value to backfill an existing row with. Those
    stay a hand-written migration — see *Schema changes* in the operations
    guide.
    """
    from sqlalchemy import inspect

    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all just made it, with every column
        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable and column.default is None and column.server_default is None:
                log.warning(
                    "column %s.%s is missing and cannot be added automatically (NOT NULL, no default)",
                    table.name,
                    column.name,
                )
                continue
            ddl = column.type.compile(dialect=conn.dialect)
            conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}')
            added.append(f"{table.name}.{column.name}")
    return added


async def init_db() -> None:
    """Create tables if they do not exist, and add any newly added columns."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        added = await conn.run_sync(_add_missing_columns)
    if added:
        log.info("database upgraded: added %s", ", ".join(added))
    log.info("database ready (%s)", _safe_url(settings.database_url))


async def healthcheck() -> bool:
    try:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover - health path
        log.exception("database healthcheck failed")
        return False


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def _safe_url(url: str) -> str:
    """Strip credentials before logging a database URL."""
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"
