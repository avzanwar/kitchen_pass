"""Resolve an import plan against the database, and apply it.

Two steps, and the split is the whole point of the feature:

* `resolve` reads the existing roster and divisions and annotates the plan —
  which players already exist, which divisions would be added to rather than
  created — adding problems for conflicts only the database can see. It writes
  nothing, so the preview and the commit run identical code.
* `apply` performs the writes, in one transaction. It re-runs `resolve` rather
  than trusting a plan resolved earlier, because the roster can change between
  the organizer's preview and their confirmation.

The whole import is all-or-nothing. A half-created division is worse than a
rejected file: the organizer cannot tell what is missing without auditing every
row by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.imports.plan import ImportPlan, PlannedDivision, name_key
from app.models import (
    Division,
    DivisionFormat,
    DrawKind,
    Entry,
    EntryPlayer,
    Player,
    Tournament,
    User,
)
from app.services.slugs import unique_slug


class ImportRejected(Exception):
    """The plan cannot be applied. Carries the plan so problems reach the client."""

    def __init__(self, message: str, plan: ImportPlan) -> None:
        super().__init__(message)
        self.plan = plan


@dataclass
class ImportOutcome:
    tournament: Tournament
    tournament_created: bool
    divisions_created: int
    divisions_reused: int
    entries_created: int
    players_created: int
    players_matched: int


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


async def resolve(
    session: AsyncSession,
    plan: ImportPlan,
    user: User,
    tournament: Tournament | None,
) -> ImportPlan:
    """Annotate `plan` with what already exists. Adds problems; writes nothing."""
    roster = await _roster(session, user)
    for planned in plan.divisions:
        for entry in planned.entries:
            for player in entry.players:
                existing = roster.get(name_key(player.name))
                player.existing_id = existing.id if existing else None

    if tournament is None:
        return plan

    existing_divisions = {
        name_key(d.name): d
        for d in (
            await session.exec(
                select(Division).where(Division.tournament_id == tournament.id)
            )
        ).all()
    }

    for planned in plan.divisions:
        current = existing_divisions.get(name_key(planned.name))
        if current is None:
            continue
        planned.existing_id = current.id

        if current.draw_generated:
            plan.error(
                f"{planned.name!r} already exists in this tournament and its draw "
                f"has been generated — regenerate the draw to change the field",
                planned.row,
            )
            continue
        if current.format.value != planned.format:
            plan.error(
                f"{planned.name!r} already exists as {current.format.value} but the "
                f"sheet says {planned.format} — rename the division or fix the sheet",
                planned.row,
            )
            continue

        plan.warn(
            f"{planned.name!r} already exists in this tournament — its "
            f"{len(planned.entries)} team(s) will be added to it",
            planned.row,
        )
        await _check_existing_entries(session, plan, planned, current, roster)

    return plan


async def _roster(session: AsyncSession, user: User) -> dict[str, Player]:
    """The organizer's players keyed by lowercased name.

    Where a name is duplicated in the roster, the oldest wins — matching the
    person who has been played with longest is the less surprising guess, and it
    keeps the choice stable between the preview and the commit.
    """
    players = (
        await session.exec(
            select(Player)
            .where(Player.owner_id == user.id)
            .order_by(col(Player.created_at))
        )
    ).all()
    out: dict[str, Player] = {}
    for player in players:
        out.setdefault(name_key(player.name), player)
    return out


async def _check_existing_entries(
    session: AsyncSession,
    plan: ImportPlan,
    planned: PlannedDivision,
    division: Division,
    roster: dict[str, Player],
) -> None:
    """Refuse to enter someone twice in a division that already has teams."""
    entries = (
        await session.exec(select(Entry).where(Entry.division_id == division.id))
    ).all()
    #: player id -> the team they already play for in this division.
    taken: dict[str, str] = {}
    for existing_entry in entries:
        links = (
            await session.exec(
                select(EntryPlayer).where(EntryPlayer.entry_id == existing_entry.id)
            )
        ).all()
        for link in links:
            player = await session.get(Player, link.player_id)
            if player is not None:
                taken[player.id] = existing_entry.name

    for planned_entry in planned.entries:
        for wanted in planned_entry.players:
            known = roster.get(name_key(wanted.name))
            if known is not None and known.id in taken:
                plan.error(
                    f"{wanted.name} is already registered in {planned.name!r} "
                    f"as part of {taken[known.id]!r}",
                    planned_entry.row,
                )


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


async def apply(
    session: AsyncSession,
    plan: ImportPlan,
    user: User,
    *,
    tournament: Tournament | None,
    tournament_name: str | None,
) -> ImportOutcome:
    """Create everything in the plan. Raises `ImportRejected` if it cannot."""
    plan = await resolve(session, plan, user, tournament)
    if not plan.ok:
        raise ImportRejected("The sheet has errors that must be fixed first", plan)

    created_tournament = False
    if tournament is None:
        name = (tournament_name or "").strip()
        if not name:
            raise ImportRejected("A name is needed for the new tournament", plan)
        tournament = Tournament(
            name=name, owner_id=user.id, slug=await unique_slug(session, name)
        )
        session.add(tournament)
        await session.flush()
        created_tournament = True

    roster = await _roster(session, user)
    players_created = 0
    players_matched = 0
    # Players are created once per distinct name across the whole sheet, so the
    # same person entered in three divisions is one roster row, not three.
    resolved: dict[str, Player] = {}
    for key, display in plan.player_names.items():
        existing = roster.get(key)
        if existing is not None:
            resolved[key] = existing
            players_matched += 1
            continue
        player = Player(
            name=display,
            owner_id=user.id,
            avatar={"type": "initials", "color": _colour_for(display)},
        )
        session.add(player)
        resolved[key] = player
        players_created += 1
    await session.flush()

    # Ratings are only written onto players this import created. Overwriting a
    # rating the organizer already curated, from a column they may have left at
    # a stale value, is not a change an import should make on its own.
    for planned in plan.divisions:
        for planned_entry in planned.entries:
            for wanted in planned_entry.players:
                target = resolved[name_key(wanted.name)]
                if wanted.rating is not None and target.rating is None:
                    target.rating = wanted.rating
                    session.add(target)

    divisions_created = 0
    divisions_reused = 0
    entries_created = 0

    for planned in plan.divisions:
        if planned.existing_id is not None:
            found = await session.get(Division, planned.existing_id)
            if found is None:  # deleted between preview and commit
                raise ImportRejected(
                    f"{planned.name!r} was deleted while the import was being "
                    f"prepared — upload the sheet again", plan
                )
            division = found
            divisions_reused += 1
        else:
            division = Division(
                tournament_id=tournament.id,
                name=planned.name,
                format=DivisionFormat(planned.format),
                skill_bracket=planned.skill,
                age_bracket=planned.age,
                draw_kind=DrawKind(planned.draw_kind),
                draw_config=planned.draw_config,
                match_config=planned.match_config,
            )
            session.add(division)
            await session.flush()
            divisions_created += 1

        for planned_entry in planned.entries:
            entry = Entry(
                division_id=division.id,
                name=planned_entry.name,
                seed=planned_entry.seed,
            )
            session.add(entry)
            await session.flush()
            for position, wanted in enumerate(planned_entry.players):
                session.add(
                    EntryPlayer(
                        entry_id=entry.id,
                        player_id=resolved[name_key(wanted.name)].id,
                        position=position,
                    )
                )
            entries_created += 1

    await session.commit()
    await session.refresh(tournament)

    return ImportOutcome(
        tournament=tournament,
        tournament_created=created_tournament,
        divisions_created=divisions_created,
        divisions_reused=divisions_reused,
        entries_created=entries_created,
        players_created=players_created,
        players_matched=players_matched,
    )


_PALETTE = (
    "#0E7C6B", "#EA6D3A", "#3B7DC4", "#B4529E",
    "#D99A00", "#5B8A3A", "#C0453B", "#6D5BD0",
)


def _colour_for(name: str) -> str:
    """The same initials-avatar colour rule the manual player form uses."""
    return _PALETTE[len(name.strip()) % len(_PALETTE)]
