"""Async test fixtures for the API.

Each test gets a fresh in-memory SQLite database with a StaticPool, so the same
connection is reused across the session and the schema survives between
requests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import _enforce_sqlite_foreign_keys, get_session
from app.main import create_app


@pytest_asyncio.fixture
async def engine() -> AsyncIterator:
    import app.models  # noqa: F401  registers the tables

    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Match production: without this SQLite ignores foreign keys entirely, and
    # a cascade that Postgres refuses passes here unnoticed.
    _enforce_sqlite_foreign_keys(eng)
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest_asyncio.fixture
async def client(engine) -> AsyncIterator[AsyncClient]:
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: Settings(debug=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    get_settings.cache_clear()


class ApiUser:
    """A registered user plus a client that authenticates as them."""

    def __init__(self, client: AsyncClient, token: str, email: str) -> None:
        self.client = client
        self.token = token
        self.email = email
        self.headers = {"Authorization": f"Bearer {token}"}

    async def post(self, url: str, **kw):
        return await self.client.post(url, headers=self.headers, **kw)

    async def get(self, url: str, **kw):
        return await self.client.get(url, headers=self.headers, **kw)

    async def patch(self, url: str, **kw):
        return await self.client.patch(url, headers=self.headers, **kw)

    async def delete(self, url: str, **kw):
        return await self.client.delete(url, headers=self.headers, **kw)


async def register(client: AsyncClient, email: str = "org@example.com") -> ApiUser:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery",
              "display_name": "Org"},
    )
    assert response.status_code == 201, response.text
    return ApiUser(client, response.json()["access_token"], email)


@pytest_asyncio.fixture
async def organizer(client: AsyncClient) -> ApiUser:
    return await register(client)
