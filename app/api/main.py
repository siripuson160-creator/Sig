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

from app import __version__, setup_state
from app.api.routes_admin import router as admin_router
from app.api.routes_compat import router as compat_router
from app.api.routes_public import router as public_router
from app.api.routes_settings import router as settings_router
from app.api.routes_setup import router as setup_router
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
        # Whichever way the API was started — the supervisor, uvicorn directly,
        # a container — an unconfigured install needs a setup token to exist,
        # or /setup asks for one that was never written.
        if not setup_state.is_configured():
            token = setup_state.ensure_token()
            log.warning("not configured yet — open /setup?token=%s to finish the install", token)
            log.warning("the same token is in %s", setup_state.TOKEN_PATH)
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

    app.include_router(setup_router)
    app.include_router(public_router)
    app.include_router(compat_router)
    app.include_router(admin_router)
    app.include_router(settings_router)

    static_dir = os.path.join(WEB_DIR, "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        # A fresh install has nothing to show on a dashboard, so send the
        # operator to the thing they actually need to do next.
        if not setup_state.is_configured():
            return RedirectResponse("/setup")
        return RedirectResponse("/dashboard")

    # response_model=None on these three: the return type is a union of two
    # Response classes, which FastAPI would otherwise try to treat as a schema.
    @app.get("/setup", include_in_schema=False, response_model=None)
    async def setup_page() -> FileResponse | RedirectResponse:
        if setup_state.is_configured():
            return RedirectResponse("/dashboard")
        return FileResponse(os.path.join(WEB_DIR, "setup.html"))

    @app.get("/dashboard", include_in_schema=False, response_model=None)
    async def dashboard() -> FileResponse | RedirectResponse:
        if not setup_state.is_configured():
            return RedirectResponse("/setup")
        return FileResponse(os.path.join(WEB_DIR, "dashboard.html"))

    @app.get("/admin", include_in_schema=False, response_model=None)
    async def admin() -> FileResponse | RedirectResponse:
        if not setup_state.is_configured():
            return RedirectResponse("/setup")
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
