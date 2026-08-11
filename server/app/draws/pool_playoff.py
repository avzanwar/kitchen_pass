"""Pool play into a playoff bracket — the standard USA Pickleball event shape.

Snake-seeded round-robin pools, then the top N from each pool cross-seeded into
a knockout bracket. The bracket is generated up front with `pool_rank` slot
references, so the draw sheet is complete and printable before a ball is hit;
the references resolve as pools finish.
"""

from __future__ import annotations

from .round_robin import round_robin
from .seeding import snake_pools
from .single_elim import bracket_from_slots, qualifier_slots
from .types import Draw, DrawMatch


def pool_playoff_draw(
    entry_ids: list[str],
    *,
    pool_count: int = 2,
    advance_per_pool: int = 2,
    third_place: bool = False,
    double_round: bool = False,
) -> Draw:
    if pool_count < 1:
        raise ValueError("pool_count must be at least 1")
    if advance_per_pool < 1:
        raise ValueError("advance_per_pool must be at least 1")

    pools = snake_pools(entry_ids, pool_count)

    for label, members in pools.items():
        if len(members) < advance_per_pool:
            raise ValueError(
                f"pool {label} has {len(members)} entries but "
                f"{advance_per_pool} must advance"
            )
        if len(members) < 2:
            raise ValueError(f"pool {label} needs at least two entries")

    matches: list[DrawMatch] = []
    for label, members in pools.items():
        matches.extend(round_robin(members, pool=label, double_round=double_round))

    qualifiers = pool_count * advance_per_pool
    if qualifiers < 2:
        raise ValueError("a playoff needs at least two qualifiers")

    # Playoff rounds are numbered after the longest pool round so the two stages
    # sort into a sensible overall order.
    pool_rounds = max((m.round for m in matches), default=0)
    matches.extend(
        bracket_from_slots(
            qualifier_slots(sorted(pools), advance_per_pool),
            id_prefix="P",
            third_place=third_place,
            first_round_number=pool_rounds + 1,
        )
    )

    return Draw(
        kind="pool_playoff", matches=matches, pools=pools, seeds=list(entry_ids)
    )
