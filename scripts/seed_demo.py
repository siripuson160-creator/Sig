"""Populate a demo database so the dashboard can be looked at before go-live.

    PRICE_DATA_PROVIDER=csv LINE_ENABLED=false python scripts/seed_demo.py --days 30

It generates synthetic gold candles, posts synthetic Telegram messages through
the *real* ingestion path (including a few edited messages), then runs the real
result engine over them. Nothing here is used in production; it exists so the
UI and the statistics can be reviewed with plausible data.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import get_engine, init_db, session_scope  # noqa: E402
from app.engine.result_engine import ResultEngine  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.prices.providers import CsvProvider  # noqa: E402
from app.processor.message_processor import ingest_message  # noqa: E402

CHAT_ID = -1002222222222
START_PRICE = 3340.0


def generate_candles(start: datetime, minutes: int, rng: random.Random) -> list[dict]:
    """A random walk that looks like gold on a 1-minute chart."""
    candles = []
    price = START_PRICE
    for index in range(minutes):
        ts = start + timedelta(minutes=index)
        # Skip the weekend, when the market is closed.
        if ts.weekday() >= 5:
            continue
        drift = rng.gauss(0, 0.55)
        open_price = price
        close_price = round(open_price + drift, 2)
        high = round(max(open_price, close_price) + abs(rng.gauss(0, 0.35)), 2)
        low = round(min(open_price, close_price) - abs(rng.gauss(0, 0.35)), 2)
        candles.append(
            {"timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"), "open": open_price, "high": high, "low": low, "close": close_price}
        )
        price = close_price
    return candles


def write_csv(candles: list[dict], symbol: str) -> str:
    os.makedirs(settings.price_csv_path, exist_ok=True)
    path = os.path.join(settings.price_csv_path, f"{symbol}_1m.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "open", "high", "low", "close"])
        writer.writeheader()
        writer.writerows(candles)
    return path


def build_messages(candles: list[dict], rng: random.Random, count: int) -> list[list[tuple[datetime, str]]]:
    """Return message threads; a thread with 2+ entries is an edited message."""
    threads: list[list[tuple[datetime, str]]] = []
    usable = [c for c in candles[60:-360]] if len(candles) > 500 else candles[:-10]
    if not usable:
        return threads

    for _ in range(count):
        candle = rng.choice(usable)
        ts = datetime.strptime(candle["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        entry = round(candle["close"], 2)
        direction = rng.choice(["BUY", "SELL"])
        distance = rng.choice([6, 8, 10, 12])
        if direction == "BUY":
            sl, tp1, tp2 = entry - distance, entry + distance, entry + distance * 2
        else:
            sl, tp1, tp2 = entry + distance, entry - distance, entry - distance * 2

        full = f"{direction} GOLD {entry:g}\nSL {sl:g}\nTP1 {tp1:g}\nTP2 {tp2:g}"

        style = rng.random()
        if style < 0.22:
            # Posted as a heads-up first, then edited into a full signal.
            threads.append(
                [
                    (ts, "Sell now" if direction == "SELL" else "Buy now"),
                    (ts + timedelta(minutes=2), f"{direction} GOLD {entry:g}"),
                    (ts + timedelta(minutes=3), full),
                ]
            )
        elif style < 0.32:
            threads.append([(ts, full.replace("\n", " "))])  # single line variant
        else:
            threads.append([(ts, full)])

    # A few non-signal messages, which must still be forwarded.
    for text in ["Good morning", "Market is quiet, waiting for London open", "TP1 hit ✅", "Close half"]:
        candle = rng.choice(usable)
        ts = datetime.strptime(candle["timestamp"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        threads.append([(ts, text)])

    threads.sort(key=lambda thread: thread[0][0])
    return threads


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--signals", type=int, default=90)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--reset", action="store_true", help="drop existing tables first")
    args = parser.parse_args()

    configure_logging("WARNING")
    rng = random.Random(args.seed)

    if args.reset:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await init_db()

    start = (datetime.now(timezone.utc) - timedelta(days=args.days)).replace(second=0, microsecond=0)
    candles = generate_candles(start, args.days * 24 * 60, rng)
    path = write_csv(candles, settings.price_symbol)
    print(f"wrote {len(candles):,} candles to {path}")

    threads = build_messages(candles, rng, args.signals)
    message_id = 1000
    stored = 0
    async with session_scope() as session:
        for thread in threads:
            message_id += 1
            for index, (ts, text) in enumerate(thread):
                result = await ingest_message(
                    session,
                    chat_id=CHAT_ID,
                    message_id=message_id,
                    content=text,
                    tg_created_at=thread[0][0],
                    tg_edited_at=ts if index else None,
                    sender_name="Demo Signal Provider",
                    is_edit=index > 0,
                )
                stored += 1 if result.created else 0
    print(f"stored {stored} message version(s) across {len(threads)} threads")

    engine = ResultEngine(provider=CsvProvider())
    total = 0
    for _ in range(4):  # a few passes so laddered TPs settle
        async with session_scope() as session:
            changed = await engine.run_once(session)
        total += changed
        if not changed:
            break
    print(f"result engine updated {total} signal(s)")

    from app.engine import stats_engine

    async with session_scope() as session:
        overview = await stats_engine.build_overview(session)
    print(
        f"\n{overview['total_signals']} signals · {overview['wins']}W/{overview['losses']}L · "
        f"win rate {overview['win_rate']}% · P/L {overview['total_pl_points']:+g} points · "
        f"PF {overview['profit_factor']} · max DD {overview['max_drawdown_points']:+g}"
    )
    print("\nStart the dashboard with:  python -m app.main --only api")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
