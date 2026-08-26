"""Is this installation configured yet, and who is allowed to configure it.

The browser setup wizard (`/setup`) exists so a fresh VPS needs one command and
then a web page, rather than an SSH session per question. That convenience
creates a window in which an unconfigured, internet-facing server would happily
accept somebody else's Telegram and LINE credentials, so the window is closed
two ways:

* the wizard only answers while the install is genuinely unconfigured, and
* every setup request must carry the one-time token from `data/setup-token`,
  a file only someone with shell access to the server can read.

Nothing here ever touches an OTP or a 2FA password. Those go from the owner's
browser straight to Telegram (see `app/api/routes_setup.py`); this module deals
only with the durable configuration.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime

from app.setup_wizard import read_env, render_env

#: Where the .env lives. Same override the rest of the app honours.
ENV_PATH = os.getenv("ENV_FILE", ".env")

#: The one-time token that guards /setup, alongside the .env.
TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(ENV_PATH)) or ".", "data", "setup-token")

#: Without these the bridge cannot do its job, so their absence means "not set up".
REQUIRED_KEYS = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SOURCE_CHAT_ID")


def env_values(path: str | None = None) -> dict[str, str]:
    return read_env(path or ENV_PATH)


def missing_keys(path: str | None = None, *, include_environ: bool = True) -> list[str]:
    """Which of the required settings are still empty.

    The process environment counts by default, because a .env file is only one
    of the ways this app is configured: a container or a systemd unit with
    ``Environment=`` lines is fully configured with no .env at all, and must
    not be dropped into the setup wizard. Pass ``include_environ=False`` to ask
    only about the file.
    """
    values = env_values(path)
    missing = []
    for key in REQUIRED_KEYS:
        if values.get(key, "").strip():
            continue
        if include_environ and os.environ.get(key, "").strip():
            continue
        missing.append(key)
    return missing


def is_configured(path: str | None = None) -> bool:
    """True once the wizard (web or CLI) has produced a usable configuration.

    Deliberately checks the settings rather than a "done" flag, so an install
    configured with `python -m app.cli setup` is recognised too, and so
    emptying a required value re-opens the wizard instead of leaving a server
    that starts up and quietly does nothing.
    """
    return not missing_keys(path)


# --------------------------------------------------------------------- token
def read_token() -> str | None:
    try:
        with open(TOKEN_PATH, encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError:
        return None
    return token or None


def ensure_token() -> str:
    """Return the setup token, creating one if this is a fresh install.

    The installer normally writes it before the service starts, so it can print
    the link. This covers starting the app by hand.
    """
    existing = read_token()
    if existing:
        return existing

    token = secrets.token_urlsafe(24)
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    # Written 0600 from the start: never briefly world-readable.
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


def token_matches(candidate: str | None) -> bool:
    expected = read_token()
    if not expected or not candidate:
        return False
    return secrets.compare_digest(candidate, expected)


def clear_token() -> None:
    """Called once setup succeeds: the link stops working."""
    try:
        os.remove(TOKEN_PATH)
    except OSError:
        pass


# ----------------------------------------------------------------- writing
def write_env(values: dict[str, str], path: str | None = None) -> str:
    """Write the .env the wizard collected, keeping a timestamped backup.

    Returns the path written. Mode 0600 because this file holds the LINE
    channel token and the admin password.
    """
    target = path or ENV_PATH
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)

    if os.path.exists(target) and os.path.getsize(target) > 0:
        backup = f"{target}.bak-{datetime.now():%Y%m%d%H%M%S}"
        with open(target, encoding="utf-8") as src, open(backup, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        os.chmod(backup, 0o600)

    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(render_env(values))
    os.chmod(target, 0o600)
    return target
