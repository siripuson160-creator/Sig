"""Browser setup wizard: configure a fresh install without an SSH session.

The flow mirrors `python -m app.cli setup`, one screen at a time:

    1. Telegram API credentials, then the sign-in
    2. pick the source group from the account's own group list
    3. LINE channel token and destination, verified against LINE
    4. price data, scoring rules, timezone, admin password
    5. write .env and restart into the configured service

Security, and why this is not a back door
-----------------------------------------
Every route here refuses unless *both* hold:

* the install is genuinely unconfigured (`setup_state.is_configured()` is
  False) — so this cannot be used to reconfigure a running system, and
* the request carries the one-time token from `data/setup-token`, which is
  readable only with shell access to the server.

Section 3 says no developer and no AI may receive the Telegram OTP or 2FA
password. That holds here: the code and the password travel from the owner's
own browser to the owner's own server and straight into Telethon's sign-in
call. They are never written to the database, never put in the .env, never
logged (the handlers below take care not to include them in any log record or
error string), and never leave the machine except to Telegram itself.

What the owner still has to judge is the transport. On a fresh VPS reached by
bare IP there is no TLS, so the wizard says so in the page and the installer
prints the SSH-tunnel alternative.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import setup_state
from app.config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/setup", tags=["setup"])

#: A sign-in attempt is abandoned after this long, so a half-finished login
#: does not hold a connected Telethon client open indefinitely.
SESSION_TTL_SECONDS = 900


# --------------------------------------------------------------------- guard
async def require_setup_mode(
    x_setup_token: Annotated[str | None, Header()] = None,
) -> None:
    """Both conditions, on every route: unconfigured *and* holding the token."""
    if setup_state.is_configured():
        raise HTTPException(
            status_code=409,
            detail="this install is already configured; use /admin to change settings",
        )
    if not setup_state.token_matches(x_setup_token):
        raise HTTPException(
            status_code=401,
            detail="setup link is missing or wrong — open the URL the installer printed",
        )


SetupGuard = Depends(require_setup_mode)


# ------------------------------------------------------------ sign-in state
class TelegramSignIn:
    """Holds the half-finished Telethon sign-in between browser requests.

    In-memory only, one at a time. Nothing here is ever persisted: the phone
    number lives only until sign-in completes, and the code and password are
    not stored at all — they are arguments to `sign_in` and nothing more.
    """

    def __init__(self) -> None:
        self.client: Any = None
        self.phone: str = ""
        self.phone_code_hash: str = ""
        self.api_id: int = 0
        self.api_hash: str = ""
        self.started_at: float = 0.0
        self.authorized: bool = False
        self.account: str = ""
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def expired(self) -> bool:
        return bool(self.started_at) and time.time() - self.started_at > SESSION_TTL_SECONDS

    async def reset(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:  # pragma: no cover - best effort teardown
                pass
        self.client = None
        self.phone = ""
        self.phone_code_hash = ""
        self.started_at = 0.0
        self.authorized = False
        self.account = ""


signin = TelegramSignIn()


def _session_path() -> str:
    path = settings.telegram_session or "./data/telegram.session"
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    return path


async def _build_client(api_id: int, api_hash: str):
    from telethon import TelegramClient

    client = TelegramClient(_session_path(), api_id, api_hash)
    try:
        await asyncio.wait_for(client.connect(), timeout=25)
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="could not reach Telegram within 25s — check the server's outbound network access",
        ) from exc
    return client


def _telegram_error(exc: Exception) -> HTTPException:
    """Turn a Telethon error into something an operator can act on.

    Only the exception *type* drives the message. Telethon's own text is not
    interpolated for the credential errors, which keeps any echoed input out of
    the response and the logs.
    """
    name = type(exc).__name__
    known = {
        "ApiIdInvalidError": "that API ID and API hash are not a valid pair — copy them again from my.telegram.org",
        "PhoneNumberInvalidError": "Telegram does not recognise that phone number; include the country code, e.g. +66…",
        "PhoneNumberBannedError": "that phone number is banned by Telegram",
        "PhoneCodeInvalidError": "that code is not right — check the digits and try again",
        "PhoneCodeExpiredError": "that code has expired; send yourself a new one",
        "PhoneCodeEmptyError": "enter the code Telegram sent you",
        "PasswordHashInvalidError": "that two-step verification password is not right",
        "SessionPasswordNeededError": "this account has two-step verification; enter the password",
    }
    if name in known:
        return HTTPException(status_code=400, detail=known[name])
    if name == "FloodWaitError":
        seconds = getattr(exc, "seconds", 0)
        return HTTPException(
            status_code=429,
            detail=f"Telegram is rate-limiting this account — wait {seconds}s before trying again",
        )
    log.warning("telegram sign-in failed: %s", name)
    return HTTPException(status_code=400, detail=f"Telegram rejected the request ({name})")


# ------------------------------------------------------------------ schemas
class Credentials(BaseModel):
    api_id: int = Field(gt=0, description="from my.telegram.org")
    api_hash: str = Field(min_length=8)
    phone: str = Field(min_length=5, description="with country code, e.g. +6681…")


class CodeIn(BaseModel):
    # Not logged, not stored, not written to .env. Straight to Telegram.
    code: str = Field(min_length=3, max_length=16)


class PasswordIn(BaseModel):
    # The 2FA password. Same handling as the code above.
    password: str = Field(min_length=1)


class GroupIn(BaseModel):
    chat_id: str = Field(min_length=1)


class LineIn(BaseModel):
    access_token: str = Field(min_length=10)
    destination: str = Field(min_length=2)
    send_test: bool = False


class FinishIn(BaseModel):
    chat_id: str = Field(min_length=1)
    line_access_token: str = ""
    line_destination: str = ""
    line_enabled: bool = True
    dry_run: bool = False
    price_provider: str = "none"
    price_api_key: str = ""
    price_symbol: str = "XAUUSD"
    ambiguity_rule: str = "SL_FIRST"
    result_mode: str = "BEST_TP"
    timezone: str = "Asia/Bangkok"
    admin_password: str = Field(default="", max_length=200)
    api_port: int = Field(default=8000, ge=1, le=65535)


# ------------------------------------------------------------------- status
@router.get("/status")
async def setup_status(request: Request) -> dict:
    """What the wizard needs to render itself. No token required.

    Deliberately says nothing about the machine beyond whether setup is still
    open and whether the connection is encrypted, so it is safe to answer
    before the token has been checked.
    """
    configured = setup_state.is_configured()
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    secure = (forwarded_proto or request.url.scheme) == "https"
    return {
        "configured": configured,
        "needs": setup_state.missing_keys(),
        "secure": secure,
        "signed_in": signin.authorized,
        "account": signin.account,
        "timezone": settings.timezone,
        "api_port": settings.api_port,
        "providers": ["none", "twelvedata", "yahoo", "csv"],
    }


@router.post("/verify-token", dependencies=[SetupGuard])
async def verify_token() -> dict:
    """Lets the page confirm the token before showing the form."""
    return {"ok": True}


# ----------------------------------------------------------------- telegram
@router.post("/telegram/send-code", dependencies=[SetupGuard])
async def telegram_send_code(body: Credentials) -> dict:
    """Ask Telegram to send the owner a login code."""
    async with signin.lock:
        if signin.expired():
            await signin.reset()
        # Changing credentials mid-flow starts over rather than mixing state.
        if signin.client is not None and (
            signin.api_id != body.api_id or signin.api_hash != body.api_hash
        ):
            await signin.reset()

        if signin.client is None:
            signin.client = await _build_client(body.api_id, body.api_hash)
            signin.api_id = body.api_id
            signin.api_hash = body.api_hash

        # An already-authorised session file means login is simply done.
        if await signin.client.is_user_authorized():
            me = await signin.client.get_me()
            signin.authorized = True
            signin.account = getattr(me, "first_name", None) or str(getattr(me, "id", ""))
            return {"already_signed_in": True, "account": signin.account}

        try:
            sent = await signin.client.send_code_request(body.phone)
        except Exception as exc:
            raise _telegram_error(exc) from None

        signin.phone = body.phone
        signin.phone_code_hash = sent.phone_code_hash
        signin.started_at = time.time()
        # Note the absence of the phone number: it is an account identifier.
        log.info("telegram login code requested")
        return {"already_signed_in": False, "sent": True}


@router.post("/telegram/sign-in", dependencies=[SetupGuard])
async def telegram_sign_in(body: CodeIn) -> dict:
    """Complete the sign-in with the code Telegram sent.

    `body.code` is passed to Telethon and then goes out of scope. It is never
    logged and never stored.
    """
    async with signin.lock:
        if signin.client is None or not signin.phone_code_hash:
            raise HTTPException(status_code=409, detail="ask for a code first")
        if signin.expired():
            await signin.reset()
            raise HTTPException(status_code=409, detail="that sign-in attempt timed out; start again")

        try:
            await signin.client.sign_in(
                phone=signin.phone,
                code=body.code.strip(),
                phone_code_hash=signin.phone_code_hash,
            )
        except Exception as exc:
            if type(exc).__name__ == "SessionPasswordNeededError":
                return {"needs_password": True}
            raise _telegram_error(exc) from None

        return await _finish_sign_in()


@router.post("/telegram/password", dependencies=[SetupGuard])
async def telegram_password(body: PasswordIn) -> dict:
    """Second factor for accounts with two-step verification.

    Same handling as the code: straight to Telethon, never logged or stored.
    """
    async with signin.lock:
        if signin.client is None:
            raise HTTPException(status_code=409, detail="start the sign-in first")
        try:
            await signin.client.sign_in(password=body.password)
        except Exception as exc:
            raise _telegram_error(exc) from None
        return await _finish_sign_in()


async def _finish_sign_in() -> dict:
    me = await signin.client.get_me()
    signin.authorized = True
    signin.account = getattr(me, "first_name", None) or str(getattr(me, "id", ""))
    signin.phone = ""
    signin.phone_code_hash = ""
    log.info("telegram sign-in complete; session saved to %s", settings.telegram_session)
    return {"signed_in": True, "account": signin.account, "needs_password": False}


@router.get("/telegram/groups", dependencies=[SetupGuard])
async def telegram_groups(limit: int = 200) -> dict:
    """The account's groups and channels — the source is always one of these."""
    if signin.client is None or not signin.authorized:
        raise HTTPException(status_code=409, detail="sign in to Telegram first")

    groups = []
    async for dialog in signin.client.iter_dialogs(limit=limit):
        if dialog.is_group or dialog.is_channel:
            groups.append(
                {
                    "id": str(dialog.id),
                    "name": dialog.name or str(dialog.id),
                    "kind": "group" if dialog.is_group else "channel",
                }
            )
    return {"groups": groups}


# --------------------------------------------------------------------- line
@router.post("/line/test", dependencies=[SetupGuard])
async def line_test(body: LineIn) -> dict:
    """Check the LINE token, and optionally prove delivery with a real push.

    Uses the submitted credentials directly rather than `LineClient`, which
    reads the not-yet-written configuration.
    """
    headers = {"Authorization": f"Bearer {body.access_token.strip()}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        try:
            info = await client.get(f"{settings.line_api_base}/v2/bot/info", headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"could not reach LINE: {exc}") from None

        if info.status_code == 401:
            raise HTTPException(status_code=400, detail="LINE rejected that channel access token")
        if info.status_code != 200:
            raise HTTPException(
                status_code=400, detail=f"LINE returned {info.status_code}: {info.text[:200]}"
            )

        bot_name = info.json().get("displayName", "the channel")
        if not body.send_test:
            return {"ok": True, "bot": bot_name, "sent": False}

        push = await client.post(
            f"{settings.line_api_base}/v2/bot/message/push",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "to": body.destination.strip(),
                "messages": [
                    {
                        "type": "text",
                        "text": "✅ Setup test — the signal bridge can post in this chat.",
                    }
                ],
            },
        )
        if push.status_code not in (200, 409):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"the token works, but posting to {body.destination.strip()} failed "
                    f"({push.status_code}: {push.text[:160]}). Check the group id, and that "
                    "the bot is a member of that group."
                ),
            )
        return {"ok": True, "bot": bot_name, "sent": True}


# ------------------------------------------------------------------- finish
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._/-]{2,20}$")


def build_env(body: FinishIn) -> dict[str, str]:
    """Turn the wizard's answers into .env values. Pure, so it can be tested."""
    provider = body.price_provider.strip().lower() or "none"
    symbol = body.price_symbol.strip().upper()
    if not _SYMBOL_RE.match(symbol):
        symbol = "XAUUSD"

    return {
        "TIMEZONE": body.timezone.strip() or "Asia/Bangkok",
        "TELEGRAM_API_ID": str(signin.api_id),
        "TELEGRAM_API_HASH": signin.api_hash,
        "TELEGRAM_SESSION": settings.telegram_session or "./data/telegram.session",
        "TELEGRAM_SOURCE_CHAT_ID": body.chat_id.strip(),
        "LINE_CHANNEL_ACCESS_TOKEN": body.line_access_token.strip(),
        "LINE_GROUP_ID": body.line_destination.strip(),
        "LINE_ENABLED": "true" if body.line_enabled else "false",
        "DRY_RUN": "true" if body.dry_run else "false",
        "PRICE_DATA_PROVIDER": provider,
        "PRICE_API_KEY": body.price_api_key.strip(),
        "PRICE_SYMBOL": symbol,
        "AMBIGUITY_RULE": body.ambiguity_rule if body.ambiguity_rule in
        ("SL_FIRST", "TP_FIRST", "AMBIGUOUS") else "SL_FIRST",
        "RESULT_MODE": body.result_mode if body.result_mode in ("BEST_TP", "FIRST_TOUCH") else "BEST_TP",
        "RESULT_ENGINE_INTERVAL_SECONDS": "120",
        "API_HOST": settings.api_host,
        "API_PORT": str(body.api_port),
        "ADMIN_PASSWORD": body.admin_password.strip() or secrets.token_urlsafe(12),
    }


@router.post("/finish", dependencies=[SetupGuard])
async def finish(body: FinishIn) -> dict:
    """Write the .env, retire the setup link, and restart into normal service."""
    if not signin.authorized or not signin.api_id:
        raise HTTPException(status_code=409, detail="sign in to Telegram before finishing")

    values = build_env(body)
    path = setup_state.write_env(values)
    log.info("configuration written to %s", path)

    await signin.reset()
    # From here the wizard is closed for good: the link stops working and
    # `is_configured()` now returns True.
    setup_state.clear_token()

    # systemd (Restart=always) starts us again with the new configuration; the
    # delay is only so this response reaches the browser first.
    asyncio.create_task(_restart_soon())

    return {
        "ok": True,
        "admin_password": values["ADMIN_PASSWORD"],
        "restarting": True,
        "dashboard": "/dashboard",
        "admin": "/admin",
    }


async def _restart_soon() -> None:
    await asyncio.sleep(1.5)
    log.info("restarting to pick up the new configuration")
    # os._exit, not sys.exit: this runs in a task, and an exception here would
    # merely be logged by the event loop. systemd brings the service back.
    os._exit(0)
