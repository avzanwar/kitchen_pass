"""Divisions, entries, draw generation and standings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from app.api.deps import CurrentUser, OwnedDivision, OwnedTournament, SessionDep
from app.models import (
    Division,
    DivisionFormat,
    Entry,
    EntryPlayer,
    EntryStatus,
    Match,
    Player,
)
from app.schemas import (
    DivisionIn,
    DivisionOut,
    DrawOut,
    EntryIn,
    EntryOut,
    EntryStatusUpdate,
    MatchOut,
    PlayerOut,
    StandingOut,
    StandingsOut,
)
from app.services.draw_service import (
    DrawError,
    generate_and_persist,
    refresh_statuses,
    standings_for,
)

router = APIRouter(tags=["divisions"])


def _roster_size(fmt: DivisionFormat) -> int:
    return 1 if fmt is DivisionFormat.SINGLES else 2


async def _entry_out(session: SessionDep, entry: Entry) -> EntryOut:
    links = list(
        (
            await session.exec(
                select(EntryPlayer)
                .where(EntryPlayer.entry_id == entry.id)
                .order_by(col(EntryPlayer.position))
            )
        ).all()
    )
    players = []
    for link in links:
        player = await session.get(Player, link.player_id)
        if player is not None:
            players.append(PlayerOut.model_validate(player))
    # Built field by field rather than from `model_dump()`: the response models
    # forbid extras, and dumping the ORM row would drag in owner_id/created_at.
    return EntryOut(
        id=entry.id,
        division_id=entry.division_id,
        name=entry.name,
        seed=entry.seed,
        status=entry.status,
        players=players,
    )


# ---------------------------------------------------------------------------
# Divisions
# ---------------------------------------------------------------------------


@router.get("/tournaments/{tournament_id}/divisions", response_model=list[DivisionOut])
async def list_divisions(
    tournament: OwnedTournament, session: SessionDep
) -> list[Division]:
    query = (
        select(Division)
        .where(Division.tournament_id == tournament.id)
        .order_by(col(Division.name))
    )
    return list((await session.exec(query)).all())


@router.post(
    "/tournaments/{tournament_id}/divisions", response_model=DivisionOut, status_code=201
)
async def create_division(
    body: DivisionIn, tournament: OwnedTournament, session: SessionDep
) -> Division:
    data = body.model_dump()
    if data.get("tiebreakers") is None:
        # Fall through to the model's default chain rather than storing null.
        data.pop("tiebreakers", None)
    division = Division(**data, tournament_id=tournament.id)
    session.add(division)
    await session.commit()
    return division


@router.get("/divisions/{division_id}", response_model=DivisionOut)
async def get_division(division: OwnedDivision) -> Division:
    return division


@router.delete("/divisions/{division_id}", status_code=204)
async def delete_division(division: OwnedDivision, session: SessionDep) -> None:
    await session.delete(division)
    await session.commit()


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


@router.get("/divisions/{division_id}/entries", response_model=list[EntryOut])
async def list_entries(
    division: OwnedDivision, session: SessionDep
) -> list[EntryOut]:
    entries = list(
        (
            await session.exec(
                select(Entry)
                .where(Entry.division_id == division.id)
                .order_by(col(Entry.seed), col(Entry.name))
            )
        ).all()
    )
    return [await _entry_out(session, e) for e in entries]


@router.post("/divisions/{division_id}/entries", response_model=EntryOut, status_code=201)
async def create_entry(
    body: EntryIn, division: OwnedDivision, session: SessionDep, user: CurrentUser
) -> EntryOut:
    need = _roster_size(division.format)
    if len(body.player_ids) != need:
        raise HTTPException(
            status_code=422,
            detail=f"{division.format.value} needs {need} player(s), "
                   f"got {len(body.player_ids)}",
        )
    if len(set(body.player_ids)) != len(body.player_ids):
        raise HTTPException(
            status_code=422, detail="An entry cannot list the same player twice"
        )
    if division.draw_generated:
        raise HTTPException(
            status_code=409,
            detail="The draw has been generated; regenerate it to change the field",
        )

    players: list[Player] = []
    for player_id in body.player_ids:
        player = await session.get(Player, player_id)
        if player is None or player.owner_id != user.id:
            raise HTTPException(status_code=404, detail=f"Player {player_id} not found")
        players.append(player)

    # A player may only hold one entry per division — otherwise the scheduler
    # would be asked to put them on two courts at once.
    existing = list(
        (
            await session.exec(select(Entry).where(Entry.division_id == division.id))
        ).all()
    )
    taken: set[str] = set()
    for entry in existing:
        links = (
            await session.exec(
                select(EntryPlayer).where(EntryPlayer.entry_id == entry.id)
            )
        ).all()
        taken.update(link.player_id for link in links)
    clash = taken.intersection(body.player_ids)
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"Already entered in this division: {', '.join(sorted(clash))}",
        )

    entry = Entry(
        division_id=division.id,
        name=body.name or " & ".join(p.name.split(" ")[0] for p in players),
        seed=body.seed,
    )
    session.add(entry)
    await session.flush()
    for position, player in enumerate(players):
        session.add(
            EntryPlayer(entry_id=entry.id, player_id=player.id, position=position)
        )
    await session.commit()
    return await _entry_out(session, entry)


@router.patch("/entries/{entry_id}/status", response_model=EntryOut)
async def set_entry_status(
    entry_id: str, body: EntryStatusUpdate, session: SessionDep, user: CurrentUser
) -> EntryOut:
    entry = await session.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    division = await session.get(Division, entry.division_id)
    if division is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    from app.models import Tournament

    tournament = await session.get(Tournament, division.tournament_id)
    if tournament is None or tournament.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Entry not found")

    entry.status = body.status
    session.add(entry)
    await session.commit()

    # A withdrawal mid-draw changes who can advance, so recompute.
    if division.draw_generated and body.status is EntryStatus.WITHDRAWN:
        await refresh_statuses(session, division)
    return await _entry_out(session, entry)


# ---------------------------------------------------------------------------
# Draw and standings
# ---------------------------------------------------------------------------


async def _match_out(session: SessionDep, match: Match) -> MatchOut:
    async def label(
        entry_id: str | None, bye: bool, source: dict[str, Any] | None
    ) -> str:
        if bye:
            return "BYE"
        if entry_id:
            entry = await session.get(Entry, entry_id)
            return entry.name if entry else entry_id
        if source:
            if source.get("kind") == "pool_rank":
                return f"{source.get('pool')}{source.get('rank')}"
            return f"{source.get('kind')} of {source.get('match_id')}"
        return "?"

    a_entry = await session.get(Entry, match.entry_a_id) if match.entry_a_id else None
    b_entry = await session.get(Entry, match.entry_b_id) if match.entry_b_id else None
    return MatchOut(
        id=match.id,
        division_id=match.division_id,
        draw_match_id=match.draw_match_id,
        bracket=match.bracket,
        round=match.round,
        slot=match.slot,
        pool=match.pool,
        label=match.label,
        status=match.status,
        entry_a_id=match.entry_a_id,
        entry_b_id=match.entry_b_id,
        entry_a_name=a_entry.name if a_entry else None,
        entry_b_name=b_entry.name if b_entry else None,
        a_label=await label(match.entry_a_id, match.bye_a, match.source_a),
        b_label=await label(match.entry_b_id, match.bye_b, match.source_b),
        court_id=match.court_id,
        winner_entry_id=match.winner_entry_id,
        decides_title=match.decides_title,
    )


@router.post("/divisions/{division_id}/draw", response_model=DrawOut, status_code=201)
async def generate_draw(
    division: OwnedDivision, session: SessionDep, replace: bool = False
) -> DrawOut:
    try:
        await generate_and_persist(session, division, replace=replace)
    except DrawError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await read_draw(division, session)


@router.get("/divisions/{division_id}/draw", response_model=DrawOut)
async def read_draw(division: OwnedDivision, session: SessionDep) -> DrawOut:
    matches = list(
        (
            await session.exec(
                select(Match)
                .where(Match.division_id == division.id)
                .order_by(col(Match.round), col(Match.slot))
            )
        ).all()
    )
    from app.services.draw_service import division_pools

    return DrawOut(
        division_id=division.id,
        kind=division.draw_kind.value,
        pools=await division_pools(session, division.id),
        matches=[await _match_out(session, m) for m in matches],
    )


@router.get("/divisions/{division_id}/standings", response_model=list[StandingsOut])
async def read_standings(
    division: OwnedDivision, session: SessionDep
) -> list[StandingsOut]:
    tables = await standings_for(session, division)
    out: list[StandingsOut] = []
    for label, table in sorted(tables.items()):
        rows: list[StandingOut] = []
        for row in table.rows:
            entry = await session.get(Entry, row.entry_id)
            rows.append(
                StandingOut(
                    entry_id=row.entry_id,
                    entry_name=entry.name if entry else row.entry_id,
                    rank=row.rank,
                    played=row.played,
                    wins=row.wins,
                    losses=row.losses,
                    games_won=row.games_won,
                    games_lost=row.games_lost,
                    points_for=row.points_for,
                    points_against=row.points_against,
                    point_diff=row.point_diff,
                    unresolved_tie=row.unresolved_tie,
                    decided_by=row.decided_by,
                )
            )
        out.append(StandingsOut(pool=label or None, rows=rows))
    return out
