"""FastAPI application: dashboard API plus the static dashboard itself."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.routes_admin import router as admin_router
from app.api.routes_compat import router as compat_router
from app.api.routes_public import router as public_router
from app.config import settings
from app.db.session import init_db

log = logging.getLogger(__name__)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def create_app(*, init_database: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Skipped when app.main runs the API alongside the other components:
        # the supervisor has already prepared the schema.
        if init_database:
            await init_db()
        yield

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Telegram -> LINE signal bridge and trading performance dashboard.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["*"],
    )

    app.include_router(public_router)
    app.include_router(compat_router)
    app.include_router(admin_router)

    static_dir = os.path.join(WEB_DIR, "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/dashboard")

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(os.path.join(WEB_DIR, "dashboard.html"))

    @app.get("/admin", include_in_schema=False)
    async def admin() -> FileResponse:
        return FileResponse(os.path.join(WEB_DIR, "admin.html"))

    @app.get("/performance-methodology", include_in_schema=False)
    async def methodology_page() -> RedirectResponse:
        """The URL section 45 names; the content lives on the dashboard tab."""
        return RedirectResponse("/dashboard#/methodology")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
