"""Admin authentication (section 42).

Two ways in, both checked against secrets that only exist in the environment:

* ``X-Admin-Key`` — the machine key, for scripts and health checks.
* A bearer token issued by ``POST /api/admin/login`` after the password check.
  The token is an HMAC of its own payload, so nothing has to be stored server
  side and a restart does not have to invalidate every session.

There is no user table and no password reset: this guards one operator surface,
not a public account system.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.config import settings


class AdminIdentity:
    """Who is acting, for the audit trail."""

    def __init__(self, actor: str, via: str, ip: str | None = None) -> None:
        self.actor = actor
        self.via = via
        self.ip = ip

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<AdminIdentity {self.actor} via {self.via}>"


def _signing_key() -> bytes:
    """Derived from the admin secret, so changing it logs everyone out."""
    return hashlib.sha256(f"admin-session::{settings.admin_secret}".encode()).digest()


def _sign(payload: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(_signing_key(), payload, hashlib.sha256).digest()).decode().rstrip("=")


def issue_token(actor: str = "admin") -> tuple[str, int]:
    """Return ``(token, expires_at_epoch)``."""
    expires = int(time.time()) + settings.admin_session_hours * 3600
    payload = json.dumps({"sub": actor, "exp": expires}, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{body}.{_sign(payload)}", expires


def verify_token(token: str) -> str | None:
    """Return the actor if the token is valid and unexpired, else ``None``."""
    try:
        body, signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        claims = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if int(claims.get("exp", 0)) < time.time():
        return None
    return str(claims.get("sub", "admin"))


def check_password(password: str) -> bool:
    """Constant-time comparison against ADMIN_PASSWORD (or ADMIN_API_KEY)."""
    secret = settings.admin_secret
    if not secret:
        return False
    return hmac.compare_digest(password, secret)


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def require_admin(
    request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> AdminIdentity:
    """Dependency for every admin route."""
    if not settings.admin_secret:
        raise HTTPException(
            status_code=503,
            detail="admin disabled: set ADMIN_PASSWORD (or ADMIN_API_KEY) in the environment",
        )

    ip = client_ip(request)

    if x_admin_key and settings.admin_api_key and hmac.compare_digest(x_admin_key, settings.admin_api_key):
        return AdminIdentity("api-key", "x-admin-key", ip)

    if authorization and authorization.lower().startswith("bearer "):
        actor = verify_token(authorization[7:].strip())
        if actor:
            return AdminIdentity(actor, "session", ip)

    raise HTTPException(status_code=401, detail="sign in to the admin dashboard first")


AdminDep = Annotated[AdminIdentity, Depends(require_admin)]


def new_login_delay() -> float:
    """Small random delay on a failed login, to blunt brute-force timing."""
    return 0.15 + secrets.randbelow(150) / 1000
