"""Round-robin generation via the circle method."""

from __future__ import annotations

from .seeding import pool_label, snake_pools
from .types import Draw, DrawMatch, Slot


def round_robin_rounds(entry_ids: list[str]) -> list[list[tuple[str | None, str | None]]]:
    """Pair every entry with every other exactly once, grouped into rounds.

    Circle method: entry 0 is fixed and the rest rotate. With an odd field a
    `None` placeholder is added, and whoever draws it sits that round out — so
    the byes are spread evenly instead of landing on one team.
    """
    ids: list[str | None] = list(entry_ids)
    if len(ids) < 2:
        return []
    if len(ids) % 2:
        ids.append(None)

    n = len(ids)
    rounds: list[list[tuple[str | None, str | None]]] = []
    for _ in range(n - 1):
        pairs = [(ids[i], ids[n - 1 - i]) for i in range(n // 2)]
        rounds.append(pairs)
        ids = [ids[0], ids[-1], *ids[1:-1]]
    return rounds


def round_robin(
    entry_ids: list[str], *, pool: str = "A", double_round: bool = False,
    id_prefix: str | None = None,
) -> list[DrawMatch]:
    """All matches for a single pool.

    `double_round` plays the whole schedule twice with the home/away sides
    reversed — common for small leagues that want more court time.
    """
    prefix = id_prefix or f"RR-{pool}"
    rounds = round_robin_rounds(entry_ids)
    if double_round:
        rounds = rounds + [[(b, a) for a, b in rnd] for rnd in rounds]

    matches: list[DrawMatch] = []
    for round_index, pairs in enumerate(rounds, start=1):
        slot = 0
        for a, b in pairs:
            if a is None or b is None:
                # The odd-field placeholder: that entry simply doesn't play this
                # round. Nothing to schedule.
                continue
            slot += 1
            matches.append(
                DrawMatch(
                    id=f"{prefix}-R{round_index}-M{slot}",
                    bracket="pool",
                    round=round_index,
                    slot=slot,
                    pool=pool,
                    a=Slot(entry_id=a),
                    b=Slot(entry_id=b),
                    label=f"Pool {pool} · Round {round_index}",
                )
            )
    return matches


def round_robin_draw(
    entry_ids: list[str], *, pool_count: int = 1, double_round: bool = False
) -> Draw:
    """A complete round-robin draw, optionally split into snake-seeded pools."""
    if len(entry_ids) < 2:
        raise ValueError("a round robin needs at least two entries")

    pools = (
        snake_pools(entry_ids, pool_count)
        if pool_count > 1
        else {pool_label(0): list(entry_ids)}
    )

    matches: list[DrawMatch] = []
    for label, members in pools.items():
        matches.extend(round_robin(members, pool=label, double_round=double_round))

    return Draw(kind="round_robin", matches=matches, pools=pools, seeds=list(entry_ids))
