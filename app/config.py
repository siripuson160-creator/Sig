"""Application configuration.

All settings are read from environment variables (or a local `.env` file).
Secrets (Telegram OTP / 2FA password, LINE token) are never stored in the
repository and never logged.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AmbiguityRule = Literal["SL_FIRST", "TP_FIRST", "AMBIGUOUS"]
ResultMode = Literal["BEST_TP", "FIRST_TOUCH"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ general
    app_name: str = "Telegram Signal Bridge"
    timezone: str = "Asia/Bangkok"
    log_level: str = "INFO"

    # ----------------------------------------------------------------- database
    # MVP default is SQLite; production should point at PostgreSQL, e.g.
    # postgresql+asyncpg://user:pass@localhost:5432/signals
    database_url: str = "sqlite+aiosqlite:///./data/signals.db"

    # ----------------------------------------------------------------- telegram
    # A Telegram *user* account (MTProto) is required because the account is a
    # normal member of the source group, not an admin/bot.
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_session: str = "./data/telegram.session"
    # The single source group. Accepts -100... channel ids or @username.
    telegram_source_chat_id: str = ""
    # Optional extra sources, comma separated. Usually left empty.
    telegram_extra_chat_ids: str = ""

    # --------------------------------------------------------------------- line
    line_channel_access_token: str = ""
    # Group id (Cxxxx), room id (Rxxxx) or user id (Uxxxx) to push to.
    line_target_id: str = ""
    line_enabled: bool = True
    line_api_base: str = "https://api.line.me"
    line_edit_prefix: str = "EDITED"
    line_max_attempts: int = 5
    line_worker_interval_seconds: float = 1.0
    # LINE hard-limits a text message to 5000 characters.
    line_max_chars: int = 4900

    # ------------------------------------------------------------------- prices
    # "none" keeps every signal at result=PENDING_RESULT (documented MVP mode).
    price_data_provider: str = "none"
    price_symbol: str = "XAUUSD"
    price_timeframe: str = "1m"
    # Optional finer timeframe used to break TP/SL same-candle ties.
    price_drilldown_timeframe: str = "1m"
    price_api_key: str = ""
    price_csv_path: str = "./data/prices"
    # 1 "point" of price movement. Gold quoted as 3340.00 -> 1 point = 1.0.
    point_size: float = 1.0

    # --------------------------------------------------------------- evaluation
    ambiguity_rule: AmbiguityRule = "SL_FIRST"
    result_mode: ResultMode = "BEST_TP"
    # A signal that never fills or resolves is closed after this many hours.
    signal_expiry_hours: int = 72
    # How long we wait for price to touch entry before abandoning the signal.
    entry_fill_window_hours: int = 12
    result_engine_interval_seconds: int = 60

    # ---------------------------------------------------------------------- api
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Required for /admin and /api/admin/*. Empty disables the admin surface.
    admin_api_key: str = ""
    cors_origins: str = "*"
    public_dashboard_enabled: bool = True

    # --------------------------------------------------------------- validators
    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def source_chat_ids(self) -> list[str]:
        """Every chat the listener should read, primary first."""
        ids = [self.telegram_source_chat_id.strip()]
        ids += [c.strip() for c in self.telegram_extra_chat_ids.split(",")]
        return [c for c in ids if c]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
