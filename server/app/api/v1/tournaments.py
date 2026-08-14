"""Tournaments and their courts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import col, func, select

from app.api.deps import CurrentUser, OwnedTournament, SessionDep
from app.models import Court, Tournament, TournamentKind
from app.schemas import (
    CourtIn,
    CourtOut,
    TournamentIn,
    TournamentOut,
    TournamentUpdate,
)
from app.services.slugs import unique_slug

router = APIRouter(tags=["tournaments"])


@router.get("/tournaments", response_model=list[TournamentOut])
async def list_tournaments(session: SessionDep, user: CurrentUser) -> list[Tournament]:
    # The casual container holds pickup games and is not an event; showing it
    # here would put a tournament nobody created at the top of the list.
    query = (
        select(Tournament)
        .where(
            Tournament.owner_id == user.id,
            Tournament.kind == TournamentKind.TOURNAMENT,
        )
        .order_by(col(Tournament.created_at).desc())
    )
    return list((await session.exec(query)).all())


@router.post("/tournaments", response_model=TournamentOut, status_code=201)
async def create_tournament(
    body: TournamentIn, session: SessionDep, user: CurrentUser
) -> Tournament:
    tournament = Tournament(
        **body.model_dump(),
        owner_id=user.id,
        slug=await unique_slug(session, body.name),
    )
    session.add(tournament)
    await session.commit()
    return tournament


@router.get("/tournaments/{tournament_id}", response_model=TournamentOut)
async def get_tournament(tournament: OwnedTournament) -> Tournament:
    return tournament


@router.patch("/tournaments/{tournament_id}", response_model=TournamentOut)
async def update_tournament(
    body: TournamentUpdate, tournament: OwnedTournament, session: SessionDep
) -> Tournament:
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tournament, field, value)
    session.add(tournament)
    await session.commit()
    return tournament


@router.delete("/tournaments/{tournament_id}", status_code=204)
async def delete_tournament(
    tournament: OwnedTournament, session: SessionDep
) -> None:
    await session.delete(tournament)
    await session.commit()


# ---------------------------------------------------------------------------
# Courts
# ---------------------------------------------------------------------------


@router.get("/tournaments/{tournament_id}/courts", response_model=list[CourtOut])
async def list_courts(
    tournament: OwnedTournament, session: SessionDep
) -> list[Court]:
    query = (
        select(Court)
        .where(Court.tournament_id == tournament.id)
        .order_by(col(Court.sort_order), col(Court.name))
    )
    return list((await session.exec(query)).all())


@router.post(
    "/tournaments/{tournament_id}/courts", response_model=CourtOut, status_code=201
)
async def create_court(
    body: CourtIn, tournament: OwnedTournament, session: SessionDep
) -> Court:
    clash = (
        await session.exec(
            select(Court).where(
                Court.tournament_id == tournament.id,
                func.lower(Court.name) == body.name.lower(),
            )
        )
    ).first()
    if clash is not None:
        raise HTTPException(
            status_code=409, detail=f"A court named {body.name!r} already exists"
        )

    court = Court(**body.model_dump(), tournament_id=tournament.id)
    session.add(court)
    await session.commit()
    return court


@router.delete("/courts/{court_id}", status_code=204)
async def delete_court(
    court_id: str, session: SessionDep, user: CurrentUser
) -> None:
    court = await session.get(Court, court_id)
    if court is None:
        raise HTTPException(status_code=404, detail="Court not found")
    tournament = await session.get(Tournament, court.tournament_id)
    if tournament is None or tournament.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Court not found")

    await session.delete(court)
    await session.commit()
