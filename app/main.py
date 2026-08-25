"""Process supervisor.

Runs the four long-lived components in one asyncio loop:

* the Telegram listener      (reads the source group)
* the LINE queue worker      (pushes queued messages to the LINE group)
* the result engine          (checks TP/SL against price data)
* the dashboard API          (FastAPI + static dashboard)

Any component can be disabled from the command line, which is how the systemd
units in ``deploy/`` split the bridge from the web tier if needed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

import uvicorn

from app.config import settings
from app.db.session import dispose_engine, init_db
from app.engine.result_engine import ResultEngine
from app.line.queue_worker import LineQueueWorker
from app.logging_config import configure_logging

log = logging.getLogger("app.main")

#: How long a component may take to stop before it is cancelled.
SHUTDOWN_GRACE_SECONDS = 15


class ApiRunner:
    """Runs uvicorn so it can be asked to shut down gracefully.

    Cancelling the serve() task instead would tear down the ASGI lifespan
    mid-await and log a spurious traceback on every restart.
    """

    def __init__(self) -> None:
        self._server: uvicorn.Server | None = None

    async def run(self) -> None:
        from app.api.main import create_app

        config = uvicorn.Config(
            create_app(init_database=False),
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.lower(),
            access_log=False,
            loop="asyncio",
        )
        self._server = uvicorn.Server(config)
        await self._server.serve()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True


async def _run_listener() -> None:
    from app.telegram.listener import TelegramListener, TelegramNotAuthorized

    listener = TelegramListener()
    try:
        await listener.run()
    except TelegramNotAuthorized as exc:
        log.error("%s", exc)
        raise


async def run(components: set[str]) -> int:
    configure_logging()
    await init_db()

    tasks: dict[str, asyncio.Task] = {}
    line_worker: LineQueueWorker | None = None
    result_engine: ResultEngine | None = None
    api_runner: ApiRunner | None = None

    if "listener" in components:
        tasks["listener"] = asyncio.create_task(_run_listener(), name="listener")
    if "line" in components:
        line_worker = LineQueueWorker()
        tasks["line"] = asyncio.create_task(line_worker.run(), name="line")
    if "results" in components:
        result_engine = ResultEngine()
        tasks["results"] = asyncio.create_task(result_engine.run(), name="results")
    if "api" in components:
        api_runner = ApiRunner()
        tasks["api"] = asyncio.create_task(api_runner.run(), name="api")

    if not tasks:
        log.error("nothing to run")
        return 2

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            signal.signal(sig, lambda *_: stop.set())

    log.info("started components: %s", ", ".join(sorted(tasks)))

    waiter = asyncio.create_task(stop.wait(), name="shutdown")
    done, _pending = await asyncio.wait({*tasks.values(), waiter}, return_when=asyncio.FIRST_COMPLETED)

    exit_code = 0
    for task in done:
        if task is waiter:
            log.info("shutdown signal received")
            continue
        name = task.get_name()
        if task.cancelled():
            continue
        error = task.exception()
        if error is not None:
            log.error("component %s crashed: %s", name, error, exc_info=error)
            exit_code = 1
        else:
            log.warning("component %s exited", name)

    # Ask every component to finish on its own terms first.
    if line_worker is not None:
        line_worker.stop()
    if result_engine is not None:
        result_engine.stop()
    if api_runner is not None:
        api_runner.stop()

    waiter.cancel()
    pending = [task for task in tasks.values() if not task.done()]
    if pending:
        await asyncio.wait(pending, timeout=SHUTDOWN_GRACE_SECONDS)
    for task in tasks.values():
        if not task.done():
            log.warning("component %s did not stop in time; cancelling", task.get_name())
            task.cancel()
    await asyncio.gather(*tasks.values(), waiter, return_exceptions=True)
    await dispose_engine()
    log.info("shutdown complete")
    return exit_code


ALL_COMPONENTS = ("listener", "line", "results", "api")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="app.main", description="Telegram -> LINE bridge + dashboard")
    parser.add_argument(
        "--only",
        nargs="+",
        choices=ALL_COMPONENTS,
        help="run only these components (default: all)",
    )
    for name in ALL_COMPONENTS:
        parser.add_argument(f"--no-{name}", action="store_true", help=f"disable the {name} component")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    components = set(args.only) if args.only else set(ALL_COMPONENTS)
    for name in ALL_COMPONENTS:
        if getattr(args, f"no_{name}"):
            components.discard(name)
    try:
        return asyncio.run(run(components))
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 0


if __name__ == "__main__":
    sys.exit(main())
