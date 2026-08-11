"""Async engine and session wiring."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

# SQLModel's AsyncSession adds `.exec()`, which preserves the model type through
# `select(Model)`; SQLAlchemy's plain AsyncSession would erase it to Row.
from sqlmodel.ext.asyncio.session import AsyncSession

from .config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _connect_args(settings: Settings) -> dict[str, object]:
    if settings.database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        future=True,
        connect_args=_connect_args(settings),
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. One session per request, rolled back on error."""
    async with session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def create_all(engine: AsyncEngine | None = None) -> None:
    """Create the schema directly, bypassing Alembic.

    For tests and first-run development only — deployments run migrations.
    """
    # Importing the models module is what registers the tables on
    # SQLModel.metadata; without it this silently creates nothing.
    from app import models  # noqa: F401  (import for side effect)

    engine = engine or get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def dispose() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
