"""Operator commands.

    python -m app.cli login       # one-time Telegram sign-in (you type the code)
    python -m app.cli chats       # list your groups with their chat ids
    python -m app.cli check       # validate configuration and connectivity
    python -m app.cli evaluate    # run the result engine once over open signals
    python -m app.cli stats       # print the current overview

The login step is interactive on purpose. The OTP and the 2FA password are
typed by the account owner into this terminal, are handed straight to Telegram
and are never stored, logged or transmitted anywhere else (section 3).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.config import settings
from app.db.session import dispose_engine, init_db, session_scope
from app.logging_config import configure_logging


async def cmd_login() -> int:
    from app.telegram.listener import build_client

    print("Telegram sign-in")
    print("----------------")
    print("You will be asked for your phone number, the code Telegram sends you,")
    print("and your 2FA password if you have one.")
    print("Type them yourself - nobody else should ever be given these.\n")

    client = build_client()
    await client.start()  # Telethon prompts for phone / code / password
    me = await client.get_me()
    print(f"\nSigned in as {me.first_name} (id={me.id}).")
    print(f"Session saved to {settings.telegram_session}. Keep this file secret - it is a login.")
    await client.disconnect()
    return 0


async def cmd_chats(limit: int) -> int:
    from app.telegram.listener import build_client

    client = build_client()
    await client.connect()
    if not await client.is_user_authorized():
        print("Not signed in yet. Run 'python -m app.cli login' first.")
        await client.disconnect()
        return 1

    print(f"{'chat id':>16}  type       title")
    print("-" * 60)
    async for dialog in client.iter_dialogs(limit=limit):
        kind = "group" if dialog.is_group else "channel" if dialog.is_channel else "user"
        print(f"{dialog.id:>16}  {kind:<9}  {dialog.name}")
    print("\nPut the id of the source group in TELEGRAM_SOURCE_CHAT_ID.")
    await client.disconnect()
    return 0


async def cmd_check() -> int:
    from app.db.session import healthcheck
    from app.line.client import LineClient
    from app.prices.providers import get_provider

    ok = True
    print(f"timezone                : {settings.timezone}")
    print(f"database                : {'reachable' if await healthcheck() else 'UNREACHABLE'}")

    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("telegram credentials    : MISSING (TELEGRAM_API_ID / TELEGRAM_API_HASH)")
        ok = False
    else:
        print("telegram credentials    : set")

    if not settings.source_chat_ids:
        print("telegram source chat    : MISSING (TELEGRAM_SOURCE_CHAT_ID)")
        ok = False
    else:
        print(f"telegram source chat    : {', '.join(settings.source_chat_ids)}")

    try:
        from app.telegram.listener import build_client

        client = build_client()
        await client.connect()
        authorized = await client.is_user_authorized()
        print(f"telegram session        : {'authorised' if authorized else 'NOT AUTHORISED (run login)'}")
        ok = ok and authorized
        await client.disconnect()
    except Exception as exc:
        print(f"telegram session        : ERROR {exc}")
        ok = False

    if settings.line_enabled:
        async with LineClient() as line:
            line_ok, detail = await line.verify()
        print(f"line credentials        : {'ok - ' + detail if line_ok else 'FAILED - ' + detail}")
        ok = ok and line_ok
    else:
        print("line credentials        : delivery disabled (LINE_ENABLED=false)")

    provider = get_provider()
    note = "" if provider.available else "  (signals will stay at PENDING_RESULT)"
    print(f"price provider          : {provider.name}{note}")
    print(f"admin dashboard         : {'enabled' if settings.admin_api_key else 'disabled (ADMIN_API_KEY unset)'}")
    return 0 if ok else 1


async def cmd_evaluate() -> int:
    from app.engine.result_engine import ResultEngine

    engine = ResultEngine()
    async with session_scope() as session:
        changed = await engine.run_once(session)
    await engine.provider.close()
    print(f"updated {changed} signal(s) using provider '{engine.provider.name}'")
    return 0


async def cmd_stats(range_key: str) -> int:
    from app.engine import stats_engine

    async with session_scope() as session:
        overview = await stats_engine.build_overview(session, range_key)
    print(json.dumps(overview, indent=2, ensure_ascii=False))
    return 0


async def cmd_drain() -> int:
    """Push everything currently queued to LINE and exit."""
    from app.line.client import LineClient
    from app.line.queue_worker import LineQueueWorker

    worker = LineQueueWorker()
    async with LineClient() as client:
        total = 0
        while True:
            sent = await worker.drain_once(client)
            if not sent:
                break
            total += sent
    print(f"delivered {total} message(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="one-time interactive Telegram sign-in")

    chats = sub.add_parser("chats", help="list dialogs with their chat ids")
    chats.add_argument("--limit", type=int, default=50)

    sub.add_parser("check", help="validate configuration and connectivity")
    sub.add_parser("evaluate", help="run the result engine once")
    sub.add_parser("drain", help="flush the LINE queue once")

    stats = sub.add_parser("stats", help="print the performance overview")
    stats.add_argument("--range", default="all")

    return parser


async def _dispatch(args: argparse.Namespace) -> int:
    await init_db()
    try:
        if args.command == "login":
            return await cmd_login()
        if args.command == "chats":
            return await cmd_chats(args.limit)
        if args.command == "check":
            return await cmd_check()
        if args.command == "evaluate":
            return await cmd_evaluate()
        if args.command == "drain":
            return await cmd_drain()
        if args.command == "stats":
            return await cmd_stats(args.range)
    finally:
        await dispose_engine()
    return 1


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    sys.exit(main())
