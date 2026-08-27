"""Test configuration.

The environment is set before anything from ``app`` is imported, because the
settings object is built once at import time.
"""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="signal-tests-")

os.environ.setdefault("ENV_FILE", os.path.join(_TMP, "nonexistent.env"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP}/test.db"
os.environ["TELEGRAM_API_ID"] = "1"
os.environ["TELEGRAM_API_HASH"] = "test-hash"
os.environ["TELEGRAM_SOURCE_CHAT_ID"] = "-1001234567890"
os.environ["TELEGRAM_SESSION"] = os.path.join(_TMP, "test.session")
os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "test-token"
os.environ["LINE_TARGET_ID"] = "Ctestgroup"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
os.environ["PRICE_DATA_PROVIDER"] = "none"
# The engine tests talk in whole dollars ("a 10 point loss" for 3340 -> 3330),
# so the unit is pinned here rather than following the deployment default.
# The tests that care about the unit itself set it explicitly.
os.environ["POINT_SIZE"] = "1.0"
os.environ["TIMEZONE"] = "Asia/Bangkok"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.db.models import Base  # noqa: E402
from app.db.session import dispose_engine, get_engine, get_sessionmaker  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def session():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with get_sessionmaker()() as db:
        yield db
    await dispose_engine()
