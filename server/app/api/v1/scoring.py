"""Live scoring: event append, leases, and the realtime feeds."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import col, select

from app.api.deps import CurrentUser, SessionDep
from app.core.db import session_factory
from app.core.security import TokenError, create_court_token, decode_token
from app.models import Division, Match, MatchStatus, Tournament
from app.realtime.hub import board_topic, hub, match_topic
from app.services.casual_service import is_casual_division
from app.services.draw_service import refresh_statuses
from app.services.match_service import (
    LeaseError,
    ScoringError,
    append_events,
    claim_match,
    lease_held_by_other,
    load_events,
    load_state,
    release_match,
    set_first_server,
    state_payload,
)

router = APIRouter(tags=["scoring"])


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    team: str | None = None
    #: Client-generated so a retried offline batch is idempotent.
    client_event_id: str = Field(min_length=1, max_length=64)


class EventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[EventIn] = Field(default_factory=list, max_length=500)


class TossIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_server: str


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Take a match from a scorekeeper whose lease is still live.
    force: bool = False


async def _match_for(session: SessionDep, match_id: str, user: CurrentUser) -> Match:
    match = await session.get(Match, match_id)
    if match is None:
        raise HTTPException(status_code=404, detail="Match not found")
    division = await session.get(Division, match.division_id)
    if division is None:
        raise HTTPException(status_code=404, detail="Match not found")
    tournament = await session.get(Tournament, division.tournament_id)
    if tournament is None or tournament.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


async def _broadcast(session: SessionDep, match: Match, payload: dict[str, Any]) -> None:
    await hub.publish(match_topic(match.id), payload)
    division = await session.get(Division, match.division_id)
    if division is not None:
        await hub.publish(
            board_topic(division.tournament_id),
            {"kind": "match", "match_id": match.id,
             "status": match.status.value, "division_id": match.division_id},
        )


@router.get("/matches/{match_id}")
async def read_match(
    match_id: str, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    match = await _match_for(session, match_id, user)
    try:
        state = await load_state(session, match)
    except ScoringError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    events = await load_events(session, match.id)
    payload = state_payload(match, state)
    payload["seq"] = max((e.seq for e in events), default=0)
    payload["lease"] = {
        "scorekeeper_id": match.scorekeeper_id,
        "held_by_other": lease_held_by_other(match, user.id),
        "expires_at": match.lease_expires_at,
    }
    return payload


@router.post("/matches/{match_id}/claim")
async def claim(
    match_id: str, body: ClaimIn, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    match = await _match_for(session, match_id, user)
    try:
        await claim_match(session, match, user.id, force=body.force)
    except LeaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"match_id": match.id, "expires_at": match.lease_expires_at}


@router.post("/matches/{match_id}/release")
async def release(
    match_id: str, session: SessionDep, user: CurrentUser
) -> dict[str, str]:
    match = await _match_for(session, match_id, user)
    await release_match(session, match)
    return {"match_id": match.id}


@router.post("/matches/{match_id}/toss")
async def toss(
    match_id: str, body: TossIn, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    match = await _match_for(session, match_id, user)
    try:
        await set_first_server(session, match, body.first_server)
    except ScoringError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"match_id": match.id, "first_server": match.first_server}


@router.post("/matches/{match_id}/events")
async def post_events(
    match_id: str, body: EventBatch, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    """Append rally events. Safe to retry: dedup is on `client_event_id`."""
    match = await _match_for(session, match_id, user)

    if lease_held_by_other(match, user.id):
        raise HTTPException(
            status_code=409,
            detail="another device is scoring this match; take over to continue",
        )

    was_open = match.status not in (MatchStatus.COMPLETE, MatchStatus.ABANDONED)
    try:
        state = await append_events(
            session,
            match,
            [e.model_dump() for e in body.events],
            actor_id=user.id,
        )
    except ScoringError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # A finished match unlocks whatever it feeds — the next bracket round, or a
    # playoff waiting on final pool standings.
    if was_open and match.status in (MatchStatus.COMPLETE, MatchStatus.ABANDONED):
        division = await session.get(Division, match.division_id)
        # A pickup game has no bracket to advance and no pool to rank, so draw
        # resolution has nothing to do — and running it over a division that was
        # never drawn is a class of surprise worth not having.
        if division is not None and not is_casual_division(division):
            await refresh_statuses(session, division)

    events = await load_events(session, match.id)
    payload = state_payload(match, state)
    payload["seq"] = max((e.seq for e in events), default=0)
    await _broadcast(session, match, payload)
    return payload


@router.get("/matches/{match_id}/court-code")
async def court_code(
    match_id: str, session: SessionDep, user: CurrentUser
) -> dict[str, Any]:
    """A short-lived token so a volunteer can score one match without an account."""
    match = await _match_for(session, match_id, user)
    division = await session.get(Division, match.division_id)
    assert division is not None
    return {
        "match_id": match.id,
        "token": create_court_token(match.id, division.tournament_id),
    }


# ---------------------------------------------------------------------------
# WebSockets
# ---------------------------------------------------------------------------


@router.websocket("/ws/matches/{match_id}")
async def match_feed(websocket: WebSocket, match_id: str) -> None:
    """Live scoreboard feed. Read-only and unauthenticated: a scoreboard on the
    fence is meant to be watchable, and it exposes nothing an organizer has not
    already put on the public draw."""
    await websocket.accept()
    topic = match_topic(match_id)
    await hub.subscribe(topic, websocket)

    try:
        async with session_factory()() as session:
            match = await session.get(Match, match_id)
            if match is not None:
                try:
                    state = await load_state(session, match)
                    await websocket.send_json(state_payload(match, state))
                except ScoringError:
                    await websocket.send_json({"match_id": match_id, "status": "pending"})

        while True:
            # No client-to-server protocol; this just parks until disconnect.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(topic, websocket)


@router.websocket("/ws/tournaments/{public_token}/board")
async def board_feed(websocket: WebSocket, public_token: str) -> None:
    """Court-board feed for the public view, keyed by the share token."""
    await websocket.accept()

    async with session_factory()() as session:
        result = await session.exec(
            select(Tournament).where(Tournament.public_token == public_token)
        )
        tournament = result.first()

    if tournament is None:
        await websocket.close(code=4404)
        return

    topic = board_topic(tournament.id)
    await hub.subscribe(topic, websocket)
    try:
        await websocket.send_json({"kind": "hello", "tournament_id": tournament.id})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unsubscribe(topic, websocket)


@router.get("/divisions/{division_id}/matches")
async def division_matches(
    division_id: str, session: SessionDep, user: CurrentUser
) -> list[dict[str, Any]]:
    """Match list with live scores, for the organizer's division view."""
    division = await session.get(Division, division_id)
    if division is None:
        raise HTTPException(status_code=404, detail="Division not found")
    tournament = await session.get(Tournament, division.tournament_id)
    if tournament is None or tournament.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Division not found")

    matches = list(
        (
            await session.exec(
                select(Match)
                .where(Match.division_id == division_id)
                .order_by(col(Match.round), col(Match.slot))
            )
        ).all()
    )
    out: list[dict[str, Any]] = []
    for match in matches:
        from app.models import Game

        games = list(
            (
                await session.exec(
                    select(Game)
                    .where(Game.match_id == match.id)
                    .order_by(col(Game.game_number))
                )
            ).all()
        )
        out.append(
            {
                "id": match.id,
                "draw_match_id": match.draw_match_id,
                "status": match.status.value,
                "round": match.round,
                "slot": match.slot,
                "pool": match.pool,
                "label": match.label,
                "court_id": match.court_id,
                "winner_entry_id": match.winner_entry_id,
                "games": [
                    {"n": g.game_number, "a": g.score_a, "b": g.score_b,
                     "status": g.status}
                    for g in games
                ],
            }
        )
    return out


def decode_court_token(token: str) -> dict[str, Any]:
    """Used by the volunteer scorekeeper flow."""
    try:
        return decode_token(token, "court")
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
