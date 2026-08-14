"""Slug allocation, shared by everything that can create a tournament."""

from __future__ import annotations

from slugify import slugify
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Tournament


async def unique_slug(session: AsyncSession, name: str) -> str:
    """A URL-safe slug for `name`, suffixed until it is free.

    Slugs are globally unique rather than per-owner because they appear in
    shareable links, so two organizers running "Spring Open" must not collide.
    """
    base = slugify(name) or "tournament"
    slug = base
    n = 2
    while (
        await session.exec(select(Tournament).where(Tournament.slug == slug))
    ).first() is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug
