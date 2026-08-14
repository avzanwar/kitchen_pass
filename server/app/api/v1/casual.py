"""Pickup games: set one up, list the recent ones, delete one.

There is deliberately no scoring endpoint here. A casual match is a real `Match`,
so it is scored through `/matches/{id}/events` like any other — which is what
makes the offline queue, undo and the live feed work for it unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, SessionDep
from app.schemas import CasualMatchIn, CasualMatchOut, PlayerOut
from app.services.casual_service import (
    CasualError,
    casual_matches,
    create_casual_match,
    delete_casual_match,
)

router = APIRouter(prefix="/casual", tags=["casual"])


@router.post("/matches", response_model=CasualMatchOut, status_code=201)
async def start_casual_match(
    body: CasualMatchIn, session: SessionDep, user: CurrentUser
) -> CasualMatchOut:
    """Set up a pickup game. Score it at `/matches/{match_id}`."""
    try:
        match = await create_casual_match(session, user, body)
    except CasualError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    for row in await casual_matches(session, user):
        if row["match_id"] == match.id:
            return _out(row)
    raise HTTPException(status_code=500, detail="The game was created but not found")


@router.get("/matches", response_model=list[CasualMatchOut])
async def list_casual_matches(
    session: SessionDep, user: CurrentUser
) -> list[CasualMatchOut]:
    return [_out(row) for row in await casual_matches(session, user)]


@router.delete("/matches/{match_id}", status_code=204)
async def remove_casual_match(
    match_id: str, session: SessionDep, user: CurrentUser
) -> None:
    try:
        await delete_casual_match(session, user, match_id)
    except CasualError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _out(row: dict) -> CasualMatchOut:  # type: ignore[type-arg]
    # Built field by field rather than from a dump: the response models forbid
    # extras, and the ORM rows carry owner_id and created_at that clients do not
    # need.
    return CasualMatchOut(
        match_id=row["match_id"],
        division_id=row["division_id"],
        status=row["status"],
        format=row["format"],
        scoring=row["scoring"],
        target=row["target"],
        best_of=row["best_of"],
        created_at=row["created_at"],
        a_name=row["a_name"],
        b_name=row["b_name"],
        a_players=[PlayerOut.model_validate(p) for p in row["a_players"]],
        b_players=[PlayerOut.model_validate(p) for p in row["b_players"]],
        winner=row["winner"],
        games_won=row["games_won"],
        games=row["games"],
    )
