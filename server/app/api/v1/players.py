"""Player roster CRUD.

Players belong to the organizer who created them, which is what keeps one
club's roster out of another's picker.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.models import EntryPlayer, Player
from app.schemas import PlayerIn, PlayerOut

router = APIRouter(prefix="/players", tags=["players"])


async def _owned(session: SessionDep, player_id: str, user: CurrentUser) -> Player:
    player = await session.get(Player, player_id)
    if player is None or player.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@router.get("", response_model=list[PlayerOut])
async def list_players(
    session: SessionDep,
    user: CurrentUser,
    search: str | None = None,
    include_guests: bool = False,
) -> list[Player]:
    """The saved roster.

    Guests — one-off names typed at the court for a pickup game — are excluded
    unless asked for. They are real players with real ids, but the roster is
    "people I play with", not "everyone who ever held a paddle", and a guest
    appearing in a tournament entry picker would be a mistake waiting to happen.
    """
    query = select(Player).where(Player.owner_id == user.id)
    if not include_guests:
        query = query.where(col(Player.is_guest).is_(False))
    if search:
        query = query.where(col(Player.name).ilike(f"%{search}%"))
    if include_guests:
        # Guests are picked again by recency ("that Mike, from last Tuesday"),
        # not alphabetically.
        return list(
            (await session.exec(query.order_by(col(Player.created_at).desc()))).all()
        )
    return list((await session.exec(query.order_by(col(Player.name)))).all())


@router.post("", response_model=PlayerOut, status_code=201)
async def create_player(
    body: PlayerIn, session: SessionDep, user: CurrentUser
) -> Player:
    player = Player(**body.model_dump(), owner_id=user.id)
    session.add(player)
    await session.commit()
    return player


@router.get("/{player_id}", response_model=PlayerOut)
async def get_player(
    player_id: str, session: SessionDep, user: CurrentUser
) -> Player:
    return await _owned(session, player_id, user)


@router.patch("/{player_id}", response_model=PlayerOut)
async def update_player(
    player_id: str, body: PlayerIn, session: SessionDep, user: CurrentUser
) -> Player:
    player = await _owned(session, player_id, user)
    for field, value in body.model_dump().items():
        setattr(player, field, value)
    session.add(player)
    await session.commit()
    return player


@router.delete("/{player_id}", status_code=204)
async def delete_player(
    player_id: str, session: SessionDep, user: CurrentUser
) -> None:
    player = await _owned(session, player_id, user)

    # Deleting a player who is in a draw would orphan the entry and corrupt
    # serve stats that are keyed by player id, so refuse rather than cascade.
    in_use = (
        await session.exec(
            select(EntryPlayer).where(EntryPlayer.player_id == player_id)
        )
    ).first()
    if in_use is not None:
        raise HTTPException(
            status_code=409,
            detail="This player is registered in a division and cannot be deleted",
        )

    await session.delete(player)
    await session.commit()
