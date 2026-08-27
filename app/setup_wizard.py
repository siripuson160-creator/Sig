"""Interactive first-run setup.

``python -m app.cli setup`` asks a short list of questions and writes a
complete ``.env``. It never asks for the Telegram code or 2FA password —
those belong to the sign-in step, where you type them straight to Telegram.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones

ENV_PATH = os.getenv("ENV_FILE", ".env")

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def _colour(text: str, code: str) -> str:
    return text if os.getenv("NO_COLOR") else f"{code}{text}{RESET}"


def heading(text: str) -> None:
    print(f"\n{_colour(text, BOLD)}")
    print(_colour("─" * max(len(text), 40), DIM))


def note(text: str) -> None:
    print(_colour(f"  {text}", DIM))


def ok(text: str) -> None:
    print(_colour(f"  ✓ {text}", GREEN))


def warn(text: str) -> None:
    print(_colour(f"  ! {text}", YELLOW))


def ask(
    question: str,
    *,
    default: str = "",
    required: bool = False,
    validate=None,
    hint: str = "",
) -> str:
    """Ask one question until the answer is acceptable."""
    if hint:
        note(hint)
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            answer = input(f"  {question}{suffix}: ").strip()
        except EOFError:
            answer = ""
            print()
        if not answer:
            answer = default
        if not answer and required:
            warn("This one is required.")
            continue
        if answer and validate is not None:
            problem = validate(answer)
            if problem:
                warn(problem)
                continue
        return answer


def ask_yes_no(question: str, *, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            answer = input(f"  {question} {suffix}: ").strip().lower()
        except EOFError:
            print()
            return default
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        warn("Please answer y or n.")


def ask_choice(question: str, options: list[tuple[str, str]], *, default: str) -> str:
    """Numbered menu. ``options`` is [(value, description)]."""
    print(f"  {question}")
    for index, (value, description) in enumerate(options, start=1):
        marker = " (default)" if value == default else ""
        print(f"    {index}) {value}{marker} — {description}")
    while True:
        try:
            answer = input(f"  Choose 1-{len(options)} [{default}]: ").strip()
        except EOFError:
            print()
            return default
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        if answer in {value for value, _ in options}:
            return answer
        warn(f"Enter a number between 1 and {len(options)}.")


# --------------------------------------------------------------- validators
def _digits(value: str) -> str | None:
    return None if value.isdigit() else "Must be a number."


def _api_hash(value: str) -> str | None:
    return None if re.fullmatch(r"[0-9a-f]{32}", value.lower()) else "Should be 32 hex characters."


def _timezone(value: str) -> str | None:
    try:
        ZoneInfo(value)
    except Exception:
        return "Unknown timezone. Example: Asia/Bangkok"
    return None


def _line_target(value: str) -> str | None:
    if value[0] in {"C", "R", "U"} and len(value) > 10:
        return None
    return "A LINE group id starts with C (group), R (room) or U (user)."


# ------------------------------------------------------------- env handling
def read_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def update_env_value(path: str, key: str, value: str) -> None:
    """Set one key in an existing .env, preserving comments and order."""
    lines: list[str] = []
    replaced = False
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()

    for index, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
    os.chmod(path, 0o600)


def render_env(values: dict[str, str]) -> str:
    """Produce a commented .env from the collected answers."""
    get = values.get
    return f"""# Written by 'python -m app.cli setup' on {datetime.now():%Y-%m-%d %H:%M}.
# Re-run that command any time to rebuild this file, or edit it by hand.
# Keep this file secret: chmod 600 .env

# ------------------------------------------------------------ general
TIMEZONE={get('TIMEZONE', 'Asia/Bangkok')}
LOG_LEVEL={get('LOG_LEVEL', 'INFO')}

# ----------------------------------------------------------- database
DATABASE_URL={get('DATABASE_URL', 'sqlite+aiosqlite:///./data/signals.db')}

# ----------------------------------------------------------- telegram
# From https://my.telegram.org -> API development tools.
TELEGRAM_API_ID={get('TELEGRAM_API_ID', '')}
TELEGRAM_API_HASH={get('TELEGRAM_API_HASH', '')}
# This file is a login to your account. Never share or commit it.
TELEGRAM_SESSION={get('TELEGRAM_SESSION', './data/telegram.session')}
# Filled in by 'python -m app.cli chats --pick'.
TELEGRAM_SOURCE_CHAT_ID={get('TELEGRAM_SOURCE_CHAT_ID', '')}
TELEGRAM_EXTRA_CHAT_IDS={get('TELEGRAM_EXTRA_CHAT_IDS', '')}

# --------------------------------------------------------------- line
LINE_CHANNEL_ACCESS_TOKEN={get('LINE_CHANNEL_ACCESS_TOKEN', '')}
LINE_GROUP_ID={get('LINE_GROUP_ID', get('LINE_TARGET_ID', ''))}
LINE_ENABLED={get('LINE_ENABLED', 'true')}
ADD_EDITED_PREFIX={get('ADD_EDITED_PREFIX', 'true')}
LINE_EDIT_PREFIX={get('LINE_EDIT_PREFIX', 'EDITED')}
LINE_MAX_ATTEMPTS={get('LINE_MAX_ATTEMPTS', '3')}

# ------------------------------------------------------------- prices
PRICE_DATA_PROVIDER={get('PRICE_DATA_PROVIDER', 'none')}
PRICE_SYMBOL={get('PRICE_SYMBOL', 'XAUUSD')}
PRICE_TIMEFRAME={get('PRICE_TIMEFRAME', '1m')}
PRICE_DRILLDOWN_TIMEFRAME={get('PRICE_DRILLDOWN_TIMEFRAME', '1m')}
PRICE_API_KEY={get('PRICE_API_KEY', '')}
PRICE_CSV_PATH={get('PRICE_CSV_PATH', './data/prices')}
POINT_SIZE={get('POINT_SIZE', '1.0')}
# Only used to read targets quoted as a distance ("TP: 50/100Pips").
# 0.1 means the source calls a $1 move on gold 10 pips. If yours calls it
# 100 pips, set 0.01; if it calls it 1 pip, set 1.0.
PIP_SIZE={get('PIP_SIZE', '0.1')}

# --------------------------------------------------------- evaluation
# Where a published result comes from:
#   price   = replay each signal against real price history (verified)
#   message = take the provider at its word ("90 Pips! Can secure as TP2"),
#             which needs no price feed but is only as honest as the source.
# The dashboard states which is in use, either way.
RESULT_SOURCE={get('RESULT_SOURCE', 'price')}
# SL_FIRST (conservative) | TP_FIRST | AMBIGUOUS
AMBIGUITY_RULE={get('AMBIGUITY_RULE', 'SL_FIRST')}
# BEST_TP | FIRST_TOUCH
RESULT_MODE={get('RESULT_MODE', 'BEST_TP')}
ENTRY_FILL_WINDOW_HOURS={get('ENTRY_FILL_WINDOW_HOURS', '12')}
SIGNAL_EXPIRY_HOURS={get('SIGNAL_EXPIRY_HOURS', '72')}
RESULT_ENGINE_INTERVAL_SECONDS={get('RESULT_ENGINE_INTERVAL_SECONDS', '60')}

# ---------------------------------------------------------- test mode
# true = store everything as normal but never push to LINE.
DRY_RUN={get('DRY_RUN', 'false')}

# ---------------------------------------------------------------- api
API_HOST={get('API_HOST', '0.0.0.0')}
API_PORT={get('API_PORT', '8000')}
# Password for the /admin sign-in. Empty disables the admin pages entirely.
ADMIN_PASSWORD={get('ADMIN_PASSWORD', '')}
ADMIN_SESSION_HOURS={get('ADMIN_SESSION_HOURS', '12')}
CORS_ORIGINS={get('CORS_ORIGINS', '*')}
DASHBOARD_REFRESH_SECONDS={get('DASHBOARD_REFRESH_SECONDS', '10')}

# ------------------------------------------------------------ logging
LOG_FILE={get('LOG_FILE', '')}
"""


# ------------------------------------------------------------------ wizard
def run_setup(env_path: str = ENV_PATH, *, run_followups: bool = True) -> int:
    print(_colour("\nTelegram → LINE bridge · setup", BOLD))
    note("Answer the questions below. Press Enter to accept the value in brackets.")
    note("Nothing here is your Telegram code or password — those come later, and")
    note("you type them straight to Telegram.")

    existing = read_env(env_path)
    if existing:
        heading("Existing configuration found")
        note(f"{env_path} already exists. Its current values become the defaults.")
        if not ask_yes_no("Continue and rewrite it?", default=True):
            print("\nNothing changed.")
            return 0

    values: dict[str, str] = dict(existing)

    # ---------------------------------------------------------- telegram
    heading("1. Telegram")
    note("Open https://my.telegram.org → API development tools → create an app.")
    note("These identify the app, not your account.")
    values["TELEGRAM_API_ID"] = ask(
        "API ID", default=existing.get("TELEGRAM_API_ID", ""), required=True, validate=_digits
    )
    values["TELEGRAM_API_HASH"] = ask(
        "API hash", default=existing.get("TELEGRAM_API_HASH", ""), required=True, validate=_api_hash
    )

    # -------------------------------------------------------------- line
    heading("2. LINE")
    note("LINE Developers console → your Messaging API channel → issue a")
    note("long-lived channel access token, and invite the bot to your group.")
    if ask_yes_no("Set up LINE delivery now?", default=True):
        values["LINE_ENABLED"] = "true"
        values["LINE_CHANNEL_ACCESS_TOKEN"] = ask(
            "Channel access token", default=existing.get("LINE_CHANNEL_ACCESS_TOKEN", ""), required=True
        )
        values["LINE_GROUP_ID"] = ask(
            "Group id (starts with C)",
            default=existing.get("LINE_GROUP_ID", existing.get("LINE_TARGET_ID", "")),
            required=True,
            validate=_line_target,
            hint="Read it from a webhook event — see docs/OPERATIONS.md.",
        )
        values["DRY_RUN"] = (
            "true"
            if ask_yes_no("Start in test mode (store everything, send nothing to LINE)?", default=False)
            else "false"
        )
        if values["DRY_RUN"] == "true":
            note("Test mode is on. Set DRY_RUN=false in .env when you are ready to go live.")
    else:
        values["LINE_ENABLED"] = "false"
        warn("LINE delivery is off. Messages are stored but not forwarded.")
        note("Set LINE_ENABLED=true in .env once you have the credentials.")

    # ---------------------------------------------------------- database
    heading("3. Database")
    choice = ask_choice(
        "Where should the data live?",
        [
            ("sqlite", "one file on this server — fine for a single VPS"),
            ("postgres", "PostgreSQL — recommended once it is running for real"),
        ],
        default="sqlite" if not existing.get("DATABASE_URL", "").startswith("postgresql") else "postgres",
    )
    if choice == "postgres":
        host = ask("PostgreSQL host", default="localhost")
        port = ask("Port", default="5432", validate=_digits)
        name = ask("Database name", default="signals")
        user = ask("User", default="signal")
        password = ask("Password", required=True)
        values["DATABASE_URL"] = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"
    else:
        values["DATABASE_URL"] = "sqlite+aiosqlite:///./data/signals.db"

    # ------------------------------------------------------------ prices
    heading("4. Price data")
    note("This is what decides whether a signal hit TP or SL. Without it the")
    note("dashboard still shows every signal, but every result stays PENDING.")

    symbol = ask("Symbol your group trades", default=existing.get("PRICE_SYMBOL", "XAUUSD")).upper()
    values["PRICE_SYMBOL"] = symbol
    is_metal = symbol in {"XAUUSD", "XAGUSD"}

    if is_metal:
        note("")
        note(f"For spot {symbol} the free option is Twelve Data:")
        note("  1. open https://twelvedata.com/pricing and take the free plan")
        note("  2. copy the API key from the dashboard (takes about a minute)")
        note("Yahoo is not offered for gold or silver: it only carries the futures")
        note("contract, which trades away from spot, so results would be wrong.")

    options = [("twelvedata", "Twelve Data — free API key, real spot prices")]
    if not is_metal:
        options.append(("yahoo", "Yahoo Finance — no key at all (FX, crypto, indices)"))
    options += [
        ("csv", "OHLC files you export from a broker or MT5"),
        ("none", "none yet — signals are stored and judged once you add one"),
    ]

    provider = ask_choice("Price source", options, default=existing.get("PRICE_DATA_PROVIDER", "twelvedata"))
    values["PRICE_DATA_PROVIDER"] = provider

    if provider == "twelvedata":
        values["PRICE_API_KEY"] = ask(
            "Twelve Data API key",
            default=existing.get("PRICE_API_KEY", ""),
            required=True,
            hint="Paste it here. Free plan allows 800 requests a day, which is plenty.",
        )
    elif provider == "csv":
        values["PRICE_CSV_PATH"] = ask("Folder holding the CSV files", default="./data/prices")
    elif provider == "none":
        warn("Results will stay PENDING until you add a price source.")
        note("Add one later by re-running this setup — past signals are judged retroactively.")

    # -------------------------------------------------------- evaluation
    heading("5. Scoring rules")
    values["AMBIGUITY_RULE"] = ask_choice(
        "If one candle contains both the take profit and the stop loss:",
        [
            ("SL_FIRST", "count it as a loss — conservative, hardest on the numbers"),
            ("TP_FIRST", "count it as a win"),
            ("AMBIGUOUS", "mark it unclear and leave it out of the win rate"),
        ],
        default=existing.get("AMBIGUITY_RULE", "SL_FIRST"),
    )
    values["RESULT_MODE"] = ask_choice(
        "If the stop loss is touched after a take profit was already reached:",
        [
            ("BEST_TP", "book it at the best take profit reached"),
            ("FIRST_TOUCH", "the first level touched ends the trade"),
        ],
        default=existing.get("RESULT_MODE", "BEST_TP"),
    )

    # --------------------------------------------------------- dashboard
    heading("6. Dashboard")
    values["API_PORT"] = ask("Port to serve on", default=existing.get("API_PORT", "8000"), validate=_digits)
    values["TIMEZONE"] = ask("Timezone", default=existing.get("TIMEZONE", "Asia/Bangkok"), validate=_timezone)

    admin_password = existing.get("ADMIN_PASSWORD", "") or existing.get("ADMIN_API_KEY", "")
    if admin_password and ask_yes_no("Keep the existing admin password?", default=True):
        values["ADMIN_PASSWORD"] = admin_password
    elif ask_yes_no("Choose the admin password yourself?", default=False):
        values["ADMIN_PASSWORD"] = ask("Admin password", required=True)
    else:
        values["ADMIN_PASSWORD"] = secrets.token_hex(16)
        ok("Generated an admin password (shown at the end).")

    # ------------------------------------------------------------- write
    if os.path.exists(env_path):
        backup = f"{env_path}.bak"
        shutil.copy2(env_path, backup)
        note(f"Previous file kept as {backup}")

    with open(env_path, "w", encoding="utf-8") as handle:
        handle.write(render_env(values))
    os.chmod(env_path, 0o600)

    heading("Configuration saved")
    ok(f"{os.path.abspath(env_path)} (readable only by you)")

    if not run_followups:
        return 0

    # ---------------------------------------------------------- sign-in
    heading("7. Telegram sign-in")
    note("Telegram will send a code to your phone or Telegram app.")
    note("You type it here — it is never stored or sent anywhere else.")
    if ask_yes_no("Sign in now?", default=True):
        if _run_cli("login") != 0:
            warn("Sign-in did not finish. Run 'python -m app.cli login' when ready.")
            return _finish(values, env_path)

        heading("8. Choose the source group")
        if ask_yes_no("Pick the Telegram group to forward from?", default=True):
            _run_cli("chats", "--pick")
    else:
        note("Run 'python -m app.cli login' and then 'python -m app.cli chats --pick'.")

    return _finish(values, env_path)


def _run_cli(*args: str) -> int:
    """Run another CLI command in a fresh process so it reads the new .env."""
    return subprocess.run([sys.executable, "-m", "app.cli", *args]).returncode


def _finish(values: dict[str, str], env_path: str) -> int:
    heading("Almost there")
    print()
    _run_cli("check")

    port = values.get("API_PORT", "8000")
    print()
    heading("Next steps")
    print(f"  Start it:        {_colour(f'{sys.executable} -m app.main', BOLD)}")
    print(f"  Member view:     http://<your-server>:{port}/dashboard")
    print(f"  Admin view:      http://<your-server>:{port}/admin")
    print(f"  Admin password:  {values.get('ADMIN_PASSWORD', '')}")
    note("Also in .env. Anyone with it can correct results — every change is audited.")
    if values.get("DRY_RUN") == "true":
        warn("Test mode is on: nothing will be sent to LINE until DRY_RUN=false.")
    print()
    note("Run it as a service with:  sudo bash scripts/install.sh --service-only")
    return 0


def timezone_suggestions() -> list[str]:  # pragma: no cover - helper for humans
    return sorted(tz for tz in available_timezones() if tz.startswith("Asia/"))
