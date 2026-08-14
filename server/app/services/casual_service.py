"""Pickup games — a real match, in a container nobody sees.

Four people turn up and want to play one game. That is not a tournament, but it
is exactly the same scoring problem, so the cheapest correct answer is to make a
casual match a *real* `Match` and reuse everything: the engine, the offline
outbox, undo, leases, the live feed, resume-after-closing-the-app.

The shape that makes that work: one hidden `Tournament` per user, and **one
`Division` per pickup game**. A division per game is not a workaround — the
settings a pickup game chooses (format, scoring mode, target, win-by-2, best-of)
are precisely the fields of `Division.match_config`, which is where the engine
already reads them from.

The alternative, making `Match.division_id` nullable, would mean a null branch in
every read path that walks Match -> Division -> Tournament to check ownership.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    Division,
    DivisionFormat,
    DrawKind,
    Entry,
    EntryPlayer,
    Game,
    Match,
    MatchStatus,
    Player,
    Tournament,
    TournamentKind,
    TournamentStatus,
    User,
)
from app.schemas import CasualMatchIn, CasualTeamIn
from app.services.slugs import unique_slug

CASUAL_TOURNAMENT_NAME = "Pickup games"
#: Every casual division holds exactly one match, so the slot id is a constant.
CASUAL_DRAW_MATCH_ID = "CASUAL"

_PALETTE = (
    "#0E7C6B", "#EA6D3A", "#3B7DC4", "#B4529E",
    "#D99A00", "#5B8A3A", "#C0453B", "#6D5BD0",
)


class CasualError(ValueError):
    """The pickup game cannot be set up as asked."""


def _roster_size(fmt: DivisionFormat) -> int:
    return 1 if fmt is DivisionFormat.SINGLES else 2


def _colour_for(name: str) -> str:
    return _PALETTE[len(name.strip()) % len(_PALETTE)]


# ---------------------------------------------------------------------------
# The hidden container
# ---------------------------------------------------------------------------


async def casual_tournament(session: AsyncSession, user: User) -> Tournament:
    """Get or create the user's hidden pickup-game container."""
    existing = (
        await session.exec(
            select(Tournament).where(
                Tournament.owner_id == user.id,
                Tournament.kind == TournamentKind.CASUAL,
            )
        )
    ).first()
    if existing is not None:
        return existing

    tournament = Tournament(
        name=CASUAL_TOURNAMENT_NAME,
        owner_id=user.id,
        slug=await unique_slug(session, f"pickup-{user.id[:8]}"),
        kind=TournamentKind.CASUAL,
        status=TournamentStatus.LIVE,
    )
    session.add(tournament)
    await session.flush()
    return tournament


def is_casual_division(division: Division) -> bool:
    return division.draw_kind is DrawKind.ROUND_ROBIN and division.draw_config.get(
        "casual"
    ) is True


# ---------------------------------------------------------------------------
# Creating a game
# ---------------------------------------------------------------------------


async def _resolve_players(
    session: AsyncSession, user: User, team: CasualTeamIn, need: int
) -> list[Player]:
    if len(team.players) != need:
        raise CasualError(f"this format needs {need} player(s) per side")

    players: list[Player] = []
    for slot in team.players:
        if slot.player_id:
            player = await session.get(Player, slot.player_id)
            if player is None or player.owner_id != user.id:
                raise CasualError(f"Player {slot.player_id} not found")
            players.append(player)
            continue

        # A typed name always becomes a NEW guest row. Never match on name: that
        # is exactly how the prototype collapsed two players called "Mike" into
        # one serve-stat bucket. Picking the same guest twice is done from the
        # recent-guests list, which is a deliberate act rather than a string
        # coincidence.
        name = (slot.name or "").strip()
        guest = Player(
            name=name,
            owner_id=user.id,
            is_guest=True,
            avatar={"type": "initials", "color": _colour_for(name)},
        )
        session.add(guest)
        players.append(guest)

    await session.flush()
    if len({p.id for p in players}) != len(players):
        raise CasualError("A team cannot list the same player twice")
    return players


def _team_name(explicit: str | None, players: list[Player]) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    # Same convention as tournament entries, so a pickup team and a registered
    # one read the same way on the scoreboard.
    return " & ".join(p.name.split(" ")[0] for p in players)


def build_match_config(spec: CasualMatchIn) -> dict[str, Any]:
    """The engine config for a pickup game.

    `games_to` is a single entry because pickup play keeps one target for the
    whole match — the engine repeats the last entry, so [11] means every game to
    11 regardless of best_of.
    """
    config: dict[str, Any] = {
        "format": "singles" if spec.format is DivisionFormat.SINGLES else "doubles",
        "scoring": spec.scoring,
        "best_of": spec.best_of,
        "games_to": [spec.target],
        "win_by_2": spec.win_by_2,
        "switch_ends": "deciding_game",
    }
    if spec.scoring == "rally" and spec.freeze_at is not None:
        config["freeze_at"] = spec.freeze_at
    return config


async def create_casual_match(
    session: AsyncSession, user: User, spec: CasualMatchIn
) -> Match:
    """Set up a pickup game and return its Match, ready to score."""
    if spec.scoring != "rally" and spec.freeze_at is not None:
        raise CasualError("freeze_at applies to rally scoring only")

    need = _roster_size(spec.format)
    a_players = await _resolve_players(session, user, spec.a, need)
    b_players = await _resolve_players(session, user, spec.b, need)

    overlap = {p.id for p in a_players} & {p.id for p in b_players}
    if overlap:
        raise CasualError("A player cannot be on both teams")

    tournament = await casual_tournament(session, user)
    a_name = _team_name(spec.a.name, a_players)
    b_name = _team_name(spec.b.name, b_players)

    division = Division(
        tournament_id=tournament.id,
        name=f"{a_name} vs {b_name}",
        format=spec.format,
        draw_kind=DrawKind.ROUND_ROBIN,
        # `casual` marks the division so scoring can skip draw resolution;
        # `draw_generated` stops anything offering to generate a draw for it.
        draw_config={"casual": True},
        match_config=build_match_config(spec),
        draw_generated=True,
    )
    session.add(division)
    await session.flush()

    entries: list[Entry] = []
    for name, players in ((a_name, a_players), (b_name, b_players)):
        entry = Entry(division_id=division.id, name=name)
        session.add(entry)
        await session.flush()
        for position, player in enumerate(players):
            session.add(
                EntryPlayer(
                    entry_id=entry.id, player_id=player.id, position=position
                )
            )
        entries.append(entry)

    match = Match(
        division_id=division.id,
        draw_match_id=CASUAL_DRAW_MATCH_ID,
        bracket="casual",
        label="Pickup game",
        entry_a_id=entries[0].id,
        entry_b_id=entries[1].id,
        status=MatchStatus.READY,
        first_server=spec.first_server,
    )
    session.add(match)
    await session.commit()
    await session.refresh(match)
    return match


# ---------------------------------------------------------------------------
# Reading and deleting
# ---------------------------------------------------------------------------


async def _players_of(session: AsyncSession, entry_id: str | None) -> list[Player]:
    if entry_id is None:
        return []
    links = (
        await session.exec(
            select(EntryPlayer)
            .where(EntryPlayer.entry_id == entry_id)
            .order_by(col(EntryPlayer.position))
        )
    ).all()
    out: list[Player] = []
    for link in links:
        player = await session.get(Player, link.player_id)
        if player is not None:
            out.append(player)
    return out


async def casual_matches(
    session: AsyncSession, user: User, limit: int = 40
) -> list[dict[str, Any]]:
    """Recent pickup games, newest first, with their per-game scores."""
    tournament = (
        await session.exec(
            select(Tournament).where(
                Tournament.owner_id == user.id,
                Tournament.kind == TournamentKind.CASUAL,
            )
        )
    ).first()
    if tournament is None:
        return []

    divisions = list(
        (
            await session.exec(
                select(Division).where(Division.tournament_id == tournament.id)
            )
        ).all()
    )
    by_division = {d.id: d for d in divisions}
    if not by_division:
        return []

    matches = list(
        (
            await session.exec(
                select(Match)
                .where(col(Match.division_id).in_(list(by_division)))
                .order_by(col(Match.created_at).desc())
                .limit(limit)
            )
        ).all()
    )

    out: list[dict[str, Any]] = []
    for match in matches:
        division = by_division[match.division_id]
        games = list(
            (
                await session.exec(
                    select(Game)
                    .where(Game.match_id == match.id)
                    .order_by(col(Game.game_number))
                )
            ).all()
        )
        a_players = await _players_of(session, match.entry_a_id)
        b_players = await _players_of(session, match.entry_b_id)
        a_entry = await session.get(Entry, match.entry_a_id) if match.entry_a_id else None
        b_entry = await session.get(Entry, match.entry_b_id) if match.entry_b_id else None

        winner: str | None = None
        if match.winner_entry_id == match.entry_a_id and match.entry_a_id:
            winner = "A"
        elif match.winner_entry_id == match.entry_b_id and match.entry_b_id:
            winner = "B"

        config = division.match_config or {}
        out.append(
            {
                "match_id": match.id,
                "division_id": division.id,
                "status": match.status,
                "format": str(config.get("format", "doubles")),
                "scoring": str(config.get("scoring", "sideout")),
                "target": int((config.get("games_to") or [11])[0]),
                "best_of": int(config.get("best_of", 1)),
                "created_at": match.created_at,
                "a_name": a_entry.name if a_entry else "Team A",
                "b_name": b_entry.name if b_entry else "Team B",
                "a_players": a_players,
                "b_players": b_players,
                "winner": winner,
                "games_won": {
                    "A": sum(1 for g in games if g.winner == "A"),
                    "B": sum(1 for g in games if g.winner == "B"),
                },
                "games": [{"a": g.score_a, "b": g.score_b} for g in games],
            }
        )
    return out


async def delete_casual_match(
    session: AsyncSession, user: User, match_id: str
) -> None:
    """Remove a pickup game, and any guests it leaves behind.

    Deleting the division cascades to the entries, the match and its rally log.
    Guests are then only worth keeping if some other game still references them,
    so unreferenced ones go too — otherwise every stranger who ever played would
    accumulate forever in a list nobody can prune.
    """
    match = await session.get(Match, match_id)
    if match is None:
        raise CasualError("Match not found")
    division = await session.get(Division, match.division_id)
    if division is None or not is_casual_division(division):
        raise CasualError("Match not found")
    tournament = await session.get(Tournament, division.tournament_id)
    if (
        tournament is None
        or tournament.owner_id != user.id
        or tournament.kind is not TournamentKind.CASUAL
    ):
        raise CasualError("Match not found")

    guest_ids = [
        p.id
        for p in (
            await _players_of(session, match.entry_a_id)
            + await _players_of(session, match.entry_b_id)
        )
        if p.is_guest
    ]

    await session.delete(division)
    await session.flush()

    for guest_id in guest_ids:
        still_used = (
            await session.exec(
                select(EntryPlayer).where(EntryPlayer.player_id == guest_id)
            )
        ).first()
        if still_used is None:
            guest = await session.get(Player, guest_id)
            if guest is not None:
                await session.delete(guest)

    await session.commit()
