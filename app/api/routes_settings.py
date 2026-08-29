"""Editing the connection settings from the admin page.

The operator needs to paste a LINE token or fix a Telegram credential without
an SSH session. That is a write path into the configuration, so it is fenced:

* **A fixed allow-list.** Only the keys in ``EDITABLE`` can be written. Nothing
  else in the .env is reachable, and in particular there is no way to type in a
  statistic (section 43) — the result and statistics engines remain the only
  source of those.
* **Secrets are never handed back.** A GET returns whether a secret is set and
  a masked hint, never the value. Leaving a secret field blank keeps what is
  already stored, so the page can be saved without re-typing tokens.
* **Every change is audited** (section 44), recording which keys moved, by
  whom, from where. Secret *values* are excluded from the audit entry — the
  record says "LINE_CHANNEL_ACCESS_TOKEN changed", never to what.

Settings live in the .env, which stays the reproducible description of a
running system; saving rewrites that file and restarts the service so the
configuration in force is always the configuration on disk.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit, setup_state
from app.api.security import AdminDep
from app.config import settings
from app.db.models import AuditEvent
from app.db.session import get_session
from app.setup_wizard import read_env, update_env_value

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class Editable:
    """One setting the admin page may change."""

    def __init__(self, key: str, kind: str = "text", *, secret: bool = False, choices: list[str] | None = None):
        self.key = key
        self.kind = kind          # text | number | bool | choice
        self.secret = secret
        self.choices = choices


#: The allow-list. Adding a key here is the only way to make it editable.
EDITABLE: list[Editable] = [
    # Where messages go
    Editable("DELIVERY_TARGET", "choice", choices=["line", "telegram"]),
    Editable("TELEGRAM_TARGET_CHAT_ID"),
    # Telegram
    Editable("TELEGRAM_API_ID", "number"),
    Editable("TELEGRAM_API_HASH", secret=True),
    Editable("TELEGRAM_SOURCE_CHAT_ID"),
    # LINE
    Editable("LINE_CHANNEL_ACCESS_TOKEN", secret=True),
    Editable("LINE_GROUP_ID"),
    Editable("LINE_ENABLED", "bool"),
    Editable("ADD_EDITED_PREFIX", "bool"),
    Editable("LINE_EDIT_PREFIX"),
    Editable("DRY_RUN", "bool"),
    # Scoring
    Editable("RESULT_SOURCE", "choice", choices=["price", "message"]),
    Editable("PRICE_DATA_PROVIDER", "choice", choices=["none", "twelvedata", "yahoo", "csv"]),
    Editable("PRICE_API_KEY", secret=True),
    Editable("PRICE_SYMBOL"),
    Editable("POINT_SIZE", "number"),
    Editable("PIP_SIZE", "number"),
    Editable("AMBIGUITY_RULE", "choice", choices=["SL_FIRST", "TP_FIRST", "AMBIGUOUS"]),
    # Dashboard
    Editable("TIMEZONE"),
    Editable("PUBLIC_BROADCAST_ENABLED", "bool"),
]

_BY_KEY = {item.key: item for item in EDITABLE}


def _mask(value: str) -> str:
    """A hint that identifies a secret without disclosing it."""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def current_values() -> list[dict[str, Any]]:
    """What the page renders. Secrets come back masked, never in full."""
    stored = read_env(setup_state.ENV_PATH)
    rows = []
    for item in EDITABLE:
        raw = stored.get(item.key, os.environ.get(item.key, ""))
        rows.append(
            {
                "key": item.key,
                "kind": item.kind,
                "choices": item.choices,
                "secret": item.secret,
                "is_set": bool(raw.strip()),
                "value": _mask(raw) if item.secret else raw,
            }
        )
    return rows


class SettingsPatch(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    #: Restart afterwards so the new configuration is the one in force.
    restart: bool = True


def _clean(key: str, raw: str) -> str | None:
    """Normalise one submitted value, or return None to leave it alone."""
    item = _BY_KEY.get(key)
    if item is None:
        return None
    value = (raw or "").strip()

    # A blank secret means "keep what is stored", so the page can be saved
    # without re-typing a token that is already correct.
    if item.secret and not value:
        return None

    if item.kind == "bool":
        return "true" if value.lower() in {"1", "true", "yes", "on"} else "false"
    if item.kind == "number":
        try:
            float(value)
        except ValueError:
            return None
        return value
    if item.kind == "choice" and item.choices and value not in item.choices:
        return None
    return value


@router.get("/settings/editable")
async def read_editable(identity: AdminDep) -> dict:
    return {
        "items": current_values(),
        "env_path": os.path.abspath(setup_state.ENV_PATH),
        "note": "Saving rewrites the settings file and restarts the service, so what is "
        "running is always what is on disk. Leave a secret blank to keep the stored value.",
    }


@router.post("/settings/editable")
async def write_editable(
    identity: AdminDep,
    patch: SettingsPatch,
    session: AsyncSession = Depends(get_session),
) -> dict:
    before = read_env(setup_state.ENV_PATH)
    changed: list[str] = []
    ignored: list[str] = []

    for key, raw in patch.values.items():
        if key not in _BY_KEY:
            ignored.append(key)     # not on the allow-list; silently refused
            continue
        value = _clean(key, raw)
        if value is None:
            continue
        if before.get(key, "") == value:
            continue
        update_env_value(setup_state.ENV_PATH, key, value)
        changed.append(key)

    if not changed:
        return {"changed": [], "ignored": ignored, "restarting": False}

    # The audit entry names the keys, never a secret's value.
    await audit.record(
        session,
        AuditEvent.ADMIN_ACTION,
        entity_type="settings",
        entity_id="env",
        summary=f"Settings changed: {', '.join(sorted(changed))}",
        actor=identity.actor,
        source_ip=identity.ip,
        reason="edited from the admin settings page",
    )
    await session.commit()
    log.info("settings changed by %s: %s", identity.actor, ", ".join(sorted(changed)))

    if patch.restart:
        asyncio.create_task(_restart_soon())

    return {"changed": sorted(changed), "ignored": ignored, "restarting": patch.restart}


async def _restart_soon() -> None:
    await asyncio.sleep(1.5)
    log.info("restarting to pick up the new settings")
    # os._exit, not sys.exit: this runs in a task, where an exception would
    # only be logged. systemd (Restart=always) brings the service back.
    os._exit(0)
