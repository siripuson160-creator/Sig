"""SQLAlchemy models.

Design notes
------------
* ``telegram_messages`` stores **one row per version** of a Telegram message.
  A message that was edited three times produces four rows (v1..v4) and no row
  is ever mutated in place except for its LINE delivery bookkeeping. That table
  is therefore both the audit log (section 12: edit history) and the LINE
  outbox (section 11: status / sent_at / line_message_id).
* ``signals`` holds at most one row per Telegram message thread, so an edited
  message updates its existing signal instead of creating an orphan
  (section 16). Every parse is snapshotted into ``signal_versions``.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, TypeDecorator


class UTCDateTime(TypeDecorator):
    """Timezone-aware timestamps that survive SQLite.

    SQLite has no timestamp type, so a value written as UTC comes back naive
    and any comparison against ``datetime.now(timezone.utc)`` explodes. Values
    are normalised to UTC on the way in and re-tagged as UTC on the way out, so
    application code only ever sees aware datetimes on either backend.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON}


# --------------------------------------------------------------------- enums
class EventType(str, enum.Enum):
    NEW = "NEW"
    EDIT = "EDIT"


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # duplicate / delivery disabled


class Direction(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalStatus(str, enum.Enum):
    PENDING = "PENDING"  # parsed, waiting for entry fill
    ACTIVE = "ACTIVE"  # entry filled, running
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    TP3_HIT = "TP3_HIT"
    SL_HIT = "SL_HIT"
    CLOSED = "CLOSED"  # expired / closed at market
    CANCELLED = "CANCELLED"  # never filled, or cancelled by admin
    AMBIGUOUS = "AMBIGUOUS"  # TP and SL in the same candle, unresolvable


class SignalResult(str, enum.Enum):
    PENDING_RESULT = "PENDING_RESULT"
    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    AMBIGUOUS = "AMBIGUOUS"
    CANCELLED = "CANCELLED"


# Enums are stored as portable VARCHAR (not a native PG enum type) so that new
# members can be added without a migration.
def _enum_col(py_enum: type[enum.Enum], name: str) -> Enum:
    return Enum(py_enum, native_enum=False, validate_strings=True, name=name, length=24)


EventTypeCol = _enum_col(EventType, "event_type")
DeliveryStatusCol = _enum_col(DeliveryStatus, "delivery_status")
DirectionCol = _enum_col(Direction, "direction")
SignalStatusCol = _enum_col(SignalStatus, "signal_status")
SignalResultCol = _enum_col(SignalResult, "signal_result")


#: Statuses that the result engine still needs to look at.
OPEN_STATUSES = (SignalStatus.PENDING, SignalStatus.ACTIVE, SignalStatus.TP1_HIT, SignalStatus.TP2_HIT)


# ------------------------------------------------------------------- tables
class TelegramMessage(Base):
    """One row per (chat, message, version)."""

    __tablename__ = "telegram_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[EventType] = mapped_column(EventTypeCol, nullable=False)

    # Telegram timestamps.
    created_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Our timestamps.
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    line_message_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[DeliveryStatus] = mapped_column(DeliveryStatusCol, default=DeliveryStatus.PENDING, nullable=False)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)

    sender_name: Mapped[str | None] = mapped_column(String(255))
    has_media: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", "version", name="uq_tg_msg_version"),
        Index("ix_tg_msg_thread", "chat_id", "message_id"),
        Index("ix_tg_msg_status", "status"),
        Index("ix_tg_msg_received", "received_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<TelegramMessage {self.chat_id}/{self.message_id} v{self.version} {self.status}>"


class Signal(Base):
    __tablename__ = "signals"

    signal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Version of the Telegram message this parse came from.
    source_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    direction: Mapped[Direction | None] = mapped_column(DirectionCol)
    symbol: Mapped[str | None] = mapped_column(String(16))
    entry: Mapped[float | None] = mapped_column(Float)
    sl: Mapped[float | None] = mapped_column(Float)
    tp1: Mapped[float | None] = mapped_column(Float)
    tp2: Mapped[float | None] = mapped_column(Float)
    tp3: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    # Telegram time of the version that produced the current parse; the clock
    # the result engine uses when replaying price history.
    signal_time: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    status: Mapped[SignalStatus] = mapped_column(SignalStatusCol, default=SignalStatus.PENDING, nullable=False)
    result: Mapped[SignalResult] = mapped_column(SignalResultCol, default=SignalResult.PENDING_RESULT, nullable=False)
    profit_points: Mapped[float | None] = mapped_column(Float)
    loss_points: Mapped[float | None] = mapped_column(Float)
    price_source: Mapped[str | None] = mapped_column(String(32))

    # Evaluation bookkeeping.
    entry_filled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    max_tp_hit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evaluation_note: Mapped[str | None] = mapped_column(Text)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Parser bookkeeping.
    parser_name: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, default="", nullable=False)

    versions: Mapped[list["SignalVersion"]] = relationship(
        back_populates="signal", cascade="all, delete-orphan", order_by="SignalVersion.version"
    )

    __table_args__ = (
        UniqueConstraint("telegram_chat_id", "telegram_message_id", name="uq_signal_source"),
        Index("ix_signal_status", "status"),
        Index("ix_signal_time", "signal_time"),
        Index("ix_signal_resolved", "resolved_at"),
    )

    @property
    def net_points(self) -> float:
        """Signed P/L in points. 0.0 while unresolved."""
        return (self.profit_points or 0.0) - (self.loss_points or 0.0)


class SignalVersion(Base):
    """Immutable snapshot of a parse. Never deleted (section 12)."""

    __tablename__ = "signal_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(36), ForeignKey("signals.signal_id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_version: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    parsed: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    signal: Mapped[Signal] = relationship(back_populates="versions")

    __table_args__ = (UniqueConstraint("signal_id", "version", name="uq_signal_version"),)


class PriceCandle(Base):
    """Cached OHLC used by the result engine, keyed by provider."""

    __tablename__ = "price_candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "symbol", "timeframe", "ts", name="uq_candle"),
        Index("ix_candle_lookup", "provider", "symbol", "timeframe", "ts"),
    )


class AppSetting(Base):
    """Small runtime key/value store (e.g. delivery paused)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
