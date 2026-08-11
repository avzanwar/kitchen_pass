"""Single-elimination bracket generation."""

from __future__ import annotations

from .seeding import next_power_of_two, seed_order, seed_slots
from .types import Draw, DrawMatch, Slot, Source


def bracket_from_slots(
    first_round: list[Slot],
    *,
    id_prefix: str = "W",
    third_place: bool = False,
    kind: str = "single_elimination",
    first_round_number: int = 1,
) -> list[DrawMatch]:
    """Build a knockout bracket from an already-ordered list of round-1 slots.

    `first_round` must have a power-of-two length and be in bracket-slot order
    (consecutive pairs are the first-round matchups). Shared by
    `single_elimination`, which fills the slots with entry ids, and the pool
    playoff, which fills them with "2nd in pool B" references.
    """
    size = len(first_round)
    if size < 2 or size & (size - 1):
        raise ValueError(f"first_round must be a power of two >= 2, got {size}")

    total_rounds = size.bit_length() - 1
    matches: list[DrawMatch] = []

    for index in range(0, size, 2):
        slot = index // 2 + 1
        rnd = first_round_number
        matches.append(
            DrawMatch(
                id=f"{id_prefix}-R{rnd}-M{slot}",
                bracket="winners",
                round=rnd,
                slot=slot,
                a=first_round[index],
                b=first_round[index + 1],
                label=_round_label(1, total_rounds),
            )
        )

    for offset in range(2, total_rounds + 1):
        rnd = first_round_number + offset - 1
        prev = rnd - 1
        for slot in range(1, size // (2**offset) + 1):
            matches.append(
                DrawMatch(
                    id=f"{id_prefix}-R{rnd}-M{slot}",
                    bracket="winners",
                    round=rnd,
                    slot=slot,
                    a=Slot(source=Source(kind="winner",
                                         match_id=f"{id_prefix}-R{prev}-M{2 * slot - 1}")),
                    b=Slot(source=Source(kind="winner",
                                         match_id=f"{id_prefix}-R{prev}-M{2 * slot}")),
                    decides_title=(offset == total_rounds),
                    label=_round_label(offset, total_rounds),
                )
            )

    if total_rounds == 1:
        # A two-slot bracket is a single match, and that match is the final.
        matches[0].decides_title = True

    if third_place and total_rounds >= 2:
        semi = first_round_number + total_rounds - 2
        final_round = first_round_number + total_rounds - 1
        matches.append(
            DrawMatch(
                id=f"{id_prefix}-3P",
                bracket="final",
                round=final_round,
                slot=2,
                a=Slot(source=Source(kind="loser", match_id=f"{id_prefix}-R{semi}-M1")),
                b=Slot(source=Source(kind="loser", match_id=f"{id_prefix}-R{semi}-M2")),
                label="Third place",
            )
        )

    return matches


def single_elimination(
    entry_ids: list[str], *, third_place: bool = False, id_prefix: str = "W"
) -> Draw:
    """Build a seeded knockout bracket.

    `entry_ids` must be in seed order. The field is padded to the next power of
    two with byes, which land on the top seeds — seed 1 gets the first bye.

    Bye matches are emitted rather than skipped so the bracket renders with the
    right shape and advancement stays uniform; `resolve_draw` completes them
    without anyone stepping on a court.
    """
    if len(entry_ids) < 2:
        raise ValueError("a bracket needs at least two entries")

    slots = [
        Slot(entry_id=eid) if eid else Slot(bye=True) for eid in seed_slots(entry_ids)
    ]
    matches = bracket_from_slots(slots, id_prefix=id_prefix, third_place=third_place)
    return Draw(kind="single_elimination", matches=matches, seeds=list(entry_ids))


def qualifier_slots(
    pools: list[str], advance_per_pool: int
) -> list[Slot]:
    """Round-1 slots for a playoff fed by pool finishes.

    Qualifiers are ordered rank-major (every pool winner, then every runner-up),
    so standard bracket seeding keeps pool winners apart and — for the common
    two-advance case — pairs A1 vs B2 and B1 vs A2 rather than replaying a pool
    match in the first round.
    """
    qualifiers = [
        (pool, rank)
        for rank in range(1, advance_per_pool + 1)
        for pool in pools
    ]
    size = next_power_of_two(max(len(qualifiers), 2))
    return [
        Slot(source=Source(kind="pool_rank", pool=qualifiers[seed - 1][0],
                           rank=qualifiers[seed - 1][1]))
        if seed <= len(qualifiers)
        else Slot(bye=True)
        for seed in seed_order(size)
    ]


def _round_label(offset: int, total_rounds: int) -> str:
    remaining = total_rounds - offset
    if remaining == 0:
        return "Final"
    if remaining == 1:
        return "Semifinal"
    if remaining == 2:
        return "Quarterfinal"
    return f"Round of {2 ** (remaining + 1)}"
