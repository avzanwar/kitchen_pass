"""FastAPI application.

In deployment this single service serves both the API and the built frontend.
That keeps a free-tier deployment to one process and one URL, which also means
no CORS and same-origin WebSockets.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import (
    auth,
    casual,
    courts,
    divisions,
    imports,
    players,
    public,
    scoring,
    tournaments,
)
from app.core.config import get_settings
from app.core.db import create_all, dispose, session_factory
from app.core.startup import run_migrations, seed_demo_if_empty
from app.realtime.hub import hub

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("kitchen_pass")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Fail fast rather than serving with a placeholder signing key.
    settings.assert_production_safe()

    if settings.debug:
        # Local convenience; deployments migrate instead.
        await create_all()
        log.info("schema ensured (debug mode)")
    else:
        await run_migrations(settings)

    if settings.seed_on_start:
        async with session_factory()() as session:
            await seed_demo_if_empty(session)

    if settings.redis_url:
        await hub.connect_redis(settings.redis_url)

    log.info("ready")
    yield
    await hub.close()
    await dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        description="Pickleball tournament manager — draws, scoring and standings.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = "/api/v1"
    app.include_router(auth.router, prefix=api)
    app.include_router(players.router, prefix=api)
    app.include_router(tournaments.router, prefix=api)
    app.include_router(divisions.router, prefix=api)
    app.include_router(scoring.router, prefix=api)
    app.include_router(courts.router, prefix=api)
    app.include_router(public.router, prefix=api)
    app.include_router(imports.router, prefix=api)
    app.include_router(casual.router, prefix=api)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    _mount_frontend(app, settings.static_dir)
    return app


def _mount_frontend(app: FastAPI, static_dir: str) -> None:
    """Serve the built SPA, if there is one.

    Skipped entirely in development, where Vite serves the frontend and proxies
    /api to this process.
    """
    if not static_dir:
        return
    root = Path(static_dir)
    if not (root / "index.html").is_file():
        log.warning("KP_STATIC_DIR=%s has no index.html; not serving a frontend", root)
        return

    # Hashed build output. Mounted before the catch-all so real files win.
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    # response_model=None: the return type is a union of Response subclasses,
    # which FastAPI would otherwise try to turn into a Pydantic model.
    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    async def spa(request: Request, path: str) -> FileResponse | JSONResponse:
        # An unmatched /api path is a genuine 404, not a route for the SPA to
        # handle — returning index.html there would turn every API typo into a
        # confusing 200 with an HTML body.
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root.resolve()):
            return FileResponse(candidate)
        return FileResponse(root / "index.html")

    log.info("serving frontend from %s", root)


app = create_app()
