"""Unauthenticated spectator view, and CSV export.

Everything here is reached with the tournament's `public_token` — an
unguessable share link, not a password. It exposes draws, standings and the
court board, and nothing about accounts.
"""

from __future__ import annotations

import csv
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from sqlmodel import col, select

from app.api.deps import SessionDep, public_tournament
from app.models import Court, Division, Entry, Game, Match, MatchStatus, Tournament
from app.services.draw_service import division_pools, standings_for

router = APIRouter(prefix="/public", tags=["public"])

PublicTournament = Annotated[Tournament, Depends(public_tournament)]


async def _entry_names(session: SessionDep, division_id: str) -> dict[str, str]:
    entries = await session.exec(
        select(Entry).where(Entry.division_id == division_id)
    )
    return {e.id: e.name for e in entries.all()}


@router.get("/{public_token}")
async def overview(
    tournament: PublicTournament, session: SessionDep
) -> dict[str, Any]:
    divisions = list(
        (
            await session.exec(
                select(Division)
                .where(Division.tournament_id == tournament.id)
                .order_by(col(Division.name))
            )
        ).all()
    )
    return {
        "id": tournament.id,
        "name": tournament.name,
        "slug": tournament.slug,
        "starts_on": tournament.starts_on,
        "ends_on": tournament.ends_on,
        "status": tournament.status.value,
        "divisions": [
            {
                "id": d.id,
                "name": d.name,
                "format": d.format.value,
                "draw_kind": d.draw_kind.value,
                "draw_generated": d.draw_generated,
            }
            for d in divisions
        ],
    }


@router.get("/{public_token}/divisions/{division_id}")
async def public_division(
    public_token: str, division_id: str, tournament: PublicTournament,
    session: SessionDep,
) -> dict[str, Any]:
    division = await session.get(Division, division_id)
    if division is None or division.tournament_id != tournament.id:
        return {"error": "not found"}

    names = await _entry_names(session, division_id)
    matches = list(
        (
            await session.exec(
                select(Match)
                .where(Match.division_id == division_id)
                .order_by(col(Match.round), col(Match.slot))
            )
        ).all()
    )
    courts = {
        c.id: c.name
        for c in (
            await session.exec(
                select(Court).where(Court.tournament_id == tournament.id)
            )
        ).all()
    }

    rows: list[dict[str, Any]] = []
    for match in matches:
        games = list(
            (
                await session.exec(
                    select(Game)
                    .where(Game.match_id == match.id)
                    .order_by(col(Game.game_number))
                )
            ).all()
        )
        rows.append(
            {
                "id": match.id,
                "draw_match_id": match.draw_match_id,
                "bracket": match.bracket,
                "round": match.round,
                "slot": match.slot,
                "pool": match.pool,
                "label": match.label,
                "status": match.status.value,
                "court": courts.get(match.court_id or ""),
                "a": names.get(match.entry_a_id or "", "BYE" if match.bye_a else "—"),
                "b": names.get(match.entry_b_id or "", "BYE" if match.bye_b else "—"),
                "winner": names.get(match.winner_entry_id or ""),
                "games": [
                    {"n": g.game_number, "a": g.score_a, "b": g.score_b} for g in games
                ],
            }
        )

    tables = await standings_for(session, division)
    return {
        "id": division.id,
        "name": division.name,
        "format": division.format.value,
        "draw_kind": division.draw_kind.value,
        "pools": await division_pools(session, division_id),
        "matches": rows,
        "standings": [
            {
                "pool": label or None,
                "rows": [
                    {
                        **row.model_dump(),
                        "entry_name": names.get(row.entry_id, row.entry_id),
                        "point_diff": row.point_diff,
                    }
                    for row in table.rows
                ],
            }
            for label, table in sorted(tables.items())
        ],
    }


@router.get("/{public_token}/board")
async def public_board(
    tournament: PublicTournament, session: SessionDep
) -> dict[str, Any]:
    courts = list(
        (
            await session.exec(
                select(Court)
                .where(Court.tournament_id == tournament.id)
                .order_by(col(Court.sort_order), col(Court.name))
            )
        ).all()
    )
    divisions = {
        d.id: d
        for d in (
            await session.exec(
                select(Division).where(Division.tournament_id == tournament.id)
            )
        ).all()
    }
    matches = list(
        (
            await session.exec(
                select(Match).where(col(Match.division_id).in_(list(divisions)))
            )
        ).all()
    ) if divisions else []

    names: dict[str, str] = {}
    for division_id in divisions:
        names |= await _entry_names(session, division_id)

    def describe(match: Match) -> dict[str, Any]:
        return {
            "match_id": match.id,
            "division": divisions[match.division_id].name,
            "label": match.label,
            "status": match.status.value,
            "a": names.get(match.entry_a_id or "", "—"),
            "b": names.get(match.entry_b_id or "", "—"),
        }

    on_court: dict[str, list[dict[str, Any]]] = {c.id: [] for c in courts}
    for match in matches:
        if match.court_id in on_court and match.status in (
            MatchStatus.LIVE, MatchStatus.READY
        ):
            on_court[match.court_id].append(describe(match))

    return {
        "tournament": tournament.name,
        "courts": [
            {"id": c.id, "name": c.name, "matches": on_court.get(c.id, [])}
            for c in courts
        ],
    }


@router.get("/{public_token}/divisions/{division_id}/results.csv")
async def results_csv(
    public_token: str, division_id: str, tournament: PublicTournament,
    session: SessionDep,
) -> Response:
    division = await session.get(Division, division_id)
    if division is None or division.tournament_id != tournament.id:
        return Response(status_code=404, content="not found")

    names = await _entry_names(session, division_id)
    matches = list(
        (
            await session.exec(
                select(Match)
                .where(Match.division_id == division_id)
                .order_by(col(Match.round), col(Match.slot))
            )
        ).all()
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["match", "round", "pool", "team_a", "team_b", "status", "winner", "scores"]
    )
    for match in matches:
        games = list(
            (
                await session.exec(
                    select(Game)
                    .where(Game.match_id == match.id)
                    .order_by(col(Game.game_number))
                )
            ).all()
        )
        writer.writerow(
            [
                match.draw_match_id,
                match.round,
                match.pool or "",
                names.get(match.entry_a_id or "", "BYE" if match.bye_a else ""),
                names.get(match.entry_b_id or "", "BYE" if match.bye_b else ""),
                match.status.value,
                names.get(match.winner_entry_id or "", ""),
                " ".join(f"{g.score_a}-{g.score_b}" for g in games),
            ]
        )

    filename = f"{division.name.replace(' ', '_').lower()}_results.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{public_token}/divisions/{division_id}/standings.csv")
async def standings_csv(
    public_token: str, division_id: str, tournament: PublicTournament,
    session: SessionDep,
) -> Response:
    division = await session.get(Division, division_id)
    if division is None or division.tournament_id != tournament.id:
        return Response(status_code=404, content="not found")

    names = await _entry_names(session, division_id)
    tables = await standings_for(session, division)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["pool", "rank", "team", "played", "won", "lost", "points_for",
         "points_against", "diff", "decided_by", "unresolved_tie"]
    )
    for label, table in sorted(tables.items()):
        for row in table.rows:
            writer.writerow(
                [
                    label or "",
                    row.rank,
                    names.get(row.entry_id, row.entry_id),
                    row.played,
                    row.wins,
                    row.losses,
                    row.points_for,
                    row.points_against,
                    row.point_diff,
                    row.decided_by or "",
                    "yes" if row.unresolved_tie else "",
                ]
            )

    filename = f"{division.name.replace(' ', '_').lower()}_standings.csv"
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
