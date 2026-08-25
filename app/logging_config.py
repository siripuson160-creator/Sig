"""Logging setup.

Secrets never reach the log: the LINE token, the Telegram api_hash and any OTP
are only read from the environment and are never passed to a logger.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime

from app.config import settings


class _LocalTimeFormatter(logging.Formatter):
    """Timestamps in the configured timezone (Asia/Bangkok by default)."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = datetime.fromtimestamp(record.created, tz=settings.tz)
        return moment.strftime(datefmt or "%Y-%m-%d %H:%M:%S %Z")


def configure_logging(level: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_LocalTimeFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, (level or settings.log_level).upper(), logging.INFO))

    # Third-party noise.
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
