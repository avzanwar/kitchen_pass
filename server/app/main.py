"""FastAPI application."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, courts, divisions, players, public, scoring, tournaments
from app.core.config import get_settings
from app.core.db import create_all, dispose
from app.realtime.hub import hub

log = logging.getLogger("kitchen_pass")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Fail fast rather than serving with a placeholder signing key.
    settings.assert_production_safe()

    if settings.debug:
        # Convenience for local development only; deployments run Alembic.
        await create_all()
        log.info("schema ensured (debug mode)")

    if settings.redis_url:
        await hub.connect_redis(settings.redis_url)

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

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
