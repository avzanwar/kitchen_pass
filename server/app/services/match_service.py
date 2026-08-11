"""Live match scoring: append events, fold state, project results.

The rally log is the source of truth. Everything else — the `Game` rows, the
match winner, bracket advancement — is derived from folding it, so a resync can
always rebuild the truth from the events alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    Division,
    Entry,
    EntryPlayer,
    Game,
    Match,
    MatchStatus,
    Player,
    RallyEvent,
)
from app.scoring import (
    Event,
    EventType,
    InvalidEvent,
    MatchConfig,
    MatchState,
    PlayerRef,
    TeamRoster,
    current_serve_side,
    current_server,
    fold,
    score_call,
)
from app.scoring.events import Team

#: How long a scorekeeper holds a match before another device may take over.
LEASE_MINUTES = 30


def _team(value: str | None) -> Team | None:
    """Narrow a stored/posted team string to the engine's Literal type."""
    if value is None:
        return None
    if value == "A":
        return "A"
    if value == "B":
        return "B"
    raise ScoringError(f"team must be 'A' or 'B', got {value!r}")


class ScoringError(ValueError):
    """The request cannot be applied to this match."""


class LeaseError(ScoringError):
    """Someone else is currently scoring this match."""


class SeqConflict(ScoringError):
    """The client's sequence numbers do not line up with the server's log."""

    def __init__(self, message: str, server_seq: int) -> None:
        super().__init__(message)
        self.server_seq = server_seq


# ---------------------------------------------------------------------------
# Building engine inputs from the database
# ---------------------------------------------------------------------------


async def _roster(session: AsyncSession, entry_id: str | None) -> TeamRoster | None:
    if entry_id is None:
        return None
    entry = await session.get(Entry, entry_id)
    if entry is None:
        return None
    links = list(
        (
            await session.exec(
                select(EntryPlayer)
                .where(EntryPlayer.entry_id == entry_id)
                .order_by(col(EntryPlayer.position))
            )
        ).all()
    )
    players: list[PlayerRef] = []
    for link in links:
        player = await session.get(Player, link.player_id)
        if player is not None:
            players.append(PlayerRef(id=player.id, name=player.name))
    return TeamRoster(name=entry.name, players=players)


async def match_config(
    session: AsyncSession, match: Match, division: Division | None = None
) -> MatchConfig:
    division = division or await session.get(Division, match.division_id)
    if division is None:
        raise ScoringError("division not found")

    raw: dict[str, Any] = dict(division.match_config or {})
    raw.setdefault(
        "format", "singles" if division.format.value == "singles" else "doubles"
    )
    # The coin toss is per match, not per division.
    raw["first_server"] = match.first_server
    return MatchConfig(**raw)


async def load_events(session: AsyncSession, match_id: str) -> list[RallyEvent]:
    return list(
        (
            await session.exec(
                select(RallyEvent)
                .where(RallyEvent.match_id == match_id)
                .order_by(col(RallyEvent.seq))
            )
        ).all()
    )


async def load_state(session: AsyncSession, match: Match) -> MatchState:
    """Fold the stored log into current match state."""
    config = await match_config(session, match)
    team_a = await _roster(session, match.entry_a_id)
    team_b = await _roster(session, match.entry_b_id)
    if team_a is None or team_b is None:
        raise ScoringError("both entries must be known before this match can be scored")

    rows = await load_events(session, match.id)
    events = [
        Event(type=EventType(r.type), team=_team(r.team),
              client_event_id=r.client_event_id, seq=r.seq)
        for r in rows
    ]
    teams: dict[Team, TeamRoster] = {"A": team_a, "B": team_b}
    return fold(config, teams, events)


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------


def lease_held_by_other(match: Match, user_id: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    if match.scorekeeper_id is None or match.scorekeeper_id == user_id:
        return False
    if match.lease_expires_at is None:
        return False
    expires = match.lease_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > now


async def claim_match(
    session: AsyncSession, match: Match, user_id: str, *, force: bool = False
) -> Match:
    if not force and lease_held_by_other(match, user_id):
        raise LeaseError(
            "another device is scoring this match; take over to continue"
        )
    match.scorekeeper_id = user_id
    match.lease_expires_at = datetime.now(UTC) + timedelta(minutes=LEASE_MINUTES)
    session.add(match)
    await session.commit()
    return match


async def release_match(session: AsyncSession, match: Match) -> Match:
    match.scorekeeper_id = None
    match.lease_expires_at = None
    session.add(match)
    await session.commit()
    return match


# ---------------------------------------------------------------------------
# Appending events
# ---------------------------------------------------------------------------


async def append_events(
    session: AsyncSession,
    match: Match,
    incoming: list[dict[str, Any]],
    *,
    actor_id: str | None,
) -> MatchState:
    """Append a batch of rally events and return the resulting state.

    Idempotent: events whose `client_event_id` is already stored are skipped, so
    a client that retries after a lost acknowledgement does not double-count.
    """
    if match.status in (MatchStatus.COMPLETE, MatchStatus.BYE, MatchStatus.SKIPPED):
        raise ScoringError(f"match is {match.status.value}")

    existing = await load_events(session, match.id)
    known = {r.client_event_id for r in existing}
    next_seq = max((r.seq for r in existing), default=0) + 1

    config = await match_config(session, match)
    team_a = await _roster(session, match.entry_a_id)
    team_b = await _roster(session, match.entry_b_id)
    if team_a is None or team_b is None:
        raise ScoringError("both entries must be known before this match can be scored")
    teams: dict[Team, TeamRoster] = {"A": team_a, "B": team_b}

    replay = [
        Event(type=EventType(r.type), team=_team(r.team),
              client_event_id=r.client_event_id)
        for r in existing
    ]

    accepted: list[RallyEvent] = []
    for raw in incoming:
        client_id = raw.get("client_event_id")
        if not client_id:
            raise ScoringError("every event needs a client_event_id")
        if client_id in known:
            continue  # already applied — a retry, not a new rally

        try:
            event = Event(type=EventType(raw["type"]), team=_team(raw.get("team")),
                          client_event_id=client_id)
        except (KeyError, ValueError) as exc:
            raise ScoringError(f"unknown event: {raw!r}") from exc

        # Validate against the engine before persisting, so an illegal event is
        # rejected rather than poisoning the log.
        try:
            fold(config, teams, [*replay, event])
        except InvalidEvent as exc:
            raise ScoringError(str(exc)) from exc

        replay.append(event)
        known.add(client_id)
        accepted.append(
            RallyEvent(
                match_id=match.id,
                seq=next_seq,
                client_event_id=client_id,
                type=event.type.value,
                team=event.team,
                actor_id=actor_id,
            )
        )
        next_seq += 1

    session.add_all(accepted)

    state = fold(config, teams, replay)
    await _project(session, match, state)
    await session.commit()
    return state


async def _project(session: AsyncSession, match: Match, state: MatchState) -> None:
    """Write the denormalised Game rows and match outcome from folded state."""
    rows = list(
        (
            await session.exec(select(Game).where(Game.match_id == match.id))
        ).all()
    )
    by_number = {row.game_number: row for row in rows}

    for game in state.games:
        row = by_number.pop(game.number, None)
        if row is None:
            row = Game(match_id=match.id, game_number=game.number, target=game.target)
        row.target = game.target
        row.score_a = game.score["A"]
        row.score_b = game.score["B"]
        row.status = game.status
        row.winner = game.winner
        session.add(row)

    # Undo can remove a game entirely; drop the stale projection with it.
    for orphan in by_number.values():
        await session.delete(orphan)

    if state.status == "complete" and state.winner:
        match.status = MatchStatus.COMPLETE
        match.winner_entry_id = (
            match.entry_a_id if state.winner == "A" else match.entry_b_id
        )
    elif state.status == "abandoned":
        match.status = MatchStatus.ABANDONED
        match.winner_entry_id = None
    else:
        match.status = MatchStatus.LIVE
        match.winner_entry_id = None
    session.add(match)


async def set_first_server(
    session: AsyncSession, match: Match, team: str
) -> Match:
    """Record the coin toss. Only legal before the first rally."""
    if team not in ("A", "B"):
        raise ScoringError("first server must be 'A' or 'B'")
    events = await load_events(session, match.id)
    if events:
        raise ScoringError("the match has already started")
    match.first_server = team
    session.add(match)
    await session.commit()
    return match


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def state_payload(match: Match, state: MatchState) -> dict[str, Any]:
    """The shape the scoreboard UI consumes."""
    game = state.current_game
    server = current_server(state)
    return {
        "match_id": match.id,
        "division_id": match.division_id,
        "status": state.status,
        "winner": state.winner,
        "winner_entry_id": match.winner_entry_id,
        "ended_early": state.ended_early,
        "forfeited_by": state.forfeited_by,
        "games_won": state.games_won,
        "config": state.config.model_dump(mode="json"),
        "teams": {
            "A": {**state.teams["A"].model_dump(), "entry_id": match.entry_a_id},
            "B": {**state.teams["B"].model_dump(), "entry_id": match.entry_b_id},
        },
        "games": [g.model_dump(mode="json") for g in state.games],
        "current": None
        if game is None
        else {
            "number": game.number,
            "target": game.target,
            "score": game.score,
            "serving_team": game.serving_team,
            "server_id": server.id if server else None,
            "server_name": server.name if server else None,
            "server_num": game.server_num,
            "side": current_serve_side(state),
            "call": score_call(state),
            "pos": game.pos,
            "timeouts_used": game.timeouts_used,
            "ends_swapped": game.ends_swapped,
        },
        "serve_points": state.serve_points,
        "serve_names": state.serve_names,
    }
