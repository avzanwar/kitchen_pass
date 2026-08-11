"""Pool standings and tiebreakers.

The prototype ranked teams by wins then raw point differential
(`kitchen-pass.jsx:1057`). That disagrees with USA Pickleball, which resolves
ties head-to-head first. This implements the sanctioned order as a *configurable
chain*, applied recursively so that a three-way tie broken partway through is
then re-broken among the teams that are still level.

Ties that no criterion can separate are reported honestly as `unresolved`
rather than silently ordered — that is an organizer's coin flip to make, not
ours.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Tiebreaker = Literal[
    "head_to_head", "point_diff", "points_allowed", "game_diff", "wins"
]

#: USA Pickleball pool-play order, after match wins.
DEFAULT_TIEBREAKERS: tuple[Tiebreaker, ...] = (
    "head_to_head",
    "point_diff",
    "points_allowed",
)


class MatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_id: str
    a_entry: str
    b_entry: str
    winner: str
    #: Total points across every game of the match.
    points_a: int = 0
    points_b: int = 0
    games_a: int = 0
    games_b: int = 0
    pool: str | None = None
    forfeit: bool = False

    @property
    def loser(self) -> str:
        return self.b_entry if self.winner == self.a_entry else self.a_entry


class Standing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    played: int = 0
    wins: int = 0
    losses: int = 0
    games_won: int = 0
    games_lost: int = 0
    points_for: int = 0
    points_against: int = 0
    rank: int = 0
    #: True when this entry could not be separated from its neighbours by any
    #: configured criterion — the organizer needs to break it manually.
    unresolved_tie: bool = False
    #: Which criterion decided this entry's position, for display.
    decided_by: str | None = None

    @property
    def point_diff(self) -> int:
        return self.points_for - self.points_against

    @property
    def game_diff(self) -> int:
        return self.games_won - self.games_lost


class StandingsTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pool: str | None = None
    rows: list[Standing] = Field(default_factory=list)

    def entry_at(self, rank: int) -> str | None:
        for row in self.rows:
            if row.rank == rank:
                return row.entry_id
        return None


def compute_standings(
    entry_ids: Sequence[str],
    results: Sequence[MatchResult],
    *,
    tiebreakers: Sequence[Tiebreaker] = DEFAULT_TIEBREAKERS,
    pool: str | None = None,
) -> StandingsTable:
    """Rank `entry_ids` from `results`.

    Only results involving the given entries are counted, so you can pass a
    whole tournament's results and scope them to one pool.
    """
    members = set(entry_ids)
    relevant = [
        r for r in results if r.a_entry in members and r.b_entry in members
    ]

    rows = {eid: Standing(entry_id=eid) for eid in entry_ids}
    for result in relevant:
        a, b = rows[result.a_entry], rows[result.b_entry]
        a.played += 1
        b.played += 1
        a.points_for += result.points_a
        a.points_against += result.points_b
        b.points_for += result.points_b
        b.points_against += result.points_a
        a.games_won += result.games_a
        a.games_lost += result.games_b
        b.games_won += result.games_b
        b.games_lost += result.games_a
        if result.winner == result.a_entry:
            a.wins += 1
            b.losses += 1
        else:
            b.wins += 1
            a.losses += 1

    seed_index = {eid: i for i, eid in enumerate(entry_ids)}
    ordered = _rank_group(
        list(rows.values()), relevant, list(tiebreakers), seed_index, decided_by="wins"
    )

    for position, row in enumerate(ordered, start=1):
        row.rank = position

    return StandingsTable(pool=pool, rows=ordered)


def _rank_group(
    group: list[Standing],
    results: Sequence[MatchResult],
    tiebreakers: list[Tiebreaker],
    seed_index: dict[str, int],
    *,
    decided_by: str,
) -> list[Standing]:
    """Order one group, recursing into any sub-group that is still level."""
    if len(group) <= 1:
        for row in group:
            row.decided_by = row.decided_by or decided_by
        return group

    # Primary split is always match wins.
    if decided_by == "wins":
        buckets = _bucket(group, {r.entry_id: r.wins for r in group})
        out: list[Standing] = []
        for _, bucket in buckets:
            out.extend(
                _resolve_tie(bucket, results, tiebreakers, seed_index)
                if len(bucket) > 1
                else _tag(bucket, "wins")
            )
        return out

    return _resolve_tie(group, results, tiebreakers, seed_index)


def _resolve_tie(
    group: list[Standing],
    results: Sequence[MatchResult],
    tiebreakers: list[Tiebreaker],
    seed_index: dict[str, int],
) -> list[Standing]:
    # An entry with no matches yet has zero wins, which ties it with everyone
    # who has played and lost. Differential would then rank the team that hasn't
    # turned up above the team that lost a close one. Park the unplayed entries
    # at the bottom of their group instead — the table is provisional anyway.
    played = [row for row in group if row.played]
    idle = [row for row in group if not row.played]
    if played and idle:
        ranked_idle = sorted(idle, key=lambda r: (seed_index.get(r.entry_id, 0),
                                                  r.entry_id))
        for row in ranked_idle:
            row.decided_by = "unplayed"
        return _resolve_tie(played, results, tiebreakers, seed_index) + ranked_idle

    for position, criterion in enumerate(tiebreakers):
        keys = _criterion_keys(criterion, group, results)
        if keys is None:
            continue  # not applicable to this group (e.g. incomplete head-to-head)

        buckets = _bucket(group, keys)
        if len(buckets) == 1:
            continue  # everyone level on this criterion; try the next one

        remaining = tiebreakers[position + 1:]
        out: list[Standing] = []
        for _, bucket in buckets:
            if len(bucket) == 1:
                out.extend(_tag(bucket, criterion))
            else:
                out.extend(_resolve_tie(bucket, results, remaining, seed_index))
        return out

    # Nothing separated them. Order deterministically so the table is stable,
    # but flag it — this needs a coin flip, not a silent decision.
    for row in group:
        row.unresolved_tie = True
        row.decided_by = None
    return sorted(group, key=lambda row: (seed_index.get(row.entry_id, 0), row.entry_id))


def _criterion_keys(
    criterion: Tiebreaker, group: list[Standing], results: Sequence[MatchResult]
) -> dict[str, int] | None:
    ids = {row.entry_id for row in group}

    if criterion == "head_to_head":
        head = [r for r in results if r.a_entry in ids and r.b_entry in ids]
        # Only meaningful once every tied entry has played every other one.
        played = {frozenset((r.a_entry, r.b_entry)) for r in head}
        needed = {frozenset(pair) for pair in combinations(sorted(ids), 2)}
        if not needed <= played:
            return None
        return {
            row.entry_id: sum(1 for r in head if r.winner == row.entry_id)
            for row in group
        }

    if criterion == "wins":
        return {row.entry_id: row.wins for row in group}
    if criterion == "point_diff":
        return {row.entry_id: row.point_diff for row in group}
    if criterion == "game_diff":
        return {row.entry_id: row.game_diff for row in group}
    if criterion == "points_allowed":
        # Fewer is better, so negate to keep "higher key wins" uniform.
        return {row.entry_id: -row.points_against for row in group}
    return None


def _bucket(
    group: list[Standing], keys: dict[str, int]
) -> list[tuple[int, list[Standing]]]:
    """Group rows by their key value, best first."""
    buckets: dict[int, list[Standing]] = {}
    for row in group:
        buckets.setdefault(keys[row.entry_id], []).append(row)
    return sorted(buckets.items(), key=lambda kv: kv[0], reverse=True)


def _tag(rows: list[Standing], criterion: str) -> list[Standing]:
    for row in rows:
        if row.decided_by is None:
            row.decided_by = criterion
    return rows


def compute_pool_standings(
    pools: dict[str, list[str]],
    results: Sequence[MatchResult],
    *,
    tiebreakers: Sequence[Tiebreaker] = DEFAULT_TIEBREAKERS,
) -> dict[str, StandingsTable]:
    return {
        label: compute_standings(
            members, results, tiebreakers=tiebreakers, pool=label
        )
        for label, members in pools.items()
    }


def pool_rank_map(tables: dict[str, StandingsTable]) -> dict[tuple[str, int], str]:
    """(pool, rank) -> entry id, for feeding a playoff bracket.

    Entries in an unresolved tie are deliberately omitted: a bracket must not be
    seeded off a placement nobody has actually decided yet.
    """
    mapping: dict[tuple[str, int], str] = {}
    for label, table in tables.items():
        for row in table.rows:
            if row.unresolved_tie or row.played == 0:
                continue
            mapping[(label, row.rank)] = row.entry_id
    return mapping
