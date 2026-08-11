"""Shared vocabulary for generated draws.

A draw is a list of `DrawMatch` with *unresolved slots*: a slot either names a
concrete entry, is a bye, or points at a result that hasn't happened yet
("winner of W-R1-M3", "the team that finishes 2nd in pool B"). Advancement is
then just resolving sources as results arrive — the bracket shape never changes.

Everything here is plain data. No database, no I/O, so the generators are
directly unit-testable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Bracket = Literal["pool", "winners", "losers", "final"]
SourceKind = Literal["winner", "loser", "pool_rank"]


class Source(BaseModel):
    """A forward reference to a result that will fill this slot."""

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    #: For kind in {winner, loser}.
    match_id: str | None = None
    #: For kind == pool_rank.
    pool: str | None = None
    rank: int | None = None

    def describe(self) -> str:
        if self.kind == "pool_rank":
            return f"{self.pool}{self.rank}"
        return f"{self.kind} of {self.match_id}"


class Slot(BaseModel):
    """One side of a match."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str | None = None
    source: Source | None = None
    #: A bye is an empty slot that resolves immediately — the opponent advances.
    bye: bool = False

    @property
    def is_resolved(self) -> bool:
        return self.entry_id is not None or self.bye

    def describe(self) -> str:
        if self.bye:
            return "BYE"
        if self.entry_id is not None:
            return self.entry_id
        if self.source is not None:
            return self.source.describe()
        return "?"


class Condition(BaseModel):
    """When a conditional match is actually played.

    `slot_a_lost` means: play this only if the entry that occupied slot A of
    `match_id` lost it. That is exactly the grand-final reset rule — the reset
    happens only when the winners-bracket team drops the first grand final.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["slot_a_lost"]
    match_id: str


class DrawMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    bracket: Bracket
    round: int
    #: Position within the round; also the display order.
    slot: int
    a: Slot
    b: Slot
    #: Round-robin only.
    pool: str | None = None
    #: Set on the grand-final reset match, which is only played if the losers
    #: bracket winner takes the first grand final.
    conditional: bool = False
    condition: Condition | None = None
    #: This match can decide the title. Marked explicitly rather than inferred
    #: from round numbers, which are per-bracket and not globally comparable —
    #: and so that a third-place match is never mistaken for the final.
    decides_title: bool = False
    label: str | None = None

    @property
    def is_bye(self) -> bool:
        return self.a.bye or self.b.bye

    def describe(self) -> str:
        return f"{self.id}: {self.a.describe()} vs {self.b.describe()}"


class Draw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    matches: list[DrawMatch]
    #: Pool label -> entry ids, for round-robin and pool-play draws.
    pools: dict[str, list[str]] = {}
    #: Entry ids in seed order (index 0 is the top seed).
    seeds: list[str] = []

    def by_id(self, match_id: str) -> DrawMatch:
        for match in self.matches:
            if match.id == match_id:
                return match
        raise KeyError(match_id)

    def rounds(self, bracket: Bracket) -> int:
        rounds = [m.round for m in self.matches if m.bracket == bracket]
        return max(rounds) if rounds else 0
