"""Logging setup.

Secrets never reach the log: the LINE token, the Telegram api_hash and any OTP
are only read from the environment and are never passed to a logger.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from datetime import datetime

from app.config import settings


class _LocalTimeFormatter(logging.Formatter):
    """Timestamps in the configured timezone (Asia/Bangkok by default)."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = datetime.fromtimestamp(record.created, tz=settings.tz)
        return moment.strftime(datefmt or "%Y-%m-%d %H:%M:%S %Z")


def configure_logging(level: str | None = None) -> None:
    formatter = _LocalTimeFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    # Section 54: rotate on disk when a log file is configured. Under systemd
    # the journal already rotates, so LOG_FILE is normally left empty.
    if settings.log_file:
        directory = os.path.dirname(os.path.abspath(settings.log_file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                settings.log_file,
                maxBytes=settings.log_max_bytes,
                backupCount=settings.log_backup_count,
                encoding="utf-8",
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(getattr(logging, (level or settings.log_level).upper(), logging.INFO))

    # Third-party noise.
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
