"""Shared JSON shapes for the API."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from app.config import settings
from app.db.models import Signal, SignalVersion, TelegramMessage


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(settings.tz).isoformat()


def _value(value):
    return value.value if isinstance(value, Enum) else value


def signal_summary(signal: Signal) -> dict:
    return {
        "signal_id": signal.signal_id,
        "direction": _value(signal.direction),
        "symbol": signal.symbol,
        "entry": signal.entry,
        "sl": signal.sl,
        "tp1": signal.tp1,
        "tp2": signal.tp2,
        "tp3": signal.tp3,
        "status": _value(signal.status),
        "result": _value(signal.result),
        "profit_points": signal.profit_points,
        "loss_points": signal.loss_points,
        "net_points": round((signal.profit_points or 0.0) - (signal.loss_points or 0.0), 2),
        "max_tp_hit": signal.max_tp_hit,
        "is_complete": signal.is_complete,
        "confidence": round(signal.confidence, 2),
        "price_source": signal.price_source,
        "signal_time": _iso(signal.signal_time),
        "created_at": _iso(signal.created_at),
        "updated_at": _iso(signal.updated_at),
        "entry_filled_at": _iso(signal.entry_filled_at),
        "resolved_at": _iso(signal.resolved_at),
        "telegram_chat_id": signal.telegram_chat_id,
        "telegram_message_id": signal.telegram_message_id,
        "source_version": signal.source_version,
        "manual_override": signal.manual_override,
        "note": signal.evaluation_note,
    }


def signal_detail(signal: Signal, versions: list[SignalVersion], messages: list[TelegramMessage]) -> dict:
    payload = signal_summary(signal)
    payload["raw_text"] = signal.raw_text
    payload["parser_name"] = signal.parser_name
    payload["parse_history"] = [
        {
            "version": v.version,
            "telegram_version": v.telegram_version,
            "raw_text": v.raw_text,
            "parsed": v.parsed,
            "created_at": _iso(v.created_at),
        }
        for v in versions
    ]
    payload["message_history"] = [telegram_message(m) for m in messages]
    return payload


def telegram_message(message: TelegramMessage, *, include_delivery: bool = True) -> dict:
    payload = {
        "id": message.id,
        "chat_id": message.chat_id,
        "message_id": message.message_id,
        "version": message.version,
        "event_type": _value(message.event_type),
        "content": message.content,
        "content_hash": message.content_hash,
        "created_at": _iso(message.created_at),
        "edited_at": _iso(message.edited_at),
        "received_at": _iso(message.received_at),
        "has_media": message.has_media,
    }
    if include_delivery:
        payload.update(
            {
                "status": _value(message.status),
                "sent_at": _iso(message.sent_at),
                "line_message_id": message.line_message_id,
                "send_attempts": message.send_attempts,
                "last_error": message.last_error,
            }
        )
    return payload
