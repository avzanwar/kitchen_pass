#!/usr/bin/env python
"""Seed a realistic tournament for development and manual testing.

Creates an organizer, a 24-player roster, three divisions with different draw
formats, six courts, and generates every draw.

    uv run python scripts/seed.py            # sqlite, ./kitchen_pass.db
    KP_DATABASE_URL=postgresql+asyncpg://... uv run python scripts/seed.py

Idempotent by email: rerunning wipes the seeded organizer's data first.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402
from sqlmodel import col, delete, select  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

from app.core.db import create_all, create_engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models import (  # noqa: E402
    Court,
    Division,
    DivisionFormat,
    DrawKind,
    Entry,
    EntryPlayer,
    EntryStatus,
    Player,
    Tournament,
    TournamentStatus,
    User,
)
from app.services.draw_service import generate_and_persist  # noqa: E402

# Not a .test/.invalid domain: those are reserved, and pydantic's EmailStr
# rejects them — the seeded account has to be able to actually log in.
SEED_EMAIL = "organizer@kitchenpass.dev"
SEED_PASSWORD = "seed-password-123"

FIRST = [
    "Ann", "Bo", "Cy", "Di", "Eli", "Fay", "Gus", "Hana", "Ivo", "Jae", "Kit",
    "Lena", "Mo", "Nia", "Otto", "Pia", "Quin", "Rui", "Sam", "Tess", "Uma",
    "Vik", "Wes", "Xara",
]
LAST = [
    "Alvarez", "Brooks", "Chen", "Diallo", "Eriksen", "Ferrari", "Gupta",
    "Haddad", "Ibrahim", "Jensen", "Kowalski", "Lindqvist",
]
EMOJIS = ["🏓", "🎾", "🔥", "⭐", "🦅", "🐅", "🚀", "🦈", "⚡", "🌊", "🥇", "🧢"]
PALETTE = ["#0E7C6B", "#EA6D3A", "#3B7DC4", "#B4529E", "#D99A00", "#5B8A3A"]


async def reset(session: AsyncSession) -> None:
    """Remove anything from a previous seed run."""
    user = (
        await session.exec(select(User).where(User.email == SEED_EMAIL))
    ).first()
    if user is None:
        return

    tournaments = list(
        (
            await session.exec(
                select(Tournament).where(Tournament.owner_id == user.id)
            )
        ).all()
    )
    for tournament in tournaments:
        await session.delete(tournament)
    await session.flush()

    players = list(
        (await session.exec(select(Player).where(Player.owner_id == user.id))).all()
    )
    if players:
        await session.exec(
            delete(EntryPlayer).where(
                col(EntryPlayer.player_id).in_([p.id for p in players])
            )
        )
        for player in players:
            await session.delete(player)
    await session.delete(user)
    await session.commit()


async def seed() -> None:
    engine = create_engine()
    await create_all(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        await reset(session)

        organizer = User(
            email=SEED_EMAIL,
            hashed_password=hash_password(SEED_PASSWORD),
            display_name="Seed Organizer",
        )
        session.add(organizer)
        await session.flush()

        players: list[Player] = []
        for i, first in enumerate(FIRST):
            players.append(
                Player(
                    name=f"{first} {LAST[i % len(LAST)]}",
                    rating=round(3.0 + (i % 5) * 0.5, 1),
                    avatar={
                        "type": "emoji",
                        "value": EMOJIS[i % len(EMOJIS)],
                        "color": PALETTE[i % len(PALETTE)],
                    },
                    owner_id=organizer.id,
                )
            )
        session.add_all(players)
        await session.flush()

        tournament = Tournament(
            name="Kitchen Pass Spring Open",
            slug="kitchen-pass-spring-open",
            owner_id=organizer.id,
            starts_on="2026-09-12",
            ends_on="2026-09-13",
            status=TournamentStatus.REGISTRATION,
        )
        session.add(tournament)
        await session.flush()

        session.add_all(
            [
                Court(tournament_id=tournament.id, name=f"Court {n}", sort_order=n)
                for n in range(1, 7)
            ]
        )

        # Rosters overlap on purpose: players commonly enter more than one
        # division, and that is exactly the situation the court scheduler has to
        # detect so nobody is drawn onto two courts at the same time.
        specs = [
            (
                "4.0 Mixed Doubles",
                DivisionFormat.DOUBLES,
                DrawKind.POOL_PLAYOFF,
                {"pool_count": 2, "advance_per_pool": 2},
                players[0:16],  # 8 pairs -> two pools of four, top two advance
                2,
            ),
            (
                "3.5 Men's Doubles",
                DivisionFormat.DOUBLES,
                DrawKind.ROUND_ROBIN,
                {"pool_count": 1},
                players[8:20],  # 6 pairs -> a 15-match round robin
                2,
            ),
            (
                "Open Singles",
                DivisionFormat.SINGLES,
                DrawKind.DOUBLE_ELIMINATION,
                {},
                players[16:24],  # 8 singles entries
                1,
            ),
        ]

        divisions: list[Division] = []
        for name, fmt, kind, config, roster, per_entry in specs:
            division = Division(
                tournament_id=tournament.id,
                name=name,
                format=fmt,
                draw_kind=kind,
                draw_config=config,
                match_config={
                    "format": "singles" if fmt is DivisionFormat.SINGLES else "doubles",
                    "best_of": 3,
                    "games_to": [11, 11, 15],
                    "win_by_2": True,
                    "switch_ends": "deciding_game",
                },
            )
            session.add(division)
            await session.flush()
            divisions.append(division)

            for index in range(0, len(roster), per_entry):
                chunk = roster[index:index + per_entry]
                entry = Entry(
                    division_id=division.id,
                    name=" & ".join(p.name.split(" ")[0] for p in chunk),
                    seed=index // per_entry + 1,
                    status=EntryStatus.CHECKED_IN,
                )
                session.add(entry)
                await session.flush()
                for position, player in enumerate(chunk):
                    session.add(
                        EntryPlayer(
                            entry_id=entry.id, player_id=player.id, position=position
                        )
                    )

        await session.commit()

        for division in divisions:
            matches = await generate_and_persist(session, division, replace=True)
            print(f"  {division.name:24} {division.draw_kind.value:20} "
                  f"{len(matches):3} matches")

    await engine.dispose()

    print(f"\nSeeded '{tournament.name}'")
    print(f"  login: {SEED_EMAIL} / {SEED_PASSWORD}")
    print(f"  public standings token: {tournament.public_token}")


if __name__ == "__main__":
    asyncio.run(seed())
