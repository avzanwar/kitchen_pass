"""Court board and auto-assignment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlmodel import col, select

from app.api.deps import CurrentUser, OwnedTournament, SessionDep
from app.models import (
    Court,
    CourtStatus,
    Division,
    Entry,
    EntryPlayer,
    Match,
    MatchStatus,
    Tournament,
)
from app.scheduling.assigner import Playable, assign_courts, find_conflicts

router = APIRouter(tags=["courts"])


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Limit to one division; omit to schedule the whole tournament.
    division_id: str | None = None
    rest_waves: int = 1
    #: Preview without writing court assignments.
    dry_run: bool = False


async def _entry_players(session: SessionDep, entry_id: str | None) -> list[str]:
    if entry_id is None:
        return []
    links = await session.exec(
        select(EntryPlayer).where(EntryPlayer.entry_id == entry_id)
    )
    return [link.player_id for link in links.all()]


async def _tournament_matches(
    session: SessionDep, tournament: Tournament, division_id: str | None = None
) -> list[tuple[Match, Division]]:
    divisions = list(
        (
            await session.exec(
                select(Division).where(Division.tournament_id == tournament.id)
            )
        ).all()
    )
    if division_id:
        divisions = [d for d in divisions if d.id == division_id]
    by_id = {d.id: d for d in divisions}
    if not by_id:
        return []

    matches = list(
        (
            await session.exec(
                select(Match)
                .where(col(Match.division_id).in_(list(by_id)))
                .order_by(col(Match.round), col(Match.slot))
            )
        ).all()
    )
    return [(m, by_id[m.division_id]) for m in matches]


async def _playable(
    session: SessionDep, pairs: list[tuple[Match, Division]]
) -> list[Playable]:
    out: list[Playable] = []
    for match, division in pairs:
        if match.status is not MatchStatus.READY:
            continue
        players = await _entry_players(session, match.entry_a_id)
        players += await _entry_players(session, match.entry_b_id)
        out.append(
            Playable(
                match_id=match.id,
                division_id=division.id,
                round=match.round,
                slot=match.slot,
                player_ids=players,
                label=match.label,
                # Pool play clears before playoffs, which depend on it.
                priority=0 if match.bracket == "pool" else 1,
            )
        )
    return out


@router.get("/tournaments/{tournament_id}/board")
async def court_board(
    tournament: OwnedTournament, session: SessionDep
) -> dict[str, Any]:
    """What is on each court right now, plus what is waiting."""
    courts = list(
        (
            await session.exec(
                select(Court)
                .where(Court.tournament_id == tournament.id)
                .order_by(col(Court.sort_order), col(Court.name))
            )
        ).all()
    )
    pairs = await _tournament_matches(session, tournament)
    by_court: dict[str, list[dict[str, Any]]] = {c.id: [] for c in courts}
    queue: list[dict[str, Any]] = []

    async def describe(match: Match, division: Division) -> dict[str, Any]:
        a = await session.get(Entry, match.entry_a_id) if match.entry_a_id else None
        b = await session.get(Entry, match.entry_b_id) if match.entry_b_id else None
        return {
            "match_id": match.id,
            "division_id": division.id,
            "division_name": division.name,
            "draw_match_id": match.draw_match_id,
            "label": match.label,
            "status": match.status.value,
            "a": a.name if a else match.draw_match_id,
            "b": b.name if b else "—",
        }

    for match, division in pairs:
        if match.status in (MatchStatus.LIVE, MatchStatus.READY) and match.court_id:
            if match.court_id in by_court:
                by_court[match.court_id].append(await describe(match, division))
        elif match.status is MatchStatus.READY:
            queue.append(await describe(match, division))

    return {
        "courts": [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status.value,
                "matches": by_court.get(c.id, []),
            }
            for c in courts
        ],
        "queue": queue,
        "conflicts": [
            c.model_dump() for c in find_conflicts(await _playable(session, pairs))
        ],
    }


@router.post("/tournaments/{tournament_id}/assign-courts")
async def auto_assign(
    body: AssignIn, tournament: OwnedTournament, session: SessionDep
) -> dict[str, Any]:
    """Pack every ready match onto a court, in waves."""
    courts = list(
        (
            await session.exec(
                select(Court)
                .where(
                    Court.tournament_id == tournament.id,
                    Court.status != CourtStatus.CLOSED,
                )
                .order_by(col(Court.sort_order), col(Court.name))
            )
        ).all()
    )
    pairs = await _tournament_matches(session, tournament, body.division_id)
    playable = await _playable(session, pairs)

    # Whatever is already being scored keeps its court and its players.
    busy_courts: list[str] = []
    busy_players: list[str] = []
    for match, _ in pairs:
        if match.status is MatchStatus.LIVE:
            if match.court_id:
                busy_courts.append(match.court_id)
            busy_players += await _entry_players(session, match.entry_a_id)
            busy_players += await _entry_players(session, match.entry_b_id)

    schedule = assign_courts(
        playable,
        [c.id for c in courts],
        rest_waves=body.rest_waves,
        busy_players=busy_players,
        busy_courts=busy_courts,
    )

    if not body.dry_run:
        first_wave = {a.match_id: a.court_id for a in schedule.assignments
                      if a.wave == 0}
        for match, _ in pairs:
            if match.id in first_wave:
                match.court_id = first_wave[match.id]
                session.add(match)
        await session.commit()

    court_names = {c.id: c.name for c in courts}
    return {
        "waves": max((a.wave for a in schedule.assignments), default=-1) + 1,
        "assignments": [
            {**a.model_dump(), "court_name": court_names.get(a.court_id, a.court_id)}
            for a in schedule.assignments
        ],
        "unplaced": [c.model_dump() for c in schedule.unplaced],
        "dry_run": body.dry_run,
    }


@router.patch("/matches/{match_id}/court")
async def set_match_court(
    match_id: str, session: SessionDep, user: CurrentUser, court_id: str | None = None
) -> dict[str, Any]:
    """Move a match to a court by hand, or clear it with no court_id."""
    match = await session.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    division = await session.get(Division, match.division_id)
    if division is None:
        raise HTTPException(status_code=404, detail="Match not found")
    tournament = await session.get(Tournament, division.tournament_id)
    if tournament is None or tournament.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Match not found")

    if court_id:
        court = await session.get(Court, court_id)
        if court is None or court.tournament_id != tournament.id:
            raise HTTPException(status_code=404, detail="Court not found")

    match.court_id = court_id
    session.add(match)
    await session.commit()
    return {"match_id": match.id, "court_id": match.court_id}
