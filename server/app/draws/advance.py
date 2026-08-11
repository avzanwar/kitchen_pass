"""Resolve a draw against the results so far.

Draws are emitted with unresolved slots ("winner of W-R1-M3"). This walks the
dependency graph to a fixpoint, filling in slots as results arrive, completing
byes automatically and skipping conditional matches whose condition failed.

Pure and idempotent: feed it the same results and you get the same bracket.
Phase 4 calls it after every completed match to work out what is now playable.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from .types import Draw, DrawMatch, Slot


class MatchStatus(StrEnum):
    #: At least one slot still depends on an unplayed match.
    PENDING = "pending"
    #: Both entries known, waiting to be played.
    READY = "ready"
    #: Result recorded.
    COMPLETE = "complete"
    #: Walkover — one side is a bye, the other advances without playing.
    BYE = "bye"
    #: A conditional match whose condition turned out false (e.g. no reset
    #: needed because the winners-bracket team held on).
    SKIPPED = "skipped"


class ResolvedMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    bracket: str
    round: int
    slot: int
    pool: str | None = None
    label: str | None = None
    a_entry: str | None = None
    b_entry: str | None = None
    winner: str | None = None
    loser: str | None = None
    status: MatchStatus = MatchStatus.PENDING


class UnknownWinner(ValueError):
    """A recorded winner is not one of the match's participants."""


def resolve_draw(
    draw: Draw,
    winners: dict[str, str] | None = None,
    pool_ranks: dict[tuple[str, int], str] | None = None,
) -> dict[str, ResolvedMatch]:
    """Resolve every match in `draw`.

    `winners` maps match id -> winning entry id for matches that have been
    played. `pool_ranks` maps (pool label, 1-based rank) -> entry id, which is
    how pool-play results feed a playoff bracket; get it from
    `standings.pool_rank_map`.
    """
    winners = winners or {}
    ranks = pool_ranks or {}

    resolved: dict[str, ResolvedMatch] = {
        m.id: ResolvedMatch(
            id=m.id, bracket=m.bracket, round=m.round, slot=m.slot, pool=m.pool,
            label=m.label,
        )
        for m in draw.matches
    }
    index = {m.id: m for m in draw.matches}

    # Fixpoint. Bounded by the number of matches: each pass must settle at least
    # one match or we are done.
    for _ in range(len(draw.matches) + 2):
        changed = False
        for match in draw.matches:
            if _settle(match, resolved[match.id], resolved, winners, ranks):
                changed = True
        if not changed:
            break

    _validate_recorded_winners(winners, resolved, index)
    return resolved


def _settle(
    match: DrawMatch,
    state: ResolvedMatch,
    resolved: dict[str, ResolvedMatch],
    winners: dict[str, str],
    ranks: dict[tuple[str, int], str],
) -> bool:
    before = state.model_dump()

    if state.status in (MatchStatus.COMPLETE, MatchStatus.SKIPPED):
        return False

    if match.conditional and match.condition is not None:
        parent = resolved.get(match.condition.match_id)
        if parent is None or parent.winner is None:
            return False
        # `slot_a_lost`: play only if the entry that held slot A lost.
        if parent.a_entry is not None and parent.winner == parent.a_entry:
            state.status = MatchStatus.SKIPPED
            return state.model_dump() != before

    a_bye = _slot_is_bye(match.a, resolved)
    b_bye = _slot_is_bye(match.b, resolved)

    state.a_entry = None if a_bye else _resolve_slot(match.a, resolved, ranks)
    state.b_entry = None if b_bye else _resolve_slot(match.b, resolved, ranks)

    if a_bye and b_bye:
        # Both sides empty — nothing to play and nothing to advance.
        state.status = MatchStatus.SKIPPED
        return state.model_dump() != before

    if a_bye or b_bye:
        advancing = state.b_entry if a_bye else state.a_entry
        if advancing is not None:
            state.winner = advancing
            state.loser = None
            state.status = MatchStatus.BYE
        else:
            state.status = MatchStatus.PENDING
        return state.model_dump() != before

    if state.a_entry is not None and state.b_entry is not None:
        recorded = winners.get(match.id)
        if recorded is not None:
            state.winner = recorded
            state.loser = (
                state.b_entry if recorded == state.a_entry else state.a_entry
            )
            state.status = MatchStatus.COMPLETE
        else:
            state.status = MatchStatus.READY
    else:
        state.status = MatchStatus.PENDING

    return state.model_dump() != before


def _slot_is_bye(slot: Slot, resolved: dict[str, ResolvedMatch]) -> bool:
    """Whether this slot will never be filled.

    Statically true for a padded bracket slot. Also true *dynamically* in a
    double-elimination draw: a bye in the winners bracket produces no loser to
    drop down, so the losers-bracket slot fed by it is itself a bye. Without
    this the losers bracket deadlocks on every non-power-of-two field.
    """
    if slot.bye:
        return True
    if slot.source is None or not slot.source.match_id:
        return False
    parent = resolved.get(slot.source.match_id)
    if parent is None:
        return False
    if parent.status is MatchStatus.SKIPPED:
        return True
    return slot.source.kind == "loser" and parent.status is MatchStatus.BYE


def _resolve_slot(
    slot: Slot, resolved: dict[str, ResolvedMatch], ranks: dict[tuple[str, int], str]
) -> str | None:
    if slot.entry_id is not None:
        return slot.entry_id
    if slot.bye or slot.source is None:
        return None

    source = slot.source
    if source.kind == "pool_rank":
        if source.pool is None or source.rank is None:
            return None
        return ranks.get((source.pool, source.rank))

    parent = resolved.get(source.match_id or "")
    if parent is None:
        return None
    if source.kind == "winner":
        return parent.winner
    if source.kind == "loser":
        # A bye has no loser, so nothing drops into the losers bracket from it.
        return parent.loser
    return None


def _validate_recorded_winners(
    winners: dict[str, str],
    resolved: dict[str, ResolvedMatch],
    index: dict[str, DrawMatch],
) -> None:
    for match_id, winner in winners.items():
        if match_id not in index:
            raise UnknownWinner(f"{match_id} is not part of this draw")
        state = resolved[match_id]
        if state.status is MatchStatus.SKIPPED:
            raise UnknownWinner(
                f"{match_id} was not played (condition not met) but has a result"
            )
        if state.a_entry is None or state.b_entry is None:
            continue  # upstream not resolved yet; nothing to check against
        if winner not in (state.a_entry, state.b_entry):
            raise UnknownWinner(
                f"{match_id}: {winner!r} did not play "
                f"({state.a_entry!r} vs {state.b_entry!r})"
            )


def playable(resolved: dict[str, ResolvedMatch]) -> list[ResolvedMatch]:
    """Matches that can be sent to a court right now."""
    return sorted(
        (m for m in resolved.values() if m.status is MatchStatus.READY),
        key=lambda m: (m.round, m.slot),
    )


def champion(draw: Draw, resolved: dict[str, ResolvedMatch]) -> str | None:
    """Overall winner, or None while the title is still open.

    Driven by the `decides_title` flag rather than by round numbers: rounds are
    numbered per bracket, and a third-place match sits in the same round as the
    final. In double elimination both grand finals are title matches, and the
    reset only counts when it was actually required.
    """
    titles = [resolved[m.id] for m in draw.matches if m.decides_title]
    if not titles:
        return None  # e.g. a pure round robin — the champion comes from standings
    if any(t.status in (MatchStatus.READY, MatchStatus.PENDING) for t in titles):
        return None
    decided = [t for t in titles if t.status in (MatchStatus.COMPLETE, MatchStatus.BYE)]
    if not decided:
        return None
    return max(decided, key=lambda t: (t.round, t.slot)).winner
