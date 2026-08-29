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

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AmbiguityRule = Literal["SL_FIRST", "TP_FIRST", "AMBIGUOUS"]
ResultMode = Literal["BEST_TP", "FIRST_TOUCH"]
#: Where a published verdict comes from.
#:   price   - measured against price history (independently verified)
#:   message - what the source announced about its own trade (self-reported)
ResultSource = Literal["price", "message"]
#: Where forwarded messages are delivered.
#:   line     - a LINE group, via the Messaging API
#:   telegram - a Telegram channel, posted by the account already signed in
DeliveryTarget = Literal["line", "telegram"]


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
    # The channel messages are forwarded *into* when DELIVERY_TARGET=telegram.
    # A @username, a t.me link, or the numeric -100… id. The signed-in account
    # must be able to post there, so make it an admin of the channel.
    telegram_target_chat_id: str = ""

    # --------------------------------------------------------------------- line
    # Where forwarded messages go. Telegram needs no second token and carries
    # the source's images; LINE is text only and needs a channel token.
    delivery_target: DeliveryTarget = "line"

    line_channel_access_token: str = ""
    line_channel_secret: str = ""
    # Group id (Cxxxx), room id (Rxxxx) or user id (Uxxxx) to push to.
    # LINE_GROUP_ID is the name used in the brief; LINE_TARGET_ID is accepted
    # too because it also covers rooms and 1:1 chats.
    line_group_id: str = ""
    line_target_id: str = ""
    line_enabled: bool = True
    line_api_base: str = "https://api.line.me"
    line_edit_prefix: str = "EDITED"
    # Section 58: ADD_EDITED_PREFIX=false forwards edits without the marker.
    add_edited_prefix: bool = True
    line_max_attempts: int = 3
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
    # The size of one "point", the unit every published statistic is in.
    # 0.01 is the MT4/MT5 point for a gold quote carrying two decimals, so a
    # $1 move is 100 points and the desk's "+70 Pips" ($7.00) reads as +700 —
    # the number members recognise from their own terminal.
    # Set 1.0 to count whole dollars instead.
    point_size: float = 0.01
    # What one "pip" is worth in price, used only to read targets quoted as a
    # distance ("TP: 50/100Pips") rather than a price. For XAUUSD most desks
    # call $0.10 a pip, so "50 pips" from 4601 is 4606. Check this against your
    # own source's wording before trusting the numbers: if they call a $1 move
    # 100 pips, this is right; if they call it 10 pips, set 1.0.
    pip_size: float = 0.1

    # --------------------------------------------------------------- evaluation
    ambiguity_rule: AmbiguityRule = "SL_FIRST"
    result_mode: ResultMode = "BEST_TP"
    # "price" judges every signal against price history. "message" instead takes
    # the source at its word when it reports its own trade ("90 Pips!", "SL
    # hit"), which needs no price feed and matches what members saw in the
    # group — but is only as accurate as the source is honest. Either way the
    # signal records which was used, and the dashboard says so.
    result_source: ResultSource = "price"
    # A signal that never fills or resolves is closed after this many hours.
    signal_expiry_hours: int = 72
    # How long we wait for price to touch entry before abandoning the signal.
    entry_fill_window_hours: int = 12
    # Every pass costs one price request per symbol that has an open signal.
    # 120s is ~720/day, inside Twelve Data's free 800/day allowance.
    result_engine_interval_seconds: int = 120

    # ---------------------------------------------------------------- test mode
    # Section 57: receive, parse and store for real, but do not push to LINE.
    dry_run: bool = False

    # ---------------------------------------------------------------------- api
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Password for the /admin login form (section 42).
    admin_password: str = ""
    # Machine-to-machine key for /api/admin/* (X-Admin-Key).
    admin_api_key: str = ""
    admin_session_hours: int = 12
    cors_origins: str = "*"
    public_dashboard_enabled: bool = True
    # The archive of what was pushed to LINE, on the member dashboard. Off by
    # default: the signal text is the thing members pay for, and the public
    # dashboard is readable by anyone who has the URL. The admin page always
    # shows it regardless.
    public_broadcast_enabled: bool = False
    # How often the dashboard refreshes itself (section 47).
    dashboard_refresh_seconds: int = 10

    # ------------------------------------------------------------------ logging
    # Empty keeps logs on stdout only (journald handles rotation there).
    log_file: str = ""
    log_max_bytes: int = 10_000_000
    log_backup_count: int = 7

    # --------------------------------------------------------------- validators
    @model_validator(mode="before")
    @classmethod
    def _blank_means_default(cls, data):
        """Treat `SOMETHING=` in a .env as "unset" for non-text settings.

        A half-filled .env is normal on a fresh install — the setup wizard
        writes every key, empty ones included. Without this, `TELEGRAM_API_ID=`
        raises a validation error at import time, which crash-loops the service
        before it can serve the setup page that would fix it.

        Text settings keep their empty value: "" is a meaningful answer there
        (no LINE token yet), and their defaults are empty anyway.
        """
        if not isinstance(data, dict):
            return data
        cleaned = {}
        for key, value in data.items():
            field = cls.model_fields.get(str(key).lower())
            if value == "" and field is not None and field.annotation is not str:
                continue  # fall back to the declared default
            cleaned[key] = value
        return cleaned

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

    @property
    def line_destination(self) -> str:
        """The chat LINE pushes into: LINE_GROUP_ID wins, LINE_TARGET_ID is legacy."""
        return (self.line_group_id or self.line_target_id).strip()

    @property
    def line_delivery_enabled(self) -> bool:
        """False in dry-run: everything is stored, nothing is pushed.

        LINE_ENABLED is the historical name for the delivery switch and still
        governs both destinations, so an install already using it to pause
        delivery keeps working after switching to Telegram.
        """
        return self.line_enabled and not self.dry_run

    @property
    def delivery_destination(self) -> str:
        """The configured destination, whichever app it is in."""
        if self.delivery_target == "telegram":
            return self.telegram_target_chat_id.strip()
        return self.line_destination

    @property
    def admin_secret(self) -> str:
        """What the /admin login checks against."""
        return self.admin_password or self.admin_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
