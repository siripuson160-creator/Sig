"""The archive of what was pushed to the LINE group.

Shared by the admin page (always available) and the member page (only when
PUBLIC_BROADCAST_ENABLED is on), so both render exactly the same record.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serializers import broadcast_entry
from app.db.models import TelegramMessage
from app.processor.message_processor import render_line_text


async def _broadcast_page(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    search: str | None = None,
) -> dict:
    query = select(TelegramMessage)
    count_query = select(func.count()).select_from(TelegramMessage)

    if status:
        query = query.where(TelegramMessage.status == status.upper())
        count_query = count_query.where(TelegramMessage.status == status.upper())
    if search:
        # Match the message body or the Telegram message id, so an operator can
        # paste either and find the entry.
        like = f"%{search.strip()}%"
        clause = TelegramMessage.content.ilike(like)
        if search.strip().lstrip("-").isdigit():
            clause = or_(clause, TelegramMessage.message_id == int(search.strip()))
        query = query.where(clause)
        count_query = count_query.where(clause)

    total = (await session.execute(count_query)).scalar_one()
    rows = await session.execute(query.order_by(TelegramMessage.id.desc()).limit(limit).offset(offset))
    items = list(rows.scalars().all())
    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "items": [broadcast_entry(m, line_text=render_line_text(m)) for m in items],
    }
